"""Phase-6b tests: profiler scoring + measured-overrides-prior (no API key).

Run from llm-cost-optimizer/:  python backend\\test_profiler.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core import capabilities as caps
from backend.core.profiler import load_tests, profile_model
from backend.providers.opencode import GenerateResult

failures = 0


def check(name: str, cond: bool, extra: str = ""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


# Canned answers: GOOD model nails references, WEAK model rambles off-topic.
CANNED: dict[str, dict[str, str]] = {}


def _fake_for(model_id: str):
    def _fake(mid, messages, max_tokens=256, temperature=0.1):
        prompt = messages[-1]["content"]
        for t in load_tests():
            if t["prompt"] == prompt:
                text = CANNED[model_id].get(t["id"], "unrelated moonscape cheese")
                return GenerateResult(text=text, model_id=mid, endpoint="fake",
                                      endpoint_family="chat_completions", input_tokens=50,
                                      output_tokens=30, cached_tokens=0, latency_ms=5,
                                      raw_usage={})
        return GenerateResult(text="?", model_id=mid, endpoint="fake",
                              endpoint_family="chat_completions", input_tokens=1,
                              output_tokens=1, cached_tokens=0, latency_ms=1, raw_usage={})
    return _fake


def main() -> int:
    tests = load_tests()
    check("24 capability tests", len(tests) == 24, str(len(tests)))
    cats = {t["category"] for t in tests}
    check("6 categories covered", cats == {"reasoning", "coding", "math", "summarization",
                                           "long_context", "general"}, str(cats))

    good_id, weak_id = "model-good", "model-weak"
    CANNED[good_id] = {t["id"]: t["reference_answer"] for t in tests}
    CANNED[weak_id] = {}

    gs = profile_model(good_id, tests, _fake_for(good_id))
    ws = profile_model(weak_id, tests, _fake_for(weak_id))
    # Bar is 0.70 not 1.0 on purpose: lexical scoring caps below perfect when
    # prompt vocabulary differs from reference vocabulary by design (e.g. an
    # English prompt with a code-only reference). The metric compares models
    # RELATIVELY on one scale — upgrade path is an LLM-judge, see README.
    check("perfect answers score high", all(v >= 0.7 for v in gs.values()), str(gs))
    check("off-topic scores low", all(v < 0.4 for v in ws.values()), str(ws))
    check("good beats weak everywhere",
          all(gs[c] > ws[c] for c in gs))

    # measured overrides prior per category
    profiles = {good_id: dict(gs)}
    merged = caps.capabilities_for(good_id, profiles)
    check("measured wins over prior", merged["reasoning"] == gs["reasoning"])
    check("is_measured true", caps.is_measured(good_id, profiles) is True)
    check("unprofiled stays prior", caps.is_measured("deepseek-v4-flash", {}) is False)
    check("overall_source measured", caps.overall_source([good_id], profiles) == "measured_benchmark")
    check("overall_source mixed", caps.overall_source([good_id, "nope"], profiles) == "mixed")
    check("overall_source estimated", caps.overall_source(["nope"], {}) == "estimated_prior")

    print("RESULT:", "PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
