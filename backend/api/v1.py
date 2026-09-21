"""Gateway /v1 API — Phase G9. The multi-tenant, model-agnostic surface.

Everything here is customer-scoped via the require_customer dependency:
  - POST /v1/models/register   connect a model (base_url + key + model_id)
  - GET  /v1/models            list MY models (never another tenant's)
  - GET  /v1/models/{id}       one of MY models
  - DELETE /v1/models/{id}     remove mine
  - POST /v1/models/{id}/test  connectivity test through MY provider
  - POST /v1/models/{id}/profile  auto-profile capability (never manual tiers)
  - POST /v1/optimize          full pipeline over MY pool (hybrid routing)
  - POST /v1/chat/completions  OpenAI-compatible surface (model="auto")
  - GET  /v1/health            gateway + control-plane + store status
  - GET  /v1/analytics         MY cost/savings/quality/routing analytics

Security: provider keys are encrypted at rest (secret store), never returned,
never logged. Pricing is never fabricated — unpriced models report
pricing_status "unknown" and are excluded from cost-optimal routing until
prices are declared (or found in the catalog).
"""
from __future__ import annotations

import time

import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from backend.api.gateway import require_customer
from backend.core.customers import Customer, CustomerCreate, CustomerError, get_customer_store
from backend.core.customer_registry import (
    CustomerModelCreate,
    CustomerModelUpdate,
    RegistryError,
    get_customer_registry,
    public_view,
)
from backend.core.router import NoCapableModel
from backend.core.secret_store import SecretStoreError, get_secret_store
from backend.providers.base import ProviderError, provider_for
from backend.providers.opencode import OpenCodeError

router = APIRouter(prefix="/v1", tags=["gateway"])


# ---------------------------------------------------------------- customers
class CustomerOut(BaseModel):
    customer_id: str
    name: str
    enabled: bool
    created_at: str
    updated_at: str


class ProviderDiscoveryRequest(BaseModel):
    provider: str = "openai_compatible"
    base_url: str
    api_key: str


@router.post("/models/discover")
def discover_provider_models(body: ProviderDiscoveryRequest,
                             customer: Customer = Depends(require_customer)):
    """Discover provider models without storing the provider key."""
    del customer
    base_url = body.base_url.strip().rstrip("/")
    if not body.api_key.strip():
        raise HTTPException(400, "provider API key is required")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "base_url must be an http(s) URL")
    try:
        response = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {body.api_key.strip()}",
                     "Accept": "application/json",
                     "User-Agent": "adaptive-llm-cost-optimizer/1.0"},
            timeout=20.0,
        )
        if response.status_code in (401, 403):
            raise HTTPException(response.status_code, "provider API key was rejected")
        response.raise_for_status()
        payload = response.json()
        raw_models = payload.get("data", []) if isinstance(payload, dict) else []
        models = [{"id": item["id"], "name": item.get("name", item["id"]),
                   "owned_by": item.get("owned_by", body.provider)}
                  for item in raw_models
                  if isinstance(item, dict) and item.get("id")]
        return {"models": models, "count": len(models), "provider": body.provider}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"could not discover provider models: {e}") from e


@router.post("/customers", status_code=201)
def create_customer(body: CustomerCreate):
    """Provision a customer. The gateway API key is returned EXACTLY ONCE."""
    try:
        customer, key = get_customer_store().create(body)
    except CustomerError as e:
        raise HTTPException(e.status, str(e))
    except ValidationError as e:
        # The store re-validates the (lowercased) id server-side; surface it
        # as a clean 400 instead of an unhandled 500.
        raise HTTPException(400, str(e.errors()[0].get("msg", "invalid customer_id")))
    return {"customer": CustomerOut(**customer.model_dump()).model_dump(),
            "api_key": key,
            "note": "Store this key securely — it is shown only once."}


@router.get("/customers/me")
def whoami(customer: Customer = Depends(require_customer)):
    return {"customer": {"customer_id": customer.customer_id, "name": customer.name,
                         "enabled": customer.enabled}}


@router.post("/customers/rotate-key")
def rotate_customer_key(customer: Customer = Depends(require_customer)):
    """Generate a fresh platform key for the current customer."""
    _, key = get_customer_store().rotate_key(customer.customer_id)
    return {"api_key": key, "note": "Store this key securely — it is shown only once."}


# ---------------------------------------------------------------- models
@router.post("/models/register", status_code=201)
def register_model(body: CustomerModelCreate,
                   customer: Customer = Depends(require_customer)):
    """Connect one of the customer's own models.

    api_key (optional) is encrypted at rest and referenced by an opaque
    credential_reference — it is never stored in plaintext, never returned,
    and never logged. Pricing: declared prices win; otherwise the catalog is
    consulted; otherwise pricing_status stays "unknown" (never fabricated).
    """
    cred_ref = ""
    if body.api_key and body.api_key.strip():
        try:
            cred_ref = get_secret_store().put(body.api_key)
        except SecretStoreError as e:
            raise HTTPException(400, str(e))
    try:
        entry = get_customer_registry().create(customer.customer_id, body,
                                               credential_reference=cred_ref)
    except RegistryError as e:
        if cred_ref:
            get_secret_store().delete(cred_ref)  # don't orphan the secret
        raise HTTPException(e.status, str(e))
    return public_view(entry)


@router.get("/models")
def list_my_models(enabled_only: bool = False,
                   customer: Customer = Depends(require_customer)):
    models = [public_view(m) for m in
              get_customer_registry().list(customer.customer_id,
                                           enabled_only=enabled_only)]
    return {"models": models, "count": len(models)}


@router.get("/models/{model_id}")
def get_my_model(model_id: str, customer: Customer = Depends(require_customer)):
    try:
        return public_view(get_customer_registry().get(customer.customer_id, model_id))
    except RegistryError as e:
        raise HTTPException(e.status, str(e))


@router.put("/models/{model_id}")
def update_my_model(model_id: str, patch: CustomerModelUpdate,
                    customer: Customer = Depends(require_customer)):
    """Update my model. api_key rotation re-encrypts and swaps the ref."""
    new_ref = None
    if patch.api_key and patch.api_key.strip():
        try:
            new_ref = get_secret_store().put(patch.api_key)
        except SecretStoreError as e:
            raise HTTPException(400, str(e))
    try:
        entry = get_customer_registry().update(customer.customer_id, model_id,
                                               patch,
                                               new_credential_reference=new_ref)
    except RegistryError as e:
        if new_ref:
            get_secret_store().delete(new_ref)
        raise HTTPException(e.status, str(e))
    return public_view(entry)


@router.delete("/models/{model_id}")
def delete_my_model(model_id: str, customer: Customer = Depends(require_customer)):
    reg = get_customer_registry()
    try:
        entry = reg.get(customer.customer_id, model_id)
    except RegistryError as e:
        raise HTTPException(e.status, str(e))
    if entry.credential_reference:
        get_secret_store().delete(entry.credential_reference)
    reg.delete(customer.customer_id, model_id)
    return {"deleted": model_id}


@router.post("/models/{model_id}/test")
def test_my_model(model_id: str, customer: Customer = Depends(require_customer)):
    """Live connectivity test through the customer's own endpoint+key."""
    reg = get_customer_registry()
    try:
        entry = reg.get(customer.customer_id, model_id)
    except RegistryError as e:
        raise HTTPException(e.status, str(e))
    if not entry.base_url:
        raise HTTPException(400, f"model '{model_id}' has no base_url configured")
    api_key = get_secret_store().get(entry.credential_reference) \
        if entry.credential_reference else None
    provider = provider_for(entry.provider, entry.model_id, entry.base_url, api_key)
    result = provider.test_connection()
    reg.mark_test_result(customer.customer_id, model_id, result.ok, result.detail)
    return result.view()


@router.post("/models/{model_id}/profile")
def profile_my_model(model_id: str, customer: Customer = Depends(require_customer)):
    """Auto-profile capability on the customer's model (never manual tiers).

    Runs the standard capability test set through the customer's endpoint and
    stores measured scores; the router prefers measured over priors.
    """
    from backend.core import profiler
    reg = get_customer_registry()
    try:
        entry = reg.get(customer.customer_id, model_id)
    except RegistryError as e:
        raise HTTPException(e.status, str(e))
    if not entry.enabled:
        raise HTTPException(400, f"model '{model_id}' is disabled")
    if not entry.base_url:
        raise HTTPException(400, f"model '{model_id}' has no base_url configured")

    api_key = get_secret_store().get(entry.credential_reference) \
        if entry.credential_reference else None
    provider = provider_for(entry.provider, entry.model_id, entry.base_url, api_key)

    def _gen(mid: str, messages: list[dict], max_tokens: int = 256,
             temperature: float = 0.1):
        return provider.generate(messages, max_tokens=max_tokens,
                                 temperature=temperature)

    try:
        reg.mark_profile_status(customer.customer_id, model_id, "profiling")
    except RegistryError:
        pass
    job_id = profiler.start_job([model_id], _generate=_gen,
                                customer_id=customer.customer_id)
    return {"job_id": job_id, "models": [model_id],
            "note": "poll GET /api/models/profile/jobs/{job_id}"}


# ---------------------------------------------------------------- optimize
class OptimizeRequest(BaseModel):
    prompt: str
    max_tokens: int | None = 512
    temperature: float = 0.2
    context: str | None = None
    reference_answer: str | None = None
    max_attempts: int = 2
    use_cache: bool = True
    force_model: str | None = None


@router.post("/optimize")
def optimize(req: OptimizeRequest, customer: Customer = Depends(require_customer)):
    """Full pipeline over the customer's own pool: cache -> classify ->
    hybrid selection (Nemotron recommends, deterministic validator decides)
    -> generate -> quality -> escalate -> honest cost math."""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(400, "prompt must not be empty")
    models = get_customer_registry().list(customer.customer_id)
    if not models:
        raise HTTPException(400,
                            "no models registered — POST /v1/models/register first")
    factory = _provider_factory(customer.customer_id)
    try:
        from backend.core.optimizer import run_prompt
        res = run_prompt(
            req.prompt,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            reference=req.reference_answer,
            force_model=req.force_model,
            max_attempts=req.max_attempts,
            context=req.context,
            use_cache=req.use_cache,
            customer_id=customer.customer_id,
            customer_models=models,
            provider_factory=factory,
        )
    except (ValueError, NoCapableModel) as e:
        # ValueError: bad request shape. NoCapableModel: the pool has no
        # enabled, priced model (e.g. everything unpriced) — a client-fixable
        # 400, never an unhandled 500.
        raise HTTPException(400, str(e))
    except OpenCodeError as e:
        raise HTTPException(502, str(e))
    return _optimize_response(res, req.prompt, customer.customer_id)


def _provider_factory(customer_id: str):
    """Build a per-model provider from the customer's registry + secret store."""
    reg = get_customer_registry()
    store = get_secret_store()

    def factory(model_id: str):
        entry = reg.get(customer_id, model_id)
        api_key = store.get(entry.credential_reference) \
            if entry.credential_reference else None
        return provider_for(entry.provider, entry.model_id, entry.base_url, api_key)
    return factory


def _optimize_response(res, prompt: str, customer_id: str) -> dict:
    """Shape the optimizer result for the gateway API + persist analytics."""
    final = res.attempts[-1] if res.attempts else None
    quality_score = final.quality.overall if final and final.quality else None
    resp = {
        "customer_id": customer_id,
        "answer": res.answer,
        "selected_model": res.final_model,
        "initial_model": res.initial_model,
        "escalated": res.escalated,
        "cache_hit": res.cache_hit,
        "cache_kind": res.cache_kind,
        "tokens_avoided": res.tokens_avoided,
        "cache_saved_usd": res.cache_saved_usd,
        "input_tokens": sum(a.input_tokens for a in res.attempts if a.input_tokens is not None),
        "output_tokens": sum(a.output_tokens for a in res.attempts if a.output_tokens is not None),
        "quality_score": quality_score,
        "quality_passed": res.quality_passed,
        "verification_status": res.verification_status,
        "attempts": [
            {"model_id": a.model_id,
             "quality": a.quality.overall if a.quality else None,
             "passed": a.passed, "cost_usd": a.cost_usd,
             "latency_ms": a.latency_ms,
             "failure_type": a.failure_type, "error": a.error}
            for a in res.attempts
        ],
        "actual_cost_usd": res.total_cost_usd,
        "baseline_model": res.baseline_model,
        "baseline_cost_usd": res.baseline_cost_usd,
        "savings_usd": res.savings_usd,
        "savings_pct": res.savings_pct,
        "savings_direction": res.savings_direction,
        "control_plane_cost_usd": res.ledger.total_cost_usd,
        "net_savings_usd": res.net_savings_usd,
        "net_savings_pct": res.net_savings_pct,
        "net_savings_direction": res.net_savings_direction,
        "latency_ms": res.total_latency_ms,
        "analysis": {
            "task_type": res.analysis.task_type,
            "difficulty_score": res.analysis.difficulty_score,
            "confidence": res.analysis.confidence,
            "required_capabilities": res.analysis.required_capabilities,
            "metadata": res.analysis.metadata_view(),
        },
        "routing": {
            "selected_model": res.routing.selected_model,
            "decision_reason": res.routing.decision_reason,
            "capability_source": res.routing.capability_source,
        },
        "token_report": res.token_report,
        "control_plane": res.ledger.view(),
    }
    if getattr(res, "gateway_decision", None) is not None:
        gd = res.gateway_decision
        resp["model_selection"] = {
            "provenance": gd.provenance,
            "reason": gd.reason,
            "recommendation": gd.recommendation,
            "validation_notes": gd.validation_notes,
        }
    try:
        from backend.database.mongodb import get_store
        rid = get_store().store_request({
            "customer_id": customer_id,
            "gateway": True,
            "prompt": prompt[:2000],
            "task_type": res.analysis.task_type,
            "difficulty_score": res.analysis.difficulty_score,
            "selected_model": res.routing.selected_model,
            "initial_model": res.initial_model,
            "final_model": res.final_model,
            "cache_hit": res.cache_hit,
            "cache_kind": res.cache_kind,
            "input_tokens": resp["input_tokens"],
            "output_tokens": resp["output_tokens"],
            "actual_cost_usd": res.total_cost_usd,
            "baseline_model": res.baseline_model,
            "baseline_cost_usd": res.baseline_cost_usd,
            "savings_usd": res.savings_usd,
            "savings_direction": res.savings_direction,
            "control_plane_cost_usd": res.ledger.total_cost_usd,
            "net_savings_usd": res.net_savings_usd,
            "net_savings_direction": res.net_savings_direction,
            "quality_score": quality_score,
            "escalated": res.escalated,
            "latency_ms": res.total_latency_ms,
            "verification_status": res.verification_status,
        })
        resp["request_id"] = rid
    except Exception:
        resp["request_id"] = None
    return resp


# ------------------------------------------- OpenAI-compatible chat surface
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionsRequest(BaseModel):
    model: str = "auto"               # "auto" = gateway routes; or a specific id
    messages: list[ChatMessage]
    max_tokens: int | None = 512
    temperature: float = 0.2


@router.post("/chat/completions")
def chat_completions(req: ChatCompletionsRequest,
                     customer: Customer = Depends(require_customer)):
    """OpenAI-compatible endpoint. model="auto" -> gateway routes over the
    customer's pool (hybrid selection). A specific model id must belong to
    the customer. Response follows the OpenAI chat.completion shape plus a
    gateway extension block (routing/cost/savings provenance)."""
    if not req.messages:
        raise HTTPException(400, "messages must not be empty")
    reg = get_customer_registry()
    models = reg.list(customer.customer_id)
    if not models:
        raise HTTPException(400, "no models registered — POST /v1/models/register first")

    prompt = "\n".join(m.content for m in req.messages if m.role == "user") \
        or req.messages[-1].content
    context = "\n".join(m.content for m in req.messages if m.role == "system") or None

    force_model = None
    if req.model and req.model != "auto":
        try:
            reg.get(customer.customer_id, req.model)
        except RegistryError as e:
            raise HTTPException(e.status,
                                f"model '{req.model}' is not registered for this customer")
        force_model = req.model

    factory = _provider_factory(customer.customer_id)
    try:
        from backend.core.optimizer import run_prompt
        res = run_prompt(
            prompt,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            context=context,
            force_model=force_model,
            customer_id=customer.customer_id,
            customer_models=models,
            provider_factory=factory,
        )
    except (ValueError, NoCapableModel) as e:
        raise HTTPException(400, str(e))
    except OpenCodeError as e:
        raise HTTPException(502, str(e))

    final = res.attempts[-1] if res.attempts else None
    body = {
        "id": "gwchat-" + (final.model_id if final else "cache") + "-" + str(abs(hash(prompt)) % 10**8),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": res.final_model or req.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": res.answer},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": sum(a.input_tokens for a in res.attempts if a.input_tokens is not None),
            "completion_tokens": sum(a.output_tokens for a in res.attempts if a.output_tokens is not None),
            "total_tokens": (sum(a.input_tokens for a in res.attempts if a.input_tokens is not None)
                             + sum(a.output_tokens for a in res.attempts if a.output_tokens is not None)),
        },
        # ---- gateway extension (OpenAI clients ignore unknown fields) ----
        "gateway": {
            "customer_id": customer.customer_id,
            "routed_model": res.final_model,
            "initial_model": res.initial_model,
            "requested_model": req.model,
            "cache_hit": res.cache_hit,
            "cache_kind": res.cache_kind,
            "actual_cost_usd": res.total_cost_usd,
            "baseline_model": res.baseline_model,
            "baseline_cost_usd": res.baseline_cost_usd,
            "savings_usd": res.savings_usd,
            "savings_direction": res.savings_direction,
            "control_plane_cost_usd": res.ledger.total_cost_usd,
            "net_savings_usd": res.net_savings_usd,
            "quality_score": final.quality.overall if final and final.quality else None,
            "escalated": res.escalated,
            "decision_reason": res.routing.decision_reason,
        },
    }
    try:
        from backend.database.mongodb import get_store
        get_store().store_request({
            "customer_id": customer.customer_id,
            "gateway": True,
            "surface": "chat_completions",
            "prompt": prompt[:2000],
            "task_type": res.analysis.task_type,
            "selected_model": res.routing.selected_model,
            "final_model": res.final_model,
            "cache_hit": res.cache_hit,
            "actual_cost_usd": res.total_cost_usd,
            "baseline_cost_usd": res.baseline_cost_usd,
            "savings_usd": res.savings_usd,
            "control_plane_cost_usd": res.ledger.total_cost_usd,
            "net_savings_usd": res.net_savings_usd,
            "quality_score": final.quality.overall if final and final.quality else None,
            "escalated": res.escalated,
            "latency_ms": res.total_latency_ms,
        })
    except Exception:
        pass
    return body


# ---------------------------------------------------------------- analytics
@router.get("/analytics")
def my_analytics(customer: Customer = Depends(require_customer)):
    """Customer-scoped analytics. A customer NEVER sees another tenant's
    numbers — every doc is filtered by customer_id. Includes honest net
    savings (gross savings minus control-plane overhead)."""
    from backend.database.mongodb import get_store
    store = get_store()
    return {
        "customer_id": customer.customer_id,
        "analytics": store.analytics(customer_id=customer.customer_id),
        "routing": store.routing_stats(customer_id=customer.customer_id),
    }


# ---------------------------------------------------------------- health
@router.get("/health")
def gateway_health(customer: Customer = Depends(require_customer)):
    from backend.llm import config as cp_cfg
    from backend.llm.opencode_client import available as cp_available
    from backend.core.pricing import catalog_view
    reg = get_customer_registry()
    models = reg.list(customer.customer_id)
    priced = [m for m in models if m.pricing_status == "configured"]
    return {
        "status": "ok",
        "customer_id": customer.customer_id,
        "customer_enabled": customer.enabled,
        "models_registered": len(models),
        "models_priced": len(priced),
        "models_pricing_unknown": len(models) - len(priced),
        "control_plane": {
            "enabled": cp_cfg.OPENCODE_ENABLED,
            "model": cp_cfg.OPENCODE_MODEL,
            "available": cp_available(),
        },
        "pricing_catalog_size": len(catalog_view()),
        "cache_backend": _cache_backend(),
    }


def _cache_backend() -> str:
    try:
        from backend.core.cache import get_cache
        return get_cache().statistics().get("backend", "unknown")
    except Exception:
        return "unknown"
