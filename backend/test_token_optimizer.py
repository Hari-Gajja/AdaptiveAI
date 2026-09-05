"""Token optimization tests (spec §3/§13/§19/§21) — run with Python 3.10.

Covers: prompt normalization (fence-preserving), token estimation, output
budget prediction, cache-first classifier avoidance (exact + semantic),
router context-window guard, and token report aggregation.

Run from llm-cost-optimizer/:  python backend\\test_token_optimizer.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Hermetic routing: ignore the live profiles.json written by the profiler.
os.environ["LLMO_PROFILES_FILE"] = str(
    Path(tempfile.gettempdir()) / "llmo-test-empty-profiles.json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.core.cache as cachemod
import backend.core.optimizer as optmod
import backend.core.registry as regmod
from backend.core.cache import reset_cache_for_tests
from backend.core.quality import evaluate
from backend.core.token_optimizer import (estimate_tokens, normalize_prompt,
                                          predict_output_budget)
from backend.core.token_analytics import aggregate_token_reports, build_token_report
from backend.llm import opencode_client as client
from backend.providers.opencode import GenerateResult

failures = 0


def check(name: str, cond: bool, extra: str = ""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


def fake(text: str, usage: dict | None = None):
    u = usage or {"input_tokens": 100, "output_tokens": 50}

    def _fake(model_id, messages, max_tokens=512, temperature=0.2):
        return GenerateResult(text=text, model_id=model_id, endpoint="fake",
                              endpoint_family="chat_completions",
                              input_tokens=u["input_tokens"],
                              output_tokens=u["output_tokens"],
                              cached_tokens=0, latency_ms=5, raw_usage={})
    return _fake


def with_registry():
    reg = regmod.reset_registry_for_tests(
        Path(tempfile.gettempdir()) / "tok-test-models.json")
    optmod.get_registry = lambda: regmod.reset_registry_for_tests(
        Path(tempfile.gettempdir()) / "tok-test-models.json")
    return reg


# ---------------------------------------------------------------- normalization
def test_normalization():
    n = normalize_prompt("Hello   world\n\n\n\nHow are you?")
    check("normalization collapses whitespace",
          n.view()["normalized_chars"] < n.view()["original_chars"])
    check("normalization reports tokens saved",
          n.view()["tokens_saved"] >= 0)

    n2 = normalize_prompt("Run this:\n```python\nx = 1\n\n\ny = 2\n```")
    check("code fences preserved", "```python" in n2.text and "x = 1" in n2.text)

    n3 = normalize_prompt("short")
    check("short prompt unchanged", n3.view()["tokens_saved"] == 0)

    long_prompt = ("Please summarize the following text. " * 40)
    n4 = normalize_prompt(long_prompt)
    check("long prompt compresses", n4.view()["tokens_saved"] > 0)
    check("compression ratio sane",
          0 < n4.view()["compression_ratio"] <= 1.0)


def test_estimate_and_budget():
    check("estimate_tokens chars/4", estimate_tokens("a" * 40) == 10)
    b, sig = predict_output_budget("What is 2+2?")
    check("short factual -> small budget", b == 128, str(b))
    check("budget signals present", isinstance(sig, list))
    b2, _ = predict_output_budget(
        "Write a complete essay about the history of computing, "
        "with multiple sections, examples, and a detailed conclusion.")
    check("long generative -> larger budget", b2 >= 256, str(b2))


# ---------------------------------------------------------------- cache-first
def test_cache_first_exact_avoids_classifier():
    reg = with_registry()
    reset_cache_for_tests()
    r1 = optmod.run_prompt("What is an API?", max_tokens=64, _generate=fake("An API is a contract between programs."))
    check("r1 generates", r1.cache_kind == "miss")
    # Exact cache hit must avoid the classifier entirely.
    with mock.patch.object(client, "classify",
                           side_effect=AssertionError("classifier must not run on exact hit")):
        r2 = optmod.run_prompt("What is an API?", max_tokens=64, _generate=fake("An API is a contract between programs."))
    check("exact hit avoids classifier (cache-first)", r2.cache_kind == "exact")
    check("avoided counter incremented", r2.ledger.calls_avoided_exact == 1)
    check("hit analysis is legacy (honest)", r2.analysis.backend == "legacy_ml")
    check("hit still routes", r2.routing.selected_model != "")
    check("token report present on hit", r2.token_report is not None
          and r2.token_report["avoided"]["cache_kind"] == "exact")


def test_cache_first_semantic_avoids_classifier():
    reg = with_registry()
    reset_cache_for_tests()
    r1 = optmod.run_prompt("What is 15 percent of 200?", max_tokens=64,
                           reference="30", threshold=0.5,
                           _generate=fake("30"), _evaluate=evaluate)
    check("s1 generates", r1.cache_kind == "miss")
    with mock.patch.object(client, "classify",
                           side_effect=AssertionError("classifier must not run on semantic hit")):
        r2 = optmod.run_prompt("Calculate 15% of 200", max_tokens=64,
                               reference="30", threshold=0.5,
                               _generate=fake("30"), _evaluate=evaluate)
    check("semantic hit avoids classifier", r2.cache_kind == "semantic")
    check("semantic avoided counter", r2.ledger.calls_avoided_semantic == 1)


def test_token_report_shape():
    reg = with_registry()
    reset_cache_for_tests()
    r = optmod.run_prompt("What is 2+2?", max_tokens=64, _generate=fake("ok answer"))
    tr = r.token_report
    check("token report sections", tr is not None and
          {"task_model", "control_plane", "avoided", "totals"} <= set(tr.keys()))
    check("report totals cost", tr["totals"]["total_cost_usd"] >= 0)
    agg = aggregate_token_reports([tr, tr])
    check("aggregate doubles totals",
          agg["totals"]["total_cost_usd"] == 2 * tr["totals"]["total_cost_usd"])


def test_output_budget_wiring():
    reg = with_registry()
    reset_cache_for_tests()
    gen = fake("ok")
    r = optmod.run_prompt("What is 2+2?", max_tokens=None, _generate=gen)
    check("auto budget applied", r.estimated_output_tokens in (128, 256, 512),
          str(r.estimated_output_tokens))
    check("budget signals recorded", len(r.output_budget_signals) > 0)


def test_router_context_window():
    from backend.core.router import route
    from backend.core.task_analyzer import analyze
    reg = with_registry()
    # 4M chars -> ~1M estimated input tokens; both test models have 1M
    # windows, so prompt + expected answer must exceed at least one window.
    a = analyze("x" * 4_000_000)
    d = route(a, reg.enabled())
    check("context limit flagged for huge prompt", d.context_limit_triggered)
    check("rejected model has gap note",
          any("context window" in g for c in d.candidates for g in c.gaps))


def test_normalization_in_result():
    reg = with_registry()
    reset_cache_for_tests()
    r = optmod.run_prompt("What   is   2+2?", max_tokens=64, _generate=fake("ok"))
    check("normalization view attached", r.normalization is not None
          and "original_chars" in r.normalization)


def main():
    test_normalization()
    test_estimate_and_budget()
    test_cache_first_exact_avoids_classifier()
    test_cache_first_semantic_avoids_classifier()
    test_token_report_shape()
    test_output_budget_wiring()
    test_router_context_window()
    test_normalization_in_result()
    print(f"RESULT: {'PASS' if failures == 0 else 'FAIL'} ({failures} failures)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
