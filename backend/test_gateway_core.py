"""Gateway core tests — Phase G12. Run from llm-cost-optimizer/:
    python backend\\test_gateway_core.py

Covers: customer store (key shown once, hash-only storage, auth, rotation),
customer model registry (isolation, pricing resolution, never-fabricated
pricing), secret store (round-trip, no plaintext at rest), dynamic tiers,
and the hybrid gateway router (recommendation validation + fallback).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.cache import PromptCache
from backend.core.customer_registry import (
    CustomerModelCreate,
    CustomerModelUpdate,
    RegistryError,
    reset_customer_registry_for_tests,
)
from backend.core.customers import (
    CustomerCreate,
    CustomerError,
    hash_key,
    new_api_key,
    reset_customer_store_for_tests,
)
from backend.core.gateway_router import select_model
from backend.core.secret_store import reset_secret_store_for_tests
from backend.core.task_analyzer import analyze
from backend.core.tiers import derive_tiers, tier_of, tier_rank

failures = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # ------------------------------------------------ customer store
        store = reset_customer_store_for_tests(td / "customers.json")
        c1, key1 = store.create(CustomerCreate(customer_id="acme", name="Acme"))
        check("customer created with gw_ key", key1.startswith("gw_") and len(key1) == 35)
        check("key stored as hash only", c1.api_key_hash == hash_key(key1)
              and key1 not in (td / "customers.json").read_text())
        check("duplicate customer rejected", _raises(lambda: store.create(
            CustomerCreate(customer_id="acme")), CustomerError))
        check("authenticate valid key", store.authenticate(key1) is not None
              and store.authenticate(key1).customer_id == "acme")
        check("authenticate bad key None", store.authenticate("gw_deadbeef") is None)
        check("authenticate non-gw None", store.authenticate("sk-123") is None)
        c2, key2 = store.rotate_key("acme")
        check("rotate invalidates old key", store.authenticate(key1) is None
              and store.authenticate(key2) is not None)
        store.set_enabled("acme", False)
        check("disabled customer cannot auth", store.authenticate(key2) is None)
        store.set_enabled("acme", True)
        check("re-enabled customer can auth", store.authenticate(key2) is not None)

        # ------------------------------------------------ secret store
        secrets = reset_secret_store_for_tests(td / "keys.json", secret="unit-secret")
        ref = secrets.put("sk-provider-secret-123")
        check("secret ref format", ref.startswith("cred_"))
        check("plaintext never at rest", "sk-provider-secret-123"
              not in (td / "keys.json").read_text())
        check("round-trip decrypt", secrets.get(ref) == "sk-provider-secret-123")
        check("unknown ref None", secrets.get("cred_nope") is None)
        secrets.delete(ref)
        check("delete removes", secrets.get(ref) is None and secrets.count() == 0)

        # ------------------------------------------------ customer registry
        reg = reset_customer_registry_for_tests(td / "customer_models.json")
        # priced model (customer-declared)
        m1 = reg.create("acme", CustomerModelCreate(
            model_id="acme-mini", base_url="https://api.acme.ai/v1",
            api_key=None, input_per_1M=0.15, output_per_1M=0.60))
        check("declared pricing configured", m1.pricing_status == "configured"
              and m1.pricing_source == "customer_declared")
        # unpriced model -> unknown, NEVER fabricated
        m2 = reg.create("acme", CustomerModelCreate(
            model_id="acme-mystery", base_url="https://api.acme.ai/v2"))
        check("undeclared pricing stays unknown", m2.pricing_status == "unknown"
              and m2.pricing_source == "none" and m2.input_per_1M == 0.0)
        # catalog hit (deepseek-v4-flash is in the pricing catalog)
        m3 = reg.create("acme", CustomerModelCreate(model_id="deepseek-v4-flash"))
        check("catalog pricing resolved", m3.pricing_status == "configured"
              and m3.pricing_source == "catalog" and m3.input_per_1M > 0)
        # isolation: another customer sees nothing
        reg.create("beta", CustomerModelCreate(model_id="beta-only",
                                               base_url="https://api.beta.io/v1"))
        check("isolation: acme list excludes beta", all(
            m.model_id != "beta-only" for m in reg.list("acme")))
        check("isolation: beta list excludes acme", all(
            m.model_id != "acme-mini" for m in reg.list("beta")))
        check("cross-tenant get 404", _raises(
            lambda: reg.get("beta", "acme-mini"), RegistryError))
        # public view never leaks credential material
        reg2 = reset_customer_registry_for_tests(td / "customer_models2.json")
        ref2 = secrets.put("sk-live-key-999")
        reg2.create("acme", CustomerModelCreate(
            model_id="with-key", base_url="https://x.io/v1"), credential_reference=ref2)
        view = reg2.get("acme", "with-key").model_dump()
        check("entry stores ref not key", view["credential_reference"] == ref2
              and "sk-live-key-999" not in str(view))
        # update: rotate credential + pricing recompute
        ref3 = secrets.put("sk-rotated-key")
        upd = reg2.update("acme", "with-key", CustomerModelUpdate(input_per_1M=0.5,
                                                                  output_per_1M=1.5),
                          new_credential_reference=ref3)
        check("update recomputes pricing", upd.pricing_status == "configured"
              and upd.input_per_1M == 0.5)
        check("update swaps credential ref", upd.credential_reference == ref3)
        check("update strips api_key field", upd.model_dump().get("api_key") is None)
        # delete
        reg2.delete("acme", "with-key")
        check("delete removes entry", _raises(
            lambda: reg2.get("acme", "with-key"), RegistryError))

        # ------------------------------------------------ dynamic tiers
        def cm(mid: str, inp: float, outp: float, priced=True):
            return reg.create("acme", CustomerModelCreate(
                model_id=mid, base_url="https://t.io/v1",
                input_per_1M=inp if priced else None,
                output_per_1M=outp if priced else None))

        t1 = derive_tiers([cm("t-single", 0.1, 0.2)])
        check("1 model -> mid", tier_of("t-single", t1) == "mid")
        t2 = derive_tiers([cm("t-cheap", 0.05, 0.1), cm("t-exp", 3.0, 15.0)])
        check("2 models -> cheap/frontier",
              tier_of("t-cheap", t2) == "cheap" and tier_of("t-exp", t2) == "frontier")
        pool3 = [cm("t-a", 0.05, 0.1), cm("t-b", 0.5, 1.0), cm("t-c", 5.0, 10.0)]
        t3 = derive_tiers(pool3)
        check("3 models -> terciles",
              tier_of("t-a", t3) == "cheap" and tier_of("t-b", t3) == "mid"
              and tier_of("t-c", t3) == "frontier")
        check("tier rank ordering", tier_rank("cheap") < tier_rank("mid")
              < tier_rank("frontier") < tier_rank("unknown"))
        t4 = derive_tiers(pool3 + [cm("t-unpriced", 0, 0, priced=False)])
        check("unpriced tier unknown", tier_of("t-unpriced", t4) == "unknown")

        # ------------------------------------------------ hybrid gateway router
        # Pool: cheap (weak caps) + strong (expensive). Both priced.
        reg3 = reset_customer_registry_for_tests(td / "gw_router.json")
        cheap = reg3.create("acme", CustomerModelCreate(
            model_id="deepseek-v4-flash", base_url="https://t.io/v1",
            input_per_1M=0.22, output_per_1M=0.66))
        strong = reg3.create("acme", CustomerModelCreate(
            model_id="kimi-k3", base_url="https://t.io/v1",
            input_per_1M=3.0, output_per_1M=15.0))
        pool = [cheap, strong]

        # 1. valid recommendation accepted
        easy = analyze("What is an API?")
        d1 = select_model(easy, "acme", pool, {"model": "deepseek-v4-flash", "reason": "cheap enough"})
        check("valid rec accepted", d1.provenance == "nemotron_recommended"
              and d1.selected_model == "deepseek-v4-flash")

        # 2. recommendation for a model NOT in the customer's registry -> fallback
        d2 = select_model(easy, "acme", pool, {"model": "someone-elses-model"})
        check("foreign rec rejected -> fallback", d2.provenance == "nemotron_rejected_fallback"
              and "not in customer's registry" in d2.reason)

        # 3. recommendation violating capability -> fallback with note
        hard = analyze("Design a distributed consensus algorithm with formal proofs "
                       "and implement the Raft protocol from scratch.")
        d3 = select_model(hard, "acme", pool, {"model": "deepseek-v4-flash"})
        check("incapable rec rejected", d3.provenance == "nemotron_rejected_fallback"
              and any("capability" in n for n in d3.validation_notes))

        # 4. no recommendation -> deterministic
        d4 = select_model(easy, "acme", pool, None)
        check("no rec -> deterministic", d4.provenance == "deterministic"
              and d4.selected_model in ("deepseek-v4-flash", "kimi-k3"))

        # 5. isolation defense-in-depth: pool filtered by customer_id
        beta_model = reg3.create("beta", CustomerModelCreate(
            model_id="beta-only", base_url="https://b.io/v1",
            input_per_1M=0.01, output_per_1M=0.02))
        d5 = select_model(easy, "acme", pool + [beta_model], {"model": "beta-only"})
        check("defense in depth: beta never selected for acme",
              d5.selected_model != "beta-only")

        # 6. unpriced pool -> NoCapableModel (never route cost-blind)
        reg4 = reset_customer_registry_for_tests(td / "gw_router2.json")
        unpriced = reg4.create("acme", CustomerModelCreate(
            model_id="mystery", base_url="https://t.io/v1"))
        check("unpriced pool raises", _raises(
            lambda: select_model(easy, "acme", [unpriced], None), Exception))

        # 7. blocked model excluded
        reg5 = reset_customer_registry_for_tests(td / "gw_router3.json")
        a = reg5.create("acme", CustomerModelCreate(model_id="deepseek-v4-flash",
                                                    base_url="https://t.io/v1",
                                                    input_per_1M=0.22, output_per_1M=0.66))
        b = reg5.create("acme", CustomerModelCreate(model_id="kimi-k3",
                                                    base_url="https://t.io/v1",
                                                    input_per_1M=3.0, output_per_1M=15.0))
        reg5.update("acme", "kimi-k3", CustomerModelUpdate(blocked=True))
        # Re-fetch: update() returns a NEW entry; the stale object would lie.
        b2 = reg5.get("acme", "kimi-k3")
        d7 = select_model(easy, "acme", [a, b2], {"model": "kimi-k3"})
        check("blocked rec rejected", d7.provenance == "nemotron_rejected_fallback"
              and any("blocked" in n for n in d7.validation_notes))

    print(f"RESULT: {'PASS' if failures == 0 else f'{failures} FAILURES'}")
    return 0 if failures == 0 else 1


def _raises(fn, exc):
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
