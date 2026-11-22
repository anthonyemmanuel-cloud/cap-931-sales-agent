"""Check and clean everything the sales rep types in, before any web search or LLM call."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

URL_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
SUSPICIOUS = re.compile(r"ignore (all|any|the|previous)|system prompt|you are now|disregard|jailbreak", re.I)
LIMITS = {"product_name": 80, "product_category": 120, "value_proposition": 400, "target_customer": 80}
COMMON_PREFIXES = ("www.", "investor.", "investors.", "ir.", "about.", "corporate.")


@dataclass
class SalesInputs:
    product_name: str
    company_url: str
    company_domain: str
    company_id: str
    product_category: str
    competitors: list = field(default_factory=list)    # list of (name, url, domain)
    value_proposition: str = ""
    target_customer: str = ""
    product_document: str = ""
    category_was_given: bool = True


def normalize_url(text):
    """Accept 'hcahealthcare.com', 'www.x.com' or a full link, and return (https url, domain) or raise ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("is empty")
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if not URL_PATTERN.match(host):
        raise ValueError(f"'{text}' does not look like a website address")
    return f"https://{host}{parsed.path.rstrip('/')}", host


def company_id_from_domain(domain):
    """'www.hcahealthcare.com' -> 'hcahealthcare'. Used to name files and match SEC company names."""
    for prefix in COMMON_PREFIXES:
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain.split(".")[0]


def name_from_domain(domain):
    return company_id_from_domain(domain).replace("-", " ").title()


def split_competitors(text):
    """Competitors may be typed one per line or separated by commas."""
    return [part.strip() for part in re.split(r"[\n,;]+", text or "") if part.strip()]


def validate(product_name, company_url, product_category, competitors_text, value_proposition,
             target_customer, product_document=""):
    """Return (SalesInputs, errors, warnings). errors stop the run; warnings are shown to the rep."""
    errors, warnings = [], []
    fields = {"product_name": product_name, "product_category": product_category,
              "value_proposition": value_proposition, "target_customer": target_customer}
    fields = {k: " ".join((v or "").split()) for k, v in fields.items()}
    if not fields["product_name"]:
        errors.append("Product name is required.")
    for key, limit in LIMITS.items():
        if len(fields[key]) > limit:
            errors.append(f"{key.replace('_', ' ').capitalize()} is too long (limit {limit} characters).")
    for key, value in fields.items():
        if SUSPICIOUS.search(value):
            errors.append(f"{key.replace('_', ' ').capitalize()} contains instructions for the AI. "
                          "Please describe the product only.")
    try:
        url, domain = normalize_url(company_url)
    except ValueError as error:
        errors.append(f"Company URL {error}.")
        url, domain = "", ""
    competitors = []
    for item in split_competitors(competitors_text)[:5]:
        try:
            c_url, c_domain = normalize_url(item)
        except ValueError as error:
            errors.append(f"Competitor {error}.")
            continue
        if c_domain == domain:
            warnings.append(f"{item} is the same as the company URL, so it was skipped.")
            continue
        competitors.append((name_from_domain(c_domain), c_url, c_domain))
    if len(split_competitors(competitors_text)) > 5:
        warnings.append("Only the first 5 competitors are used.")
    if not competitors:
        warnings.append("No competitors were entered, so the competitor section will be empty.")
    if not fields["value_proposition"]:
        warnings.append("Adding a value proposition makes the insights more relevant.")
    inputs = SalesInputs(
        product_name=fields["product_name"], company_url=url, company_domain=domain,
        company_id=company_id_from_domain(domain) if domain else "", product_category=fields["product_category"],
        competitors=competitors, value_proposition=fields["value_proposition"],
        target_customer=fields["target_customer"], product_document=(product_document or "")[:6000],
        category_was_given=bool(fields["product_category"]))
    return inputs, errors, warnings
