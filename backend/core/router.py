"""Smart Router — Phase 3. Capability-constrained, task-LEVEL-aware selection.

Minimize expected Cost(m) subject to ExpectedQuality(m, task) >= Required,
with the selection policy chosen by the LEVEL of the task (from difficulty):

  easy    -> cheapest qualifying model (maximize savings, gamble is safe)
  medium  -> cheapest qualifier whose capability margin clears MEDIUM_MARGIN;
             a thin margin means a likely quality failure, and a failed cheap
             attempt + escalation costs MORE than the stronger model directly
  hard    -> strongest qualifying model DIRECTLY (no cheap-first gamble)
  low confidence (<0.60) -> safest qualifying model, regardless of level

If nothing qualifies -> strongest available with meets_requirements=False
(transparent fallback; the later Quality phase will verify the answer).
Measured capability always outranks neutral prior-only estimates when picking
the "strongest" model — an unprofiled model's 0.70 guess must not beat a
measured score.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.capabilities import capabilities_for, is_measured, overall_source
from backend.core.cost import baseline_model, estimate_cost_usd
from backend.core.registry import ModelEntry
from backend.core.task_analyzer import TaskAnalysis

LOW_CONFIDENCE = 0.60
DEFAULT_EXPECTED_OUTPUT_TOKENS = 256
# Task-level bands: easy < MEDIUM_DIFFICULTY <= medium < HIGH_DIFFICULTY <= hard.
MEDIUM_DIFFICULTY = 0.35
HIGH_DIFFICULTY = 0.65
# Medium tasks only gamble on a cheap model when its worst capability margin
# clears this bar. Below it, the expected cost of (likely fail -> escalate)
# exceeds the stronger model's price premium, so route strong directly.
MEDIUM_MARGIN = 0.05


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
    confidence_action: str  # "normal" | "low_confidence_safety" | "high_difficulty_safety"
    capability_source: str
    decision_reason: list[str]
    context_limit_triggered: bool = False  # §11: a model's window was too small
    task_level: str = "easy"  # easy | medium | hard (from difficulty_score)


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


def task_level(analysis: TaskAnalysis) -> str:
    """Task LEVEL from difficulty + explicit quality requirement.

    `quality_requirement == "high"` (difficulty >= 0.7) forces hard; otherwise
    the difficulty bands decide. Exposed on the decision so the UI and
    benchmark can show WHY a task skipped the cheap model.
    """
    if analysis.quality_requirement == "high" or analysis.difficulty_score >= HIGH_DIFFICULTY:
        return "hard"
    if analysis.difficulty_score >= MEDIUM_DIFFICULTY:
        return "medium"
    return "easy"


def _strongest(cands: list[CandidateView], measured_ids: set[str],
               baseline_cost_usd: float | None = None) -> CandidateView:
    """Highest expected quality; measured models outrank prior-only guesses.

    An unprofiled model's neutral 0.70 estimate must not beat a measured
    score — otherwise "strongest" silently picks an unverified model.
    With a baseline_cost_usd cap, models priced ABOVE the always-best
    baseline's tier rank below in-tier models: paying above-baseline prices
    is the one way optimized spend can exceed the counterfactual baseline,
    so in-tier models are preferred even at slightly lower expected quality.
    """
    def rank(c: CandidateView) -> tuple:
        above = (baseline_cost_usd is not None
                 and c.expected_cost_usd > baseline_cost_usd + 1e-12)
        return (not above, c.expected_quality, c.model_id in measured_ids,
                -c.expected_cost_usd)
    return max(cands, key=rank)


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
    # §12: expected output tokens come from the analyzer's predicted budget
    # (spec §3) instead of a fixed constant, so cost estimates reflect the
    # requested/predicted answer length.
    expected_out = analysis.estimated_output_tokens or DEFAULT_EXPECTED_OUTPUT_TOKENS
    cands: list[CandidateView] = []
    context_limit_triggered = False
    for m in enabled_models:
        caps = capabilities_for(m.model_id)
        quality, gaps = _expected_quality(caps, analysis)
        cost = estimate_cost_usd(m, analysis.estimated_input_tokens, expected_out)
        # §11: a model that cannot fit prompt + expected answer is not a
        # candidate, no matter how cheap it is.
        need = analysis.estimated_input_tokens + expected_out
        if need > m.context_window:
            gaps.append(f"context window: need ~{need} tokens > {m.context_window}")
            context_limit_triggered = True
        ok = not gaps
        cands.append(CandidateView(m.model_id, ok, quality, round(cost, 6), gaps))
        reasons.append(
            f"{m.model_id}: {'qualifies' if ok else 'rejected (' + '; '.join(gaps) + ')'}; "
            f"expected quality={quality}, expected cost=${cost:.6f}")
    if context_limit_triggered:
        reasons.append("Context window: at least one model rejected because "
                       "prompt + expected answer exceeds its window.")
    qualifying = [c for c in cands if c.qualifies]
    source = overall_source([m.model_id for m in enabled_models])
    measured_ids = {m.model_id for m in enabled_models if is_measured(m.model_id)}
    level = task_level(analysis)
    # Baseline price-tier cap: the always-best baseline model's candidate cost
    # (identical token estimates for every candidate, so candidate costs
    # compare price tiers directly). Safety picks never pay above this tier —
    # paying above-baseline prices is the one way optimized spend can exceed
    # the counterfactual "always strongest" baseline.

    base_entry = baseline_model(enabled_models)
    base_cand = next((c for c in cands if c.model_id == base_entry.model_id), None)
    base_cap = base_cand.expected_cost_usd if base_cand is not None else None
    if qualifying:
        if analysis.confidence < LOW_CONFIDENCE:
            pick = _strongest(qualifying, measured_ids, base_cap)
            action = "low_confidence_safety"
            reasons.append(f"Low confidence ({analysis.confidence} < {LOW_CONFIDENCE}): "
                           f"chose safest qualifying model {pick.model_id} over cheapest.")
        elif level == "hard":
            # Hard task: go straight to the strongest qualifier. A cheap-first
            # gamble here usually fails quality and the escalation bill
            # (cheap + strong) exceeds the strong model alone.
            pick = _strongest(qualifying, measured_ids, base_cap)
            action = "high_difficulty_safety"
            reasons.append(f"Hard task (difficulty={analysis.difficulty_score}, "
                           f"level={level}): routed directly to strongest qualifying "
                           f"model {pick.model_id} — skipping the cheap-first gamble "
                           f"because a failed cheap attempt + escalation costs more.")
        elif level == "medium":
            # Medium task: cheapest qualifier is fine ONLY if its capability
            # margin is comfortable. A thin margin predicts a quality failure,
            # and fail-then-escalate double-bills — so require headroom.
            safe = [c for c in qualifying
                    if c.expected_quality - 0.5 >= MEDIUM_MARGIN]
            if safe:
                pick = min(safe, key=lambda c: (c.expected_cost_usd, -c.expected_quality))
                reasons.append(f"Medium task (difficulty={analysis.difficulty_score}): "
                               f"cheapest qualifier with comfortable capability margin "
                               f"(>= {MEDIUM_MARGIN}): {pick.model_id}.")
            else:
                pick = _strongest(qualifying, measured_ids, base_cap)
                reasons.append(f"Medium task (difficulty={analysis.difficulty_score}): "
                               f"no qualifier has a comfortable margin (>= {MEDIUM_MARGIN}); "
                               f"chose strongest qualifying model {pick.model_id} to avoid "
                               f"a likely fail-then-escalate double bill.")
            action = "normal"
        else:
            pick = min(qualifying, key=lambda c: (c.expected_cost_usd, -c.expected_quality))
            action = "normal"
            reasons.append(f"Easy task (difficulty={analysis.difficulty_score}): "
                           f"selected cheapest qualifying model: {pick.model_id}.")
        return RoutingDecision(pick.model_id, cands, pick.expected_cost_usd,
                               True, action, source, reasons, context_limit_triggered,
                               level)
    # Nothing fully qualifies: transparent strongest-fallback.
    pick = _strongest(cands, measured_ids, base_cap)
    reasons.append(f"No model fully meets requirements; falling back to strongest "
                   f"available {pick.model_id} (flagged meets_requirements=False).")
    return RoutingDecision(pick.model_id, cands, pick.expected_cost_usd,
                           False, "normal", source, reasons, context_limit_triggered,
                           level)
