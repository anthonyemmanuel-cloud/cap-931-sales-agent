"""Alert system: watch a company for new press releases, news or job posts that contain chosen keywords,
and email the sales rep when something new appears. Run it by hand, or every morning with GitHub Actions."""

import argparse
import hashlib
import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from . import config
from .research import search_news, search_web

WATCHLIST = Path(os.environ.get("ALERTS_WATCHLIST", config.PROJECT_ROOT / "alerts.json"))
SEEN_FILE = Path(os.environ.get("ALERTS_SEEN", config.PROJECT_ROOT / "output" / "alerts_seen.json"))


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def item_key(item):
    return hashlib.sha256(item["url"].encode()).hexdigest()[:16]


def find_matches(company, keywords, include_jobs=True):
    """Search news (and job posts) and keep only results that contain at least one keyword."""
    query = f'"{company}" (' + " OR ".join(f'"{k}"' for k in keywords) + ")"
    results = [dict(r, kind="news") for r in search_news(query, max_results=10)]
    if include_jobs:
        results += [dict(r, kind="job post") for r in search_web(f'"{company}" jobs ' + " ".join(keywords), max_results=6)]
    matched = []
    for r in results:
        text = (r["title"] + " " + r.get("text", "")).lower()
        hits = [k for k in keywords if k.lower() in text]
        if hits:
            matched.append({**r, "keywords": hits})
    return matched


def new_items(watch, seen):
    """Return only the results this watch has not alerted about before."""
    fresh = []
    for item in find_matches(watch["company"], watch["keywords"], watch.get("include_jobs", True)):
        key = item_key(item)
        if key not in seen.setdefault(watch["company"], []):
            seen[watch["company"]].append(key)
            fresh.append(item)
    return fresh


def email_body(company, items):
    lines = [f"New public content about {company} that matches your keywords:", ""]
    for item in items:
        lines += [f"- [{item['kind']}] {item['title']}", f"  Keywords: {', '.join(item['keywords'])}",
                  f"  {item['url']}", ""]
    lines.append("Sent by the Sales Assistant Agent alert system.")
    return "\n".join(lines)


def send_email(to, subject, body):
    """Send with any SMTP server, for example Gmail with an app password. Without settings, print instead."""
    host, user, password = config.secret("SMTP_HOST"), config.secret("SMTP_USER"), config.secret("SMTP_PASSWORD")
    if not (host and user and password):
        print(f"(Email not configured, so printing instead)\nTo: {to}\nSubject: {subject}\n\n{body}")
        return False
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = user, to, subject
    message.set_content(body)
    with smtplib.SMTP(host, int(config.secret("SMTP_PORT", "587"))) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(message)
    return True


def run(watchlist=WATCHLIST, seen_file=SEEN_FILE, send=True):
    watches = load_json(watchlist, [])
    seen = load_json(seen_file, {})
    summary = []
    for watch in watches:
        try:
            fresh = new_items(watch, seen)
        except Exception as error:
            print(f"Could not check {watch['company']} ({type(error).__name__})")
            continue
        summary.append((watch["company"], len(fresh)))
        to = watch.get("email") if "@" in watch.get("email", "") else config.secret("ALERT_EMAIL")
        if fresh and send and "@" in (to or ""):
            send_email(to, f"Sales alert: {len(fresh)} new item(s) about {watch['company']}",
                       email_body(watch["company"], fresh))
    Path(seen_file).parent.mkdir(parents=True, exist_ok=True)
    Path(seen_file).write_text(json.dumps(seen, indent=2))
    for company, count in summary:
        print(f"{company}: {count} new item(s)")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Check watched companies and email new matching content")
    parser.add_argument("--dry-run", action="store_true", help="find new items but do not send email")
    args = parser.parse_args()
    run(send=not args.dry_run)
