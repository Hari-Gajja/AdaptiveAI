"""Phase-6b tests: benchmark runner math on fakes (no API key).

Run from llm-cost-optimizer/:  python backend\\test_benchmark.py
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
from backend.benchmark.runner import _sample_indices, load_queries, run_benchmark
from backend.core.optimizer import OptimizerResult
from backend.core.quality import QualityScore
from backend.core.router import route
from backend.core.task_analyzer import analyze
from backend.providers.opencode import GenerateResult

failures = 0


def check(name: str, cond: bool, extra: str = ""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


MINI = [
    {"id": 1, "category": "easy", "prompt": "What is an API?",
     "reference_answer": "An API lets two programs communicate via defined requests."},
    {"id": 2, "category": "easy", "prompt": "What is JSON?",
     "reference_answer": "JSON is a text format for structured data."},
    {"id": 3, "category": "repeated_context", "prompt": "How many days?",
     "context": "Policy: 30 days with receipt.",
     "reference_answer": "You have 30 days with receipt."},
    {"id": 4, "category": "repeated_context", "prompt": "Need a receipt?",
     "context": "Policy: 30 days with receipt.",
     "reference_answer": "Yes, a receipt is required."},
]


def fake_run(prompt, reference=None, context=None, max_tokens=256, use_cache=True):
    reg = regmod.reset_registry_for_tests(Path(tempfile.gettempdir()) / "bench-reg.json")
    a = analyze(prompt, context)
    d = route(a, reg.enabled())
    # pretend flash answered well on easy, pro on the rest
    mid = "deepseek-v4-flash" if "API" in prompt or "JSON" in prompt else "deepseek-v4-pro"
    entry = next(m for m in reg.enabled() if m.model_id == mid)
    from backend.core.cost import actual_cost_usd
    from backend.core.quality import evaluate
    ans = reference  # perfect answer
    q = evaluate(ans, prompt, reference)
    cost = round(actual_cost_usd(entry, 100, 50), 6)
    att = optmod.Attempt(model_id=mid, answer=ans, input_tokens=100, output_tokens=50,
                         cached_tokens=0, latency_ms=10, cost_usd=cost, quality=q, passed=True)
    res = OptimizerResult(answer=ans, analysis=a, routing=d, attempts=[att],
                          total_cost_usd=cost, total_latency_ms=10,
                          cache_hit=bool(context and "receipt" in (prompt or "")),
                          cache_kind="context" if context and "receipt" in prompt else "miss")
    return res


def fake_gen(model_id, messages, max_tokens=256, temperature=0.1):
    return GenerateResult(text="baseline says hi", model_id=model_id, endpoint="fake",
                          endpoint_family="chat_completions", input_tokens=10,
                          output_tokens=10, cached_tokens=0, latency_ms=5, raw_usage={})


def main() -> int:
    qs = load_queries()
    check("50 benchmark queries", len(qs) == 50, str(len(qs)))
    check("has repeated_context", sum(1 for q in qs if q.get("category") == "repeated_context") >= 5)
    check("all have references", all(q.get("reference_answer") for q in qs))
    check("sample spread", _sample_indices(50, 5) == sorted(_sample_indices(50, 5))
          and len(_sample_indices(50, 5)) == 5)

    with tempfile.TemporaryDirectory() as td:
        regmod.reset_registry_for_tests(Path(td) / "models.json")
        optmod.get_registry = lambda: regmod.reset_registry_for_tests(Path(td) / "models.json")
        import backend.core.cache as cachemod
        from backend.core.cache import PromptCache
        cachemod._cache = PromptCache()

        out = run_benchmark(MINI, baseline_sample_n=2, _run=fake_run, _generate=fake_gen)
        check("counts", out["queries_tested"] == 4, str(out["queries_tested"]))
        check("savings non-negative", out["savings"] >= 0, str(out["savings"]))
        check("savings pct sane", 0.0 <= out["savings_pct"] <= 100.0)
        check("quality high (perfect fakes)", out["optimizer_quality"] >= 0.9,
              str(out["optimizer_quality"]))
        check("baseline quality measured", out["baseline_quality"] is not None
              and "n=2" in out["baseline_quality_method"], str(out["baseline_quality_method"]))
        check("retention computed", out["quality_retention"] is not None)
        check("distribution sums", sum(out["model_distribution"].values()) == 4,
              str(out["model_distribution"]))
        check("methods labeled", out["baseline_cost_method"].startswith("counterfactual"))
        check("per-query rows", len(out["per_query"]) == 4)

    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
