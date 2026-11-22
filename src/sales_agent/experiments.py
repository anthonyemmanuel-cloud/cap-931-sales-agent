"""Experiments for the documentation. Every approach uses the SAME research, so the comparison is fair.

E1  No web data: the GPT model writes the brief from memory
E2  Single prompt: one big prompt with all the web sources
E3  Multi-agent chain with gpt-oss-20b
E4  Multi-agent chain with gpt-oss-120b (the final design)"""

import json
import time

import requests

from . import agents, config, llm, prompts
from .research import run_research
from .validation import validate

DEMO = dict(product_name="Wiz Cloud Security Platform", company_url="https://hcahealthcare.com",
            product_category="Cloud Security",
            competitors_text="crowdstrike.com, paloaltonetworks.com",
            value_proposition="Wiz gives security teams full visibility of risks across every cloud account in "
                              "minutes, without agents, so they can protect patient data and stay HIPAA compliant.",
            target_customer="Chief Information Security Officer")


def link_works(url):
    try:
        response = requests.get(url, headers=config.BROWSER_HEADERS, timeout=10, allow_redirects=True, stream=True)
        return response.status_code < 400
    except Exception:
        return False


def claims_from(answer, valid_ids):
    """Flatten every point in a single-prompt or no-data answer into (text, sources) pairs."""
    claims = []
    for key, items in answer.items():
        if key == "links" or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                text = item.get("point") or " ".join(str(item.get(k, "")) for k in ("name", "title")).strip()
                ids = [str(i).upper() for i in item.get("sources", []) if str(i).upper() in valid_ids] \
                    if isinstance(item.get("sources"), list) else []
                if text:
                    claims.append((text, ids))
    return claims


SECTION_KEYS = {"strategy": "strategy", "job_signals": "job_signals", "competitors": "competitor_mentions",
                "leaders": "leaders", "filings": "filings_summary", "talking_points": "talking_points"}


def score(name, claims, sections_filled, research, seconds, calls, tokens, invented_links=0, judge=True):
    cited = [c for c in claims if c[1]]
    verdicts = agents.check_claims(cited, research, config.DEFAULT_MODEL) if judge and cited else []
    return {"experiment": name, "seconds": round(seconds, 1), "llm_calls": calls, "tokens": tokens,
            "claims": len(claims), "claims_with_valid_source": len(cited),
            "supported_by_source": sum(verdicts), "sections_filled": f"{sections_filled}/6",
            "invented_or_broken_links": invented_links}


def usage_since(mark):
    used = llm.USAGE[mark:]
    return len(used), sum(u["prompt_tokens"] + u["completion_tokens"] for u in used)


def run(progress=print):
    inputs, errors, _ = validate(**DEMO)
    if errors:
        raise SystemExit(errors)
    progress("Collecting the web research once, so every experiment uses the same sources")
    research = run_research(inputs, progress=lambda m: progress("   " + m))
    valid = {s.id for s in research.sources}
    company = research.company_name
    competitors = ", ".join(research.competitor_names.values())
    fields = dict(company=company, product=inputs.product_name, category=inputs.product_category,
                  competitors=competitors, target=inputs.target_customer)
    rows = []

    progress("E1: no web data")
    llm.USAGE.clear()
    started = time.time()
    answer = llm.ask_json("No data", "", prompts.NO_DATA_PROMPT.format(url=inputs.company_url, **fields))
    links = [l for l in answer.get("links", []) if isinstance(l, str) and l.startswith("http")]
    broken = sum(not link_works(l) for l in links)
    claims = claims_from(answer, valid)
    filled = sum(bool(answer.get(k)) for k in SECTION_KEYS.values())
    calls, tokens = usage_since(0)
    rows.append(score("E1 No web data (gpt-oss-120b)", claims, filled, research, time.time() - started, calls,
                      tokens, invented_links=f"{broken} of {len(links)}", judge=False))

    progress("E2: single prompt with web data")
    llm.USAGE.clear()
    started = time.time()
    picked = research.sources[:14]
    text = "\n\n".join(s.as_prompt()[:900] for s in picked)
    answer = llm.ask_json("Single prompt", prompts.GUARDRAILS, prompts.SINGLE_PROMPT.format(sources=text, **fields))
    claims = claims_from(answer, valid)
    filled = sum(bool(answer.get(k)) for k in SECTION_KEYS.values())
    calls, tokens = usage_since(0)
    rows.append(score("E2 Single prompt with web data (gpt-oss-120b)", claims, filled, research,
                      time.time() - started, calls, tokens))

    for label, model in (("E3", "gpt-oss-20b"), ("E4", "gpt-oss-120b")):
        progress(f"{label}: multi-agent chain with {model}")
        inputs_copy, _, _ = validate(**DEMO)
        brief = agents.run_chain(inputs_copy, model=model, research=research, progress=lambda m: progress("   " + m))
        claims = [(agents.claim_text(i), i["sources"]) for key in SECTION_KEYS for i in getattr(brief, key)]
        filled = sum(bool(getattr(brief, key)) for key in SECTION_KEYS)
        stats = brief.stats
        row = score(f"{label} Multi-agent chain ({model})", claims, filled, research, stats["seconds"],
                    stats["llm_calls"], stats["tokens"])
        row["claims_removed_by_fact_checker"] = stats["claims_removed"]
        rows.append(row)

    config.OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_FOLDER / "experiments.json").write_text(json.dumps(rows, indent=2))
    header = list(rows[-1].keys())
    lines = ["# Experiments", "", f"Demo: {inputs.product_name} for {company}. Sources collected: {len(research.sources)}.",
             "", "| " + " | ".join(h.replace("_", " ") for h in header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(str(r.get(h, "n/a")) for h in header) + " |" for r in rows]
    (config.OUTPUT_FOLDER / "experiments.md").write_text("\n".join(lines) + "\n")
    progress(f"Saved {config.OUTPUT_FOLDER / 'experiments.md'}")
    return rows


def main():
    for row in run():
        print(row)
