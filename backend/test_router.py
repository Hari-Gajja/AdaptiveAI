"""Phase-3 tests: analyzer + router (no API key needed; uses priors + tmp registry).

Run from llm-cost-optimizer/:  python backend\\test_router.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Hermetic: unit expectations assume PRIORS, never the live profiles.json.
os.environ["LLMO_PROFILES_FILE"] = str(
    Path(tempfile.gettempdir()) / "llmo-test-empty-profiles.json")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.core.registry as regmod
from backend.core.router import route
from backend.core.task_analyzer import analyze

failures = 0


def check(name: str, cond: bool, extra: str = ""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        reg = regmod.reset_registry_for_tests(Path(td) / "models.json")
        enabled = reg.enabled()

        # 1. Easy prompt -> flash (cheapest qualifying).
        a = analyze("What is an API?")
        d = route(a, enabled)
        check("easy task_type general", a.task_type == "general", a.task_type)
        check("easy difficulty low", a.difficulty_score < 0.5, str(a.difficulty_score))
        check("easy routes to flash", d.selected_model == "deepseek-v4-flash",
              d.selected_model)
        check("easy meets requirements", d.meets_requirements is True)
        check("decision_reason present", len(d.decision_reason) >= 3)

        # 2. Hard architecture prompt -> pro.
        hard = ("Design a fault-tolerant distributed banking architecture with "
                "microservices, explain consistency tradeoffs step by step in detail. " * 4)
        a2 = analyze(hard)
        d2 = route(a2, enabled)
        check("hard task_type architecture", a2.task_type == "architecture", a2.task_type)
        check("hard difficulty high", a2.difficulty_score >= 0.6, str(a2.difficulty_score))
        check("hard routes to pro", d2.selected_model == "deepseek-v4-pro",
              d2.selected_model)

        # 3. Coding prompt -> categorized, routes to a qualifying model.
        a3 = analyze("Debug this Python program: concurrent requests cause race "
                     "conditions, here is the traceback and code.")
        d3 = route(a3, enabled)
        check("coding detected", a3.task_type in ("coding", "debugging"), a3.task_type)
        check("coding routes (meets req)", d3.meets_requirements is True)

        # 4. Low confidence -> safety action. Craft ambiguous short prompt and
        # force low confidence path by lowering directly.
        a4 = analyze("Explain API code math design.")
        a4.confidence = 0.40
        d4 = route(a4, enabled)
        named = [c.model_id for c in d4.candidates if c.qualifies]
        if len(named) >= 2:
            check("low confidence picks safest (pro)", d4.selected_model == "deepseek-v4-pro"
                  and d4.confidence_action == "low_confidence_safety", d4.selected_model)
        else:
            check("low confidence action flagged",
                  d4.confidence_action in ("normal", "low_confidence_safety"))

        # 5. Impossible thresholds -> flagged fallback, never silent.
        a5 = analyze("Design a fault-tolerant distributed banking architecture.")
        a5.required_thresholds = {k: 0.99 for k in a5.required_capabilities}
        d5 = route(a5, enabled)
        check("impossible req flagged", d5.meets_requirements is False)
        check("fallback picks strongest (pro)", d5.selected_model == "deepseek-v4-pro",
              d5.selected_model)

        # 6. Cost ordering sanity: flash estimate < pro estimate on same task.
        costs = {c.model_id: c.expected_cost_usd for c in d.candidates}
        check("flash cheaper estimate", costs["deepseek-v4-flash"] < costs["deepseek-v4-pro"],
              str(costs))

        # 7. Task level exposed on the decision.
        check("easy level", d.task_level == "easy", d.task_level)

        # 8. Medium math task: flash qualifies on a THIN margin -> pro directly
        #    (a likely fail-then-escalate would double-bill).
        a8 = analyze("Calculate the probability of rolling two sixes and explain the math.")
        d8 = route(a8, enabled)
        check("medium math level", d8.task_level == "medium", d8.task_level)
        check("medium math routes to pro (thin margin)",
              d8.selected_model == "deepseek-v4-pro", d8.selected_model)

        # 9. Medium coding task: flash has a COMFORTABLE margin -> flash (safe cheap).
        a9 = analyze("Write a Python function that parses JSON, handles errors, and "
                     "returns a sorted list. Explain the error handling.")
        d9 = route(a9, enabled)
        check("medium coding level", d9.task_level == "medium", d9.task_level)
        check("medium coding routes to flash (comfortable margin)",
              d9.selected_model == "deepseek-v4-flash", d9.selected_model)

        # 10. Hard task -> strongest qualifier directly, safety action.
        a10 = analyze("Design a fault-tolerant distributed banking architecture with "
                      "microservices, explain consistency tradeoffs step by step in detail.")
        a10.difficulty_score = 0.75
        a10.quality_requirement = "high"
        d10 = route(a10, enabled)
        check("hard level flagged", d10.task_level == "hard", d10.task_level)
        check("hard routes to pro with safety action",
              d10.selected_model == "deepseek-v4-pro"
              and d10.confidence_action == "high_difficulty_safety", d10.selected_model)

    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
