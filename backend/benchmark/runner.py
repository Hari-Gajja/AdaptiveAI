"""Benchmark Engine — Phase 6b. Optimizer vs counterfactual always-best baseline.

Methodology (stated on the dashboard, no hidden assumptions):
- Optimizer runs all N queries live (reference-scored). Costs MEASURED.
- Baseline cost is COUNTERFACTUAL: measured token usage x baseline pricing.
  No duplicate expensive calls.
- Baseline quality is MEASURED on a small deterministic sample (default 5,
  spread across the query list) run on the baseline model. Labeled with n.
- quality_retention = optimizer_quality / baseline_quality (guarded).
- Cache is cleared at start so repeated-context items measure warm-up honestly.

Jobs run in a background thread; poll GET /api/benchmark/jobs/{id}.
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

QUERIES_FILE = Path(__file__).resolve().parent / "queries.json"

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_queries(path: Path = QUERIES_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_indices(n: int, k: int) -> list[int]:
    """Deterministic spread: every n/k-th query (covers categories)."""
    if k <= 0 or n == 0:
        return []
    k = min(k, n)
    step = n / k
    return sorted({min(n - 1, int(i * step)) for i in range(k)})


def run_benchmark(queries: list[dict], baseline_sample_n: int = 5,
                  progress=None, _run=None, _generate=None) -> dict:
    from backend.core.cache import get_cache
    from backend.core.cost import baseline_model
    from backend.core.optimizer import run_prompt
    from backend.core.quality import evaluate
    from backend.core.registry import get_registry
    from backend.providers.opencode import generate as _real_generate
    _run = _run or run_prompt
    _generate = _generate or _real_generate

    get_cache().clear()  # cold start; repeated-context items warm it (documented)
    registry = get_registry()
    base_entry = baseline_model(registry.enabled())

    per_query: list[dict] = []
    opt_cost = 0.0
    base_cost = 0.0
    quals: list[float] = []
    model_counts: Counter = Counter()
    esc = 0
    cache_hits = 0
    lat = 0
    total_in = total_out = 0

    for i, q in enumerate(queries):
        res = _run(q["prompt"], reference=q.get("reference_answer"),
                   context=q.get("context"),
                   max_tokens=q.get("max_tokens", 256), use_cache=True)
        in_tok = sum(a.input_tokens for a in res.attempts)
        out_tok = sum(a.output_tokens for a in res.attempts)
        total_in += in_tok
        total_out += out_tok
        b_cost = in_tok / 1e6 * base_entry.input_per_1M + out_tok / 1e6 * base_entry.output_per_1M
        opt_cost += res.total_cost_usd
        base_cost += b_cost
        fq = res.final_quality
        if fq is not None:
            quals.append(fq.overall)
        model_counts[res.final_model] += 1
        esc += 1 if res.escalated else 0
        cache_hits += 1 if res.cache_hit else 0
        lat += res.total_latency_ms
        per_query.append({
            "id": q.get("id"), "category": q.get("category"),
            "selected_model": res.routing.selected_model,
            "final_model": res.final_model,
            "quality": fq.overall if fq else None,
            "quality_method": fq.method if fq else None,
            "actual_cost": res.total_cost_usd,
            "baseline_cost": round(b_cost, 6),
            "escalated": res.escalated,
            "cache_kind": res.cache_kind,
            "latency_ms": res.total_latency_ms,
        })
        if progress:
            progress(i + 1, len(queries))

    # Baseline quality: measured on a deterministic sample via the real model.
    sample_idx = _sample_indices(len(queries), baseline_sample_n)
    base_quals: list[float] = []
    for i in sample_idx:
        q = queries[i]
        try:
            r = _generate(base_entry.model_id,
                          ([{"role": "system", "content": q["context"]}] if q.get("context") else []) +
                          [{"role": "user", "content": q["prompt"]}],
                          max_tokens=q.get("max_tokens", 256), temperature=0.1)
            base_quals.append(evaluate(r.text, q["prompt"], q.get("reference_answer")).overall)
        except Exception:
            continue

    n = len(queries)
    opt_q = round(sum(quals) / len(quals), 3) if quals else 0.0
    base_q = round(sum(base_quals) / len(base_quals), 3) if base_quals else None
    savings = base_cost - opt_cost
    return {
        "queries_tested": n,
        "optimizer_cost": round(opt_cost, 6),
        "baseline_cost": round(base_cost, 6),
        "baseline_model": base_entry.model_id,
        "baseline_cost_method": "counterfactual (measured tokens x baseline pricing)",
        "savings": round(savings, 6),
        "savings_pct": round(100.0 * savings / base_cost, 2) if base_cost > 0 else 0.0,
        "optimizer_quality": opt_q,
        "optimizer_quality_method": "reference-scored, all queries",
        "baseline_quality": base_q,
        "baseline_quality_method": f"measured on deterministic sample n={len(base_quals)}",
        "quality_retention": round(opt_q / base_q, 3) if base_q else None,
        "model_distribution": dict(model_counts),
        "cache_hit_rate": round(cache_hits / n, 3) if n else 0.0,
        "escalation_rate": round(esc / n, 3) if n else 0.0,
        "avg_latency_ms": round(lat / n) if n else 0,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "per_query": per_query,
        "finished_at": _now(),
    }


def _run_job(job_id: str, limit: int, baseline_sample_n: int) -> None:
    from backend.database.mongodb import get_store
    try:
        queries = load_queries()
        if limit > 0:
            queries = queries[:limit]

        def progress(done: int, total: int):
            with _jobs_lock:
                _jobs[job_id].update({"done": done, "total": total})

        result = run_benchmark(queries, baseline_sample_n, progress)
        result["limit"] = limit
        get_store().store_benchmark(result)
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "done": len(queries),
                                  "total": len(queries), "result": result})
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(e)})


def start_job(limit: int = 0, baseline_sample_n: int = 5) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _jobs[job_id] = {"job_id": job_id, "status": "running", "done": 0,
                         "total": limit or len(load_queries()),
                         "limit": limit, "baseline_sample_n": baseline_sample_n}
    threading.Thread(target=_run_job, args=(job_id, limit, baseline_sample_n),
                     daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job = dict(job)
    # Don't dump 50 per-query rows on every poll; latest endpoint has them.
    if job.get("status") == "running" and "result" in job:
        job = {k: v for k, v in job.items() if k != "result"}
    return job
