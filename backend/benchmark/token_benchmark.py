"""Token-efficiency benchmark (spec §13/§21) — naive vs optimized prompting.

Runs the same queries twice:
- naive:      raw prompt, fixed max_tokens (TOKEN_OPT_BASELINE_OUTPUT_BUDGET,
              default 1024) — the "just send it" baseline.
- optimized:  prompt templates + predicted output budget (small/medium/large)
              + normalization, single attempt.

Reports measured token deltas and cost deltas per arm. Jobs run in a
background thread; poll GET /api/benchmark/token-efficiency/{id}.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from backend.config import TOKEN_OPT_BASELINE_OUTPUT_BUDGET

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_token_benchmark(queries: list[dict], progress=None,
                        _run=None, _generate=None) -> dict:
    from backend.core.cache import reset_cache_for_tests
    from backend.core.cost import baseline_model
    from backend.core.optimizer import run_prompt
    from backend.core.registry import get_registry
    from backend.providers.opencode import generate as _real_generate

    _run = _run or run_prompt
    _generate = _generate or _real_generate
    registry = get_registry()
    base_entry = baseline_model(registry.enabled())

    arms: dict[str, dict] = {
        "naive": {"cost": 0.0, "in": 0, "out": 0, "lat": 0, "n": 0,
                  "quals": [], "rows": []},
        "optimized": {"cost": 0.0, "in": 0, "out": 0, "lat": 0, "n": 0,
                      "quals": [], "rows": []},
    }

    for i, q in enumerate(queries):
        prompt = q["prompt"]
        ref = q.get("reference_answer")
        ctx = q.get("context")
        for arm, kwargs in (
            ("naive", {"max_tokens": TOKEN_OPT_BASELINE_OUTPUT_BUDGET,
                       "max_attempts": 1, "use_cache": False}),
            ("optimized", {"max_tokens": None, "max_attempts": 1,
                           "use_cache": False}),
        ):
            try:
                res = _run(prompt, reference=ref, context=ctx, **kwargs)
            except Exception as e:
                arms[arm]["rows"].append({
                    "id": q.get("id"), "category": q.get("category"),
                    "status": "failed", "error": str(e),
                })
                continue
            a = arms[arm]
            a["cost"] += res.total_cost_usd
            a["in"] += sum(x.input_tokens for x in res.attempts)
            a["out"] += sum(x.output_tokens for x in res.attempts)
            a["lat"] += res.total_latency_ms
            a["n"] += 1
            fq = res.final_quality
            if fq is not None:
                a["quals"].append(fq.overall)
            a["rows"].append({
                "id": q.get("id"), "category": q.get("category"),
                "status": "ok",
                "input_tokens": sum(x.input_tokens for x in res.attempts),
                "output_tokens": sum(x.output_tokens for x in res.attempts),
                "cost_usd": res.total_cost_usd,
                "latency_ms": res.total_latency_ms,
                "quality": fq.overall if fq else None,
                "estimated_output_tokens": res.estimated_output_tokens,
                "normalization_tokens_saved":
                    (res.normalization or {}).get("tokens_saved", 0),
                "model": res.final_model,
            })
        if progress:
            progress(i + 1, len(queries))

    def _avg(arm: dict, key: str) -> float:
        return round(arm[key] / arm["n"], 2) if arm["n"] else 0.0

    def _arm_summary(arm: dict) -> dict:
        return {
            "queries": arm["n"],
            "total_input_tokens": arm["in"],
            "total_output_tokens": arm["out"],
            "total_tokens": arm["in"] + arm["out"],
            "total_cost_usd": round(arm["cost"], 6),
            "avg_input_tokens": _avg(arm, "in"),
            "avg_output_tokens": _avg(arm, "out"),
            "avg_latency_ms": round(arm["lat"] / arm["n"]) if arm["n"] else 0,
            "avg_quality": (round(sum(arm["quals"]) / len(arm["quals"]), 3)
                            if arm["quals"] else None),
            "rows": arm["rows"],
        }

    naive, optimized = arms["naive"], arms["optimized"]
    tok_saved = naive["out"] - optimized["out"]
    cost_saved = naive["cost"] - optimized["cost"]
    return {
        "baseline_output_budget": TOKEN_OPT_BASELINE_OUTPUT_BUDGET,
        "baseline_model": base_entry.model_id,
        "naive": _arm_summary(naive),
        "optimized": _arm_summary(optimized),
        "output_tokens_saved": tok_saved,
        "output_tokens_saved_pct": (round(100.0 * tok_saved / naive["out"], 2)
                                    if naive["out"] else 0.0),
        "cost_saved_usd": round(cost_saved, 6),
        "cost_saved_pct": (round(100.0 * cost_saved / naive["cost"], 2)
                           if naive["cost"] else 0.0),
        "quality_delta": ((round(sum(optimized["quals"]) / len(optimized["quals"]), 3)
                           - round(sum(naive["quals"]) / len(naive["quals"]), 3))
                          if naive["quals"] and optimized["quals"] else None),
        "queries_tested": len(queries),
        "finished_at": _now(),
    }


def _run_job(job_id: str, limit: int) -> None:
    from backend.benchmark.runner import load_queries
    from backend.database.mongodb import get_store
    try:
        queries = load_queries()
        if limit > 0:
            queries = queries[:limit]

        def progress(done: int, total: int):
            with _jobs_lock:
                _jobs[job_id].update({"done": done, "total": total})

        result = run_token_benchmark(queries, progress)
        result["limit"] = limit
        get_store().store_benchmark({**result, "kind": "token_efficiency"})
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "done": len(queries),
                                  "total": len(queries), "result": result})
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(e)})


def start_token_job(limit: int = 10) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _jobs[job_id] = {"job_id": job_id, "status": "running", "done": 0,
                         "total": limit or len(load_queries()), "limit": limit,
                         "kind": "token_efficiency"}
    threading.Thread(target=_run_job, args=(job_id, limit), daemon=True).start()
    return job_id


def get_token_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job = dict(job)
    if job.get("status") == "running" and "result" in job:
        job = {k: v for k, v in job.items() if k != "result"}
    return job
