"""Cost Engine — Phase 5. Actual spend, counterfactual baseline, savings.

Baseline = "always strongest available model": the enabled model with the
highest (input + output) price. Its cost is COMPUTED from measured token
usage x baseline pricing — no duplicate expensive API calls (spec 16).
Baseline quality comparison lives in the benchmark (needs references);
live /api/chat reports baseline COST only, never invented quality.
"""
from __future__ import annotations

from backend.core.registry import ModelEntry


def estimate_cost_usd(entry: ModelEntry, input_tokens: int | None, output_tokens: int | None) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    return input_tokens / 1_000_000 * entry.input_per_1M + output_tokens / 1_000_000 * entry.output_per_1M


def actual_cost_usd(entry: ModelEntry, input_tokens: int | None, output_tokens: int | None) -> float | None:
    return estimate_cost_usd(entry, input_tokens, output_tokens)


def baseline_model(models: list[ModelEntry]) -> ModelEntry:
    """Select the highest measured-capability model, with price as tie-breaker."""
    from backend.core.capabilities import CATEGORIES, capabilities_for, is_measured

    enabled = [m for m in models if m.enabled] or list(models)
    if not enabled:
        raise ValueError("no models to choose a baseline from")
    measured = [m for m in enabled if is_measured(m.model_id)]
    candidates = measured or enabled

    def score(model: ModelEntry) -> float:
        caps = capabilities_for(model.model_id)
        return sum(caps[c] for c in CATEGORIES) / len(CATEGORIES)

    if measured:
        return max(candidates, key=lambda m: (score(m), m.input_per_1M + m.output_per_1M, m.model_id))
    return max(candidates, key=lambda m: (m.input_per_1M + m.output_per_1M, m.model_id))


def baseline_method(models: list[ModelEntry]) -> str:
    """Describe whether the baseline is measured-capability best or a price fallback."""
    from backend.core.capabilities import CATEGORIES, capabilities_for, is_measured

    enabled = [m for m in models if m.enabled] or list(models)
    if any(is_measured(m.model_id) for m in enabled):
        return "measured_capability"
    return "most_expensive_fallback"


def cost_summary(actual_usd: float | None, baseline_entry: ModelEntry,
                 input_tokens: int | None, output_tokens: int | None,
                 method: str = "most_expensive_fallback") -> dict:
    baseline = estimate_cost_usd(baseline_entry, input_tokens, output_tokens)
    if actual_usd is None or baseline is None:
        return {
            "baseline_model": baseline_entry.model_id,
            "baseline_method": method,
            "baseline_cost_status": "unavailable",
            "baseline_cost_usd": None,
            "actual_cost_status": "unavailable",
            "actual_cost_usd": None,
            "savings_status": "unavailable",
            "savings_usd": None,
            "savings_pct": None,
            "savings_direction": "unavailable",
        }
    savings = baseline - actual_usd
    # Honest direction: savings can be NEGATIVE when the optimizer legitimately
    # costs more than the always-best counterfactual (escalation double-billing,
    # control-plane overhead, provider failures). Never hide it.
    direction = ("savings" if savings > 1e-9
                 else "loss" if savings < -1e-9
                 else "breakeven")
    return {
        "baseline_model": baseline_entry.model_id,
        "baseline_method": method,
        "baseline_cost_status": "measured",
        "baseline_cost_usd": round(baseline, 6),
        "actual_cost_status": "measured",
        "actual_cost_usd": round(actual_usd, 6),
        "savings_status": "measured",
        "savings_usd": round(savings, 6),
        "savings_pct": round(100.0 * savings / baseline, 2) if baseline > 0 else 0.0,
        "savings_direction": direction,
    }
