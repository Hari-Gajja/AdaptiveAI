"""Phase 8 control-plane tests — run with Python 3.10.

Covers: prompt budget, classifier labels/confidence/fallback, evaluator modes,
cache verifier safety (can veto, never approve), ledger cost accounting
(Total = CP + Task Model), and optimizer integration with per-request knobs.

Run from llm-cost-optimizer/:  python backend\\test_control_plane.py
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
from backend.core.cache import PromptCache
from backend.core.quality import evaluate
from backend.llm import config as cp_cfg
from backend.llm import opencode_client as client
from backend.llm.cache_verifier import verify_reuse
from backend.llm.ledger import ControlPlaneLedger
from backend.llm.opencode_classifier import classify_prompt
from backend.llm.opencode_evaluator import evaluate_with_llm
from backend.llm.prompt_budget import (budget_for_classifier,
                                       budget_for_evaluator,
                                       budget_for_verifier)
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


def cp_result(kind: str, parsed: dict, in_tok=30, out_tok=10, lat=80):
    return client.ControlPlaneResult(
        kind=kind, parsed=parsed, input_tokens=in_tok, output_tokens=out_tok,
        latency_ms=lat, model_id=cp_cfg.OPENCODE_MODEL)


def with_registry():
    """Fresh registry + fresh cache."""
    reg = regmod.reset_registry_for_tests(
        Path(tempfile.gettempdir()) / "cp-test-models.json")
    cachemod._cache = PromptCache()
    optmod.get_registry = lambda: regmod.reset_registry_for_tests(
        Path(tempfile.gettempdir()) / "cp-test-models.json")
    return reg


# ---------------------------------------------------------------- prompt budget
def test_prompt_budget():
    bp = budget_for_classifier("hello world")
    check("short prompt not truncated", not bp.truncated and bp.text == "hello world")
    bp2 = budget_for_classifier("x" * 5000)
    check("long prompt truncated with marker",
          bp2.truncated and "…[truncated]" in bp2.text and len(bp2.text) < 5000)
    bp3 = budget_for_classifier("q", context="c" * 3000)
    check("context hint attached", "[context:" in bp3.text)
    check("verifier budget truncates", budget_for_verifier("y" * 9000).truncated)
    check("evaluator budget truncates", budget_for_evaluator("z" * 9000).truncated)


# ---------------------------------------------------------------- classifier
def test_classifier():
    a = classify_prompt("What is 2+2?", force_backend="legacy_ml")
    check("legacy backend flag", a.backend == "legacy_ml" and a.fallback_used)

    with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False):
        a2 = classify_prompt("What is 2+2?")
        check("disabled falls back to legacy",
              a2.backend == "legacy_ml" and "OPENCODE_ENABLED" in (a2.fallback_reason or ""))

    res = cp_result("classifier", {"t": "M", "d": "H", "c": 0.9})
    with mock.patch.object(client, "classify", return_value=res):
        a3 = classify_prompt("Solve this integral")
        check("opencode labels map to task type",
              a3.backend == "opencode" and a3.task_type == "mathematics"
              and a3.difficulty_score == 0.85 and a3.confidence == 0.9)
        check("math capability derived", "math" in a3.required_capabilities)
        check("control_plane view present", a3.control_plane is not None
              and a3.control_plane.get("input_tokens") == 30)

    with mock.patch.object(client, "classify",
                           side_effect=client.ControlPlaneError("boom")):
        a4 = classify_prompt("Write a function")
        check("fallback on control-plane error",
              a4.backend == "legacy_ml" and a4.fallback_used and "boom" in a4.fallback_reason)


# ---------------------------------------------------------------- verifier safety
def test_verifier_safety():
    # verifier says NOT same -> veto (reuse blocked)
    res = cp_result("verifier", {"same": 0, "c": 0.9}, in_tok=40, out_tok=8, lat=90)
    with mock.patch.object(client, "verify_cache", return_value=res):
        out = verify_reuse("What is 2+2?", "The answer is 5")
        check("verifier can veto reuse", not out.verified and not out.skipped)

    res2 = cp_result("verifier", {"same": 1, "c": 0.95}, in_tok=40, out_tok=8, lat=90)
    with mock.patch.object(client, "verify_cache", return_value=res2):
        out2 = verify_reuse("What is 2+2?", "The answer is 4")
        check("verifier can confirm reuse", out2.verified and not out2.skipped)

    with mock.patch.object(client, "verify_cache",
                           side_effect=client.ControlPlaneError("down")):
        out3 = verify_reuse("What is 2+2?", "The answer is 4")
        check("verifier failure fails open to gates", out3.skipped)


# ---------------------------------------------------------------- evaluator
def test_evaluator():
    score, view = evaluate_with_llm("The answer is 30", "What is 15% of 200?")
    check("math not LLM-judged", not view["used"] and view["fallback_used"])

    res = cp_result("evaluator", {"c": 1, "r": 1, "s": 0.85},
                    in_tok=60, out_tok=15, lat=120)
    with mock.patch.object(client, "evaluate_answer", return_value=res):
        score2, view2 = evaluate_with_llm("Paris is the capital of France.",
                                          "What is the capital of France?")
        check("llm judge used for subjective", view2["used"]
              and score2.scoring_detail == "llm_judge" and score2.correctness == 0.9)

    with mock.patch.object(client, "evaluate_answer",
                           side_effect=client.ControlPlaneError("down")):
        score3, view3 = evaluate_with_llm("Some answer", "Explain caching briefly")
        check("evaluator falls back on error",
              not view3["used"] and view3["fallback_used"] and score3.method == "estimated")


# ---------------------------------------------------------------- ledger
def test_ledger():
    led = ControlPlaneLedger(model_id="deepseek-v4-flash", status="active")
    led.record("classifier", 30, 12, 100)
    led.record("cache_verifier", 40, 8, 90)
    led.record("evaluator", 60, 15, 120)
    led.finalize()
    check("ledger token totals", led.total_input_tokens == 130
          and led.total_output_tokens == 35 and led.total_calls == 3)
    check("ledger prices with CP model", led.total_cost_usd > 0)
    v = led.view()
    check("ledger view shape", "classifier" in v["components"]
          and "cost_usd" in v["totals"] and v["status"] == "active")

    led2 = ControlPlaneLedger(model_id="deepseek-v4-flash")
    led2.record("classifier", 30, 12, 100, usage_estimated=False)
    led2.record("classifier", 30, 12, 100, usage_estimated=True)
    check("measured vs estimated split",
          led2.classifier.measured_calls == 1 and led2.classifier.estimated_calls == 1)

    led3 = ControlPlaneLedger(model_id="deepseek-v4-flash")
    led3.finalize()
    check("empty ledger disabled", led3.status == "disabled"
          and led3.total_calls == 0 and led3.total_cost_usd == 0.0)


# ---------------------------------------------------------------- optimizer integration
def test_optimizer_integration():
    with_registry()

    # legacy mode: no CP calls at all
    r1 = optmod.run_prompt("What is an API?", max_tokens=64,
                           classifier_backend="legacy_ml",
                           _generate=fake("An API is a contract between programs."),
                           _evaluate=evaluate)
    check("legacy mode ledger disabled", r1.ledger.status == "disabled"
          and r1.ledger.total_calls == 0)
    check("legacy analysis backend", r1.analysis.backend == "legacy_ml")

    # opencode classifier recorded (fresh cache: r1 stored its answer, and
    # cache-first would otherwise skip the classifier on the exact hit)
    cachemod.reset_cache_for_tests()
    res = cp_result("classifier", {"t": "O", "d": "E", "c": 0.8})
    with mock.patch.object(client, "classify", return_value=res):
        r2 = optmod.run_prompt("What is an API?", max_tokens=64,
                               _generate=fake("An API is a contract between programs."),
                               _evaluate=evaluate)
        check("opencode classifier recorded",
              r2.ledger.status == "active" and r2.ledger.classifier.input_tokens == 30
              and r2.ledger.total_cost_usd > 0)

    # exact hit: cache-first means the classifier is NEVER called (§13) —
    # the free legacy analyzer fills analysis/routing instead.
    with mock.patch.object(client, "classify", return_value=res) as cls_mock:
        r3 = optmod.run_prompt("What is an API?", max_tokens=64,
                               _generate=fake("An API is a contract between programs."),
                               _evaluate=evaluate)
        check("exact hit returns cached", r3.cache_hit and r3.cache_kind == "exact")
        check("exact hit avoids classifier (cache-first)",
              r3.ledger.total_calls == 0 and r3.ledger.classifier.calls == 0
              and cls_mock.assert_not_called() is None)
        check("exact hit avoided-counter incremented",
              r3.ledger.calls_avoided_exact == 1
              and r3.ledger.view()["totals"]["classifier_calls_avoided_exact"] == 1)
        check("exact hit analysis is legacy (honest)",
              r3.analysis.backend == "legacy_ml")

    # quality_check_mode=off: no LLM judge even for subjective
    with mock.patch.object(client, "classify", return_value=res):
        r4 = optmod.run_prompt("Explain caching briefly", max_tokens=64,
                               quality_check_mode="off",
                               _generate=fake("Caching stores results for reuse."),
                               _evaluate=evaluate)
        check("mode off skips LLM judge", r4.quality_evaluator is None)

    # quality_check_mode=live: LLM judge for subjective (no reference)
    eres = cp_result("evaluator", {"c": 1, "r": 1, "s": 0.85},
                     in_tok=60, out_tok=15, lat=120)
    with mock.patch.object(client, "classify", return_value=res), \
         mock.patch.object(client, "evaluate_answer", return_value=eres):
        r5 = optmod.run_prompt("Explain caching briefly", max_tokens=64,
                               quality_check_mode="live",
                               _generate=fake("Caching stores results for reuse."),
                               _evaluate=evaluate)
        check("live mode uses LLM judge", r5.quality_evaluator is not None
              and r5.quality_evaluator.get("used"))
        check("evaluator tokens in ledger", r5.ledger.evaluator.input_tokens == 60)
        check("quality counters", r5.quality_checks == 1 and r5.quality_passes == 1)

    # cache_verify=False disables verifier even when CP active
    with mock.patch.object(client, "classify", return_value=res):
        r6 = optmod.run_prompt("What is 15 percent of 200?", max_tokens=64,
                               cache_verify=False,
                               _generate=fake("30"),
                               _evaluate=evaluate)
        check("cache_verify=False skips verifier", r6.cache_verifier is None)


def main() -> int:
    test_prompt_budget()
    test_classifier()
    test_verifier_safety()
    test_evaluator()
    test_ledger()
    test_optimizer_integration()
    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
