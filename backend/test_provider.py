"""Phase-1 verification: calls BOTH configured models independently.

Usage (from llm-cost-optimizer/):
    copy ..\\.env.example .\\.env   (then fill OPENCODE_API_KEY)
    pip install -r backend\\requirements.txt
    python backend\\test_provider.py
    # or: python -m backend.test_provider
Exit 0 = both models returned text + usage. Anything else prints the fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import MODEL_PRICING, endpoint_family, settings, validate_phase1
from backend.providers.opencode import OpenCodeError, generate


def est_cost(model_id: str, inp: int, out: int) -> float:
    i, o, _ = MODEL_PRICING.get(model_id, (0.0, 0.0, 0.0))
    return inp / 1_000_000 * i + out / 1_000_000 * o


def main() -> int:
    print("== Phase 1: two-model provider check ==")
    print(f"Model A: {settings.model_a} [{endpoint_family(settings.model_a)}]")
    print(f"Model B: {settings.model_b} [{endpoint_family(settings.model_b)}]")
    for w in validate_phase1():
        print(f"WARNING: {w}")
    if not settings.openai_key:
        print("FAIL: OPENCODE_API_KEY missing. Copy .env.example to .env and fill it.")
        return 2
    ok = True
    for mid in (settings.model_a, settings.model_b):
        print(f"\n--- {mid} ---")
        try:
            r = generate(mid, [{"role": "user", "content": "Reply with exactly: PROVIDER_OK"}], max_tokens=32)
            print(f"text: {r.text.strip()[:200]}")
            print(f"tokens: in={r.input_tokens} out={r.output_tokens} cached={r.cached_tokens} latency={r.latency_ms}ms")
            print(f"endpoint: {r.endpoint} [{r.endpoint_family}]")
            print(f"est. cost: ${est_cost(mid, r.input_tokens, r.output_tokens):.6f}")
            if not r.text.strip():
                print("FAIL: empty response"); ok = False
        except OpenCodeError as e:
            print(f"FAIL: {e}"); ok = False
    print("\nRESULT:", "PASS — both models work. Proceed to Phase 2 (Model Registry)." if ok else "FAIL — fix above, re-run.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
