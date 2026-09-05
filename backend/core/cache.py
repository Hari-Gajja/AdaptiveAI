"""Prompt/context cache — Phase 7. Redis-backed, 3-tier pipeline.

Backend: real Redis when REDIS_URL is reachable; otherwise a redis-compatible
in-memory fallback (fakeredis) with the SAME API — one code path, the demo
never breaks, and swapping to real Redis is a URL change.

Pipeline (per request):
  1. normalize + fingerprint (SHA-256 over normalized text)
  2. EXACT tier   — same normalized prompt(+context) -> stored answer, no LLM.
     Savings MEASURED (the stored call's actual cost).
  3. SEMANTIC tier — similar prompt (cosine >= threshold) that passes ALL
     safety gates -> stored answer, no LLM. Savings MEASURED, labeled
     "semantic" so the UI can show the gate story.
  4. CONTEXT tier — same reusable context, new question -> LLM still called,
     context tokens counted as avoided. Savings ESTIMATED.

Counters live in Redis (HINCRBY) so stats survive restarts and are shared
across workers. Entries are JSON; semantic tier is LRU via a zset.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, asdict

from backend.config import settings
from backend.core.semantic import (Features, GateResult, canonicalize,
                                   check_gates, extract_features, similarity)

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _key(prefix: str, text: str) -> str:
    return prefix + hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def exact_key(prompt: str, context: str | None = None) -> str:
    """Key an answer by both prompt and context when context is supplied."""
    normalized_context = normalize(context or "")
    return _key("exact:", f"{normalize(prompt)}\ncontext:{normalized_context}")


def context_key(context: str) -> str:
    return _key("ctx:", context)


@dataclass
class CacheEntry:
    prompt: str
    context: str
    answer: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    context_tokens: int


# Redis key layout (prefix llmo:cache:):
#   llmo:cache:exact:<sha>  -> JSON CacheEntry
#   llmo:cache:ctx:<sha>    -> JSON CacheEntry
#   llmo:cache:sem:<sha>    -> JSON {entry, prompt}
#   llmo:cache:semidx       -> zset member=sem key, score=last-use time (LRU)
#   llmo:cache:stats        -> hash counters; llmo:cache:stats:f:<name> floats
_PREFIX = "llmo:cache:"
_EXACT = _PREFIX + "exact:"
_CTX = _PREFIX + "ctx:"
_SEM = _PREFIX + "sem:"
_SEM_IDX = _PREFIX + "semidx"
_EXACT_IDX = _PREFIX + "exactidx"
_CTX_IDX = _PREFIX + "ctxidx"
_STATS = _PREFIX + "stats"


class _RedisCompat:
    """Minimal redis-compatible facade over real redis or fakeredis.

    Chooses real Redis when REDIS_URL answers ping; falls back to fakeredis
    (same API, in-memory) so the app works with zero infrastructure.
    """

    def __init__(self) -> None:
        self.backend = "memory"
        self._r = None
        try:
            import redis
            r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.4,
                                     socket_timeout=1.0, decode_responses=True)
            r.ping()
            self._r = _r = r
            self.backend = "redis"
        except Exception:
            import fakeredis
            self._r = fakeredis.FakeStrictRedis(decode_responses=True)
            self.backend = "memory"

    # -- thin passthroughs (only what the cache needs) --
    def get(self, k): return self._r.get(k)
    def set(self, k, v): return self._r.set(k, v)
    def delete(self, *ks): return self._r.delete(*ks)
    def hincrby(self, k, f, n=1): return self._r.hincrby(k, f, n)
    def incr(self, k): return self._r.incr(k)
    def hgetall(self, k): return self._r.hgetall(k)
    def zadd(self, k, m): return self._r.zadd(k, m)
    def zrange(self, k, a, b): return self._r.zrange(k, a, b)
    def zrem(self, k, m): return self._r.zrem(k, m)
    def zcard(self, k): return self._r.zcard(k)
    def scan_iter(self, match): return list(self._r.scan_iter(match=match))


_client: _RedisCompat | None = None
_client_lock = threading.Lock()


def get_redis() -> _RedisCompat:
    global _client
    with _client_lock:
        if _client is None:
            _client = _RedisCompat()
        return _client


class PromptCache:
    """3-tier cache on the redis-py API (real Redis or in-memory fallback)."""

    def __init__(self, max_entries: int = 256, semantic_threshold: float | None = None):
        self._max = max(1, max_entries)
        self._sem_threshold = (settings.semantic_threshold
                               if semantic_threshold is None else semantic_threshold)
        self._r = get_redis()
        self._lock = threading.Lock()

    # ---- lookups ----
    def get_exact(self, prompt: str, context: str | None = None) -> CacheEntry | None:
        raw = self._r.get(_EXACT + _sha(exact_key(prompt, context)))
        if raw is None:
            return None
        return CacheEntry(**json.loads(raw))

    def get_context(self, context: str) -> CacheEntry | None:
        if not (context or "").strip():
            return None
        raw = self._r.get(_CTX + _sha(context_key(context)))
        if raw is None:
            return None
        return CacheEntry(**json.loads(raw))

    def contains(self, prompt: str, context: str | None = None) -> bool:
        return self.get_exact(prompt, context) is not None

    def lookup_semantic(self, prompt: str, context: str | None = None
                        ) -> tuple[CacheEntry | None, float, list[str]]:
        """Scan semantic tier: best cosine >= threshold, then safety gates.

        Returns (entry, score, veto_reasons). Entry None when nothing passes
        both stages; veto reasons surface in the decision trace.
        """
        if self._sem_threshold <= 0:
            return None, 0.0, ["semantic tier disabled"]
        q = extract_features(prompt)
        best: tuple[float, str] | None = None
        for key in self._r.zrange(_SEM_IDX, 0, -1):
            raw = self._r.get(key)
            if raw is None:  # evicted — drop from index
                self._r.zrem(_SEM_IDX, key)
                continue
            d = json.loads(raw)
            # Compare on canonicalized text: "15 percent" vs "15%" must score
            # as the same prompt, not as different operators.
            score = similarity(canonicalize(prompt), canonicalize(d["prompt"]))
            if score >= self._sem_threshold and (best is None or score > best[0]):
                best = (score, key)
        if best is None:
            return None, 0.0, []
        score, key = best
        d = json.loads(self._r.get(key))
        entry = CacheEntry(**d["entry"])
        gates = check_gates(q, extract_features(d["prompt"]),
                            same_context=normalize(context or "") == normalize(d["entry"]["context"] or ""))
        if not gates.safe:
            return None, score, gates.reasons
        self._r.zadd(_SEM_IDX, {key: time.time()})  # LRU refresh
        return entry, score, []

    # ---- writes ----
    def put(self, entry: CacheEntry, semantic: bool = True) -> None:
        """Store in exact + context tiers; index for semantic reuse."""
        ek = _EXACT + _sha(exact_key(entry.prompt, entry.context))
        self._r.set(ek, json.dumps(asdict(entry)))
        self._r.zadd(_EXACT_IDX, {ek: self._next_seq()})
        self._evict(_EXACT_IDX)
        if entry.context.strip():
            ck = _CTX + _sha(context_key(entry.context))
            self._r.set(ck, json.dumps(asdict(entry)))
            self._r.zadd(_CTX_IDX, {ck: self._next_seq()})
            self._evict(_CTX_IDX)
        if semantic:
            k = _SEM + hashlib.sha256(normalize(entry.prompt).encode()).hexdigest()
            self._r.set(k, json.dumps({"entry": asdict(entry), "prompt": entry.prompt}))
            self._r.zadd(_SEM_IDX, {k: self._next_seq()})
            self._evict(_SEM_IDX)

    def _evict(self, idx: str) -> None:
        while self._r.zcard(idx) > self._max:
            oldest = self._r.zrange(idx, 0, 0)
            if not oldest:
                break
            self._r.zrem(idx, oldest[0])
            self._r.delete(oldest[0])

    def _next_seq(self) -> float:
        """Monotonic LRU sequence — time.time() has ~15ms resolution on
        Windows, so three puts in one test can share a score and evict the
        wrong entry. Redis INCR guarantees strict ordering."""
        return float(self._r.incr(_PREFIX + "seq"))

    def _evict_semantic(self) -> None:
        while self._r.zcard(_SEM_IDX) > self._max:
            oldest = self._r.zrange(_SEM_IDX, 0, 0)
            if not oldest:
                break
            self._r.zrem(_SEM_IDX, oldest[0])
            self._r.delete(oldest[0])

    # ---- accounting (optimizer owns pricing) ----
    def _bump(self, field: str, n: int = 1) -> None:
        self._r.hincrby(_STATS, field, n)

    def _add_float(self, field: str, amount: float) -> None:
        fk = _STATS + ":f:" + field
        with self._lock:
            cur = float(self._r.get(fk) or 0)
            self._r.set(fk, repr(round(cur + amount, 6)))

    def note_exact_hit(self, entry: CacheEntry) -> None:
        self._bump("exact_hits")
        self._bump("tokens_avoided", entry.input_tokens + entry.output_tokens)
        if entry.cost_usd is not None:
            self._add_float("exact_saved_measured_usd", entry.cost_usd)

    def note_semantic_hit(self, entry: CacheEntry, score: float) -> None:
        self._bump("semantic_hits")
        self._bump("tokens_avoided", entry.input_tokens + entry.output_tokens)
        if entry.cost_usd is not None:
            self._add_float("semantic_saved_measured_usd", entry.cost_usd)

    def note_context_hit(self, context_tokens: int, input_price_per_1M: float) -> None:
        self._bump("context_hits")
        self._bump("tokens_avoided", context_tokens)
        self._add_float("context_saved_estimated_usd",
                        context_tokens / 1_000_000 * input_price_per_1M)

    def note_miss(self) -> None:
        self._bump("misses")

    def note_semantic_veto(self, reasons: list[str]) -> None:
        self._bump("semantic_vetoes")

    # ---- admin ----
    def clear(self) -> dict:
        keys = list(self._r.scan_iter(_PREFIX + "*"))
        n = len(keys)
        if keys:
            self._r.delete(*keys)
        return {"cleared_entries": n}

    def statistics(self) -> dict:
        h = self._r.hgetall(_STATS)
        f = {name: float(self._r.get(_STATS + ":f:" + name) or 0) for name in
             ("exact_saved_measured_usd", "semantic_saved_measured_usd",
              "context_saved_estimated_usd")}
        exact = int(h.get("exact_hits", 0))
        sem = int(h.get("semantic_hits", 0))
        ctx = int(h.get("context_hits", 0))
        miss = int(h.get("misses", 0))
        total = exact + sem + ctx + miss
        return {
            "backend": self._r.backend,
            "exact_hits": exact,
            "semantic_hits": sem,
            "context_hits": ctx,
            "misses": miss,
            "semantic_vetoes": int(h.get("semantic_vetoes", 0)),
            "requests": total,
            "hit_rate": round((exact + sem + ctx) / max(1, total), 3),
            "entries": self._r.zcard(_SEM_IDX),
            "tokens_avoided": int(h.get("tokens_avoided", 0)),
            "exact_saved_measured_usd": round(f["exact_saved_measured_usd"], 6),
            "semantic_saved_measured_usd": round(f["semantic_saved_measured_usd"], 6),
            "context_saved_estimated_usd": round(f["context_saved_estimated_usd"], 6),
            "total_saved_usd": round(f["exact_saved_measured_usd"] + f["semantic_saved_measured_usd"]
                                     + f["context_saved_estimated_usd"], 6),
            "semantic_threshold": self._sem_threshold,
        }


_cache: PromptCache | None = None


def get_cache() -> PromptCache:
    global _cache
    if _cache is None:
        _cache = PromptCache()
    return _cache


def _sha(key: str) -> str:
    """Strip the prefix and keep the hex digest part as the redis suffix."""
    return key.split(":", 1)[1]


def reset_cache_for_tests(max_entries: int = 256, semantic_threshold: float = 0.50) -> PromptCache:
    """Fresh cache on a fresh in-memory backend (tests must not share state)."""
    global _cache, _client
    import fakeredis
    _client = _RedisCompat.__new__(_RedisCompat)
    _client._r = fakeredis.FakeStrictRedis(decode_responses=True)
    _client.backend = "memory"
    _cache = PromptCache(max_entries=max_entries, semantic_threshold=semantic_threshold)
    return _cache
