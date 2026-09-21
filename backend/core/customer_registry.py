"""Customer model registry — Phase G3. Per-customer connected models.

Each customer registers THEIR OWN models: base_url + API key (stored as a
credential_reference, never plaintext) + model_id. The system auto-profiles
capability — customers NEVER label models cheap/mid/frontier.

Pricing: customers MAY declare their provider's prices; when they don't, the
entry gets pricing_status="unknown" and cost math reports "unavailable" —
prices are NEVER fabricated (master prompt §9).

Persistence: JSON file (backend/data/customer_models.json), same pattern as
the single-tenant registry. All reads/writes are customer-scoped.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field, field_validator

from backend.core.pricing import lookup_pricing

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "customer_models.json"

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,79}$")
_URL_RE = re.compile(r"^https?://[^\s]+$")


class RegistryError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class CustomerModelEntry(BaseModel):
    customer_id: str
    model_id: str
    provider: str = "custom"          # openai | openai_compatible | custom | opencode
    base_url: str = ""                # customer's endpoint (OpenAI-compatible)
    credential_reference: str = ""    # ref into the secret store (never the key)
    enabled: bool = True
    display_name: str = ""
    description: str = ""
    # Declared pricing (USD per 1M tokens). Empty/None -> pricing_status unknown.
    input_per_1M: float | None = Field(default=None, ge=0.0)
    output_per_1M: float | None = Field(default=None, ge=0.0)
    cached_per_1M: float | None = Field(default=None, ge=0.0)
    pricing_status: str = "unknown"   # configured | unknown | unavailable
    pricing_source: str = ""          # customer_declared | catalog | none
    context_window: int = Field(default=200_000, gt=0)
    profile_status: str = "unprofiled"  # unprofiled|profiling|profiled|stale
    blocked: bool = False             # admin/health flag: exclude from routing
    last_test_status: str = ""        # "" | ok | failed
    last_test_detail: str = ""
    created_at: str = ""
    updated_at: str = ""

    @field_validator("model_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        v = v.strip()
        if not _MODEL_ID_RE.match(v):
            raise ValueError("model_id must match [A-Za-z0-9._-], 1-80 chars")
        return v

    @field_validator("base_url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if v and not _URL_RE.match(v):
            raise ValueError("base_url must be an http(s) URL")
        return v

    @field_validator("pricing_status")
    @classmethod
    def _valid_pricing_status(cls, v: str) -> str:
        if v not in ("configured", "unknown", "unavailable"):
            raise ValueError("bad pricing_status")
        return v

    @field_validator("profile_status")
    @classmethod
    def _valid_profile_status(cls, v: str) -> str:
        if v not in ("unprofiled", "profiling", "profiled", "stale"):
            raise ValueError("bad profile_status")
        return v


class CustomerModelCreate(BaseModel):
    model_id: str
    provider: str = "custom"
    base_url: str = ""
    api_key: str | None = None        # plaintext IN, stored encrypted, never out
    enabled: bool = True
    display_name: str = ""
    description: str = ""
    input_per_1M: float | None = Field(default=None, ge=0.0)
    output_per_1M: float | None = Field(default=None, ge=0.0)
    cached_per_1M: float | None = Field(default=None, ge=0.0)
    context_window: int | None = Field(default=None, gt=0)

    @field_validator("model_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        v = v.strip()
        if not _MODEL_ID_RE.match(v):
            raise ValueError("model_id must match [A-Za-z0-9._-], 1-80 chars")
        return v

    @field_validator("base_url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if v and not _URL_RE.match(v):
            raise ValueError("base_url must be an http(s) URL")
        return v


class CustomerModelUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
    description: str | None = None
    base_url: str | None = None
    api_key: str | None = None        # rotate credential
    input_per_1M: float | None = Field(default=None, ge=0.0)
    output_per_1M: float | None = Field(default=None, ge=0.0)
    cached_per_1M: float | None = Field(default=None, ge=0.0)
    context_window: int | None = Field(default=None, gt=0)
    blocked: bool | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_view(entry: CustomerModelEntry) -> dict:
    """API view — NEVER includes credential material."""
    d = entry.model_dump()
    d.pop("credential_reference", None)  # opaque ref; not useful to clients
    d["has_credential"] = bool(entry.credential_reference)
    d["priced"] = entry.pricing_status == "configured"
    return d


class CustomerModelRegistry:
    """JSON-backed, customer-scoped model registry. One instance per process."""

    def __init__(self, data_file: Path = DATA_FILE):
        self._file = Path(data_file)
        self._lock = Lock()
        self._models: dict[tuple[str, str], CustomerModelEntry] = {}
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                for item in raw.get("models", []):
                    e = CustomerModelEntry(**item)
                    self._models[(e.customer_id, e.model_id)] = e
            except Exception:
                self._models = {}

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

    # ---- reads (all customer-scoped) ----
    def list(self, customer_id: str, enabled_only: bool = False) -> list[CustomerModelEntry]:
        models = [m for (cid, _), m in self._models.items() if cid == customer_id]
        if enabled_only:
            models = [m for m in models if m.enabled and not m.blocked]
        return sorted(models, key=lambda m: m.model_id)

    def get(self, customer_id: str, model_id: str) -> CustomerModelEntry:
        e = self._models.get((customer_id, model_id))
        if e is None:
            raise RegistryError(
                f"model '{model_id}' not registered for customer '{customer_id}'", 404)
        return e

    def enabled(self, customer_id: str) -> list[CustomerModelEntry]:
        return self.list(customer_id, enabled_only=True)

    # ---- writes ----
    def create(self, customer_id: str, body: CustomerModelCreate,
               credential_reference: str = "") -> CustomerModelEntry:
        """Register a model. credential_reference comes from the SecretStore
        (the plaintext api_key was already encrypted by the caller — the
        registry only ever sees the opaque ref)."""
        with self._lock:
            key = (customer_id, body.model_id)
            if key in self._models:
                raise RegistryError(
                    f"model '{body.model_id}' already registered for customer "
                    f"'{customer_id}'", 409)
            # Pricing resolution: customer-declared > global catalog > unknown.
            declared = (body.input_per_1M is not None
                        and body.output_per_1M is not None
                        and body.input_per_1M > 0 and body.output_per_1M > 0)
            if declared:
                inp, outp = body.input_per_1M, body.output_per_1M
                cached = body.cached_per_1M if body.cached_per_1M is not None else 0.0
                status, source = "configured", "customer_declared"
            else:
                cat = lookup_pricing(body.model_id)
                if cat is not None:
                    inp, outp, cached = cat["input_per_1M"], cat["output_per_1M"], cat["cached_per_1M"]
                    status, source = "configured", "catalog"
                else:
                    inp = body.input_per_1M if body.input_per_1M is not None else 0.0
                    outp = body.output_per_1M if body.output_per_1M is not None else 0.0
                    cached = body.cached_per_1M if body.cached_per_1M is not None else 0.0
                    status, source = "unknown", "none"
            entry = CustomerModelEntry(
                customer_id=customer_id,
                model_id=body.model_id,
                provider=body.provider or "custom",
                base_url=body.base_url or "",
                credential_reference=credential_reference,
                # Unknown pricing may be registered for later configuration,
                # but it must never enter the routing pool.
                enabled=body.enabled and status == "configured",
                display_name=body.display_name or body.model_id,
                description=body.description,
                input_per_1M=inp, output_per_1M=outp, cached_per_1M=cached,
                pricing_status=status, pricing_source=source,
                context_window=body.context_window or 200_000,
                created_at=_now(), updated_at=_now(),
            )
            self._models[key] = entry
            self._save_locked()
            return entry

    def update(self, customer_id: str, model_id: str, patch: CustomerModelUpdate,
               new_credential_reference: str | None = None) -> CustomerModelEntry:
        """Apply a patch. When the caller rotated the api_key it passes the
        NEW credential_reference here (the plaintext never reaches us)."""
        with self._lock:
            entry = self.get(customer_id, model_id)
            data = entry.model_dump()
            changes = patch.model_dump(exclude_unset=True)
            changes.pop("api_key", None)  # handled via new_credential_reference
            if new_credential_reference is not None:
                data["credential_reference"] = new_credential_reference
            for k, v in changes.items():
                if v is not None:
                    data[k] = v
            if changes.get("enabled") is True and data["pricing_status"] != "configured":
                raise RegistryError(
                    "pricing unavailable — configure pricing before enabling cost optimization", 400)
            if "input_per_1M" in changes or "output_per_1M" in changes:
                if data["input_per_1M"] and data["output_per_1M"]:
                    data["pricing_status"] = "configured"
                    data["pricing_source"] = "customer_declared"
                else:
                    data["pricing_status"] = "unknown"
                    data["pricing_source"] = "none"
            data["updated_at"] = _now()
            updated = CustomerModelEntry(**data)
            self._models[(customer_id, model_id)] = updated
            self._save_locked()
            return updated

    def delete(self, customer_id: str, model_id: str) -> None:
        with self._lock:
            self.get(customer_id, model_id)  # 404 if missing
            del self._models[(customer_id, model_id)]
            self._save_locked()

    def mark_profile_status(self, customer_id: str, model_id: str, status: str) -> CustomerModelEntry:
        with self._lock:
            entry = self.get(customer_id, model_id)
            data = entry.model_dump()
            data["profile_status"] = status
            data["updated_at"] = _now()
            updated = CustomerModelEntry(**data)  # validates
            self._models[(customer_id, model_id)] = updated
            self._save_locked()
            return updated

    def mark_test_result(self, customer_id: str, model_id: str,
                         ok: bool, detail: str) -> CustomerModelEntry:
        with self._lock:
            entry = self.get(customer_id, model_id)
            data = entry.model_dump()
            data["last_test_status"] = "ok" if ok else "failed"
            data["last_test_detail"] = detail[:300]
            data["updated_at"] = _now()
            updated = CustomerModelEntry(**data)
            self._models[(customer_id, model_id)] = updated
            self._save_locked()
            return updated


_registry: CustomerModelRegistry | None = None


def get_customer_registry() -> CustomerModelRegistry:
    global _registry
    if _registry is None:
        _registry = CustomerModelRegistry()
    return _registry


def reset_customer_registry_for_tests(data_file: Path) -> CustomerModelRegistry:
    return CustomerModelRegistry(data_file=data_file)
