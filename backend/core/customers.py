"""Customer store — gateway tenancy (Phase G2).

Each customer gets:
  - a stable customer_id
  - a gateway API key (gw_ + 32 hex chars) shown ONCE at creation, stored
    ONLY as a SHA-256 hash (never plaintext, never returned by any API)
  - an isolated model pool (see customer_registry.py) and analytics scope

Persistence: JSON file (backend/data/customers.json) — same pattern as the
model registry. Swappable for MongoDB later without changing the API.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field, field_validator

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "customers.json"

_CUSTOMER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


class CustomerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Customer(BaseModel):
    customer_id: str
    name: str = ""
    api_key_hash: str = ""          # sha256 hex of the full gateway key
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    @field_validator("customer_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        v = v.strip().lower()
        if not _CUSTOMER_ID_RE.match(v):
            raise ValueError("customer_id must match [a-z0-9._-], 3-64 chars")
        return v


class CustomerCreate(BaseModel):
    customer_id: str
    name: str = ""

    # Same rule as Customer: reject bad ids at request-parse time (422) instead
    # of letting the store's server-side re-validation explode as a 500.
    @field_validator("customer_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        v = v.strip().lower()
        if not _CUSTOMER_ID_RE.match(v):
            raise ValueError("customer_id must match [a-z0-9._-], 3-64 chars")
        return v


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    """Gateway key format: gw_ + 32 hex chars (128 bits of entropy)."""
    return "gw_" + secrets.token_hex(16)


def public_view(c: Customer) -> dict:
    """Never includes the key hash — safe to return from any endpoint."""
    return {
        "customer_id": c.customer_id,
        "name": c.name,
        "enabled": c.enabled,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


class CustomerStore:
    """JSON-backed customer registry. One instance per process."""

    def __init__(self, data_file: Path = DATA_FILE):
        self._file = Path(data_file)
        self._lock = Lock()
        self._customers: dict[str, Customer] = {}
        self._load_or_seed()

    # ---- persistence ----
    def _load_or_seed(self) -> None:
        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                for item in raw.get("customers", []):
                    c = Customer(**item)
                    self._customers[c.customer_id] = c
                return
            except Exception:
                pass  # corrupt file -> start empty (no demo seed needed)

    def _save_locked(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"customers": [c.model_dump() for c in self._customers.values()]}
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(self._file.parent),
            encoding="utf-8", suffix=".tmp",
        ) as f:
            json.dump(payload, f, indent=2)
            tmp = f.name
        Path(tmp).replace(self._file)

    # ---- reads ----
    def list(self) -> list[Customer]:
        return sorted(self._customers.values(), key=lambda c: c.customer_id)

    def get(self, customer_id: str) -> Customer:
        try:
            return self._customers[customer_id.strip().lower()]
        except KeyError:
            raise CustomerError(f"unknown customer '{customer_id}'", 404) from None

    def exists(self, customer_id: str) -> bool:
        return customer_id.strip().lower() in self._customers

    # ---- auth ----
    def authenticate(self, api_key: str | None) -> Customer | None:
        """Return the customer for a valid, enabled gateway key; else None.

        Constant-time comparison is not required for SHA-256 preimages of
        high-entropy random keys, but we still compare hashes (never the raw
        key) so plaintext keys never sit in memory longer than one request.
        """
        if not api_key or not api_key.startswith("gw_"):
            return None
        h = hash_key(api_key.strip())
        for c in self._customers.values():
            if c.api_key_hash == h:
                return c if c.enabled else None
        return None

    # ---- writes ----
    def create(self, body: CustomerCreate) -> tuple[Customer, str]:
        """Create a customer. Returns (customer, full_api_key). The full key
        is returned EXACTLY ONCE — the store keeps only its hash."""
        cid = body.customer_id.strip().lower()
        with self._lock:
            if cid in self._customers:
                raise CustomerError(f"customer '{cid}' already exists", 409)
            key = new_api_key()
            c = Customer(
                customer_id=cid,
                name=body.name or cid,
                api_key_hash=hash_key(key),
                enabled=True,
                created_at=_now(), updated_at=_now(),
            )
            self._customers[cid] = c
            self._save_locked()
            return c, key

    def rotate_key(self, customer_id: str) -> tuple[Customer, str]:
        """Issue a new gateway key (old one stops working immediately)."""
        with self._lock:
            c = self.get(customer_id)
            key = new_api_key()
            c.api_key_hash = hash_key(key)
            c.updated_at = _now()
            self._customers[c.customer_id] = c
            self._save_locked()
            return c, key

    def set_enabled(self, customer_id: str, enabled: bool) -> Customer:
        with self._lock:
            c = self.get(customer_id)
            c.enabled = enabled
            c.updated_at = _now()
            self._customers[c.customer_id] = c
            self._save_locked()
            return c

    def delete(self, customer_id: str) -> None:
        with self._lock:
            self.get(customer_id)  # 404 if missing
            del self._customers[customer_id.strip().lower()]
            self._save_locked()


_store: CustomerStore | None = None


def get_customer_store() -> CustomerStore:
    global _store
    if _store is None:
        _store = CustomerStore()
    return _store


def reset_customer_store_for_tests(data_file: Path) -> CustomerStore:
    return CustomerStore(data_file=data_file)
