"""The multi-agent chain: Intake -> Researcher -> 4 analysts -> Fact checker -> Editor."""

import re
import time
from dataclasses import dataclass, field

from . import config, llm, prompts
from .research import STRATEGY_WORDS, keywords_for, run_research

LAST_RUN = {}       # raw answers from each agent, saved for debugging and the documentation
CLAIM_SECTIONS = ("strategy", "job_signals", "fit", "filings", "competitors", "leaders", "talking_points")


class OutOfScope(Exception):
    pass


@dataclass
class Brief:
    inputs: object
    research: object
    product_category: str = ""
    product_highlights: list = field(default_factory=list)
    strategy: list = field(default_factory=list)
    job_signals: list = field(default_factory=list)
    fit: list = field(default_factory=list)
    filings: list = field(default_factory=list)
    competitors: list = field(default_factory=list)
    competitors_not_found: list = field(default_factory=list)
    leaders: list = field(default_factory=list)
    headline: str = ""
    talking_points: list = field(default_factory=list)
    next_steps: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def cited_ids(self):
        ids = set(re.findall(r"S\d+", self.headline))
        for section in CLAIM_SECTIONS:
            for item in getattr(self, section):
                ids.update(item.get("sources", []))
        return ids

    def cited_sources(self):
        """Sources in the order they are first cited, so the one-pager can number them neatly."""
        wanted = self.cited_ids()
        return [s for s in self.research.sources if s.id in wanted]


def inputs_block(inputs):
    competitors = ", ".join(f"{n} ({d})" for n, _, d in inputs.competitors) or "none given"
    lines = [f"Product name: {inputs.product_name}",
             f"Product category: {inputs.product_category or 'not given'}",
             f"Value proposition: {inputs.value_proposition or 'not given'}",
             f"Target company website: {inputs.company_url}",
             f"Competitors: {competitors}",
             f"Target customer: {inputs.target_customer or 'not given'}"]
    if inputs.product_document:
        lines.append("Product document (uploaded):\n" + inputs.product_document[:2500])
    return "\n".join(lines)


def sources_block(sources):
    return "\n\n".join(s.as_prompt() for s in sources[:config.MAX_SOURCES_PER_AGENT])


def tidy(value):
    """Remove URLs and raw source IDs like "(S43, S44)" from the text. The app adds clean [1] [2] links itself."""
    text = re.sub(r"https?://\S+", "", str(value))
    text = re.sub(r"[\(\[]\s*S\d+(\s*[,;]\s*S\d+)*\s*[\)\]]", "", text)
    text = re.sub(r"\bS\d+\b", "", text)
    return re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()


def clean_items(items, valid_ids, keys=("point",), limit=5):
    """Keep only well formed items whose sources really exist. This is the first, automatic fact check."""
    kept = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not all(str(item.get(k, "")).strip() for k in keys):
            continue
        ids = re.findall(r"S\d+", str(item.get("sources") or "").upper())   # accepts "S3", "[S3]", "S3, S5"
        ids = [i for i in ids if i in valid_ids]
        if not ids:
            continue
        clean = {k: tidy(v) for k, v in item.items() if k != "sources"}
        clean["sources"] = list(dict.fromkeys(ids))
        kept.append(clean)
    return kept[:limit]


def relevance(source, words):
    """How many of the topic and strategy words a source mentions. Used to pick the best sources for each agent."""
    text = (source.title + " " + source.text).lower()
    return sum(text.count(w) for w in words)


def pick(research, words, quotas):
    """Choose the most relevant sources of each kind, for example {"news": 3, "sec": 2}.
    Sources that mention none of the words are skipped, so an agent is never fed off-topic articles."""
    chosen = []
    for kind, count in quotas.items():
        ranked = sorted(research.by_kind(kind), key=lambda s: relevance(s, words), reverse=True)
        keep_anyway = kind in ("product",)
        chosen += [s for s in ranked if keep_anyway or relevance(s, words) > 0][:count]
    return chosen


def mentions(source, names):
    text = (source.title + " " + source.text).lower()
    return any(n.lower() in text for n in names)


# ---------------------------------------------------------------- the agents

def intake_agent(inputs):
    rule = ("repeat the category the rep gave" if inputs.category_was_given else
            "the rep did not give one, so identify the product category in 2 to 5 words from the product name, "
            "value proposition and document")
    answer = llm.ask_json("Intake", prompts.GUARDRAILS,
                          prompts.INTAKE.format(inputs=inputs_block(inputs), category_rule=rule),
                          model=config.FAST_MODEL, max_tokens=500)
    if answer.get("out_of_scope") or answer.get("in_scope") is False:
        raise OutOfScope(answer.get("reason") or "This request is not about researching a sales prospect.")
    category = inputs.product_category or str(answer.get("product_category", "")).strip()[:80]
    highlights = [str(h)[:100] for h in answer.get("product_highlights", []) if str(h).strip()][:4]
    return category, highlights


def analyst(name, template, sources, model, **fields):
    if not sources:
        return {}
    user = template.format(sources=sources_block(sources), **fields)
    answer = llm.ask_json(name, prompts.GUARDRAILS, user, model=model)
    LAST_RUN[name] = answer
    return answer


def claim_text(item):
    """The fact to check. For a leader we check the name and title only; "relevance" is the analyst's opinion."""
    return item.get("point") or f"{item.get('name')} is {item.get('title')}."


def check_claims(claims, research, model):
    """claims is a list of (text, [source ids]). Returns a list of True/False, one per claim.
    The LLM compares each claim with the text of the sources it cites."""
    if not claims:
        return []
    blocks = []
    for number, (text, ids) in enumerate(claims, start=1):
        quotes = [f"{i}: {research.find(i).text[:config.MAX_CHARS_PER_SOURCE]}" for i in ids[:2] if research.find(i)]
        blocks.append(f"Claim {number}: {text}\n" + ("\n".join(quotes) or "(no source)"))
    results = [True] * len(claims)
    size = 5                                          # 5 claims per call keeps prompts under the token limit
    for start in range(0, len(blocks), size):
        batch = blocks[start:start + size]
        answer = llm.ask_json("Fact checker", prompts.GUARDRAILS,
                              prompts.VERIFY.format(claims="\n\n".join(f"Claim {n}:" + b.split(":", 1)[1]
                                                                       for n, b in enumerate(batch, start=1))),
                              model=model, max_tokens=900)
        verdicts = [r for r in answer.get("results", []) if isinstance(r, dict)]
        LAST_RUN.setdefault("fact_checker", []).extend(verdicts)
        if len(verdicts) != len(batch):               # a confused answer never deletes claims
            continue
        for r in verdicts:
            number = re.findall(r"\d+", str(r.get("claim", "")))
            if number and r.get("supported") is False and 1 <= int(number[0]) <= len(batch):
                results[start + int(number[0]) - 1] = False
    return results


def verify_agent(brief, model):
    """Second fact check: remove every claim the source does not support."""
    places = [(section, item) for section in ("strategy", "job_signals", "fit", "filings", "competitors", "leaders")
              for item in getattr(brief, section)]
    verdicts = check_claims([(claim_text(item), item["sources"]) for _, item in places], brief.research, model)
    removed = 0
    for (section, item), supported in zip(places, verdicts):
        if not supported:
            getattr(brief, section).remove(item)
            removed += 1
    return len(places), removed


def run_chain(inputs, model=config.DEFAULT_MODEL, progress=print, verify=True, research=None):
    """Run every agent in order and return a finished Brief."""
    started = time.time()
    llm.USAGE.clear()
    LAST_RUN.clear()
    progress("Intake agent: checking the request and the product category")
    category, highlights = intake_agent(inputs)
    inputs.product_category = category

    if research is None:
        progress("Researcher agent: reading the website, news, job posts and SEC filings")
        research = run_research(inputs, progress=lambda m: progress("   " + m))
    brief = Brief(inputs=inputs, research=research, product_category=category, product_highlights=highlights)
    valid = {s.id for s in research.sources}
    company = research.company_name
    common = {"product": inputs.product_name, "category": category, "company": company}

    progress("Strategy analyst: company activities, executive statements and job signals")
    words = keywords_for(inputs) + list(STRATEGY_WORDS)
    strategy_sources = pick(research, words, {"news": 3, "job": 2, "sec": 2, "website": 1, "product": 1})
    answer = analyst("Strategy analyst", prompts.STRATEGY, strategy_sources, model,
                     inputs=inputs_block(inputs), **common)
    brief.strategy = clean_items(answer.get("strategy"), valid, limit=4)
    brief.job_signals = clean_items(answer.get("job_signals"), valid, limit=3)
    brief.fit = clean_items(answer.get("fit"), valid, limit=2)

    names = list(research.competitor_names.values())
    if names:
        progress("Competitor analyst: looking for competitor mentions")
        competitor_sources = research.by_kind("competitor") + [s for s in research.sources
                                                                if s.kind != "competitor" and mentions(s, names)]
        answer = analyst("Competitor analyst", prompts.COMPETITORS, competitor_sources, model,
                         competitors="\n".join(f"- {n}" for n in names), **common)
        brief.competitors = clean_items(answer.get("mentions"), valid, keys=("competitor", "point"), limit=6)
        found = {m["competitor"].lower() for m in brief.competitors}
        brief.competitors_not_found = [n for n in names if n.lower() not in found]

    progress("Leadership analyst: key leaders and what they said")
    leader_words = ["chief", "officer", "ceo", "president", "vice president", "cio", "ciso", "executive"] \
        + [w.lower() for w in (inputs.target_customer or "").split() if len(w) > 3]
    leader_sources = pick(research, leader_words, {"leadership": 5, "news": 2, "website": 1})
    answer = analyst("Leadership analyst", prompts.LEADERSHIP, leader_sources, model,
                     target=inputs.target_customer or "not given", **common)
    brief.leaders = clean_items(answer.get("leaders"), valid, keys=("name", "title"), limit=5)

    if research.by_kind("sec"):
        progress("Filings analyst: summarizing the 10-K")
        answer = analyst("Filings analyst", prompts.FILINGS, research.by_kind("sec"), model, **common)
        brief.filings = clean_items(answer.get("summary"), valid, limit=4)

    checked = removed = 0
    if verify:
        progress("Fact checker: comparing every claim with its source")
        checked, removed = verify_agent(brief, model)

    progress("Editor: writing the headline and talking points")
    findings = "\n".join(f"- [{section}] {item.get('point') or item.get('name') + ', ' + item.get('title', '')} "
                         f"({', '.join(item['sources'])})"
                         for section in ("strategy", "job_signals", "fit", "filings", "competitors", "leaders")
                         for item in getattr(brief, section))
    if findings:
        answer = llm.ask_json("Editor", prompts.GUARDRAILS, prompts.EDITOR.format(
            target=inputs.target_customer or "the buyer", company=company, product=inputs.product_name,
            value=inputs.value_proposition or "not given", findings=findings), model=model, max_tokens=900)
        cited_before = brief.cited_ids()
        headline = re.sub(r"https?://\S+", "", str(answer.get("headline", "")))
        headline = re.sub(r"S\d+", lambda m: m.group(0) if m.group(0) in cited_before else "", headline)
        headline = re.sub(r"\(\s*[,\s]*\)", "", headline)
        brief.headline = re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s{2,}", " ", headline)).strip()
        brief.talking_points = clean_items(answer.get("talking_points"), cited_before, limit=3)
        brief.next_steps = [str(s)[:160] for s in answer.get("next_steps", []) if str(s).strip()][:3]

    brief.stats = {
        "seconds": round(time.time() - started, 1),
        "sources_found": len(research.sources),
        "sources_cited": len(brief.cited_sources()),
        "claims_checked": checked,
        "claims_removed": removed,
        "llm_calls": len(llm.USAGE),
        "tokens": sum(u["prompt_tokens"] + u["completion_tokens"] for u in llm.USAGE),
        "model": model,
    }
    progress(f"Done in {brief.stats['seconds']} seconds")
    return brief
