"""Central configuration. All secrets come from environment / .env — never hard-code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

OPENCODE_API_KEY: str = os.getenv("OPENCODE_API_KEY", "")
OPENCODE_BASE_URL: str = os.getenv(
    "OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1"
).rstrip("/")
OPENCODE_SESSION_ID: str = os.getenv("OPENCODE_SESSION_ID", "llm-cost-optimizer-phase1")

MODEL_A_ID: str = os.getenv("MODEL_A_ID", "deepseek-v4-flash")
MODEL_B_ID: str = os.getenv("MODEL_B_ID", "deepseek-v4-pro")

MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DATABASE_NAME: str = os.getenv("DATABASE_NAME", "llm_optimizer")
QUALITY_THRESHOLD: float = float(os.getenv("QUALITY_THRESHOLD", "0.75"))

# --- Cache backend (Phase 7) ---
# REDIS_URL: real Redis when reachable (docker run -p 6379:6379 redis:7-alpine).
# Unreachable -> transparent in-memory fallback with the SAME redis-py API
# (fakeredis), so the demo never breaks and the code path is identical.
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Semantic tier: cosine similarity >= SEMANTIC_THRESHOLD counts as a candidate
# hit; safety gates then decide. 0.50 because char-3gram cosine scores safe
# paraphrases ("Calculate 15% of 200" vs "What is 15 percent of 200?") at ~0.55
# while unsafe operand swaps score ~0.87 — similarity CANNOT separate them.
# The GATES are the safety mechanism (exact salient-word/number/operator
# equality); the threshold only pre-filters. 0 disables the tier.
SEMANTIC_THRESHOLD: float = float(os.getenv("SEMANTIC_THRESHOLD", "0.50"))
SEMANTIC_MAX_ENTRIES: int = int(os.getenv("SEMANTIC_MAX_ENTRIES", "256"))

# Pricing: USD per 1M tokens, off-peak where DeepSeek publishes peak/off-peak.
# Source: https://opencode.ai/docs/go (Sep 2026). Update here if docs change —
# the router and cost engine read ONLY from this table, never hard-coded values.
# Format: model_id -> (input_per_1M, output_per_1M, cached_read_per_1M)
MODEL_PRICING: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.22, 0.66, 0.007),
    "deepseek-v4-flash-vision-exp": (0.22, 0.66, 0.007),
    "deepseek-v4-pro": (0.66, 1.98, 0.022),
    "mimo-v2.5": (0.14, 0.28, 0.0028),
    "mimo-v2.5-pro": (0.435, 0.87, 0.003625),
    "glm-5.1": (1.40, 4.40, 0.26),
    "glm-5.2": (1.40, 4.40, 0.26),
    "glm-5.3": (1.40, 4.40, 0.26),
    "glm-5.3-flash": (0.15, 0.50, 0.03),
    "kimi-k2.6": (0.95, 4.00, 0.16),
    "kimi-k2.7-code": (0.95, 4.00, 0.19),
    "kimi-k3": (3.00, 15.00, 0.30),
    "longcat-2.0": (0.30, 1.20, 0.006),
    "hy3": (0.14, 0.58, 0.035),
    "hy4-preview": (0.834, 2.501, 0.042),
    "omen-alpha": (0.20, 0.66, 0.04),
    # Responses-API family (different request shape — Phase 1 supports it but
    # prefer same-family pairs to keep the MVP to one protocol):
    "grok-4.6": (2.00, 6.00, 0.50),
    "gpt-5.6-luna": (0.20, 1.20, 0.02),
    "muse-spark-1.2-contributor": (0.10, 0.20, 0.002),
    "muse-spark-1.3-contributor": (0.10, 0.20, 0.002),
    # Messages-API (Anthropic-compatible) family:
    "minimax-m2.7": (0.30, 1.20, 0.06),
    "minimax-m3": (0.30, 1.20, 0.06),
    "qwen3.6-plus": (0.50, 3.00, 0.05),
    "qwen3.7-plus": (0.40, 1.60, 0.04),
    "qwen3.7-max": (2.50, 7.50, 0.50),
    "qwen3.8-flash": (0.15, 0.47, 0.016),
    "qwen3.8-max": (2.00, 6.00, 0.25),
}

# model_id -> endpoint family. Source: https://opencode.ai/docs/go #endpoints
CHAT_COMPLETIONS_MODELS = {
    "glm-5.1", "glm-5.2", "glm-5.3", "glm-5.3-flash",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6",
    "longcat-2.0",
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
    "mimo-v2.5", "mimo-v2.5-pro", "mimo-v2-pro", "mimo-v2-omni",
    "hy4-preview", "hy3", "hy3-preview", "omen-alpha",
}
RESPONSES_MODELS = {
    "grok-4.6", "grok-4.5", "gpt-5.6-luna",
    "muse-spark-1.2-contributor", "muse-spark-1.3-contributor",
}
MESSAGES_MODELS = {
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus",
    "qwen3.6-plus", "qwen3.5-plus",
}


def endpoint_family(model_id: str) -> str:
    mid = model_id.strip()
    if mid in CHAT_COMPLETIONS_MODELS:
        return "chat_completions"
    if mid in RESPONSES_MODELS:
        return "responses"
    if mid in MESSAGES_MODELS:
        return "messages"
    # Unknown / future model: default to OpenAI-compatible chat/completions and
    # let the provider surface a clear error if the API rejects it.
    return "chat_completions"


@dataclass
class Settings:
    openai_key: str = OPENCODE_API_KEY
    base_url: str = OPENCODE_BASE_URL
    session_id: str = OPENCODE_SESSION_ID
    model_a: str = MODEL_A_ID
    model_b: str = MODEL_B_ID
    quality_threshold: float = QUALITY_THRESHOLD
    redis_url: str = REDIS_URL
    semantic_threshold: float = SEMANTIC_THRESHOLD
    configured_models: list[str] = field(
        default_factory=lambda: [MODEL_A_ID, MODEL_B_ID]
    )


settings = Settings()


def validate_phase1() -> list[str]:
    """Return list of config problems (empty = ready for live calls)."""
    problems: list[str] = []
    if not settings.openai_key:
        problems.append("OPENCODE_API_KEY is missing (copy .env.example to .env and fill it).")
    if not settings.model_a or not settings.model_b:
        problems.append("MODEL_A_ID / MODEL_B_ID must both be set.")
    fam_a, fam_b = endpoint_family(settings.model_a), endpoint_family(settings.model_b)
    if fam_a != fam_b:
        problems.append(
            f"Phase-1 recommendation: pick two models in the SAME endpoint family. "
            f"Got {settings.model_a}={fam_a} vs {settings.model_b}={fam_b}. "
            f"It will still work (provider handles all three), but debugging is easier single-protocol."
        )
    return problems
