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
    "glm-5.3-flash": {
        "reasoning": 0.76, "coding": 0.78, "math": 0.72,
        "summarization": 0.84, "long_context": 0.80, "general": 0.82,
    },
    "kimi-k2.6": {
        "reasoning": 0.85, "coding": 0.88, "math": 0.82,
        "summarization": 0.86, "long_context": 0.82, "general": 0.87,
    },
    "kimi-k3": {
        "reasoning": 0.92, "coding": 0.90, "math": 0.90,
        "summarization": 0.91, "long_context": 0.88, "general": 0.92,
    },
    "longcat-2.0": {
        "reasoning": 0.74, "coding": 0.72, "math": 0.68,
        "summarization": 0.80, "long_context": 0.86, "general": 0.78,
    },
    "hy3": {
        "reasoning": 0.62, "coding": 0.60, "math": 0.58,
        "summarization": 0.74, "long_context": 0.66, "general": 0.70,
    },
    "omen-alpha": {
        "reasoning": 0.78, "coding": 0.76, "math": 0.72,
        "summarization": 0.82, "long_context": 0.78, "general": 0.80,
    },
    "qwen3.8-flash": {
        "reasoning": 0.76, "coding": 0.74, "math": 0.70,
        "summarization": 0.80, "long_context": 0.76, "general": 0.79,
    },
    "minimax-m2.7": {
        "reasoning": 0.77, "coding": 0.75, "math": 0.73,
        "summarization": 0.81, "long_context": 0.77, "general": 0.80,
    },
    "gpt-5.6-luna": {
        "reasoning": 0.87, "coding": 0.86, "math": 0.84,
        "summarization": 0.88, "long_context": 0.82, "general": 0.89,
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
