"""Gemini requests that survive a busy model: retries with backoff, then other models.

Gemini sometimes answers 503 "This model is currently experiencing high demand" (or 429 / 500 /
504). Such errors are temporary, so a scan must not be lost because of one: every model is tried
up to RETRIES times with a growing pause, then the next model of the list is used.

    GEMINI_MODEL            the first model (default gemini-3.1-flash-lite)
    GEMINI_FALLBACK_MODELS  comma separated, tried in order when the first one stays busy
                            (default gemini-2.5-flash-lite,gemini-2.5-flash)
"""
import json
import os
import time

DEFAULT_FALLBACK_MODELS = "gemini-2.5-flash-lite,gemini-2.5-flash"
RETRIES = 3
TRANSIENT_CODES = (429, 500, 502, 503, 504)


def model_list(primary):
    fallback = os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    models = [primary] + [m.strip() for m in fallback.split(",") if m.strip()]
    return list(dict.fromkeys(models))  # no duplicates, order kept


def _error_code(exc):
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def generate(client, primary_model, contents, config=None, sleep=time.sleep, log=print):
    """client.models.generate_content with retries and model fallback. Returns the response."""
    last_error = None
    for model in model_list(primary_model):
        for attempt in range(RETRIES):
            try:
                return client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as exc:  # google.genai.errors.APIError, or a network error
                last_error = exc
                code = _error_code(exc)
                if code == 404:  # this model is not available for the key: try the next one
                    log(f"  Модель {model} недоступна, пробуем следующую.")
                    break
                network = code is None and isinstance(exc, (OSError, ConnectionError, TimeoutError)) or \
                    type(exc).__name__ in ("ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError")
                if code not in TRANSIENT_CODES and not network:
                    raise
                if attempt < RETRIES - 1:
                    wait = 3 * 2 ** attempt  # 3, 6 seconds
                    log(f"  Gemini ({model}) занят ({code or type(exc).__name__}), повтор через {wait} с...")
                    sleep(wait)
        log(f"  Gemini ({model}) не ответил, пробуем другую модель...")
    raise last_error


def parse_json_object(text):
    """The JSON object in a model answer (tolerates ``` fences and text around it)."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"в ответе Gemini нет JSON: {text[:120]!r}")
    return json.loads(text[start:end + 1])
