"""Smart Router — Phase 3. Cheapest-capable selection, no cheap/frontier labels.

Minimize expected Cost(m) subject to ExpectedQuality(m, task) >= Required.
Low confidence (<0.60) -> safest qualifying model instead of cheapest.
If nothing qualifies -> strongest available with meets_requirements=False
(transparent fallback; the later Quality phase will verify the answer).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.capabilities import capabilities_for, overall_source
from backend.core.cost import estimate_cost_usd
from backend.core.registry import ModelEntry
from backend.core.task_analyzer import TaskAnalysis

LOW_CONFIDENCE = 0.60
DEFAULT_EXPECTED_OUTPUT_TOKENS = 256


class NoCapableModel(RuntimeError):
    pass


@dataclass
class CandidateView:
    model_id: str
    qualifies: bool
    expected_quality: float
    expected_cost_usd: float
    gaps: list[str] = field(default_factory=list)


@dataclass
class RoutingDecision:
    selected_model: str
    candidates: list[CandidateView]
    expected_cost_usd: float
    meets_requirements: bool
    confidence_action: str  # "normal" | "low_confidence_safety"
    capability_source: str
    decision_reason: list[str]


def _expected_quality(caps: dict[str, float], analysis: TaskAnalysis) -> tuple[float, list[str]]:
    gaps: list[str] = []
    margins: list[float] = []
    for cap in analysis.required_capabilities:
        have = caps.get(cap, 0.0)
        need = analysis.required_thresholds.get(cap, 0.5)
        margins.append(have - need)
        if have < need:
            gaps.append(f"{cap} {have:.2f} < required {need:.2f}")
    # quality in 0..1: 0.5 + min_margin clamped
    quality = max(0.0, min(1.0, 0.5 + (min(margins) if margins else 0.0)))
    return round(quality, 3), gaps


def route(analysis: TaskAnalysis, enabled_models: list[ModelEntry]) -> RoutingDecision:
    enabled_models = [m for m in enabled_models
                      if m.input_per_1M > 0 and m.output_per_1M > 0]
    if not enabled_models:
        raise NoCapableModel("no enabled models with configured pricing in registry")
    reasons: list[str] = [
        f"Task classified as {analysis.task_type} "
        f"(difficulty={analysis.difficulty_score}, confidence={analysis.confidence})",
        "Required: " + ", ".join(
            f"{c}>={analysis.required_thresholds[c]:.2f}"
            for c in analysis.required_capabilities),
    ]
    cands: list[CandidateView] = []
    for m in enabled_models:
        caps = capabilities_for(m.model_id)
        quality, gaps = _expected_quality(caps, analysis)
        cost = estimate_cost_usd(m, analysis.estimated_input_tokens,
                                 DEFAULT_EXPECTED_OUTPUT_TOKENS)
        ok = not gaps
        cands.append(CandidateView(m.model_id, ok, quality, round(cost, 6), gaps))
        reasons.append(
            f"{m.model_id}: {'qualifies' if ok else 'rejected (' + '; '.join(gaps) + ')'}; "
            f"expected quality={quality}, expected cost=${cost:.6f}")
    qualifying = [c for c in cands if c.qualifies]
    source = overall_source([m.model_id for m in enabled_models])
    if qualifying:
        if analysis.confidence < LOW_CONFIDENCE:
            pick = max(qualifying, key=lambda c: (c.expected_quality, -c.expected_cost_usd))
            action = "low_confidence_safety"
            reasons.append(f"Low confidence ({analysis.confidence} < {LOW_CONFIDENCE}): "
                           f"chose safest qualifying model {pick.model_id} over cheapest.")
        else:
            pick = min(qualifying, key=lambda c: (c.expected_cost_usd, -c.expected_quality))
            action = "normal"
            reasons.append(f"Selected cheapest qualifying model: {pick.model_id}.")
        return RoutingDecision(pick.model_id, cands, pick.expected_cost_usd,
                               True, action, source, reasons)
    # Nothing fully qualifies: transparent strongest-fallback.
    pick = max(cands, key=lambda c: (c.expected_quality, -c.expected_cost_usd))
    reasons.append(f"No model fully meets requirements; falling back to strongest "
                   f"available {pick.model_id} (flagged meets_requirements=False).")
    return RoutingDecision(pick.model_id, cands, pick.expected_cost_usd,
                           False, "normal", source, reasons)
