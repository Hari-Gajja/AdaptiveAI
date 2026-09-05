"""OpenCode classifier — LLM task classification with legacy fallback.

Returns the SAME shape as the legacy analyzer consumers need, plus the raw
control-plane labels for explainability:

  task_type            "mathematics" | "coding" | "general" (mapped from M/C/O)
  difficulty_score     0.2 (E) / 0.55 (M) / 0.85 (H)
  confidence           from the model, clamped 0..1
  required_capabilities / required_thresholds
                       derived from (task_type, difficulty) with the SAME
                       thresholds the legacy analyzer uses, so the router
                       needs no changes.
  backend              "opencode" | "legacy_ml" (which path produced this)
  fallback_used / fallback_reason
                       set when the LLM call failed and legacy ran instead.
  control_plane        raw labels {"t","d","c"} + measured token usage.

Policy (spec): hard tasks and low confidence (< CLASSIFIER_CONFIDENCE_THRESHOLD)
route to the strongest model; easy tasks to the cheapest capable; medium to
cheap-qualifying. The ROUTER stays deterministic — only classification is LLM.
"""
from __future__ import annotations

from backend.core.task_analyzer import TaskAnalysis, analyze as legacy_analyze
from backend.llm import config as cp_cfg
from backend.llm import opencode_client as client
from backend.llm.prompt_budget import budget_for_classifier

# Control-plane label -> legacy task_type vocabulary (router capabilities keys).
_LABEL_TO_TYPE = {"M": "mathematics", "C": "coding", "O": "general"}
_LABEL_TO_DIFFICULTY = {"E": 0.20, "M": 0.55, "H": 0.85}

_PRIMARY_CAPS = {
    "mathematics": ["math", "reasoning"],
    "coding": ["coding"],
    "general": ["general"],
}


def _derive_requirements(task_type: str, difficulty: float) -> tuple[list[str], dict[str, float]]:
    required = list(_PRIMARY_CAPS.get(task_type, ["general"]))
    if difficulty >= 0.6 and "reasoning" not in required:
        required.append("reasoning")
    thresh = round(0.50 + 0.40 * difficulty, 3)
    thresholds = {c: (thresh if c != "general" else round(0.40 + 0.30 * difficulty, 3))
                  for c in required}
    return required, thresholds


def _legacy_result(prompt: str, context: str | None, reason: str) -> TaskAnalysis:
    a = legacy_analyze(prompt, context)
    a.backend = "legacy_ml"
    a.fallback_used = True
    a.fallback_reason = reason
    a.control_plane = None
    return a


def classify_prompt(prompt: str, context: str | None = None,
                    force_backend: str | None = None) -> TaskAnalysis:
    """Classify with the configured backend; fall back to legacy on any
    control-plane failure. Never raises for control-plane issues."""
    backend = force_backend or cp_cfg.CLASSIFIER_BACKEND
    if backend == "legacy_ml":
        return _legacy_result(prompt, context, "CLASSIFIER_BACKEND=legacy_ml")
    if not cp_cfg.OPENCODE_ENABLED:
        return _legacy_result(prompt, context, "OPENCODE_ENABLED=false")

    bp = budget_for_classifier(prompt, context)
    try:
        res = client.classify(prompt, bp.text)
    except client.ControlPlaneError as e:
        return _legacy_result(prompt, context, f"control-plane classifier failed: {e}")

    labels = res.parsed  # {"t","d","c"} validated by the client
    task_type = _LABEL_TO_TYPE[labels["t"]]
    difficulty = _LABEL_TO_DIFFICULTY[labels["d"]]
    confidence = float(labels["c"])
    required, thresholds = _derive_requirements(task_type, difficulty)

    a = TaskAnalysis(
        task_type=task_type,
        difficulty_score=difficulty,
        confidence=confidence,
        required_capabilities=required,
        required_thresholds=thresholds,
        detected_signals=[f"llm_label_{labels['t']}", f"llm_difficulty_{labels['d']}"],
        estimated_input_tokens=max(8, len(prompt) // 4) + (len(context) // 4 if context else 0),
        word_count=len(prompt.split()),
    )
    a.backend = "opencode"
    a.fallback_used = False
    a.fallback_reason = ""
    a.control_plane = {
        "labels": labels,
        "prompt_view": bp.view(),
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "latency_ms": res.latency_ms,
        "model_id": res.model_id,
        "usage_estimated": res.usage_estimated,
    }
    return a
