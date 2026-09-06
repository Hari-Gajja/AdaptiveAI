"""Persistence — Phase 6a. MongoDB when reachable, in-memory fallback otherwise.

The app NEVER breaks when MongoDB is down: RequestStore tries MongoDB once
(short timeout) and otherwise keeps everything in process memory. Analytics
are computed in Python over stored docs, so numbers are identical in both
modes. For multi-process / production use, set MONGODB_URI to Atlas.

Collections (db = DATABASE_NAME): requests, benchmarks.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

log = logging.getLogger("llm_optimizer.store")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RequestStore:
    def __init__(self, uri: str, db_name: str):
        self._lock = threading.Lock()
        self.mode = "memory"
        self._mem_requests: list[dict] = []
        self._mem_benchmarks: list[dict] = []
        self._db = None
        try:
            from pymongo import MongoClient
            client = MongoClient(uri, serverSelectionTimeoutMS=2500)
            client.admin.command("ping")
            self._db = client[db_name]
            self.mode = "mongo"
            log.info("MongoDB connected (%s)", db_name)
        except Exception as e:
            log.warning("MongoDB unavailable (%s) — using in-memory store.", e)

    # ---- requests ----
    def store_request(self, doc: dict) -> str:
        doc = dict(doc)
        doc.setdefault("request_id", uuid.uuid4().hex[:12])
        doc.setdefault("timestamp", _now())
        with self._lock:
            if self._db is not None:
                try:
                    self._db.requests.insert_one(dict(doc))
                except Exception as e:
                    log.warning("mongo insert failed, keeping memory copy: %s", e)
                    self._mem_requests.append(doc)
            else:
                self._mem_requests.append(doc)
                self._mem_requests = self._mem_requests[-5000:]
        return doc["request_id"]

    def get_request(self, request_id: str) -> dict | None:
        with self._lock:
            if self._db is not None:
                try:
                    d = self._db.requests.find_one({"request_id": request_id},
                                                   {"_id": 0})
                    if d:
                        return d
                except Exception:
                    pass
            return next((d for d in self._mem_requests
                         if d.get("request_id") == request_id), None)

    def _all_requests(self, limit: int = 5000) -> list[dict]:
        if self._db is not None:
            try:
                docs = list(self._db.requests.find({}, {"_id": 0})
                            .sort("timestamp", -1).limit(limit))
                if docs:
                    return docs
            except Exception:
                pass
        return list(self._mem_requests[-limit:])

    def analytics(self) -> dict:
        docs = self._all_requests()
        n = len(docs)
        if n == 0:
            return {"total_requests": 0, "mode": self.mode}
        cost = sum(d.get("actual_cost_usd", 0) or 0 for d in docs)
        base = sum(d.get("baseline_cost_usd", 0) or 0 for d in docs)
        quals = [d["quality_score"] for d in docs
                 if isinstance(d.get("quality_score"), (int, float))]
        cache_hits = sum(1 for d in docs if d.get("cache_hit"))
        esc = sum(1 for d in docs if d.get("escalated"))
        savings = base - cost
        # Net savings = savings minus control-plane overhead (honest).
        cp_cost = sum(d.get("control_plane_cost_usd", 0) or 0 for d in docs)
        net_savings = savings - cp_cost
        direction = ("savings" if savings > 1e-9
                     else "loss" if savings < -1e-9
                     else "breakeven")
        net_direction = ("savings" if net_savings > 1e-9
                         else "loss" if net_savings < -1e-9
                         else "breakeven")
        return {
            "total_requests": n,
            "total_cost_usd": round(cost, 6),
            "baseline_cost_usd": round(base, 6),
            "savings_usd": round(savings, 6),
            "savings_pct": round(100.0 * savings / base, 2) if base > 0 else 0.0,
            "savings_direction": direction,
            "control_plane_cost_usd": round(cp_cost, 6),
            "net_savings_usd": round(net_savings, 6),
            "net_savings_pct": round(100.0 * net_savings / base, 2) if base > 0 else 0.0,
            "net_savings_direction": net_direction,
            "avg_quality": round(sum(quals) / len(quals), 3) if quals else None,
            "cache_hit_rate": round(cache_hits / n, 3),
            "escalation_rate": round(esc / n, 3),
            "mode": self.mode,
        }

    def routing_stats(self) -> dict:
        docs = self._all_requests()
        by_model: dict[str, int] = {}
        by_task: dict[str, dict[str, int]] = {}
        for d in docs:
            m = d.get("final_model") or d.get("selected_model") or "unknown"
            by_model[m] = by_model.get(m, 0) + 1
            t = d.get("task_type") or "unknown"
            by_task.setdefault(t, {}).update(
                {m: by_task.get(t, {}).get(m, 0) + 1})
        return {"total_requests": len(docs), "by_model": by_model,
                "by_task_model": by_task, "mode": self.mode}

    # ---- benchmarks ----
    def store_benchmark(self, doc: dict) -> str:
        doc = dict(doc)
        doc.setdefault("benchmark_id", uuid.uuid4().hex[:12])
        doc.setdefault("timestamp", _now())
        with self._lock:
            if self._db is not None:
                try:
                    self._db.benchmarks.insert_one(dict(doc))
                except Exception as e:
                    log.warning("mongo benchmark insert failed: %s", e)
                    self._mem_benchmarks.append(doc)
            else:
                self._mem_benchmarks.append(doc)
        return doc["benchmark_id"]

    def latest_benchmark(self) -> dict | None:
        with self._lock:
            if self._db is not None:
                try:
                    d = self._db.benchmarks.find_one({}, {"_id": 0},
                                                     sort=[("timestamp", -1)])
                    if d:
                        return d
                except Exception:
                    pass
            return self._mem_benchmarks[-1] if self._mem_benchmarks else None


_store: RequestStore | None = None


def get_store() -> RequestStore:
    global _store
    if _store is None:
        from backend.config import DATABASE_NAME, MONGODB_URI
        _store = RequestStore(MONGODB_URI, DATABASE_NAME)
    return _store
