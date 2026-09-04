"""Phase-2 tests: Model Registry CRUD, guards, persistence (no API key needed).

Run from llm-cost-optimizer/:  python backend\\test_registry.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.registry import ModelCreate, ModelUpdate, RegistryError, reset_registry_for_tests


def check(name: str, cond: bool):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        check.failed += 1
check.failed = 0


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        reg = reset_registry_for_tests(Path(td) / "models.json")
        seeded = {m.model_id for m in reg.list()}
        check("seed contains flash+pro", {"deepseek-v4-flash", "deepseek-v4-pro"} <= seeded)

        flash = reg.get("deepseek-v4-flash")
        pro = reg.get("deepseek-v4-pro")
        check("seed pricing filled", flash.input_per_1M == 0.22 and pro.input_per_1M == 0.66)
        check("flash cheaper than pro (router needs this)",
              flash.input_per_1M < pro.input_per_1M and flash.output_per_1M < pro.output_per_1M)

        created = reg.create(ModelCreate(model_id="mimo-v2.5"))
        check("create auto-fills pricing", created.input_per_1M == 0.14 and created.context_window > 0)
        try:
            reg.create(ModelCreate(model_id="mimo-v2.5"))
            check("duplicate rejected", False)
        except RegistryError as e:
            check("duplicate rejected", e.status == 409)
        try:
            reg.create(ModelCreate(model_id="BAD ID!!"))
            check("bad id rejected", False)
        except Exception:
            check("bad id rejected", True)

        reg.update("mimo-v2.5", ModelUpdate(display_name="MiMo cheap", enabled=False))
        check("disable non-last ok", reg.get("mimo-v2.5").enabled is False)

        # Disabling down to zero enabled must be blocked: disable flash, then pro-disable must fail.
        reg.update("deepseek-v4-flash", ModelUpdate(enabled=False))
        try:
            reg.update("deepseek-v4-pro", ModelUpdate(enabled=False))
            check("last-enabled disable blocked", False)
        except RegistryError as e:
            check("last-enabled disable blocked", e.status == 400)
        reg.update("deepseek-v4-flash", ModelUpdate(enabled=True))

        reg.delete("mimo-v2.5")
        try:
            reg.get("mimo-v2.5")
            check("delete removes", False)
        except RegistryError:
            check("delete removes", True)
        try:
            reg.delete("nope-not-here")
            check("delete missing 404", False)
        except RegistryError as e:
            check("delete missing 404", e.status == 404)

        # Persistence: new instance over same file keeps edits.
        reg.update("deepseek-v4-flash", ModelUpdate(display_name="Flash!"))
        reg2 = reset_registry_for_tests(Path(td) / "models.json")
        check("reload persists", reg2.get("deepseek-v4-flash").display_name == "Flash!")

    print("RESULT:", "PASS" if check.failed == 0 else f"{check.failed} FAILURES")
    return 0 if check.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
