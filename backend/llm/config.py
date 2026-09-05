"""Control-plane configuration — env-driven, all optional with safe defaults.

The control plane reuses the existing OpenCode provider
(backend/providers/opencode.py) so there is exactly ONE place that knows
OpenCode specifics. These settings only decide WHEN and WITH WHAT BUDGET the
control plane calls it.
"""
from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v.strip() if v is not None and v.strip() else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# Master switch: when False the whole control plane is bypassed and the
# legacy deterministic pipeline runs exactly as before.
OPENCODE_ENABLED: bool = _env_bool("OPENCODE_ENABLED", True)

# Cheap model used for ALL control-plane calls (classifier/verifier/evaluator).
# Must be an enabled registry model with configured pricing; the client
# validates and falls back to legacy when it is not.
OPENCODE_MODEL: str = _env("OPENCODE_MODEL", "deepseek-v4-flash")

# Optional explicit provider tag for display only (the actual endpoint family
# is resolved by the existing provider from the model id).
OPENCODE_PROVIDER: str = _env("OPENCODE_PROVIDER", "opencode")

# Hard timeout for each control-plane call (seconds). The underlying provider
# has its own retry/deadline logic; this budget bounds the whole call.
OPENCODE_TIMEOUT_SECONDS: float = _env_float("OPENCODE_TIMEOUT_SECONDS", 20.0)

# Token budgets (spec): tiny outputs, JSON only, no chain-of-thought.
CLASSIFIER_MAX_OUTPUT_TOKENS: int = _env_int("CLASSIFIER_MAX_OUTPUT_TOKENS", 50)
VERIFIER_MAX_OUTPUT_TOKENS: int = _env_int("VERIFIER_MAX_OUTPUT_TOKENS", 40)
EVALUATOR_MAX_OUTPUT_TOKENS: int = _env_int("EVALUATOR_MAX_OUTPUT_TOKENS", 80)

# Classifier confidence below this -> treat as low confidence (safety routing
# to the strongest model, mirroring the legacy router's LOW_CONFIDENCE rule).
CLASSIFIER_CONFIDENCE_THRESHOLD: float = _env_float(
    "CLASSIFIER_CONFIDENCE_THRESHOLD", 0.70)

# Classifier backend: "opencode" (LLM control plane) or "legacy_ml"
# (the deterministic keyword analyzer in core/task_analyzer.py).
CLASSIFIER_BACKEND: str = _env("CLASSIFIER_BACKEND", "opencode")
if CLASSIFIER_BACKEND not in ("opencode", "legacy_ml"):
    CLASSIFIER_BACKEND = "opencode"

# Quality checking: off | benchmark | live
#   off        never run quality checks (deterministic only)
#   benchmark  only when a reference answer exists (objective scoring)
#   live       also run the LLM evaluator on subjective tasks without reference
QUALITY_CHECK_MODE: str = _env("QUALITY_CHECK_MODE", "live")
if QUALITY_CHECK_MODE not in ("off", "benchmark", "live"):
    QUALITY_CHECK_MODE = "live"

# Cache verification: when a semantic-cache candidate passes all deterministic
# safety gates, optionally ask the control-plane model to confirm the cached
# answer still answers the prompt. NEVER overrides a hard gate block.
CACHE_VERIFY_ENABLED: bool = _env_bool("CACHE_VERIFY_ENABLED", True)

# Prompt budget: max characters of the user prompt fed to control-plane calls
# (task-aware truncation lives in prompt_budget.py).
CONTROL_PLANE_PROMPT_MAX_CHARS: int = _env_int("CONTROL_PLANE_PROMPT_MAX_CHARS", 1200)

# When True, control-plane failures are surfaced in the API response
# (control_plane_status / fallback_used / fallback_reason) but never fail the
# request itself.
CONTROL_PLANE_STRICT: bool = _env_bool("CONTROL_PLANE_STRICT", False)
