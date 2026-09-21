"""Dynamic cost tiers — Phase G7.

Cost tiers are DERIVED from the customer's own model pool, never hard-coded:
  1 model  -> single tier (everything is "mid")
  2 models -> cheap / frontier split at the median price
  3+       -> cheap / mid / frontier by price terciles

Tiers are used by the hybrid router to sanity-check Nemotron's recommendation
and by escalation to know which direction is "up". Prices of 0 (free models)
sort as cheapest. Unpriced models (pricing_status unknown) get tier "unknown"
and are excluded from tier-based reasoning but can still be validated on
capability alone.
"""
from __future__ import annotations

from backend.core.customer_registry import CustomerModelEntry


def _price(m: CustomerModelEntry) -> float:
    """Blended price per 1M tokens (input+output average) for tier ordering."""
    if m.pricing_status != "configured":
        return float("inf")
    return (m.input_per_1M + m.output_per_1M) / 2.0


def derive_tiers(models: list[CustomerModelEntry]) -> dict[str, list[str]]:
    """model_id -> tier name, derived from the customer's pool."""
    priced = sorted((m for m in models if m.pricing_status == "configured"),
                    key=_price)
    tiers: dict[str, list[str]] = {}
    n = len(priced)
    if n == 0:
        return tiers
    if n == 1:
        tiers[priced[0].model_id] = "mid"
        return tiers
    if n == 2:
        tiers[priced[0].model_id] = "cheap"
        tiers[priced[1].model_id] = "frontier"
        return tiers
    # n >= 3: terciles by price. Ties keep the cheaper side.
    t = n / 3.0
    for i, m in enumerate(priced):
        if i < t:
            tiers[m.model_id] = "cheap"
        elif i < 2 * t:
            tiers[m.model_id] = "mid"
        else:
            tiers[m.model_id] = "frontier"
    return tiers


def tier_of(model_id: str, tiers: dict[str, list[str]]) -> str:
    return tiers.get(model_id, "unknown")


def tier_rank(tier: str) -> int:
    """cheap=0 < mid=1 < frontier=2 < unknown=3 (unknown sorts last)."""
    return {"cheap": 0, "mid": 1, "frontier": 2}.get(tier, 3)
