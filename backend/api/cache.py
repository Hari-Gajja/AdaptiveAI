"""Cache observability — stats + clear for the demo and dashboard."""
from __future__ import annotations

from fastapi import APIRouter

from backend.core.cache import get_cache

router = APIRouter(prefix="/api/cache", tags=["cache"])


@router.get("/stats")
def cache_stats():
    return get_cache().statistics()


@router.post("/clear")
def cache_clear():
    return get_cache().clear()
