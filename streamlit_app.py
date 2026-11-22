"""Streamlit front end for the Sales Assistant Agent. Run it with:  uv run streamlit run streamlit_app.py"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))   # lets Streamlit Cloud find the package without installing it

from sales_agent import agents, alerts, config, llm, onepager
from sales_agent.documents import SUPPORTED, read_upload
from sales_agent.experiments import DEMO
from sales_agent.validation import split_competitors, validate

st.set_page_config(page_title="Sales Assistant Agent", page_icon="🧭", layout="wide")

FIELDS = ["product_name", "company_url", "product_category", "competitors_text", "value_proposition", "target_customer"]


def load_demo():
    for key in FIELDS:
        st.session_state[key] = DEMO[key]


def clear_form():
    for key in FIELDS:
        st.session_state[key] = ""
    st.session_state.pop("brief", None)


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.image(str(config.ASSETS / "perscholas_logo.png"), width=180)
    st.header("Settings")
    model = st.selectbox("GPT model for the analyst agents", list(config.MODELS),
                         help="\n\n".join(f"**{k}**: {v[1]}" for k, v in config.MODELS.items()))
    verify = st.toggle("Fact checker agent", value=True,
                       help="Removes any claim that its source does not support. Slower but more accurate.")
    st.button("Load demo example", on_click=load_demo, width="stretch")
    st.button("Clear form", on_click=clear_form, width="stretch")
    st.divider()
    st.caption("This assistant only researches prospective accounts for sales reps. It does not answer general "
               "questions. All insights come from public web pages, news, job posts and SEC filings, and every "
               "point links to its source.")
    if not config.secret("GROQ_API_KEY"):
        st.error("GROQ_API_KEY is missing. Add it to your .env file or Streamlit secrets.")

st.title("🧭 Sales Assistant Agent")
st.write("Enter what you sell and who you are selling to. A team of AI agents researches the account and writes a "
         "one-page brief with the company's strategy, competitor mentions, leaders and filings.")

tab_brief, tab_alerts, tab_how = st.tabs(["Account brief", "Alerts", "How it works"])

# ---------------------------------------------------------------- tab 1: inputs and the one-pager
with tab_brief:
    with st.form("inputs"):
        left, right = st.columns(2)
        with left:
            st.text_input("Product name *", key="product_name", placeholder="e.g. Wiz Cloud Security Platform")
            st.text_input("Company URL *", key="company_url", placeholder="e.g. hcahealthcare.com",
                          help="The website of the company you are selling to")
            st.text_input("Product category", key="product_category", placeholder="e.g. Cloud Security",
                          help="Leave blank and the Intake agent will identify it from your description")
            st.text_area("Competitors (URLs)", key="competitors_text", height=96,
                         placeholder="One per line or separated by commas, e.g. crowdstrike.com")
        with right:
            st.text_area("Value proposition", key="value_proposition", height=96,
                         placeholder="One sentence on the value of your product")
            st.text_input("Target customer", key="target_customer",
                          placeholder="Name or title of the person you are selling to")
            upload = st.file_uploader("Optional: product overview sheet or deck", type=list(SUPPORTED))
        submitted = st.form_submit_button("Generate account brief", type="primary", width="stretch")

    if submitted:
        document, upload_error = "", None
        if upload is not None:
            try:
                document = read_upload(upload.name, upload.getvalue())
            except ValueError as error:
                upload_error = str(error)
        inputs, errors, warnings = validate(**{k: st.session_state.get(k, "") for k in FIELDS},
                                            product_document=document)
        if upload_error:
            errors.append(upload_error)
        for warning in warnings:
            st.warning(warning)
        if errors:
            for error in errors:
                st.error(error)
        else:
            if document:
                st.success(f"Read {len(document):,} characters from {upload.name}")
            try:
                with st.status("The agents are working...", expanded=True) as status:
                    brief = agents.run_chain(inputs, model=model, verify=verify, progress=status.write)
                    status.update(label=f"Brief ready in {brief.stats['seconds']} seconds", state="complete",
                                  expanded=False)
                st.session_state["brief"] = brief
                st.session_state["usage"] = list(llm.USAGE)
                st.session_state["raw"] = json.loads(json.dumps(agents.LAST_RUN, default=str))
            except agents.OutOfScope as reason:
                st.error(f"This assistant only researches sales prospects. {reason}")
            except llm.LLMError as error:
                st.error(str(error))

    brief = st.session_state.get("brief")
    if brief:
        st.divider()
        stats = brief.stats
        cols = st.columns(5)
        cols[0].metric("Sources found", stats["sources_found"])
        cols[1].metric("Sources cited", stats["sources_cited"])
        cols[2].metric("Claims checked", stats["claims_checked"])
        cols[3].metric("Unsupported removed", stats["claims_removed"])
        cols[4].metric("Time", f"{stats['seconds']} s")
        markdown = onepager.to_markdown(brief)
        pdf = onepager.to_pdf(brief)
        name = f"account_brief_{brief.inputs.company_id}"
        d1, d2 = st.columns(2)
        d1.download_button("Download one-pager (PDF)", pdf, f"{name}.pdf", "application/pdf",
                           type="primary", width="stretch")
        d2.download_button("Download as Markdown", markdown, f"{name}.md", "text/markdown", width="stretch")
        with st.container(border=True):
            st.markdown(markdown)
        with st.expander(f"All {len(brief.research.sources)} sources the Researcher found"):
            st.dataframe(pd.DataFrame([{"id": s.id, "type": s.kind, "title": s.title, "date": s.date, "url": s.url}
                                       for s in brief.research.sources]), width="stretch", hide_index=True)
        with st.expander("Research log and agent usage"):
            st.write("\n".join(f"- {line}" for line in brief.research.log))
            if st.session_state.get("usage"):
                st.dataframe(pd.DataFrame(st.session_state["usage"]), width="stretch", hide_index=True)
        with st.expander("Raw answers from each agent (for debugging)"):
            st.json(st.session_state.get("raw", {}), expanded=False)

# ---------------------------------------------------------------- tab 2: alerts
with tab_alerts:
    st.subheader("Keyword alerts")
    st.write("Watch a company for new press releases, news or job posts that mention your keywords. "
             "**Check now** shows what is new. Saved watches are checked every morning by a GitHub Actions job, "
             "which emails anything new.")
    with st.form("alert"):
        a_company = st.text_input("Company name", value="HCA Healthcare")
        a_keywords = st.text_input("Keywords (comma separated)", value="cloud security, cybersecurity, CISO")
        a_email = st.text_input("Send alerts to (email)", help="Leave blank to use ALERT_EMAIL from your secrets, so your address never goes to GitHub")
        a_jobs = st.checkbox("Include job posts", value=True)
        check, save = st.columns(2)
        do_check = check.form_submit_button("Check now", width="stretch")
        do_save = save.form_submit_button("Save to watchlist", width="stretch")
    keywords = split_competitors(a_keywords)
    if do_check:
        if not a_company or not keywords:
            st.error("Enter a company and at least one keyword.")
        else:
            with st.spinner("Searching..."):
                try:
                    found = alerts.find_matches(a_company, keywords, a_jobs)
                except Exception as error:
                    found = []
                    st.error(f"Search failed ({type(error).__name__}). Try again in a minute.")
            st.write(f"**{len(found)}** matching item(s)")
            for item in found:
                st.markdown(f"- **[{item['title']}]({item['url']})** ({item['kind']}, keywords: "
                            f"{', '.join(item['keywords'])})")
    if do_save:
        if not (a_company and keywords) or (a_email and "@" not in a_email):
            st.error("Enter a company, keywords and, if you add one, a valid email.")
        else:
            watches = alerts.load_json(alerts.WATCHLIST, [])
            watches = [w for w in watches if w["company"].lower() != a_company.lower()]
            watches.append({"company": a_company, "keywords": keywords, "email": a_email, "include_jobs": a_jobs})
            alerts.WATCHLIST.write_text(json.dumps(watches, indent=2))
            st.success(f"Saved. Watching {len(watches)} compan{'y' if len(watches) == 1 else 'ies'}.")
    st.caption(f"Watchlist file: {alerts.WATCHLIST.name}")

# ---------------------------------------------------------------- tab 3: how it works
with tab_how:
    st.subheader("The agent team")
    st.markdown("""
| Step | Agent | Model | What it does |
| --- | --- | --- | --- |
| 1 | Intake | gpt-oss-20b | Checks the request is a sales research task and identifies the product category if blank |
| 2 | Researcher | no LLM | Reads the company website, recent news, job posts, competitor mentions and the latest SEC 10-K |
| 3 | Strategy analyst | selected model | Company activities, executive statements, job signals and product fit |
| 4 | Competitor analyst | selected model | Mentions of each competitor together with the company |
| 5 | Leadership analyst | selected model | Key leaders and why they matter for this sale |
| 6 | Filings analyst | selected model | What the 10-K says about priorities, technology and risk |
| 7 | Fact checker | selected model | Removes any claim its source does not support |
| 8 | Editor | selected model | Headline, talking points and next steps |
""")
    st.markdown("Every claim must cite a source ID, the app removes claims with missing or fake IDs, and links "
                "are added by the app from the sources, so the model can never invent a URL.")
