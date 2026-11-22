"""Talk to the GPT models on Groq. Tracks tokens and time for every call, and retries when rate limited."""

import json
import re
import time

from . import config

USAGE = []      # one dict per call: agent, model, tokens, seconds
_client = None


class LLMError(Exception):
    pass


def client():
    global _client
    if _client is None:
        import groq
        key = config.secret("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY is missing. Add it to your .env file, or to Streamlit secrets when deployed.")
        _client = groq.Groq(api_key=key)
    return _client


def chat(agent, system, user, model=config.DEFAULT_MODEL, json_mode=True, temperature=0.2, max_tokens=1800):
    """Send one message and return the text. Waits and tries again on rate limits."""
    import groq
    model_id = config.MODELS[model][0]
    options = {"model": model_id, "temperature": temperature, "max_completion_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if model_id.startswith("openai/gpt-oss"):
        options["reasoning_effort"] = "low"
    if json_mode:
        options["response_format"] = {"type": "json_object"}
    started = time.time()
    for attempt in range(6):
        try:
            response = client().chat.completions.create(**options)
            usage = response.usage
            USAGE.append({"agent": agent, "model": model, "seconds": round(time.time() - started, 1),
                          "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                          "completion_tokens": getattr(usage, "completion_tokens", 0)})
            return response.choices[0].message.content or ""
        except groq.RateLimitError as error:
            wait = float(error.response.headers.get("retry-after", 10 * (attempt + 1)))
            if wait > 90:
                raise LLMError(f"Groq's daily limit for {model_id} is used up. Try again later.") from error
            time.sleep(wait + 1)
        except groq.BadRequestError as error:
            if "response_format" in options and "json" in str(error).lower():
                options.pop("response_format")      # the model broke JSON once, ask again without JSON mode
                continue
            raise LLMError(f"Groq rejected the request: {error}") from error
        except (groq.APIConnectionError, groq.InternalServerError):
            time.sleep(5)
    raise LLMError(f"No answer from {model_id} after several tries.")


def parse_json(text):
    """Read a JSON object from the answer, even if it is wrapped in other text."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def ask_json(agent, system, user, model=config.DEFAULT_MODEL, **kwargs):
    return parse_json(chat(agent, system, user, model=model, json_mode=True, **kwargs))
