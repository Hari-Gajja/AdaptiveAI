"""Phase-5 tests: cache + cost/baseline (no API key needed).

Run from llm-cost-optimizer/:  python backend\\test_cache_cost.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Hermetic routing: ignore the live profiles.json written by the profiler.
os.environ["LLMO_PROFILES_FILE"] = str(
    Path(tempfile.gettempdir()) / "llmo-test-empty-profiles.json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.core.optimizer as optmod
import backend.core.registry as regmod
from backend.core.cache import PromptCache, context_key, exact_key
from backend.core.cost import baseline_model, cost_summary
from backend.providers.opencode import GenerateResult

failures = 0


def check(name: str, cond: bool, extra: str = ""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


def fake(text: str):
    def _fake(model_id, messages, max_tokens=512, temperature=0.2):
        return GenerateResult(text=text, model_id=model_id, endpoint="fake",
                              endpoint_family="chat_completions", input_tokens=100,
                              output_tokens=50, cached_tokens=0, latency_ms=5, raw_usage={})
    return _fake


GOOD = ("An API (Application Programming Interface) is a contract letting two "
        "programs communicate through defined requests and responses.")
REF = ("An API is an Application Programming Interface: a contract that lets "
       "two software programs communicate via defined requests and responses.")


def main() -> int:
    # ---- key stability ----
    check("exact key whitespace/case-insensitive",
          exact_key("  Hello   World ") == exact_key("hello world"))
    check("context key differs from exact", context_key("x") != exact_key("x"))

    # ---- cache unit ----
    c = PromptCache(max_entries=2)
    check("miss returns None", c.get_exact("nope") is None)
    c.note_miss()
    from backend.core.cache import CacheEntry
    c.put(CacheEntry(prompt="Hi", context="", answer="Hello!", model_id="m",
                     input_tokens=10, output_tokens=5, cost_usd=0.001, context_tokens=0))
    check("contains after put", c.contains("hi"))
    c.note_exact_hit(c.get_exact("HI"))
    s = c.statistics()
    check("stats hit_rate", s["exact_hits"] == 1 and s["misses"] == 1, str(s))
    check("stats measured savings", s["exact_saved_measured_usd"] == 0.001, str(s))
    c.put(CacheEntry(prompt="a", context="", answer="a", model_id="m",
                     input_tokens=1, output_tokens=1, cost_usd=0.0, context_tokens=0))
    c.put(CacheEntry(prompt="b", context="", answer="b", model_id="m",
                     input_tokens=1, output_tokens=1, cost_usd=0.0, context_tokens=0))
    check("LRU eviction", c.get_exact("Hi") is None and c.contains("b"))
    cleared = c.clear()
    check("clear resets", cleared["cleared_entries"] >= 0 and c.statistics()["requests"] == 0)

    # ---- cost engine ----
    with tempfile.TemporaryDirectory() as td:
        reg = regmod.reset_registry_for_tests(Path(td) / "models.json")
        base = baseline_model(reg.enabled())
        check("baseline is pro (priciest)", base.model_id == "deepseek-v4-pro", base.model_id)
        s = cost_summary(0.001, base, 1000, 500)
        exp_base = 1000 / 1e6 * 0.66 + 500 / 1e6 * 1.98
        check("baseline math", abs(s["baseline_cost_usd"] - round(exp_base, 6)) < 1e-9, str(s))
        check("savings math", abs(s["savings_usd"] - round(exp_base - 0.001, 6)) < 1e-9)
        check("savings pct range", 0.0 <= s["savings_pct"] <= 100.0)

        # ---- optimizer: exact hit skips LLM ----
        import backend.core.cache as cachemod
        cachemod._cache = PromptCache()
        optmod.get_registry = lambda: regmod.reset_registry_for_tests(Path(td) / "models.json")
        calls = {"n": 0}

        def counting(model_id, messages, max_tokens=512, temperature=0.2):
            calls["n"] += 1
            return fake(GOOD)(model_id, messages, max_tokens, temperature)

        from backend.core.quality import evaluate
        r1 = optmod.run_prompt("What is an API?", reference=REF, _generate=counting,
                               _evaluate=evaluate)
        check("first call misses, generates", calls["n"] == 1 and r1.cache_kind == "miss")
        r2 = optmod.run_prompt("What is an API?", reference=REF, _generate=counting,
                               _evaluate=evaluate)
        check("repeat exact-hits, no LLM", calls["n"] == 1 and r2.cache_kind == "exact"
              and r2.cache_hit is True, f"calls={calls['n']} kind={r2.cache_kind}")
        check("exact savings measured", r2.cache_saved_kind == "measured"
              and r2.cache_saved_usd > 0, str(r2.cache_saved_usd))
        check("exact actual cost zero", r2.total_cost_usd == 0.0)
        check("exact baseline reported", r2.baseline_model == "deepseek-v4-pro"
              and r2.baseline_cost_usd > 0)

        # ---- optimizer: context hit, new question ----
        ctx = "ACME return policy: 30 days with receipt. No refunds on opened software."

        def policy_fake(model_id, messages, max_tokens=512, temperature=0.2):
            calls["n"] += 1
            q = messages[-1]["content"].lower()
            if "opened" in q:
                text = ("Opened software is not refundable per the policy; "
                        "unopened items have 30 days with receipt.")
            else:
                text = "You have 30 days with receipt per the ACME return policy."
            return GenerateResult(text=text, model_id=model_id, endpoint="fake",
                                  endpoint_family="chat_completions", input_tokens=200,
                                  output_tokens=40, cached_tokens=0, latency_ms=5,
                                  raw_usage={})

        r3 = optmod.run_prompt("How many days do I have?", context=ctx,
                               _generate=policy_fake, _evaluate=evaluate)
        check("first context use is miss-kind", r3.cache_kind == "miss", r3.cache_kind)
        r4 = optmod.run_prompt("What about opened software?", context=ctx,
                               _generate=policy_fake, _evaluate=evaluate)
        check("same context new q = context hit", r4.cache_kind == "context"
              and r4.cache_hit is True, r4.cache_kind)
        check("context savings estimated", r4.cache_saved_kind == "estimated"
              and r4.tokens_avoided > 0)
        check("context still generates", len(r4.attempts) == 1)

        # ---- use_cache=False bypasses ----
        n_before = calls["n"]
        r5 = optmod.run_prompt("What is an API?", reference=REF, use_cache=False,
                               _generate=counting, _evaluate=evaluate)
        check("cache bypass generates", calls["n"] == n_before + 1
              and r5.cache_kind == "miss")

        # ---- baseline on normal path ----
        check("normal path savings fields", r1.savings_usd >= 0
              and r1.baseline_model == "deepseek-v4-pro", str((r1.savings_usd, r1.baseline_cost_usd)))

    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
