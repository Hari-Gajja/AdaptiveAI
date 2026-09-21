"""Gateway API — customer auth + /v1 endpoints (Phases G2/G9).

Customers authenticate with THEIR gateway key (gw_...), never with provider
keys. The dependency resolves the customer; every /v1 route is scoped to that
customer so isolation is enforced in one place.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from backend.core.customers import Customer, get_customer_store


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip() or None


def require_customer(request: Request) -> Customer:
    """FastAPI dependency: resolve + validate the gateway API key.

    401 on missing/invalid key; 403 when the customer is disabled. The raw
    key is never logged or echoed back.
    """
    key = _extract_key(request)
    if not key:
        raise HTTPException(401, "missing gateway API key (Authorization: Bearer gw_... or x-api-key)")
    customer = get_customer_store().authenticate(key)
    if customer is None:
        if key.startswith(("oc_sk_", "sk-")):
            raise HTTPException(
                401,
                "OpenCode provider key supplied where a gateway key is required; "
                "use a gw_... key from Provision demo customer",
            )
        raise HTTPException(401, "invalid or disabled gateway API key; use a gw_... key")
    return customer
