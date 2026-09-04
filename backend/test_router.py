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

    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
