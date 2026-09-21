"""Provider credential store — secrets never stored in plaintext.

Customer provider API keys (for THEIR LLM endpoints) are stored encrypted
with a keystream derived from GATEWAY_SECRET_KEY (XOR stream + SHA-256
chaining — adequate for at-rest obfuscation in a hackathon MVP; a production
system would use a KMS). Rules enforced everywhere:

  - keys are NEVER returned by any API endpoint
  - keys are NEVER logged
  - registry entries reference keys by `credential_reference` (a random id),
    not by value
"""
from __future__ import annotations

import hashlib
import json
import secrets
import tempfile
from pathlib import Path
from threading import Lock

from backend.config import GATEWAY_SECRET_KEY

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "customer_keys.json"


class SecretStoreError(Exception):
    pass


def _keystream(key_material: str, nonce: str, length: int) -> bytes:
    """Deterministic keystream: repeated SHA-256(secret || nonce || counter)."""
    out = bytearray()
    counter = 0
    seed = f"{key_material}:{nonce}".encode("utf-8")
    while len(out) < length:
        out.extend(hashlib.sha256(seed + b":" + str(counter).encode()).digest())
        counter += 1
    return bytes(out[:length])


def _xor_bytes(data: bytes, ks: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, ks))


def _encrypt(plaintext: str, secret: str) -> tuple[str, str]:
    nonce = secrets.token_hex(8)
    data = plaintext.encode("utf-8")
    ks = _keystream(secret, nonce, len(data))
    return nonce, _xor_bytes(data, ks).hex()


def _decrypt(nonce: str, blob_hex: str, secret: str) -> str:
    blob = bytes.fromhex(blob_hex)
    ks = _keystream(secret, nonce, len(blob))
    return _xor_bytes(blob, ks).decode("utf-8")


class SecretStore:
    """JSON-backed credential store: ref -> {nonce, blob}. One per process."""

    def __init__(self, data_file: Path = DATA_FILE, secret: str | None = None):
        self._file = Path(data_file)
        self._secret = secret if secret is not None else GATEWAY_SECRET_KEY
        self._lock = Lock()
        self._entries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                self._entries = {k: dict(v) for k, v in raw.get("keys", {}).items()}
            except Exception:
                self._entries = {}

    def _save_locked(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"keys": self._entries}
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=str(self._file.parent),
            encoding="utf-8", suffix=".tmp",
        ) as f:
            json.dump(payload, f, indent=2)
            tmp = f.name
        Path(tmp).replace(self._file)

    def put(self, plaintext_key: str) -> str:
        """Store a provider key; return its credential_reference (random id)."""
        if not plaintext_key or not plaintext_key.strip():
            raise SecretStoreError("empty provider key")
        ref = "cred_" + secrets.token_hex(8)
        with self._lock:
            nonce, blob = _encrypt(plaintext_key.strip(), self._secret)
            self._entries[ref] = {"nonce": nonce, "blob": blob}
            self._save_locked()
        return ref

    def get(self, ref: str) -> str | None:
        """Decrypt and return the provider key, or None when unknown."""
        with self._lock:
            entry = self._entries.get(ref)
        if entry is None:
            return None
        try:
            return _decrypt(entry["nonce"], entry["blob"], self._secret)
        except Exception:
            return None

    def delete(self, ref: str) -> None:
        with self._lock:
            if ref in self._entries:
                del self._entries[ref]
                self._save_locked()

    def count(self) -> int:
        return len(self._entries)


_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    global _store
    if _store is None:
        _store = SecretStore()
    return _store


def reset_secret_store_for_tests(data_file: Path, secret: str = "test-secret") -> SecretStore:
    return SecretStore(data_file=data_file, secret=secret)
