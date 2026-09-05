"""Benchmark Lab API — run optimizer-vs-baseline jobs, poll, fetch latest."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.benchmark.runner import get_job, load_queries, start_job
from backend.database.mongodb import get_store

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"])


class BenchmarkStart(BaseModel):
    limit: int = 0  # 0 = all queries
    baseline_sample_n: int = 5
    baseline_quality_mode: str = "sampled"  # sampled | full
    mode: str = "full_optimizer"  # Phase 8 A/B mode (see MODES)


MODES = ("always_frontier", "legacy_classifier", "opencode_classifier",
         "exact_cache", "full_optimizer", "full_plus_llm_eval")


@router.get("/queries")
def benchmark_queries():
    qs = load_queries()
    cats: dict[str, int] = {}
    for q in qs:
        cats[q.get("category", "?")] = cats.get(q.get("category", "?"), 0) + 1
    return {"count": len(qs), "categories": cats,
            "sample": [{k: q.get(k) for k in ("id", "category", "prompt")} for q in qs[:3]]}


@router.post("/run")
def benchmark_run(body: BenchmarkStart):
    total = len(load_queries())
    if body.limit < 0 or body.limit > total:
        raise HTTPException(400, f"limit must be 0..{total}")
    if body.baseline_quality_mode not in ("sampled", "full"):
        raise HTTPException(400, "baseline_quality_mode must be sampled or full")
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    return {"job_id": start_job(body.limit, body.baseline_sample_n,
                                body.baseline_quality_mode, body.mode),
            "queries": body.limit or total,
            "baseline_sample_n": body.baseline_sample_n,
            "baseline_quality_mode": body.baseline_quality_mode,
            "mode": body.mode}


@router.get("/jobs/{job_id}")
def benchmark_job(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, f"unknown benchmark job '{job_id}'")
    return job


@router.get("/latest")
def benchmark_latest():
    doc = get_store().latest_benchmark()
    if doc is None:
        raise HTTPException(404, "no benchmark has been run yet")
    return doc
