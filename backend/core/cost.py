"""Cost Engine — Phase 5. Actual spend, counterfactual baseline, savings.

Baseline = "always strongest available model": the enabled model with the
highest (input + output) price. Its cost is COMPUTED from measured token
usage x baseline pricing — no duplicate expensive API calls (spec 16).
Baseline quality comparison lives in the benchmark (needs references);
live /api/chat reports baseline COST only, never invented quality.
"""
from __future__ import annotations

from backend.core.registry import ModelEntry


def estimate_cost_usd(entry: ModelEntry, input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1_000_000 * entry.input_per_1M + output_tokens / 1_000_000 * entry.output_per_1M


def actual_cost_usd(entry: ModelEntry, input_tokens: int, output_tokens: int) -> float:
    return estimate_cost_usd(entry, input_tokens, output_tokens)


def baseline_model(models: list[ModelEntry]) -> ModelEntry:
    """Strongest-available proxy: highest combined per-1M price."""
    enabled = [m for m in models if m.enabled] or list(models)
    if not enabled:
        raise ValueError("no models to choose a baseline from")
    return max(enabled, key=lambda m: (m.input_per_1M + m.output_per_1M, m.model_id))


def cost_summary(actual_usd: float, baseline_entry: ModelEntry,
                 input_tokens: int, output_tokens: int) -> dict:
    baseline = estimate_cost_usd(baseline_entry, input_tokens, output_tokens)
    savings = baseline - actual_usd
    return {
        "baseline_model": baseline_entry.model_id,
        "baseline_cost_usd": round(baseline, 6),
        "actual_cost_usd": round(actual_usd, 6),
        "savings_usd": round(savings, 6),
        "savings_pct": round(100.0 * savings / baseline, 2) if baseline > 0 else 0.0,
    }
