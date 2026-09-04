"""Phase-4 tests: quality scoring + escalation (no API key needed).

Run from llm-cost-optimizer/:  python backend\\test_quality.py
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

import backend.core.registry as regmod
from backend.core.optimizer import escalation_order, run_prompt
from backend.core.quality import QualityScore, evaluate
from backend.core.router import route
from backend.core.task_analyzer import analyze
from backend.providers.opencode import GenerateResult

failures = 0


def check(name: str, cond: bool, extra: str = ""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


REF = ("An API is an Application Programming Interface: a contract that lets "
       "two software programs communicate via defined requests and responses.")
GOOD = ("An API (Application Programming Interface) is a contract letting two "
        "programs communicate through defined requests and responses.")
BAD = "The moon is made of cheese and tastes delicious with crackers."


def fake_generate_factory(texts: dict[str, str]):
    def _fake(model_id, messages, max_tokens=512, temperature=0.2):
        return GenerateResult(text=texts[model_id], model_id=model_id,
                              endpoint="fake", endpoint_family="chat_completions",
                              input_tokens=100, output_tokens=50,
                              cached_tokens=0, latency_ms=10, raw_usage={})
    return _fake


def main() -> int:
    # ---- reference-based quality ----
    q = evaluate(GOOD, "What is an API?", REF)
    check("reference method labeled", q.method == "reference", q.method)
    check("good answer scores high", q.overall >= 0.6, str(q))
    q2 = evaluate(BAD, "What is an API?", REF)
    check("bad answer scores low", q2.overall < 0.4, str(q2))
    check("formula weights", abs(q.overall - round(0.5 * q.correctness + 0.3 * q.relevance + 0.2 * q.completeness, 3)) < 1e-9)
    q3 = evaluate("", "What is an API?", REF)
    check("empty answer zero", q3.overall == 0.0)

    # ---- estimated quality ----
    e = evaluate(GOOD, "What is an API?")
    check("estimated method labeled", e.method == "estimated", e.method)
    check("estimated good passes-ish", e.overall >= 0.5, str(e))
    er = evaluate("I'm sorry, but I can't help with that.", "What is an API?")
    check("refusal scored low", er.overall < 0.4, str(er))
    check("refusal still estimated", er.method == "estimated")

    with tempfile.TemporaryDirectory() as td:
        regmod.reset_registry_for_tests(Path(td) / "models.json")
        import backend.core.optimizer as optmod
        optmod.get_registry = lambda: regmod.reset_registry_for_tests(Path(td) / "models.json")
        # NOTE: run_prompt calls get_registry() from optimizer's namespace;
        # monkey-patch there (done above). Registry seeds flash+pro.

        # ---- escalation: flash garbage -> pro good ----
        res = run_prompt(
            "What is an API?", reference=REF, max_attempts=2, use_cache=False,
            _generate=fake_generate_factory(
                {"deepseek-v4-flash": BAD, "deepseek-v4-pro": GOOD}),
            _evaluate=evaluate)
        check("escalation triggered", res.escalated is True)
        check("initial flash", res.initial_model == "deepseek-v4-flash", res.initial_model)
        check("final pro", res.final_model == "deepseek-v4-pro", res.final_model)
        check("two attempts", len(res.attempts) == 2)
        check("cost sums attempts", abs(res.total_cost_usd - round(sum(a.cost_usd for a in res.attempts), 6)) < 1e-9)
        check("final answer good", res.answer == GOOD)

        # ---- no escalation when first passes ----
        res2 = run_prompt(
            "What is an API?", reference=REF, max_attempts=2, use_cache=False,
            _generate=fake_generate_factory(
                {"deepseek-v4-flash": GOOD, "deepseek-v4-pro": GOOD}),
            _evaluate=evaluate)
        check("no needless escalation", res2.escalated is False and len(res2.attempts) == 1)

        # ---- max_attempts respected ----
        res3 = run_prompt(
            "What is an API?", reference=REF, max_attempts=1, use_cache=False,
            _generate=fake_generate_factory(
                {"deepseek-v4-flash": BAD, "deepseek-v4-pro": GOOD}),
            _evaluate=evaluate)
        check("max_attempts=1 stops", len(res3.attempts) == 1 and res3.escalated is False)

        # ---- force_model demo path ----
        res4 = run_prompt(
            "What is an API?", reference=REF, max_attempts=2, use_cache=False,
            force_model="deepseek-v4-pro",
            _generate=fake_generate_factory(
                {"deepseek-v4-flash": GOOD, "deepseek-v4-pro": GOOD}),
            _evaluate=evaluate)
        check("force_model first", res4.initial_model == "deepseek-v4-pro", res4.initial_model)

        # ---- escalation order helper ----
        reg = regmod.reset_registry_for_tests(Path(td) / "models.json")
        d = route(analyze("Design a fault-tolerant distributed banking architecture." * 3),
                  reg.enabled())
        order = escalation_order(d.candidates, {d.selected_model})
        check("order excludes tried", d.selected_model not in order)
        check("order covers rest", len(order) == len(d.candidates) - 1)

    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
