"""Model Profiler — Phase 6b. Measures real capability scores per model.

Runs every enabled model over backend/benchmark/capability_tests.json,
scores each answer against its reference with quality.evaluate, and writes
backend/data/profiles.json. The router then prefers these MEASURED scores
over the estimated priors (see capabilities.py).

Scores are labeled as benchmark performance on OUR test set — never as
universal intelligence scores.

Jobs run in a background thread; poll GET /api/models/profile/jobs/{id}.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.core.capabilities import CATEGORIES, PROFILES_FILE

TESTS_FILE = Path(__file__).resolve().parent.parent / "benchmark" / "capability_tests.json"

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tests(path: Path = TESTS_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def profile_model(model_id: str, tests: list[dict], _generate=None) -> dict:
    """Run one model over the tests. _generate injectable for keyless tests."""
    if _generate is None:
        from backend.providers.opencode import generate as _generate
    from backend.core.quality import evaluate
    by_cat: dict[str, list[float]] = {}
    for t in tests:
        msgs = ([{"role": "system", "content": t["context"]}] if t.get("context") else []) + \
               [{"role": "user", "content": t["prompt"]}]
        r = _generate(model_id, msgs, max_tokens=t.get("max_tokens", 256),
                      temperature=0.1)
        q = evaluate(r.text, t["prompt"], t.get("reference_answer"))
        by_cat.setdefault(t["category"], []).append(q.overall)
    return {c: round(sum(v) / len(v), 3) for c, v in by_cat.items()
            if v and c in CATEGORIES}


def _save_profiles(new: dict[str, dict]) -> dict:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    for mid, scores in new.items():
        entry = dict(scores)
        entry["measured_at"] = _now()
        entry["n"] = sum(1 for _ in load_tests())
        current[mid] = entry
    tmp = PROFILES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    tmp.replace(PROFILES_FILE)
    return current


def _run_job(job_id: str, model_ids: list[str]) -> None:
    from backend.core.registry import get_registry
    tests = load_tests()
    done = 0
    total = len(model_ids) * len(tests)
    results: dict[str, dict] = {}
    try:
        for mid in model_ids:
            with _jobs_lock:
                _jobs[job_id]["current_model"] = mid
            scores = profile_model(mid, tests)
            results[mid] = scores
            try:
                get_registry().mark_profile_status(mid, "profiled")
            except Exception:
                pass
            done += len(tests)
            with _jobs_lock:
                _jobs[job_id]["done"] = done
        _save_profiles(results)
        with _jobs_lock:
            _jobs[job_id].update({"status": "done", "done": total,
                                  "results": results})
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id].update({"status": "error", "error": str(e)})


def start_job(model_ids: list[str]) -> str:
    job_id = uuid.uuid4().hex[:8]
    total = len(model_ids) * len(load_tests())
    with _jobs_lock:
        _jobs[job_id] = {"job_id": job_id, "status": "running", "models": model_ids,
                         "done": 0, "total": total, "current_model": model_ids[0] if model_ids else ""}
    threading.Thread(target=_run_job, args=(job_id, model_ids), daemon=True).start()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None
