"""Pricing registry — Phase G5. Prices come from DATA, never invented.

Sources, in priority order:
  1. customer_declared — the customer told us their provider's prices
  2. catalog           — backend/data/pricing_registry.json (curated, sourced)
  3. unknown           — pricing_status="unknown": cost math reports
                         "unavailable"; savings are NEVER fabricated

The catalog file is the ONLY place global prices live. Every entry carries a
`source` string so the dashboard can show provenance. Free models are allowed
with price 0.0 and `free: true` (e.g. the Nemotron control-plane model).
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "pricing_registry.json"


def _load() -> dict[str, dict]:
    try:
        raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return {m["model_id"]: m for m in raw.get("models", []) if m.get("model_id")}
    except Exception:
        return {}


def lookup_pricing(model_id: str) -> dict | None:
    """Catalog entry for a model id, or None when unknown (never a guess)."""
    return _load().get(model_id.strip())


def pricing_status_for(model_id: str) -> str:
    return "configured" if lookup_pricing(model_id) is not None else "unknown"


def catalog_view() -> list[dict]:
    return sorted(_load().values(), key=lambda m: m["model_id"])
