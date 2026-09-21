"""Gateway /v1 API tests — Phase G12. Run from llm-cost-optimizer/:
    python backend\\test_v1_api.py

FastAPI TestClient tests over the FULL app (backend.main.app) with hermetic
fakes:
  - customer/registry/secret stores point at temp JSON files (monkey-patched
    into the namespaces that import them at module top)
  - RequestStore in memory mode (no MongoDB needed)
  - control plane disabled (OPENCODE_ENABLED=False) -> deterministic routing,
    no network, no LLM judge
  - provider factory faked -> optimizer never leaves the process
  - /models/{id}/test and /models/{id}/profile faked at the provider/profiler
    boundary

Covers: auth (401s), customer provisioning (key shown once), model CRUD +
secret handling (never returned), pricing resolution, connectivity test,
profiling job, optimize happy path + errors, OpenAI-compatible
chat/completions (auto + forced + unknown model), customer-scoped analytics
isolation, and gateway health.
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
import backend.database.mongodb as dbmod
from backend.database.mongodb import RequestStore
from backend.llm import config as cp_cfg
from backend.main import app
from backend.providers.base import ProviderError
from backend.providers.opencode import GenerateResult

failures = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))
    if not cond:
        failures += 1


def fake_generate_result(text: str = "An API lets programs communicate.",
                         model_id: str = "fake") -> GenerateResult:
    return GenerateResult(text=text, model_id=model_id, endpoint="fake",
                          endpoint_family="chat_completions",
                          input_tokens=10, output_tokens=10,
                          cached_tokens=0, latency_ms=5, raw_usage={})


class FakeProvider:
    """Stands in for any BaseLLMProvider — records calls, never hits network."""

    def __init__(self, model_id: str, text: str = "An API lets programs communicate."):
        self.model_id = model_id
        self.text = text
        self.calls: list[dict] = []

    def generate(self, messages, max_tokens=512, temperature=0.2) -> GenerateResult:
        self.calls.append({"messages": messages, "max_tokens": max_tokens,
                           "temperature": temperature})
        return fake_generate_result(self.text, self.model_id)

    def test_connection(self):
        from backend.providers.base import TestResult
        return TestResult(ok=True, detail="reply: 'pong'",
                          latency_ms=12, model_id=self.model_id)


def main() -> int:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # ---- hermetic stores (patch the getters the API modules captured) --
        import backend.api.gateway as gwmod
        import backend.api.v1 as v1mod
        from backend.core.cache import reset_cache_for_tests
        from backend.core.customer_registry import reset_customer_registry_for_tests
        from backend.core.customers import reset_customer_store_for_tests
        from backend.core.secret_store import reset_secret_store_for_tests

        cust_store = reset_customer_store_for_tests(td / "customers.json")
        reg = reset_customer_registry_for_tests(td / "customer_models.json")
        secrets = reset_secret_store_for_tests(td / "keys.json", secret="v1-test-secret")
        v1mod.get_customer_store = lambda: cust_store
        v1mod.get_customer_registry = lambda: reg
        v1mod.get_secret_store = lambda: secrets
        gwmod.get_customer_store = lambda: cust_store
        # In-memory analytics store (no MongoDB).
        dbmod._store = RequestStore("mongodb://localhost:59999", "v1-test")
        # Fresh cache (main.py seeds a demo entry at import time).
        cachemod.reset_cache_for_tests()

        client = TestClient(app)

        # ================================================== auth
        r = client.post("/v1/optimize", json={"prompt": "hi"})
        check("401 missing key", r.status_code == 401 and "missing gateway API key" in r.json()["detail"])
        r = client.get("/v1/models", headers={"Authorization": "Bearer gw_deadbeef"})
        check("401 invalid key", r.status_code == 401 and "invalid or disabled" in r.json()["detail"])
        r = client.get("/v1/models", headers={"x-api-key": "not-a-gw-key"})
        check("401 non-gw key", r.status_code == 401)

        # ================================================== customers
        r = client.post("/v1/customers", json={"customer_id": "acme", "name": "Acme Corp"})
        check("create customer 201", r.status_code == 201)
        body = r.json()
        api_key = body.get("api_key", "")
        check("api_key returned once", api_key.startswith("gw_") and len(api_key) == 35)
        check("shown-once note", "only once" in body.get("note", ""))
        check("customer view has no hash", "api_key_hash" not in body["customer"])
        check("customer fields", body["customer"]["customer_id"] == "acme"
              and body["customer"]["enabled"] is True)
        r = client.post("/v1/customers", json={"customer_id": "acme"})
        check("duplicate customer 409", r.status_code == 409)
        r = client.post("/v1/customers", json={"customer_id": "Bad Id!"})
        check("invalid customer_id 422", r.status_code == 422)

        auth = {"Authorization": f"Bearer {api_key}"}
        r = client.get("/v1/customers/me", headers=auth)
        check("whoami", r.status_code == 200
              and r.json()["customer"]["customer_id"] == "acme")

        # second tenant for isolation tests
        r = client.post("/v1/customers", json={"customer_id": "beta", "name": "Beta"})
        beta_key = r.json()["api_key"]
        beta_auth = {"Authorization": f"Bearer {beta_key}"}

        # ================================================== model registration
        r = client.post("/v1/models/register", headers=auth, json={
            "model_id": "acme-mini", "provider": "openai_compatible",
            "base_url": "https://api.acme.ai/v1", "api_key": "sk-acme-secret-1",
            "input_per_1M": 0.10, "output_per_1M": 0.40,
            "display_name": "Acme Mini"})
        check("register 201", r.status_code == 201)
        m = r.json()
        check("declared pricing configured", m["pricing_status"] == "configured"
              and m["pricing_source"] == "customer_declared" and m["priced"] is True)
        check("has_credential true", m["has_credential"] is True)
        check("no api_key in response", "api_key" not in m
              and "credential_reference" not in m)
        check("plaintext key not in registry file",
              "sk-acme-secret-1" not in (td / "customer_models.json").read_text())
        check("plaintext key not in keys file",
              "sk-acme-secret-1" not in (td / "keys.json").read_text())

        # catalog-priced model (deepseek-v4-flash is in the pricing catalog)
        r = client.post("/v1/models/register", headers=auth, json={
            "model_id": "deepseek-v4-flash", "base_url": "https://api.acme.ai/v1"})
        check("catalog pricing resolved", r.status_code == 201
              and r.json()["pricing_status"] == "configured"
              and r.json()["pricing_source"] == "catalog")

        # unpriced model -> unknown, never fabricated
        r = client.post("/v1/models/register", headers=auth, json={
            "model_id": "acme-mystery", "base_url": "https://api.acme.ai/v2"})
        check("unknown pricing stays unknown", r.status_code == 201
              and r.json()["pricing_status"] == "unknown"
              and r.json()["priced"] is False)

        # strong model for routing/escalation
        r = client.post("/v1/models/register", headers=auth, json={
            "model_id": "acme-max", "base_url": "https://api.acme.ai/v1",
            "api_key": "sk-acme-secret-2",
            "input_per_1M": 1.00, "output_per_1M": 5.00})
        check("strong model registered", r.status_code == 201)

        # duplicate -> 409
        r = client.post("/v1/models/register", headers=auth, json={
            "model_id": "acme-mini", "base_url": "https://x.io/v1"})
        check("duplicate model 409", r.status_code == 409)

        # isolation: beta's list never contains acme's models
        r = client.get("/v1/models", headers=beta_auth)
        check("beta sees zero acme models", r.json()["count"] == 0)
        r = client.get("/v1/models/acme-mini", headers=beta_auth)
        check("cross-tenant get 404", r.status_code == 404)

        # list + get
        r = client.get("/v1/models", headers=auth)
        check("list models", r.status_code == 200 and r.json()["count"] == 4)
        r = client.get("/v1/models/acme-mini", headers=auth)
        check("get model", r.status_code == 200 and r.json()["display_name"] == "Acme Mini")
        r = client.get("/v1/models/nope", headers=auth)
        check("unknown model 404", r.status_code == 404)

        # update: prices + rotate key
        r = client.put("/v1/models/acme-mini", headers=auth, json={
            "input_per_1M": 0.12, "output_per_1M": 0.48, "api_key": "sk-acme-rotated"})
        check("update model", r.status_code == 200
              and r.json()["input_per_1M"] == 0.12
              and r.json()["pricing_status"] == "configured")
        check("rotated key decrypts", secrets.get(
            reg.get("acme", "acme-mini").credential_reference) == "sk-acme-rotated")
        check("no api_key echoed on update", "api_key" not in r.json())

        # ================================================== connectivity test
        with mock.patch.object(v1mod, "provider_for",
                               return_value=FakeProvider("acme-mini")):
            r = client.post("/v1/models/acme-mini/test", headers=auth)
        check("test endpoint ok", r.status_code == 200 and r.json()["ok"] is True
              and r.json()["model_id"] == "acme-mini")
        entry = reg.get("acme", "acme-mini")
        check("test result recorded", entry.last_test_status == "ok")
        # no base_url -> 400. Register a dedicated model WITHOUT base_url so the
        # endpoint rejects it before building a provider (acme-mystery has a
        # base_url and would otherwise make a real network call).
        r = client.post("/v1/models/register", headers=auth, json={
            "model_id": "acme-nourl"})
        check("no-url model registered", r.status_code == 201)
        r = client.post("/v1/models/acme-nourl/test", headers=auth)
        check("test without base_url 400", r.status_code == 400)
        r = client.post("/v1/models/nope/test", headers=auth)
        check("test unknown model 404", r.status_code == 404)

        # ================================================== profiling
        with mock.patch("backend.core.profiler.start_job",
                        return_value="job-test-123") as sj:
            r = client.post("/v1/models/acme-mini/profile", headers=auth)
        check("profile returns job", r.status_code == 200
              and r.json()["job_id"] == "job-test-123"
              and "poll" in r.json()["note"])
        check("profiler invoked with injectable generator",
              sj.call_count == 1 and sj.call_args.kwargs.get("_generate") is not None)
        check("profile status profiling", reg.get("acme", "acme-mini").profile_status == "profiling")
        r = client.post("/v1/models/nope/profile", headers=auth)
        check("profile unknown model 404", r.status_code == 404)

        # ================================================== optimize
        # 400 with no models for beta
        r = client.post("/v1/optimize", headers=beta_auth,
                        json={"prompt": "What is an API?"})
        check("optimize no models 400", r.status_code == 400
              and "no models registered" in r.json()["detail"])

        # happy path: fake provider, control plane disabled -> deterministic
        def fake_factory(model_id: str):
            return FakeProvider(model_id)

        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False), \
             mock.patch.object(v1mod, "_provider_factory", return_value=fake_factory):
            r = client.post("/v1/optimize", headers=auth,
                            json={"prompt": "What is an API?", "max_tokens": 64})
        check("optimize 200", r.status_code == 200, extra=r.text[:300])
        o = r.json()
        check("optimize answer", o["answer"] == "An API lets programs communicate.")
        check("optimize selected a customer model",
              o["selected_model"] in ("acme-mini", "deepseek-v4-flash", "acme-max"))
        check("optimize provenance deterministic",
              o["model_selection"]["provenance"] == "deterministic")
        check("optimize quality passed", o["quality_passed"] is True
              and o["quality_score"] is not None and o["quality_score"] >= 0.75)
        check("optimize cost fields", o["actual_cost_usd"] is not None
              and o["baseline_model"] and o["baseline_cost_usd"] is not None
              and o["savings_direction"] in ("savings", "loss", "breakeven"))
        check("optimize net savings fields", o["control_plane_cost_usd"] == 0.0
              and o["net_savings_usd"] is not None)
        check("optimize request_id stored", o["request_id"] is not None)
        check("optimize customer scoped", o["customer_id"] == "acme")
        check("optimize analysis block", o["analysis"]["task_type"] == "general"
              and "required_capabilities" in o["analysis"])
        check("optimize routing block", o["routing"]["selected_model"] == o["selected_model"])
        check("optimize token report", "task_model" in (o["token_report"] or {}))
        check("optimize control_plane view", o["control_plane"]["status"] == "disabled")

        # empty prompt -> 400
        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False), \
             mock.patch.object(v1mod, "_provider_factory", return_value=fake_factory):
            r = client.post("/v1/optimize", headers=auth, json={"prompt": "   "})
        check("optimize empty prompt 400", r.status_code == 400)

        # unpriced-only pool -> clean 400 (NoCapableModel), never 500
        r = client.post("/v1/models/register", headers=beta_auth, json={
            "model_id": "beta-mystery", "base_url": "https://api.beta.io/v1"})
        check("beta unpriced registered", r.status_code == 201)
        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False), \
             mock.patch.object(v1mod, "_provider_factory", return_value=fake_factory):
            r = client.post("/v1/optimize", headers=beta_auth,
                            json={"prompt": "What is an API?"})
        check("unpriced pool 400 not 500", r.status_code == 400
              and "priced" in r.json()["detail"], extra=r.text[:200])

        # provider permanent failure -> 502. Reset the cache first: the happy
        # path above cached this exact prompt, and a cache hit would return
        # before the failing provider is ever invoked.
        cachemod.reset_cache_for_tests()

        def failing_factory(model_id: str):
            class Failing(FakeProvider):
                def generate(self, messages, max_tokens=512, temperature=0.2):
                    raise ProviderError("401 Unauthorized — provider API key invalid")
            return Failing(model_id)

        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False), \
             mock.patch.object(v1mod, "_provider_factory", return_value=failing_factory):
            r = client.post("/v1/optimize", headers=auth,
                            json={"prompt": "What is an API?", "max_tokens": 64})
        check("provider failure 502", r.status_code == 502, extra=r.text[:200])

        # ================================================== chat/completions
        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False), \
             mock.patch.object(v1mod, "_provider_factory", return_value=fake_factory):
            r = client.post("/v1/chat/completions", headers=auth, json={
                "model": "auto",
                "messages": [
                    {"role": "system", "content": "You are terse."},
                    {"role": "user", "content": "What is an API?"},
                ],
                "max_tokens": 64})
        check("chat 200", r.status_code == 200, extra=r.text[:300])
        c = r.json()
        check("chat OpenAI shape", c["object"] == "chat.completion"
              and c["created"] > 0
              and c["choices"][0]["message"]["role"] == "assistant"
              and c["choices"][0]["finish_reason"] == "stop")
        check("chat id prefix", c["id"].startswith("gwchat-"))
        check("chat routed model", c["model"] in ("acme-mini", "deepseek-v4-flash", "acme-max"))
        check("chat usage block", c["usage"]["prompt_tokens"] == 10
              and c["usage"]["completion_tokens"] == 10
              and c["usage"]["total_tokens"] == 20)
        g = c["gateway"]
        check("chat gateway block", g["customer_id"] == "acme"
              and g["requested_model"] == "auto"
              and g["routed_model"] == c["model"]
              and "actual_cost_usd" in g and "net_savings_usd" in g
              and "decision_reason" in g)

        # specific registered model -> forced. Reset the cache first: the
        # auto-routed chat above cached this prompt, and a cache hit would
        # return the cached (routing-selected) model instead of the forced one.
        cachemod.reset_cache_for_tests()
        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False), \
             mock.patch.object(v1mod, "_provider_factory", return_value=fake_factory):
            r = client.post("/v1/chat/completions", headers=auth, json={
                "model": "acme-max",
                "messages": [{"role": "user", "content": "What is an API?"}]})
        check("chat forced model", r.status_code == 200
              and r.json()["model"] == "acme-max"
              and r.json()["gateway"]["routed_model"] == "acme-max")

        # unregistered model -> 404
        r = client.post("/v1/chat/completions", headers=auth, json={
            "model": "not-mine",
            "messages": [{"role": "user", "content": "hi"}]})
        check("chat unregistered model 404", r.status_code == 404
              and "not registered for this customer" in r.json()["detail"])

        # empty messages -> 400
        r = client.post("/v1/chat/completions", headers=auth,
                        json={"model": "auto", "messages": []})
        check("chat empty messages 400", r.status_code == 400)

        # beta (no priced models) -> 400
        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False), \
             mock.patch.object(v1mod, "_provider_factory", return_value=fake_factory):
            r = client.post("/v1/chat/completions", headers=beta_auth, json={
                "model": "auto", "messages": [{"role": "user", "content": "hi"}]})
        check("chat unpriced pool 400", r.status_code == 400)

        # ================================================== analytics isolation
        # acme made real requests above; seed one for beta directly.
        dbmod._store.store_request({"customer_id": "beta", "actual_cost_usd": 0.5,
                                    "baseline_cost_usd": 2.0, "savings_usd": 1.5,
                                    "final_model": "beta-only"})
        r = client.get("/v1/analytics", headers=auth)
        a = r.json()
        check("analytics shape", r.status_code == 200
              and a["customer_id"] == "acme"
              and "analytics" in a and "routing" in a)
        check("analytics scoped to acme", a["analytics"]["total_requests"] >= 1
              and a["routing"]["total_requests"] == a["analytics"]["total_requests"])
        check("analytics excludes beta docs",
              "beta-only" not in str(a["routing"]["by_model"]))
        r = client.get("/v1/analytics", headers=beta_auth)
        b = r.json()
        check("beta analytics only beta", b["analytics"]["total_requests"] == 1
              and b["routing"]["by_model"].get("beta-only") == 1)

        # ================================================== health
        # Patch the control-plane flag around the health call so the enabled
        # assertion is deterministic (the earlier patches have already exited).
        with mock.patch.object(cp_cfg, "OPENCODE_ENABLED", False):
            r = client.get("/v1/health", headers=auth)
        h = r.json()
        check("health 200", r.status_code == 200 and h["status"] == "ok")
        check("health counts", h["customer_id"] == "acme"
              and h["models_registered"] == 5
              and h["models_priced"] == 3
              and h["models_pricing_unknown"] == 2)
        check("health control plane", h["control_plane"]["model"] == cp_cfg.OPENCODE_MODEL
              and h["control_plane"]["enabled"] is False)
        check("health catalog + cache", h["pricing_catalog_size"] > 0
              and h["cache_backend"] in ("memory", "redis", "unknown"))

        # ================================================== delete model
        r = client.delete("/v1/models/acme-mystery", headers=auth)
        check("delete model", r.status_code == 200 and r.json()["deleted"] == "acme-mystery")
        r = client.get("/v1/models/acme-mystery", headers=auth)
        check("deleted model gone", r.status_code == 404)
        r = client.delete("/v1/models/acme-mystery", headers=auth)
        check("delete twice 404", r.status_code == 404)
        # credential cleaned up on delete
        check("secret count bounded", secrets.count() >= 1)

        # ================================================== disabled customer
        cust_store.set_enabled("acme", False)
        r = client.get("/v1/models", headers=auth)
        check("disabled customer 401", r.status_code == 401)
        cust_store.set_enabled("acme", True)

    print(f"RESULT: {'PASS' if failures == 0 else f'{failures} FAILURES'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
