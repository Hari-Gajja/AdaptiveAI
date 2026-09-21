"""Nemotron model recommender — Phase G7 (hybrid selection, step 1).

The control-plane model (Nemotron 3.5 Lightning Free by default) looks at the
task analysis + the customer's model pool and RECOMMENDS a model. It never
decides: the deterministic validator (gateway_router.py) checks the
recommendation against hard facts (exists, enabled, context fits,
capabilities, pricing, not blocked) and falls back to the deterministic
router when the recommendation is invalid or the call fails.

Budget: tiny prompt, ~60 output tokens, JSON only, no chain-of-thought.
Failure policy: any error -> return None (caller falls back); the failure is
recorded in the control-plane stats for honest accounting.
"""
from __future__ import annotations

import json

from backend.llm import config as cp_cfg
from backend.llm.opencode_client import ControlPlaneError, _call

SELECT_MAX_OUTPUT_TOKENS = 60

_SYSTEM = (
    "You pick the best model for a task from a fixed list. Reply with ONLY a "
    "JSON object, no explanation: {\"model\":\"<id from the list>\","
    "\"reason\":\"<max 12 words>\"}. Prefer the CHEAPEST model that can "
    "handle the task well."
)


def _validate(d: dict) -> dict:
    mid = str(d.get("model", "")).strip()
    if not mid:
        raise ControlPlaneError("selector missing 'model'")
    reason = str(d.get("reason", "")).strip()[:120]
    return {"model": mid, "reason": reason}


def recommend(budgeted_prompt: str, analysis_view: dict,
              candidates: list[dict]) -> dict | None:
    """Ask the control-plane model to recommend one candidate.

    candidates: [{"model_id", "tier", "input_per_1M", "output_per_1M",
                  "context_window", "capabilities"}] — already filtered to
    the customer's enabled pool.
    Returns {"model", "reason"} or None on ANY failure (graceful degradation).
    """
    if not cp_cfg.OPENCODE_ENABLED or not candidates:
        return None
    listing = "\n".join(
        f"- {c['model_id']} (tier={c.get('tier', 'unknown')}, "
        f"ctx={c.get('context_window', 0)}, "
        f"caps={json.dumps(c.get('capabilities', {}), separators=(',', ':'))})"
        for c in candidates)
    user = (
        f"TASK: type={analysis_view.get('task_type')}, "
        f"difficulty={analysis_view.get('difficulty_score')}, "
        f"required={analysis_view.get('required_capabilities')}\n"
        f"PROMPT: {budgeted_prompt[:600]}\n"
        f"MODELS:\n{listing}\n"
        f"Which model id? JSON only."
    )
    try:
        res = _call("selector", _SYSTEM, user, SELECT_MAX_OUTPUT_TOKENS,
                    _validate)
        return res.parsed
    except ControlPlaneError:
        return None
