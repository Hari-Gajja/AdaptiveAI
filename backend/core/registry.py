"""Model Registry — Phase 2.

Owns the list of models the organization has connected. No cheap/frontier
labels anywhere: the router (Phase 5) compares measured cost + capabilities
dynamically, so this file stores facts, not verdicts.

Persistence: JSON file (backend/data/models.json) for the hackathon MVP.
Swapped for MongoDB in Phase 8 without changing the public methods.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field, field_validator

from backend.config import MODEL_PRICING, endpoint_family, settings

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "models.json"

# Conservative known context windows (tokens). Admin-editable via PUT.
# Sources: models.dev / Go docs tracker, Sep 2026. Default 200k when unknown.
KNOWN_CONTEXT = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-flash-vision-exp": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "glm-5.1": 203_000,
    "glm-5.2": 1_000_000,
    "glm-5.3": 1_000_000,
    "glm-5.3-flash": 1_000_000,
    "mimo-v2.5": 1_000_000,
    "mimo-v2.5-pro": 1_000_000,
    "kimi-k2.6": 256_000,
    "kimi-k2.7-code": 256_000,
    "kimi-k3": 256_000,
    "longcat-2.0": 128_000,
    "hy3": 128_000,
    "hy4-preview": 200_000,
    "omen-alpha": 200_000,
    "qwen3.8-flash": 1_000_000,
    "qwen3.8-max": 262_144,
    "minimax-m2.7": 200_000,
    "minimax-m3": 1_000_000,
    "gpt-5.6-luna": 400_000,
    "grok-4.6": 2_000_000,
}
DEFAULT_CONTEXT = 200_000

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,79}$")


class RegistryError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class ModelEntry(BaseModel):
    model_id: str
    provider: str = "opencode"
    enabled: bool = True
    display_name: str = ""
    description: str = ""
    input_per_1M: float = Field(default=0.0, ge=0.0)
    output_per_1M: float = Field(default=0.0, ge=0.0)
    cached_per_1M: float = Field(default=0.0, ge=0.0)
    context_window: int = Field(default=DEFAULT_CONTEXT, gt=0)
    profile_status: str = "unprofiled"  # unprofiled|profiling|profiled|stale
    pricing_status: str = "configured"  # configured|unavailable
    created_at: str = ""
    updated_at: str = ""

    @field_validator("model_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        v = v.strip()
        if not _MODEL_ID_RE.match(v):
            raise ValueError("model_id must match [A-Za-z0-9._-], 1-80 chars")
        return v

    @field_validator("profile_status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in ("unprofiled", "profiling", "profiled", "stale"):
            raise ValueError("bad profile_status")
        return v

    @field_validator("pricing_status")
    @classmethod
    def _valid_pricing_status(cls, v: str) -> str:
        if v not in ("configured", "unavailable"):
            raise ValueError("bad pricing_status")
        return v


class ModelCreate(BaseModel):
    model_id: str
    provider: str = "opencode"
    enabled: bool = True
    display_name: str = ""
    description: str = ""
    input_per_1M: float | None = None  # None = auto-fill from pricing table
    output_per_1M: float | None = None
    cached_per_1M: float | None = None
    context_window: int | None = None  # None = auto-fill from known table

    @field_validator("model_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        v = v.strip()
        if not _MODEL_ID_RE.match(v):
            raise ValueError("model_id must match [A-Za-z0-9._-], 1-80 chars")
        return v


class ModelUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
    description: str | None = None
    input_per_1M: float | None = Field(default=None, ge=0.0)
    output_per_1M: float | None = Field(default=None, ge=0.0)
    cached_per_1M: float | None = Field(default=None, ge=0.0)
    context_window: int | None = Field(default=None, gt=0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _defaults_for(model_id: str) -> tuple[float, float, float, int]:
    inp, outp, cached = MODEL_PRICING.get(model_id, (0.0, 0.0, 0.0))
    return inp, outp, cached, KNOWN_CONTEXT.get(model_id, DEFAULT_CONTEXT)


def public_view(entry: ModelEntry) -> dict:
    d = entry.model_dump()
    d["endpoint_family"] = endpoint_family(entry.model_id)
    d["priced"] = entry.pricing_status == "configured"
    return d


class ModelRegistry:
    """JSON-backed registry. One instance per process (see get_registry)."""

    def __init__(self, data_file: Path = DATA_FILE):
        self._file = Path(data_file)
        self._lock = Lock()
        self._models: dict[str, ModelEntry] = {}
        self._load_or_seed()

    # ---- persistence ----
    def _load_or_seed(self) -> None:
        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                for item in raw.get("models", []):
                    e = ModelEntry(**item)
                    self._models[e.model_id] = e
                if self._models:
                    return
            except Exception:
                pass  # corrupt file -> reseed below
        for mid in dict.fromkeys([settings.model_a, settings.model_b]):
            inp, outp, cached, ctx = _defaults_for(mid)
            self._models[mid] = ModelEntry(
                model_id=mid, input_per_1M=inp, output_per_1M=outp,
                cached_per_1M=cached, context_window=ctx,
                display_name=mid, created_at=_now(), updated_at=_now(),
            )
        self._save_locked()

    def _save_locked(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"models": [m.model_dump() for m in self._models.values()]}
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(self._file.parent),
            encoding="utf-8", suffix=".tmp",
        ) as f:
            json.dump(payload, f, indent=2)
            tmp = f.name
        Path(tmp).replace(self._file)

    # ---- reads ----
    def list(self, enabled_only: bool = False) -> list[ModelEntry]:
        models = list(self._models.values())
        if enabled_only:
            models = [m for m in models if m.enabled]
        return sorted(models, key=lambda m: m.model_id)

    def get(self, model_id: str) -> ModelEntry:
        try:
            return self._models[model_id]
        except KeyError:
            raise RegistryError(f"unknown model '{model_id}'", 404) from None

    def enabled(self) -> list[ModelEntry]:
        return self.list(enabled_only=True)

    # ---- writes ----
    def create(self, body: ModelCreate) -> ModelEntry:
        with self._lock:
            if body.model_id in self._models:
                raise RegistryError(f"model '{body.model_id}' already registered", 409)
            inp, outp, cached, ctx = _defaults_for(body.model_id)
            entry = ModelEntry(
                model_id=body.model_id,
                provider=body.provider or "opencode",
                enabled=body.enabled,
                display_name=body.display_name or body.model_id,
                description=body.description,
                input_per_1M=body.input_per_1M if body.input_per_1M is not None else inp,
                output_per_1M=body.output_per_1M if body.output_per_1M is not None else outp,
                cached_per_1M=body.cached_per_1M if body.cached_per_1M is not None else cached,
                context_window=body.context_window or ctx,
                pricing_status=("configured" if (body.input_per_1M if body.input_per_1M is not None else inp) > 0
                                and (body.output_per_1M if body.output_per_1M is not None else outp) > 0
                                else "unavailable"),
                created_at=_now(), updated_at=_now(),
            )
            if entry.enabled and entry.pricing_status == "unavailable":
                raise RegistryError("pricing unavailable — configure pricing before enabling cost optimization", 400)
            self._models[entry.model_id] = entry
            self._save_locked()
            return entry

    def update(self, model_id: str, patch: ModelUpdate) -> ModelEntry:
        with self._lock:
            entry = self.get(model_id)
            data = entry.model_dump()
            changes = patch.model_dump(exclude_unset=True)
            if changes.get("enabled") is True and entry.pricing_status == "unavailable":
                raise RegistryError("pricing unavailable — configure pricing before enabling cost optimization", 400)
            if changes.get("enabled") is False:
                others = [m for m in self._models.values()
                          if m.model_id != model_id and m.enabled]
                if not others:
                    raise RegistryError("cannot disable the last enabled model", 400)
            for k, v in changes.items():
                if v is not None:
                    data[k] = v
            if "input_per_1M" in changes or "output_per_1M" in changes:
                data["pricing_status"] = ("configured" if data["input_per_1M"] > 0 and data["output_per_1M"] > 0
                                            else "unavailable")
            data["updated_at"] = _now()
            updated = ModelEntry(**data)
            self._models[model_id] = updated
            self._save_locked()
            return updated

    def delete(self, model_id: str) -> None:
        with self._lock:
            self.get(model_id)  # 404 if missing
            if self._models[model_id].enabled:
                others = [m for m in self._models.values()
                          if m.model_id != model_id and m.enabled]
                if not others:
                    raise RegistryError("cannot delete the last enabled model", 400)
            del self._models[model_id]
            self._save_locked()

    def mark_profile_status(self, model_id: str, status: str) -> ModelEntry:
        with self._lock:
            entry = self.get(model_id)
            data = entry.model_dump()
            data["profile_status"] = status
            data["updated_at"] = _now()
            updated = ModelEntry(**data)  # validates status
            self._models[model_id] = updated
            self._save_locked()
            return updated


_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry


def reset_registry_for_tests(data_file: Path) -> ModelRegistry:
    """Test hook: fresh registry pointed at a tmp file."""
    return ModelRegistry(data_file=data_file)
