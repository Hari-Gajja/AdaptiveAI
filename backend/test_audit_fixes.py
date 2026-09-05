"""Regression coverage for audited correctness and credibility fixes."""
from __future__ import annotations

import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.cache import CacheEntry, PromptCache
from backend.core.cost import baseline_model, cost_summary
from backend.core.optimizer import run_prompt
from backend.core.registry import ModelCreate, ModelUpdate, RegistryError, reset_registry_for_tests
from backend.providers.opencode import GenerateResult, OpenCodeError, _usage_int


def check(name: str, condition: bool) -> None:
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def main() -> int:
    cache = PromptCache()
    entry = CacheEntry("same", "context-a", "a", "m", 10, 5, 0.01, 2)
    cache.put(entry)
    check("same prompt and context exact hit", cache.get_exact("same", "context-a") is not None)
    check("different context exact miss", cache.get_exact("same", "context-b") is None)
    check("different prompt exact miss", cache.get_exact("other", "context-a") is None)
    check("missing usage stays unavailable", _usage_int({}, "prompt_tokens") is None)
    check("reported zero usage stays zero", _usage_int({"prompt_tokens": 0}, "prompt_tokens") == 0)
    with tempfile.TemporaryDirectory() as td:
        reg_path = Path(td) / "models.json"
        reg = reset_registry_for_tests(reg_path)
        flash = reg.get("deepseek-v4-flash")
        pro = reg.get("deepseek-v4-pro")
        flash.input_per_1M, flash.output_per_1M = 0.01, 0.01
        pro.input_per_1M, pro.output_per_1M = 1.0, 1.0
        import backend.core.capabilities as capabilities
        old_profiles = capabilities.PROFILES_FILE
        profiles_path = Path(td) / "profiles.json"
        profiles_path.write_text(
            '{"deepseek-v4-flash":{"reasoning":0.95,"coding":0.95,"math":0.95,"summarization":0.95,"long_context":0.95,"general":0.95},'
            '"deepseek-v4-pro":{"reasoning":0.70,"coding":0.70,"math":0.70,"summarization":0.70,"long_context":0.70,"general":0.70}}',
            encoding="utf-8",
        )
        capabilities.PROFILES_FILE = profiles_path
        try:
            check("higher capability beats higher price", baseline_model(reg.enabled()).model_id == "deepseek-v4-flash")
        finally:
            capabilities.PROFILES_FILE = old_profiles
        unavailable = cost_summary(None, pro, None, 10)
        check("missing tokens make cost unavailable", unavailable["actual_cost_status"] == "unavailable" and unavailable["actual_cost_usd"] is None)
        check("configured tokens make measured cost", cost_summary(0.1, pro, 10, 10)["actual_cost_status"] == "measured")
        unknown = reg.create(ModelCreate(model_id="unknown-model", enabled=False))
        check("unknown model is unpriced", unknown.pricing_status == "unavailable")
        try:
            reg.update("unknown-model", ModelUpdate(enabled=True))
            check("unpriced model cannot enable", False)
        except RegistryError:
            check("unpriced model cannot enable", True)

        def failing(model_id, messages, max_tokens=512, temperature=0.2):
            return GenerateResult("bad", model_id, "fake", "chat_completions", 10, 10, 0, 1, {})

        result = run_prompt("What is an API?", reference="An API lets programs communicate.",
                            max_attempts=1, use_cache=False, _generate=failing)
        check("all failed attempts expose verification failure", result.verification_status == "verification_failed" and not result.quality_passed)

        def transient(model_id, messages, max_tokens=512, temperature=0.2):
            if model_id == "deepseek-v4-flash":
                raise OpenCodeError("429 rate-limited")
            return GenerateResult("An API lets programs communicate.", model_id, "fake", "chat_completions", 10, 10, 0, 1, {})

        result = run_prompt("What is an API?", reference="An API lets programs communicate.",
                            max_attempts=2, use_cache=False, _generate=transient)
        check("transient provider failure falls back", result.initial_model == "deepseek-v4-flash" and result.final_model == "deepseek-v4-pro")
        check("failed provider attempt makes cost status explicit", result.cost_status == "unavailable")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
