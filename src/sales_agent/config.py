"""Settings in one place. Keys come from .env on your Mac or from Streamlit secrets when deployed."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS = PROJECT_ROOT / "assets"
OUTPUT_FOLDER = Path(os.environ.get("SALES_AGENT_OUTPUT", PROJECT_ROOT / "output"))

# Short name: (Groq model id, why we use it)
MODELS = {
    "gpt-oss-120b": ("openai/gpt-oss-120b", "OpenAI GPT model, 120B parameters. Most accurate, used for the analyst and editor agents"),
    "gpt-oss-20b": ("openai/gpt-oss-20b", "OpenAI GPT model, 20B parameters. Faster, used for the quick scope check and in experiments"),
}
DEFAULT_MODEL = "gpt-oss-120b"
FAST_MODEL = "gpt-oss-20b"

# SEC asks every program that uses EDGAR to name itself with a contact email
SEC_USER_AGENT = "Per Scholas student project sales-agent contact@example.com"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_CHARS_PER_SOURCE = 1400      # keeps every prompt under Groq's free tier token limits
MAX_SOURCES_PER_AGENT = 8


def secret(name, default=""):
    """Read a key from the environment first, then from Streamlit secrets if the app is deployed."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default
