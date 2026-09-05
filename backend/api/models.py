"""Model Registry HTTP API — CRUD over backend/core/registry.py + profiler jobs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.core import profiler
from backend.core.capabilities import capabilities_for, is_measured, load_profiles
from backend.core.registry import (
    ModelCreate,
    ModelUpdate,
    RegistryError,
    get_registry,
    public_view,
)
from backend.llm import config as cp_cfg
from backend.llm.opencode_client import (
    control_plane_stats,
    health as cp_health,
)
from backend.providers.opencode import OpenCodeError, list_models

router = APIRouter(prefix="/api/models", tags=["models"])


class ProfileStart(BaseModel):
    model_ids: list[str] | None = None  # None = all enabled


def _reg():
    return get_registry()


@router.get("")
def list_models_api(enabled_only: bool = Query(False), include_catalog: bool = Query(False)):
    reg = _reg()
    models = [public_view(m) for m in reg.list(enabled_only=enabled_only)]
    out: dict = {"models": models, "count": len(models)}
    if include_catalog:
        try:
            catalog = list_models()
            out["catalog"] = catalog
            out["catalog_size"] = len(catalog)
        except OpenCodeError as e:
            out["catalog_error"] = str(e)
    return out


@router.get("/profiles")
def model_profiles():
    """Measured capability profiles + priors, per model (measured wins)."""
    profiles = load_profiles()
    out = []
    for m in _reg().list():
        out.append({
            "model_id": m.model_id,
            "measured": is_measured(m.model_id, profiles),
            "capabilities": capabilities_for(m.model_id, profiles),
            "measured_at": (profiles.get(m.model_id, {}) or {}).get("measured_at"),
        })
    return {"profiles": out}


@router.get("/control-plane")
def control_plane_status():
    """Phase 8: control-plane health, config, and lifetime call counters."""
    h = cp_health()
    stats = control_plane_stats()  # already a dict (view())
    return {
        "enabled": cp_cfg.OPENCODE_ENABLED,
        "model_id": cp_cfg.OPENCODE_MODEL,
        "classifier_backend": cp_cfg.CLASSIFIER_BACKEND,
        "quality_check_mode": cp_cfg.QUALITY_CHECK_MODE,
        "cache_verify_enabled": cp_cfg.CACHE_VERIFY_ENABLED,
        "budgets": {
            "classifier_max_output_tokens": cp_cfg.CLASSIFIER_MAX_OUTPUT_TOKENS,
            "verifier_max_output_tokens": cp_cfg.VERIFIER_MAX_OUTPUT_TOKENS,
            "evaluator_max_output_tokens": cp_cfg.EVALUATOR_MAX_OUTPUT_TOKENS,
            "timeout_seconds": cp_cfg.OPENCODE_TIMEOUT_SECONDS,
        },
        "health": h,
        "stats": stats,
    }


@router.get("/{model_id}")
def get_model(model_id: str):
    try:
        return public_view(_reg().get(model_id))
    except RegistryError as e:
        raise HTTPException(e.status, str(e))


@router.post("", status_code=201)
def create_model(body: ModelCreate):
    try:
        return public_view(_reg().create(body))
    except RegistryError as e:
        raise HTTPException(e.status, str(e))


@router.put("/{model_id}")
def update_model(model_id: str, patch: ModelUpdate):
    try:
        return public_view(_reg().update(model_id, patch))
    except RegistryError as e:
        raise HTTPException(e.status, str(e))


@router.delete("/{model_id}")
def delete_model(model_id: str):
    try:
        _reg().delete(model_id)
        return {"deleted": model_id}
    except RegistryError as e:
        raise HTTPException(e.status, str(e))


@router.post("/profile")
def start_profiling(body: ProfileStart):
    """Profile models in background; poll GET /api/models/profile/jobs/{id}."""
    try:
        models = [m.model_id for m in _reg().enabled()]
    except Exception as e:
        raise HTTPException(400, str(e))
    wanted = body.model_ids or models
    unknown = [m for m in wanted if m not in models]
    if unknown:
        raise HTTPException(400, f"unknown or disabled models: {unknown}")
    for m in wanted:
        try:
            _reg().mark_profile_status(m, "profiling")
        except RegistryError:
            pass
    return {"job_id": profiler.start_job(wanted), "models": wanted}


@router.get("/profile/jobs/{job_id}")
def profiling_job(job_id: str):
    job = profiler.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"unknown profiling job '{job_id}'")
    return job


@router.post("/{model_id}/profile")
def profile_model(model_id: str):
    try:
        entry = _reg().get(model_id)
    except RegistryError as e:
        raise HTTPException(e.status, str(e))
    if not entry.enabled:
        raise HTTPException(400, f"model '{model_id}' is disabled")
    try:
        _reg().mark_profile_status(model_id, "profiling")
    except RegistryError:
        pass
    return {"job_id": profiler.start_job([model_id]), "models": [model_id]}
