"""OpenCode Go provider adapter — the ONLY file that knows OpenCode specifics.

Everything else in the app (router, cost engine, benchmark, frontend) talks to:
    generate(model_id, messages, max_tokens=..., temperature=...)
and gets back a provider-agnostic GenerateResult.

Endpoint families (source: https://opencode.ai/docs/go #endpoints, Sep 2026):
  chat_completions  POST {base}/chat/completions  OpenAI-compatible
  responses         POST {base}/responses          OpenAI Responses API
  messages          POST {base}/messages           Anthropic Messages API

Auth:  Authorization: Bearer <OPENCODE_API_KEY>
Docs also recommend sending x-opencode-session so OpenCode can optimize
prompt caching and not flag the traffic as abusive.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from backend.config import endpoint_family, settings


class OpenCodeError(RuntimeError):
    """Raised for transport, auth, rate-limit, timeout, or malformed responses."""


@dataclass
class GenerateResult:
    text: str
    model_id: str
    endpoint: str
    endpoint_family: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    raw_usage: dict = field(default_factory=dict)


def _headers() -> dict[str, str]:
    if not settings.openai_key:
        raise OpenCodeError(
            "OPENCODE_API_KEY is not set. Copy .env.example to .env and fill it."
        )
    h = {
        "Authorization": f"Bearer {settings.openai_key}",
        "Content-Type": "application/json",
    }
    if settings.session_id:
        h["x-opencode-session"] = settings.session_id
    return h


def _post_once(url: str, payload: dict, headers: dict, timeout_s: float,
               box: dict) -> None:
    """Runs in a daemon thread so a hung socket can be abandoned (see _post)."""
    try:
        box["resp"] = httpx.post(url, json=payload, headers=headers, timeout=timeout_s)
    except Exception as e:  # noqa: BLE001 — transported back to caller
        box["err"] = e


def _post(url: str, payload: dict, timeout_s: float = 60.0) -> dict:
    """POST with retries on 429/5xx/timeout. Raises OpenCodeError with actionable msg.

    Hard deadline: each attempt runs in a daemon thread joined at
    timeout_s + 15s. httpx read timeouts reset on every received byte, so a
    trickling/hung socket could otherwise wedge a benchmark or demo forever.
    An abandoned thread leaks harmlessly (daemon) and the attempt counts as a
    timeout — the escalation path then tries the next model.
    """
    import threading

    last_err: str = ""
    for attempt in (1, 2, 3):
        box: dict = {}
        t = threading.Thread(target=_post_once,
                             args=(url, payload, _headers(), timeout_s, box),
                             daemon=True)
        t.start()
        t.join(timeout_s + 15)
        if t.is_alive():
            last_err = f"hard deadline exceeded ({timeout_s + 15:.0f}s, attempt {attempt}/3)"
            time.sleep(2 * attempt)
            continue
        if "err" in box:
            e = box["err"]
            if isinstance(e, httpx.TimeoutException):
                last_err = f"timeout after {timeout_s}s (attempt {attempt}/3)"
                time.sleep(2 * attempt)
                continue
            if isinstance(e, httpx.HTTPError):
                raise OpenCodeError(f"transport error calling {url}: {e}") from e
            raise OpenCodeError(f"transport error calling {url}: {e}") from e
        r = box["resp"]
        if r.status_code == 401:
            raise OpenCodeError("401 Unauthorized — OPENCODE_API_KEY is invalid or missing.")
        if r.status_code == 402:
            raise OpenCodeError("402 Payment/limit — Go usage limit reached. Check console https://opencode.ai/auth .")
        if r.status_code == 429:
            last_err = f"429 rate-limited (attempt {attempt}/3)"
            time.sleep(3 * attempt)
            continue
        if 500 <= r.status_code < 600:
            last_err = f"{r.status_code} server error (attempt {attempt}/3): {r.text[:300]}"
            time.sleep(2 * attempt)
            continue
        if r.status_code != 200:
            raise OpenCodeError(f"{r.status_code} from {url}: {r.text[:500]}")
        try:
            return r.json()
        except Exception as e:
            raise OpenCodeError(f"malformed JSON from {url}: {r.text[:300]}") from e
    raise OpenCodeError(last_err or "request failed")


# ---- family-specific calls ----

def _generate_chat_completions(
    model_id: str, messages: list[dict], max_tokens: int, temperature: float
) -> GenerateResult:
    url = f"{settings.base_url}/chat/completions"
    t0 = time.perf_counter()
    data = _post(url, {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    })
    latency = int((time.perf_counter() - t0) * 1000)
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise OpenCodeError(f"unexpected chat/completions shape: {str(data)[:400]}") from e
    usage = data.get("usage", {}) or {}
    details = usage.get("prompt_tokens_details", {}) or {}
    return GenerateResult(
        text=text,
        model_id=model_id,
        endpoint=url,
        endpoint_family="chat_completions",
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
        cached_tokens=int(details.get("cached_tokens", 0) or 0),
        latency_ms=latency,
        raw_usage=usage,
    )


def _generate_responses(
    model_id: str, messages: list[dict], max_tokens: int, temperature: float
) -> GenerateResult:
    """OpenAI Responses API: input is a plain string or message list; output is data['output']."""
    url = f"{settings.base_url}/responses"
    # Flatten chat messages to a single input string (Responses API accepts this).
    flat = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
    )
    t0 = time.perf_counter()
    data = _post(url, {
        "model": model_id,
        "input": flat,
        "max_output_tokens": max_tokens,
    })
    latency = int((time.perf_counter() - t0) * 1000)
    text = ""
    try:
        for item in data.get("output", []):
            for part in item.get("content", []):
                if isinstance(part, dict) and "text" in part:
                    t = part["text"]
                    text += t if isinstance(t, str) else t.get("value", "")
    except Exception:
        text = ""
    if not text:  # fallbacks for minor shape variants
        text = data.get("output_text", "") or data.get("text", "") or ""
    if not text:
        raise OpenCodeError(f"unexpected responses-API shape: {str(data)[:400]}")
    usage = data.get("usage", {}) or {}
    return GenerateResult(
        text=text,
        model_id=model_id,
        endpoint=url,
        endpoint_family="responses",
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cached_tokens=int(usage.get("cached_tokens", 0) or 0),
        latency_ms=latency,
        raw_usage=usage,
    )


def _generate_messages(
    model_id: str, messages: list[dict], max_tokens: int, temperature: float
) -> GenerateResult:
    """Anthropic Messages API shape behind {base}/messages."""
    url = f"{settings.base_url}/messages"
    # Anthropic splits system out; fold it in simply for Phase 1.
    msgs = [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in messages if m.get("role") != "system"
    ]
    system = " ".join(m.get("content", "") for m in messages if m.get("role") == "system")
    payload: dict = {"model": model_id, "max_tokens": max_tokens, "messages": msgs or [{"role": "user", "content": "hi"}]}
    if system:
        payload["system"] = system
    t0 = time.perf_counter()
    # Same Bearer auth; if OpenCode ever requires anthropic-version header it
    # will be added here — isolated to this adapter by design.
    data = _post(url, payload)
    latency = int((time.perf_counter() - t0) * 1000)
    text = ""
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
    if not text:
        raise OpenCodeError(f"unexpected messages-API shape: {str(data)[:400]}")
    usage = data.get("usage", {}) or {}
    return GenerateResult(
        text=text,
        model_id=model_id,
        endpoint=url,
        endpoint_family="messages",
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cached_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        latency_ms=latency,
        raw_usage=usage,
    )


def generate(
    model_id: str,
    messages: list[dict],
    max_tokens: int = 512,
    temperature: float = 0.2,
) -> GenerateResult:
    """Provider-agnostic entry point. Router calls ONLY this."""
    family = endpoint_family(model_id)
    if family == "responses":
        return _generate_responses(model_id, messages, max_tokens, temperature)
    if family == "messages":
        return _generate_messages(model_id, messages, max_tokens, temperature)
    return _generate_chat_completions(model_id, messages, max_tokens, temperature)


def list_models() -> list[str]:
    """Unauthenticated metadata endpoint (docs: GET {base}/models)."""
    try:
        r = httpx.get(f"{settings.base_url}/models", timeout=15.0)
        r.raise_for_status()
        return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
    except Exception as e:
        raise OpenCodeError(f"could not fetch model catalog: {e}") from e
