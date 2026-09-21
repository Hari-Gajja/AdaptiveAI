"""Gateway router — Phase G7 (hybrid selection, step 2: deterministic validation).

Nemotron RECOMMENDS; this module VALIDATES. A recommendation is accepted only
when the model:
  1. exists in the customer's registry
  2. belongs to the requesting customer (isolation)
  3. is enabled and not blocked
  4. context window fits prompt + expected answer
  5. satisfies the task's required capabilities
  6. has valid pricing (configured; free models count as valid)
  7. is not otherwise excluded

Invalid recommendation -> deterministic route() over the customer's pool.
Provenance is always reported: nemotron_recommended | deterministic |
nemotron_rejected_fallback (+ reason), so the dashboard can show WHY.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.capabilities import capabilities_for
from backend.core.customer_registry import CustomerModelEntry
from backend.core.router import NoCapableModel, RoutingDecision, route
from backend.core.task_analyzer import TaskAnalysis
from backend.core.tiers import derive_tiers, tier_of


@dataclass
class GatewayDecision:
    selected_model: str
    provenance: str            # nemotron_recommended | deterministic | nemotron_rejected_fallback
    reason: str
    routing: RoutingDecision | None = None
    recommendation: dict | None = None
    validation_notes: list[str] = field(default_factory=list)


def _validate_candidate(entry: CustomerModelEntry, analysis: TaskAnalysis,
                        expected_out: int) -> list[str]:
    """Return a list of rejection reasons (empty = valid)."""
    notes: list[str] = []
    if not entry.enabled:
        notes.append("model is disabled")
    if entry.blocked:
        notes.append("model is blocked")
    if entry.pricing_status not in ("configured",):
        notes.append(f"pricing {entry.pricing_status} — cannot route cost-optimally")
    need = analysis.estimated_input_tokens + expected_out
    if need > entry.context_window:
        notes.append(f"context window: need ~{need} > {entry.context_window}")
    caps = capabilities_for(entry.model_id)
    for cap in analysis.required_capabilities:
        have = caps.get(cap, 0.0)
        need_q = analysis.required_thresholds.get(cap, 0.5)
        if have < need_q:
            notes.append(f"capability {cap}: {have:.2f} < required {need_q:.2f}")
    return notes


def select_model(analysis: TaskAnalysis, customer_id: str,
                 models: list[CustomerModelEntry],
                 recommendation: dict | None,
                 budgeted_prompt: str = "") -> GatewayDecision:
    """Hybrid selection: validate the recommendation, else route deterministically.

    `models` MUST already be scoped to customer_id (isolation is the caller's
    contract; we re-check ownership anyway — defense in depth).
    """
    pool = [m for m in models if m.customer_id == customer_id
            and m.pricing_status == "configured"]
    enabled = [m for m in pool if m.enabled and not m.blocked
               and m.pricing_status == "configured"]
    expected_out = analysis.estimated_output_tokens or 256

    if recommendation:
        rec_id = str(recommendation.get("model", "")).strip()
        entry = next((m for m in pool if m.model_id == rec_id), None)
        if entry is None:
            notes = ["recommended model not in customer's registry"]
        else:
            notes = _validate_candidate(entry, analysis, expected_out)
        if entry is not None and not notes:
            return GatewayDecision(
                selected_model=rec_id, provenance="nemotron_recommended",
                reason=str(recommendation.get("reason", ""))[:120],
                recommendation=recommendation, validation_notes=[])
        reason = "; ".join(notes) if notes else "recommendation invalid"
        if entry is not None:
            reason = f"recommendation rejected: {reason}"
        else:
            reason = f"recommendation rejected: {reason}"
    else:
        reason = "no recommendation (control plane unavailable or disabled)"

    # Deterministic fallback over the customer's pool. The single-tenant
    # router works on ModelEntry; adapt customer entries to the shape it needs.
    if not enabled:
        raise NoCapableModel(
            f"no enabled, priced models for customer '{customer_id}' — "
            f"register models with pricing (or declare prices) first")
    adapted = [_to_model_entry(m) for m in enabled]
    routing = route(analysis, adapted)
    prov = "nemotron_rejected_fallback" if recommendation else "deterministic"
    return GatewayDecision(selected_model=routing.selected_model, provenance=prov,
                           reason=reason, routing=routing,
                           recommendation=recommendation,
                           validation_notes=notes if recommendation else [])


def _to_model_entry(m: CustomerModelEntry):
    """Adapt a CustomerModelEntry to the single-tenant router's ModelEntry."""
    from backend.core.registry import ModelEntry
    return ModelEntry(
        model_id=m.model_id,
        provider=m.provider,
        enabled=m.enabled,
        display_name=m.display_name,
        description=m.description,
        input_per_1M=m.input_per_1M or 0.0,
        output_per_1M=m.output_per_1M or 0.0,
        cached_per_1M=m.cached_per_1M or 0.0,
        context_window=m.context_window,
        profile_status=m.profile_status,
        pricing_status="configured" if m.pricing_status == "configured" else "unavailable",
    )


def candidate_views(customer_id: str, models: list[CustomerModelEntry]) -> list[dict]:
    """Compact candidate list for the Nemotron selector prompt."""
    pool = [m for m in models if m.customer_id == customer_id
            and m.enabled and not m.blocked
            and m.pricing_status == "configured"]
    tiers = derive_tiers(pool)
    out = []
    for m in pool:
        out.append({
            "model_id": m.model_id,
            "tier": tier_of(m.model_id, tiers),
            "input_per_1M": m.input_per_1M,
            "output_per_1M": m.output_per_1M,
            "context_window": m.context_window,
            "capabilities": capabilities_for(m.model_id),
        })
    return out
