"""OpenCode evaluator — LLM-as-judge for subjective quality checks.

Evaluation architecture (spec):
  math/logic   -> objective validator (deterministic, no LLM)
  coding       -> executable/heuristic checks (deterministic, no LLM)
  subjective   -> OpenCode evaluator (LLM judge, budgeted)

The evaluator NEVER replaces the reference-based objective scoring when a
reference answer exists — it augments the estimated (no-reference) path and
the benchmark's subjective categories. Output blends into the existing
QualityScore shape so the router/optimizer/UI need no structural changes.
"""
from __future__ import annotations

from backend.core.quality import QualityScore, _combine, content_tokens, _recall
from backend.llm import config as cp_cfg
from backend.llm import opencode_client as client
from backend.llm.prompt_budget import budget_for_evaluator


def _should_use_llm(prompt: str, reference: str | None) -> bool:
    """LLM judge applies to subjective tasks without a reference answer.
    Objective domains (math/logic) keep deterministic scoring."""
    if reference:
        return False
    low = (prompt or "").lower()
    math_words = ("calculate", "solve", "equation", "percent", "%",
                  "how many", "integral", "derivative", "probability")
    if any(w in low for w in math_words):
        return False  # objective validator handles math
    return True


def evaluate_with_llm(answer: str, prompt: str) -> tuple[QualityScore, dict]:
    """LLM-judged quality. Returns (QualityScore, control_plane_view).
    Falls back to the deterministic estimated heuristic on any failure."""
    cp_view: dict = {"used": False, "fallback_used": False, "fallback_reason": ""}
    if not cp_cfg.OPENCODE_ENABLED:
        cp_view["fallback_used"] = True
        cp_view["fallback_reason"] = "OPENCODE_ENABLED=false"
        return evaluate_estimated(answer, prompt), cp_view
    if not _should_use_llm(prompt, None):
        cp_view["fallback_used"] = True
        cp_view["fallback_reason"] = "objective domain — deterministic scoring"
        return evaluate_estimated(answer, prompt), cp_view

    bp = budget_for_evaluator(prompt)
    try:
        res = client.evaluate_answer(bp.text, answer)
    except client.ControlPlaneError as e:
        cp_view["fallback_used"] = True
        cp_view["fallback_reason"] = f"evaluator unavailable: {e}"
        return evaluate_estimated(answer, prompt), cp_view

    p = res.parsed  # {"c":0|1,"r":0|1,"s":0.00}
    correctness = 0.9 if p["c"] == 1 else 0.25
    relevance = 0.9 if p["r"] == 1 else 0.3
    overall = float(p["s"])
    # Keep the 0.5/0.3/0.2 blend shape; anchor completeness to the judge score
    # so overall stays consistent with the existing QualityScore contract.
    completeness = max(0.0, min(1.0, 2 * overall - 0.5 * correctness - 0.3 * relevance) / 0.2) \
        if overall is not None else 0.5
    completeness = round(max(0.0, min(1.0, completeness)), 3)
    score = QualityScore(correctness=correctness, relevance=relevance,
                         completeness=completeness, overall=round(overall, 3),
                         method="estimated", scoring_detail="llm_judge")
    cp_view.update({
        "used": True,
        "flags": {"correct": p["c"], "relevant": p["r"]},
        "score": p["s"],
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "latency_ms": res.latency_ms,
        "model_id": res.model_id,
        "usage_estimated": res.usage_estimated,
        "prompt_view": bp.view(),
    })
    return score, cp_view


def evaluate_estimated(answer: str, prompt: str) -> QualityScore:
    """Deterministic no-reference heuristic (existing quality.evaluate path)."""
    from backend.core.quality import evaluate
    return evaluate(answer, prompt, None)
