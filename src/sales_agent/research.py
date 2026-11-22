"""The Researcher agent. It does not use an LLM. It collects real public web data and numbers every source,
so the LLM agents can only write about things that were actually found, and every link is real."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config
from .validation import name_from_domain

SECTION_WORDS = ("about", "leadership", "management", "executive", "newsroom", "news", "press", "investor", "careers")
STRATEGY_WORDS = ("strategy", "invest", "technology", "digital", "cloud", "data", "security", "cyber", "platform",
                  "transform", "innovation", "artificial intelligence", "ai ", "partnership", "priority")
LEADER_TITLES = ("chief executive", "ceo", "president", "chief information", "cio", "chief technology", "cto",
                 "chief information security", "ciso", "chief data", "chief digital", "chief financial", "cfo",
                 "chief compliance", "chief operating", "coo", "chief medical", "chief nursing", "vice president")
TITLE_SPLIT = r"\s+[|\-–—]\s+|:\s+"     # "CrowdStrike: Stop breaches" -> "CrowdStrike"
JOB_SITES = ("linkedin.com/jobs", "indeed.com", "glassdoor.com", "careers", "jobs", "workday", "greenhouse", "lever.co")


@dataclass
class Source:
    id: str
    kind: str          # website, news, job, sec, competitor, leadership, product
    title: str
    url: str
    text: str
    date: str = ""
    publisher: str = ""

    def as_prompt(self):
        meta = " | ".join(x for x in (self.kind, self.publisher, self.date) if x)
        return f"[{self.id}] {self.title} ({meta})\nURL: {self.url}\n{self.text[:config.MAX_CHARS_PER_SOURCE]}"


@dataclass
class Research:
    company_name: str
    company_domain: str
    description: str = ""
    sec: dict = field(default_factory=dict)
    competitor_names: dict = field(default_factory=dict)    # domain -> name
    sources: list = field(default_factory=list)
    log: list = field(default_factory=list)
    seconds: float = 0.0

    def by_kind(self, *kinds):
        return [s for s in self.sources if s.kind in kinds]

    def find(self, source_id):
        return next((s for s in self.sources if s.id == source_id), None)


# ---------------------------------------------------------------- low level web helpers (easy to replace in tests)

def http_get(url, headers=None, timeout=20):
    response = requests.get(url, headers=headers or config.BROWSER_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def clean_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "form", "header"]):
        tag.decompose()
    text = soup.get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def fetch_page(url):
    """Return title, description, site name, visible text and links of a web page."""
    html = http_get(url).text
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    links = [(a.get_text(" ", strip=True), urljoin(url, a["href"])) for a in soup.find_all("a", href=True)]
    return {"title": (soup.title.get_text(strip=True) if soup.title else ""),
            "description": meta("og:description", "description"),
            "site_name": meta("og:site_name", "application-name"),
            "text": clean_text(html), "links": links}


def google_news(query, max_results=6):
    import feedparser
    feed = feedparser.parse("https://news.google.com/rss/search?q=" + quote_plus(query + " when:1y")
                            + "&hl=en-US&gl=US&ceid=US:en")
    results = []
    for entry in feed.entries[:max_results]:
        results.append({"title": entry.get("title", ""), "url": entry.get("link", ""),
                        "text": clean_text(entry.get("summary", "")),
                        "date": time.strftime("%Y-%m-%d", entry.published_parsed) if entry.get("published_parsed") else "",
                        "publisher": entry.get("source", {}).get("title", "")})
    return results


def duckduckgo_news(query, max_results=6):
    from ddgs import DDGS
    found = DDGS().news(query, max_results=max_results, timelimit="y")
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "text": r.get("body", ""),
             "date": (r.get("date") or "")[:10], "publisher": r.get("source", "")} for r in found if r.get("url")]


def search_news(query, max_results=6):
    """Recent news from the last year. Uses DuckDuckGo news, and Google News when DuckDuckGo fails
    or returns nothing (search sites sometimes give cloud servers empty results)."""
    try:
        results = duckduckgo_news(query, max_results)
        if results:
            return results
    except Exception:
        pass
    try:
        return google_news(query, max_results)
    except Exception:
        return []


def search_web(query, max_results=6):
    """General web search. Tries DuckDuckGo 3 times, then falls back to Google News so the run never stops."""
    from ddgs import DDGS
    for attempt in range(3):
        try:
            found = DDGS().text(query, max_results=max_results)
            return [{"title": r.get("title", ""), "url": r.get("href", ""), "text": r.get("body", "")}
                    for r in found if r.get("href")]
        except Exception:
            time.sleep(2 * (attempt + 1))         # DuckDuckGo blocks bursts of searches, so wait and retry
    return search_news(query, max_results)


def site_name(page, fallback):
    """'CrowdStrike.com' or 'CrowdStrike: Stop breaches' -> 'CrowdStrike'."""
    name = page["site_name"] or re.split(TITLE_SPLIT, page["title"])[0]
    name = re.sub(r"\.(com|org|net|io|ai|co)$", "", name.strip(), flags=re.I)
    return name if 2 < len(name) < 60 else fallback


# ---------------------------------------------------------------- SEC EDGAR (10-K for public companies)

def normalize(name):
    name = re.sub(r"\b(inc|corp|corporation|co|company|ltd|plc|holdings|group|the)\b\.?", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


def find_sec_company(company_id, company_name=""):
    """Match the website to an SEC registrant using the official company list."""
    tickers = http_get("https://www.sec.gov/files/company_tickers.json",
                       headers={"User-Agent": config.SEC_USER_AGENT}).json()
    wanted = {normalize(company_id), normalize(company_name)} - {""}
    best = None
    for item in tickers.values():
        title = normalize(item["title"])
        if not title:
            continue
        if title in wanted:
            return {"cik": int(item["cik_str"]), "ticker": item["ticker"], "name": item["title"]}
        if best is None and any(w.startswith(title) or title.startswith(w) for w in wanted if len(w) >= 4 and len(title) >= 4):
            best = {"cik": int(item["cik_str"]), "ticker": item["ticker"], "name": item["title"]}
    return best


def latest_10k(cik):
    data = http_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                    headers={"User-Agent": config.SEC_USER_AGENT}).json()
    recent = data["filings"]["recent"]
    for form, accession, document, date in zip(recent["form"], recent["accessionNumber"],
                                               recent["primaryDocument"], recent["filingDate"]):
        if form == "10-K":
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"
            return {"url": url, "date": date}
    return None


def best_passages(text, keywords, count=4, size=1600):
    """Split a long document into passages and keep the ones that mention the keywords most."""
    words = text.split()
    passages = [" ".join(words[i:i + size // 6]) for i in range(0, len(words), size // 6)]
    lowered = [k.lower() for k in keywords if k]
    scored = sorted(((sum(p.lower().count(k) for k in lowered), i, p) for i, p in enumerate(passages)), reverse=True)
    chosen = sorted(scored[:count], key=lambda x: x[1])
    return [p for score, _, p in chosen if score > 0]


# ---------------------------------------------------------------- the research plan

def keywords_for(inputs):
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", " ".join([inputs.product_category, inputs.value_proposition,
                                                             inputs.product_name]))
    stop = {"that", "with", "from", "your", "their", "this", "helps", "help", "teams", "into", "more", "less"}
    return list(dict.fromkeys(w.lower() for w in words if w.lower() not in stop))[:12]


def run_research(inputs, progress=print):
    """Collect sources about the company, its strategy, leaders, jobs and competitor mentions."""
    started = time.time()
    research = Research(company_name=name_from_domain(inputs.company_domain), company_domain=inputs.company_domain)
    raw = []    # (kind, title, url, text, date, publisher)
    topic = inputs.product_category or inputs.product_name

    def note(message):
        research.log.append(message)
        progress(message)

    # 1. company website, to learn the company's real name and what it does
    try:
        page = fetch_page(inputs.company_url)
        research.company_name = site_name(page, research.company_name)
        research.description = page["description"]
        raw.append(("website", page["title"] or research.company_name, inputs.company_url,
                    (page["description"] + " " + page["text"])[:4000], "", research.company_name))
        section_links = []
        for label, link in page["links"]:
            host = urlparse(link).hostname or ""
            if ".".join(inputs.company_domain.split(".")[-2:]) in host and any(w in label.lower() for w in SECTION_WORDS):
                if link not in section_links:
                    section_links.append(link)
        for link in section_links[:3]:
            try:
                sub = fetch_page(link)
                kind = "leadership" if re.search(r"leader|executive|management", link + sub["title"], re.I) else "website"
                raw.append((kind, sub["title"] or link, link, sub["text"][:4000], "", research.company_name))
            except Exception as error:
                note(f"Could not read {link} ({type(error).__name__})")
        note(f"Read the company website ({1 + min(3, len(section_links))} pages)")
    except Exception as error:
        note(f"Could not read the company website ({type(error).__name__}). Continuing with news and filings.")

    company = research.company_name

    # 2. competitor names from their websites
    for c_name, c_url, c_domain in inputs.competitors:
        name = c_name
        try:
            name = site_name(fetch_page(c_url), c_name)
        except Exception:
            pass
        research.competitor_names[c_domain] = name

    # 3. news, leadership, jobs and competitor searches run at the same time
    searches = {
        ("news", f'"{company}" {topic}'): search_news,
        ("news", f'"{company}" technology strategy'): search_news,
        ("leadership", f'"{company}" CEO OR "chief information officer" OR "chief information security officer" OR CTO'): search_news,
        ("leadership", f'"{company}" "{inputs.target_customer or "chief information officer"}"'): search_web,
        ("job", f'"{company}" jobs {topic} skills'): search_web,
    }
    for name in research.competitor_names.values():
        searches[("competitor", f'"{company}" "{name}"')] = search_web
    with ThreadPoolExecutor(max_workers=3) as pool:      # fewer at once, so search sites do not block us
        futures = {key: pool.submit(function, key[1]) for key, function in searches.items()}
    for (kind, query), future in futures.items():
        try:
            for r in future.result():
                if kind == "job" and not any(site in r["url"].lower() for site in JOB_SITES) \
                        and not re.search(r"\b(job|jobs|hiring|career|engineer|analyst)\b", r["title"], re.I):
                    continue
                raw.append((kind, r["title"], r["url"], r["text"], r.get("date", ""), r.get("publisher", "")))
            note(f"Searched: {query}")
        except Exception as error:
            note(f"Search failed: {query} ({type(error).__name__})")

    # 4. SEC 10-K, for public companies
    try:
        sec = find_sec_company(inputs.company_id, company)
        if sec:
            filing = latest_10k(sec["cik"])
            if filing:
                research.sec = {**sec, **filing}
                text = clean_text(http_get(filing["url"], headers={"User-Agent": config.SEC_USER_AGENT}, timeout=60).text)
                keys = keywords_for(inputs) + ["strategy", "cybersecurity", "technology", "executive officers"] \
                    + [n.lower() for n in research.competitor_names.values()]
                for number, passage in enumerate(best_passages(text, keys, count=5), start=1):
                    raw.append(("sec", f"{sec['name']} Form 10-K, part {number}", filing["url"], passage,
                                filing["date"], "SEC EDGAR"))
                # Every 10-K lists the executive officers with their titles, a reliable source for the Leadership section
                officers = best_passages(text, ["executive officers", "chief executive officer", "chief financial officer",
                                                "chief operating officer", "president", "age"], count=2)
                for number, passage in enumerate(officers, start=1):
                    raw.append(("leadership", f"{sec['name']} Form 10-K, executive officers ({number})", filing["url"],
                                passage, filing["date"], "SEC EDGAR"))
                note(f"Read the latest 10-K for {sec['name']} ({sec['ticker']}), filed {filing['date']}")
        else:
            note("No SEC filing found, so the company may be private. Skipping the 10-K.")
    except Exception as error:
        note(f"Could not read SEC EDGAR ({type(error).__name__})")

    # 5. product sheet uploaded by the rep
    if inputs.product_document:
        raw.append(("product", f"{inputs.product_name} product overview (uploaded)", "", inputs.product_document,
                    "", "Sales rep upload"))

    # 6. remove duplicates and number the sources S1, S2, ...
    seen = set()
    for kind, title, url, text, date, publisher in raw:
        key = (kind if publisher == "SEC EDGAR" else "", url or title, text[:80])   # a 10-K passage may serve 2 agents
        if key in seen or not text.strip():
            continue
        seen.add(key)
        research.sources.append(Source(f"S{len(research.sources) + 1}", kind, title.strip()[:160], url, text.strip(),
                                       date, publisher))
    research.seconds = round(time.time() - started, 1)
    note(f"Collected {len(research.sources)} sources in {research.seconds} seconds")
    return research
