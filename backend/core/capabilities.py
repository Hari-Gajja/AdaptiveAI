"""Model capabilities — priors overridden by measured profiler scores.

Lookup order per model/category: measured_benchmark (profiler, Phase 6b) >
hard-coded prior (clearly an estimate) > neutral 0.70.

Profiles live in backend/data/profiles.json, written by the profiler:
  {model_id: {category: score, ..., "measured_at": iso, "n": int}}
Source labels: "measured_benchmark" | "mixed" | "estimated_prior".
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PRIORS: dict[str, dict[str, float]] = {
    # DeepSeek V4 family: public reports put Pro clearly above Flash on
    # reasoning/coding; both handle summarization well. Values are priors.
    "deepseek-v4-flash": {
        "reasoning": 0.72, "coding": 0.78, "math": 0.70,
        "summarization": 0.82, "long_context": 0.75, "general": 0.80,
    },
    "deepseek-v4-pro": {
        "reasoning": 0.88, "coding": 0.92, "math": 0.85,
        "summarization": 0.90, "long_context": 0.88, "general": 0.90,
    },
    "mimo-v2.5": {
        "reasoning": 0.68, "coding": 0.72, "math": 0.65,
        "summarization": 0.80, "long_context": 0.72, "general": 0.78,
    },
    "glm-5.2": {
        "reasoning": 0.86, "coding": 0.88, "math": 0.83,
        "summarization": 0.89, "long_context": 0.87, "general": 0.88,
    },
}

SOURCE = "estimated_prior"  # legacy default; prefer overall_source() now
NEUTRAL = 0.70
CATEGORIES = ("reasoning", "coding", "math", "summarization", "long_context", "general")

DEFAULT_CAPS = {c: NEUTRAL for c in CATEGORIES}

PROFILES_FILE = Path(os.getenv(
    "LLMO_PROFILES_FILE",
    str(Path(__file__).resolve().parent.parent / "data" / "profiles.json")))


def load_profiles(path: Path | None = None) -> dict:
    path = PROFILES_FILE if path is None else path
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def is_measured(model_id: str, profiles: dict | None = None) -> bool:
    profiles = load_profiles() if profiles is None else profiles
    p = profiles.get(model_id, {})
    return any(isinstance(p.get(c), (int, float)) for c in CATEGORIES)


def capabilities_for(model_id: str, profiles: dict | None = None) -> dict[str, float]:
    profiles = load_profiles() if profiles is None else profiles
    caps = dict(DEFAULT_CAPS)
    caps.update(PRIORS.get(model_id, {}))
    measured = profiles.get(model_id, {})
    for c in CATEGORIES:
        if isinstance(measured.get(c), (int, float)):
            caps[c] = float(measured[c])
    return caps


def overall_source(model_ids: list[str], profiles: dict | None = None) -> str:
    profiles = load_profiles() if profiles is None else profiles
    flags = [is_measured(m, profiles) for m in model_ids]
    if flags and all(flags):
        return "measured_benchmark"
    if any(flags):
        return "mixed"
    return "estimated_prior"
