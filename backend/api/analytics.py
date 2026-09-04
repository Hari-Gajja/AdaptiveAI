"""Analytics over stored request history — powers the dashboard KPIs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.database.mongodb import get_store

router = APIRouter(tags=["analytics"])


@router.get("/api/analytics")
def analytics():
    return get_store().analytics()


@router.get("/api/routing-stats")
def routing_stats():
    return get_store().routing_stats()


@router.get("/api/requests/{request_id}")
def get_request(request_id: str):
    doc = get_store().get_request(request_id)
    if doc is None:
        raise HTTPException(404, f"unknown request '{request_id}'")
    return doc
