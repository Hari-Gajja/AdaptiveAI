"""Prompt/context cache — Phase 5. In-memory MVP (no Redis, per spec).

Two hit kinds, tracked separately because their savings claims differ:
  exact    same normalized prompt seen before -> full cached answer, NO LLM
           call. Saved cost is MEASURED (the stored call's actual cost).
  context  same reusable context (docs/policy) with a NEW question -> LLM
           still called, but context tokens counted as avoided. Saved cost is
           ESTIMATED (context tokens x input price) and labeled as such.

Key = sha256 over normalized (whitespace-collapsed, lowercased) text.
"""
from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _key(prefix: str, text: str) -> str:
    return prefix + hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def exact_key(prompt: str) -> str:
    return _key("exact:", prompt)


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
    cost_usd: float
    context_tokens: int


class PromptCache:
    def __init__(self, max_entries: int = 256):
        self._lock = threading.Lock()
        self._exact: OrderedDict[str, CacheEntry] = OrderedDict()
        self._ctx: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max = max(1, max_entries)
        self.exact_hits = 0
        self.context_hits = 0
        self.misses = 0
        self.tokens_avoided = 0
        self.exact_saved_measured = 0.0
        self.context_saved_estimated = 0.0

    # ---- lookups ----
    def get_exact(self, prompt: str) -> CacheEntry | None:
        with self._lock:
            e = self._exact.get(exact_key(prompt))
            if e is not None:
                self._exact.move_to_end(exact_key(prompt))
            return e

    def get_context(self, context: str) -> CacheEntry | None:
        if not (context or "").strip():
            return None
        with self._lock:
            e = self._ctx.get(context_key(context))
            if e is not None:
                self._ctx.move_to_end(context_key(context))
            return e

    def contains(self, prompt: str) -> bool:
        return self.get_exact(prompt) is not None

    # ---- writes ----
    def put(self, entry: CacheEntry) -> None:
        with self._lock:
            self._exact[exact_key(entry.prompt)] = entry
            while len(self._exact) > self._max:
                self._exact.popitem(last=False)
            if entry.context.strip():
                self._ctx[context_key(entry.context)] = entry
                while len(self._ctx) > self._max:
                    self._ctx.popitem(last=False)

    # ---- accounting (called by the optimizer, which owns pricing) ----
    def note_exact_hit(self, entry: CacheEntry) -> None:
        with self._lock:
            self.exact_hits += 1
            self.tokens_avoided += entry.input_tokens + entry.output_tokens
            self.exact_saved_measured += entry.cost_usd

    def note_context_hit(self, context_tokens: int, input_price_per_1M: float) -> None:
        with self._lock:
            self.context_hits += 1
            self.tokens_avoided += context_tokens
            self.context_saved_estimated += context_tokens / 1_000_000 * input_price_per_1M

    def note_miss(self) -> None:
        with self._lock:
            self.misses += 1

    def clear(self) -> dict:
        with self._lock:
            n = len(self._exact) + len(self._ctx)
            self._exact.clear()
            self._ctx.clear()
            self.exact_hits = self.context_hits = self.misses = 0
            self.tokens_avoided = 0
            self.exact_saved_measured = self.context_saved_estimated = 0.0
            return {"cleared_entries": n}

    def statistics(self) -> dict:
        with self._lock:
            total = self.exact_hits + self.context_hits + self.misses
            return {
                "exact_hits": self.exact_hits,
                "context_hits": self.context_hits,
                "misses": self.misses,
                "requests": total,
                "hit_rate": round((self.exact_hits + self.context_hits) / max(1, total), 3),
                "entries": len(self._exact) + len(self._ctx),
                "tokens_avoided": self.tokens_avoided,
                "exact_saved_measured_usd": round(self.exact_saved_measured, 6),
                "context_saved_estimated_usd": round(self.context_saved_estimated, 6),
                "total_saved_usd": round(self.exact_saved_measured + self.context_saved_estimated, 6),
            }


_cache: PromptCache | None = None


def get_cache() -> PromptCache:
    global _cache
    if _cache is None:
        _cache = PromptCache()
    return _cache
