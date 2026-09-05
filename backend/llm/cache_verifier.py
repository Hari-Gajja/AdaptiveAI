"""Cache verifier — LLM double-check for semantic-cache reuse.

SAFETY CONTRACT (spec, non-negotiable):
  - The verifier runs ONLY AFTER all deterministic safety gates passed.
  - It can only VETO an otherwise-eligible reuse (same=0 -> treat as miss).
  - It can NEVER approve a reuse that the gates blocked, and it can never
    touch the exact tier (exact hits are byte-identical, no verification).
  - On any control-plane failure the reuse decision falls back to the
    deterministic gate result (fail-open to the gates, never fail-closed
    to a wrong answer).
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.llm import config as cp_cfg
from backend.llm import opencode_client as client
from backend.llm.prompt_budget import budget_for_verifier


@dataclass
class VerifyOutcome:
    verified: bool          # True when the LLM confirmed reuse is safe
    skipped: bool           # verifier not run (disabled / failure)
    reason: str             # human-readable explanation for the trace
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    confidence: float | None = None
    usage_estimated: bool = False

    def view(self) -> dict:
        return {
            "verified": self.verified, "skipped": self.skipped,
            "reason": self.reason, "confidence": self.confidence,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms, "usage_estimated": self.usage_estimated,
        }


def verify_reuse(prompt: str, cached_answer: str) -> VerifyOutcome:
    """Ask the control-plane model whether the cached answer still answers the
    prompt. Called by the optimizer ONLY when the semantic gates already said
    safe. A failure or a 'same=0' verdict vetoes reuse."""
    if not cp_cfg.OPENCODE_ENABLED or not cp_cfg.CACHE_VERIFY_ENABLED:
        return VerifyOutcome(verified=False, skipped=True,
                             reason="cache verifier disabled — gate decision stands")
    bp = budget_for_verifier(prompt)
    try:
        res = client.verify_cache(bp.text, cached_answer)
    except client.ControlPlaneError as e:
        return VerifyOutcome(verified=False, skipped=True,
                             reason=f"verifier unavailable ({e}) — gate decision stands")
    same = int(res.parsed.get("same", 0))
    conf = float(res.parsed.get("c", 0.0))
    if same == 1:
        return VerifyOutcome(
            verified=True, skipped=False,
            reason=f"LLM verifier confirmed reuse (confidence {conf:.2f})",
            input_tokens=res.input_tokens, output_tokens=res.output_tokens,
            latency_ms=res.latency_ms, confidence=conf,
            usage_estimated=res.usage_estimated)
    return VerifyOutcome(
        verified=False, skipped=False,
        reason=f"LLM verifier VETOED reuse (same=0, confidence {conf:.2f}) — treating as miss",
        input_tokens=res.input_tokens, output_tokens=res.output_tokens,
        latency_ms=res.latency_ms, confidence=conf,
        usage_estimated=res.usage_estimated)
