# Sales Assistant Agent (CAP 931 Capstone)

A prototype sales assistant that helps a new sales rep prepare for a prospect. The rep enters what they sell and
who they are selling to, and a team of GPT agents researches public web data and writes a **one-page account
brief** with the company's strategy, competitor mentions, key leaders, a 10-K summary and links to every source.

Built with Python 3.13, uv, Streamlit and OpenAI's gpt-oss GPT models running on Groq.

## How it works

| Step | Agent | Model | What it does |
| --- | --- | --- | --- |
| 1 | Intake | gpt-oss-20b | Checks the request is a sales research task and identifies the product category if it was left blank |
| 2 | Researcher | no LLM | Reads the company website, recent news, job posts, competitor mentions and the latest SEC 10-K |
| 3 | Strategy analyst | gpt-oss-120b | Company activities, executive statements, job signals and product fit |
| 4 | Competitor analyst | gpt-oss-120b | Mentions of each competitor together with the company |
| 5 | Leadership analyst | gpt-oss-120b | Key leaders and why they matter for this sale |
| 6 | Filings analyst | gpt-oss-120b | What the 10-K says about priorities, technology and risk |
| 7 | Fact checker | gpt-oss-120b | Removes any claim its source does not support |
| 8 | Editor | gpt-oss-120b | Headline, talking points and next steps |

Every claim must cite a numbered source. The app removes claims with missing or fake source IDs, and adds the
links itself from the sources, so the model can never invent a URL.

## Project structure

| File | What it does |
| --- | --- |
| `streamlit_app.py` | The web page: inputs, progress, the one-pager, downloads, alerts tab |
| `src/sales_agent/validation.py` | Checks and cleans every input (URLs, lengths, prompt injection) |
| `src/sales_agent/documents.py` | Reads an uploaded PDF, Word, PowerPoint or text product sheet |
| `src/sales_agent/research.py` | The Researcher agent: website, news, job posts, competitor mentions, SEC 10-K |
| `src/sales_agent/prompts.py` | Every prompt, with guardrails and delimiters |
| `src/sales_agent/agents.py` | The multi-agent chain and the two fact checks |
| `src/sales_agent/llm.py` | Groq client with retries and token tracking |
| `src/sales_agent/onepager.py` | Builds the one-pager as Markdown and as a Per Scholas styled PDF |
| `src/sales_agent/alerts.py` | Email alerts for new press releases, news and job posts with chosen keywords |
| `src/sales_agent/experiments.py` | Compares four approaches on the same research |
| `src/sales_agent/cli.py` | Runs the demo from the terminal |
| `.github/workflows/alerts.yml` | Runs the alerts every weekday morning |

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. Create a free Groq API key at https://console.groq.com/keys.
3. Copy `.env.example` to `.env` and paste your key after `GROQ_API_KEY=`. Never commit `.env`.
4. Run the tests. They use fake web pages and a fake LLM, so they need no key or internet.

```bash
uv sync
uv run pytest
```

## Run

```bash
uv run streamlit run streamlit_app.py      # the app, opens in your browser
uv run sales-agent                         # the demo from the terminal, saves the one-pager in output/
uv run sales-agent-experiments             # the four experiments, saves output/experiments.md
uv run sales-agent-alerts --dry-run        # check the alert watchlist without sending email
```

## Deploy

The app runs on Streamlit Community Cloud. Choose `streamlit_app.py` as the entry point and Python 3.13, then add
`GROQ_API_KEY` under **Settings > Secrets**.

## Data sources

Company websites, DuckDuckGo and Google News search results, and SEC EDGAR (sec.gov). All are public. The app
shows the source of every point so the rep can check it before a meeting.
