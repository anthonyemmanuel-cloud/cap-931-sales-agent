"""Run the agent from the terminal with the demo inputs, without the Streamlit page."""

import argparse
import json

from . import agents, config, onepager
from .experiments import DEMO
from .validation import validate


def save_brief(brief):
    config.OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    stem = f"account_brief_{brief.inputs.company_id}"
    pdf_path = config.OUTPUT_FOLDER / f"{stem}.pdf"
    md_path = config.OUTPUT_FOLDER / f"{stem}.md"
    pdf_path.write_bytes(onepager.to_pdf(brief))
    md_path.write_text(onepager.to_markdown(brief))
    log = {"stats": brief.stats, "research_log": brief.research.log, "agent_answers": agents.LAST_RUN,
           "sources": [s.__dict__ for s in brief.research.sources]}
    (config.OUTPUT_FOLDER / f"{stem}_log.json").write_text(json.dumps(log, indent=2))
    return pdf_path, md_path


def main():
    parser = argparse.ArgumentParser(description="Create a one-page account brief for the demo scenario")
    parser.add_argument("--model", choices=list(config.MODELS), default=config.DEFAULT_MODEL)
    args = parser.parse_args()
    inputs, errors, warnings = validate(**DEMO)
    for warning in warnings:
        print("Note:", warning)
    if errors:
        raise SystemExit("\n".join(errors))
    brief = agents.run_chain(inputs, model=args.model)
    pdf_path, md_path = save_brief(brief)
    print(f"Saved {pdf_path}\nSaved {md_path}")
    print(brief.stats)
