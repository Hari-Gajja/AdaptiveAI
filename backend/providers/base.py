"""Provider adapter hierarchy — Phase G4.

BaseLLMProvider is the contract every customer model adapter implements:
    generate(messages, max_tokens, temperature) -> GenerateResult
    test_connection() -> {"ok": bool, "detail": str, "latency_ms": int}

The existing OpenCode adapter (backend/providers/opencode.py) stays untouched
and keeps serving the single-tenant pipeline + control plane. Customer models
use the adapters here so the gateway can talk to ANY OpenAI-compatible
endpoint (OpenAI, vLLM, LiteLLM, OpenRouter, private gateways...).

Security: adapters receive the decrypted provider key at construction and
never log it; error messages are redacted before raising.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from backend.providers.opencode import GenerateResult, OpenCodeError, _usage_int


class ProviderError(OpenCodeError):
    """Raised for transport/auth/rate-limit/timeout/malformed responses.

    Subclasses OpenCodeError so the optimizer's escalation path (which catches
    OpenCodeError and classifies transient vs permanent) treats customer
    provider failures exactly like platform provider failures: transient
    errors fall through to the next model, permanent ones surface honestly.
    """

    def __init__(self, message: str):
        # Redact anything that looks like a bearer key before storing.
        import re
        redacted = re.sub(r"(Bearer\s+)\S+", r"\1[REDACTED]", str(message))
        # OpenCode keys use oc_sk_; retain support for older sk- keys too.
        redacted = re.sub(r"(?:oc_)?sk-[A-Za-z0-9_\-]{8,}", "[REDACTED]", redacted)
        super().__init__(redacted)


@dataclass
class TestResult:
    ok: bool
    detail: str
    latency_ms: int = 0
    model_id: str = ""

    def view(self) -> dict:
        return {"ok": self.ok, "detail": self.detail,
                "latency_ms": self.latency_ms, "model_id": self.model_id}


class BaseLLMProvider(ABC):
    """Contract for all customer-model adapters."""

    name: str = "base"

    def __init__(self, model_id: str, base_url: str, api_key: str | None):
        self.model_id = model_id
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""

    @abstractmethod
    def generate(self, messages: list[dict], max_tokens: int = 512,
                 temperature: float = 0.2) -> GenerateResult:
        ...

    @abstractmethod
    def test_connection(self) -> TestResult:
        ...

    # ---- shared helpers ----
    def _auth_headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post(self, url: str, payload: dict, timeout_s: float = 45.0) -> dict:
        """POST with a hard deadline (daemon-thread join) so a hung socket
        can never wedge the gateway. Same pattern as providers/opencode.py."""
        box: dict = {}

        def _once() -> None:
            try:
                box["resp"] = httpx.post(url, json=payload,
                                         headers=self._auth_headers(),
                                         timeout=timeout_s)
            except Exception as e:  # noqa: BLE001
                box["err"] = e

        t = threading.Thread(target=_once, daemon=True)
        t0 = time.perf_counter()
        t.start()
        t.join(timeout_s + 15)
        latency = int((time.perf_counter() - t0) * 1000)
        if t.is_alive():
            raise ProviderError(f"hard deadline exceeded ({timeout_s + 15:.0f}s) calling {url}")
        if "err" in box:
            raise ProviderError(f"transport error calling {url}: {box['err']}")
        r = box["resp"]
        if r.status_code == 401:
            raise ProviderError("401 Unauthorized — provider API key invalid")
        if r.status_code == 429:
            raise ProviderError("429 rate-limited by provider")
        if 500 <= r.status_code < 600:
            raise ProviderError(f"{r.status_code} provider server error: {r.text[:300]}")
        if r.status_code != 200:
            raise ProviderError(f"{r.status_code} from {url}: {r.text[:300]}")
        try:
            return r.json()
        except Exception as e:
            raise ProviderError(f"malformed JSON from {url}: {r.text[:300]}") from e


class OpenAICompatibleProvider(BaseLLMProvider):
    """Any endpoint exposing POST {base_url}/chat/completions (OpenAI shape).

    Covers OpenAI itself, vLLM, LiteLLM, OpenRouter, Together, private
    gateways — everything that speaks the chat/completions dialect.
    """

    name = "openai_compatible"

    def generate(self, messages: list[dict], max_tokens: int = 512,
                 temperature: float = 0.2) -> GenerateResult:
        url = f"{self.base_url}/chat/completions"
        data = self._post(url, {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"unexpected chat/completions shape: {str(data)[:400]}") from e
        usage = data.get("usage", {}) or {}
        details = usage.get("prompt_tokens_details", {}) or {}
        return GenerateResult(
            text=text, model_id=self.model_id, endpoint=url,
            endpoint_family="chat_completions",
            input_tokens=_usage_int(usage, "prompt_tokens"),
            output_tokens=_usage_int(usage, "completion_tokens"),
            cached_tokens=_usage_int(details, "cached_tokens"),
            latency_ms=0, raw_usage=usage,
        )

    def test_connection(self) -> TestResult:
        t0 = time.perf_counter()
        try:
            r = self.generate([{"role": "user", "content": "ping"}],
                              max_tokens=8, temperature=0.0)
            return TestResult(ok=True, detail=f"reply: {r.text[:80]!r}",
                              latency_ms=int((time.perf_counter() - t0) * 1000),
                              model_id=self.model_id)
        except ProviderError as e:
            return TestResult(ok=False, detail=str(e)[:300],
                              latency_ms=int((time.perf_counter() - t0) * 1000),
                              model_id=self.model_id)


class OpenAIProvider(OpenAICompatibleProvider):
    """First-party OpenAI: default base_url when the customer omits one."""

    name = "openai"

    def __init__(self, model_id: str, base_url: str = "", api_key: str | None = None):
        super().__init__(model_id, base_url or "https://api.openai.com/v1", api_key)


class CustomProvider(OpenAICompatibleProvider):
    """Arbitrary customer endpoint. Same wire shape; base_url is required."""

    name = "custom"


def provider_for(provider: str, model_id: str, base_url: str,
                 api_key: str | None) -> BaseLLMProvider:
    """Factory: map a registry `provider` tag to the right adapter."""
    p = (provider or "custom").strip().lower()
    if p == "openai":
        return OpenAIProvider(model_id, base_url, api_key)
    if p in ("openai_compatible", "compatible"):
        return OpenAICompatibleProvider(model_id, base_url, api_key)
    return CustomProvider(model_id, base_url, api_key)
