# Live end-to-end smoke test of the /v1 gateway API against a running server.
# Uses REAL OpenCode Zen models as customer models so generation is REAL.
# Run: python backend/smoke_v1_live.py
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = os.environ.get("GATEWAY_BASE", "http://127.0.0.1:8000")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OC_KEY = os.environ.get("OPENCODE_API_KEY", "")
OC_BASE = os.environ.get("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1")

# Unique-per-run tenant ids (gw_ keys are shown once, so re-runs need fresh ids)
SUF = format(int(time.time()) % 100000, "05d")
CID_A = f"smoke-acme-{SUF}"
CID_B = f"smoke-globex-{SUF}"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def call(method, path, key=None, body=None, expect=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            code = resp.status
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {"raw": "<non-json>"}
    if expect is not None:
        check(f"{method} {path} -> {expect}", code == expect, f"got {code}: {json.dumps(payload)[:160]}")
    return code, payload


print("=" * 70)
print("LIVE /v1 GATEWAY SMOKE TEST")
print("=" * 70)
print(f"server: {BASE}")
print(f"opencode key: {'set (len %d)' % len(OC_KEY) if OC_KEY else 'NOT SET'}")
print(f"tenants this run: {CID_A} / {CID_B}")
print()

# ---------------------------------------------------------------- 1. auth
print("[1] Auth")
_, err = call("GET", "/v1/customers/me", expect=401)
check("401 message mentions gateway key", "gateway API key" in str(err.get("detail", "")))
_, err2 = call("GET", "/v1/customers/me", key="gw_invalidkey0000000000000000", expect=401)
check("invalid key rejected", "invalid or disabled" in str(err2.get("detail", "")))

# ------------------------------------------------------- 2. provision customer
print("[2] Provision customer")
code, cust = call("POST", "/v1/customers", body={"customer_id": CID_A, "name": "Smoke Acme"}, expect=201)
KEY = cust.get("api_key", "")
check("api_key returned once, gw_ prefix", KEY.startswith("gw_") and len(KEY) == 35, f"len={len(KEY)}")
code, dup = call("POST", "/v1/customers", body={"customer_id": CID_A, "name": "dup"}, expect=409)
code, me = call("GET", "/v1/customers/me", key=KEY, expect=200)
check("whoami customer_id", me.get("customer", {}).get("customer_id") == CID_A,
      str(me.get("customer", {}).get("customer_id")))

# --------------------------------------------------- 3. register models
# All routed models use REAL OpenCode ids; the unpriced fake id is never routed.
print("[3] Register models (3 pricing paths)")
code, m1 = call("POST", "/v1/models/register", key=KEY, body={
    "model_id": "qwen3.8-flash", "provider": "openai_compatible", "base_url": OC_BASE,
    "api_key": OC_KEY, "input_per_1M": 0.12, "output_per_1M": 0.48,
    "context_window": 128000, "description": "cheap tier (declared)",
}, expect=201)
check("declared pricing -> configured/customer_declared",
      m1.get("pricing_status") == "configured" and m1.get("pricing_source") == "customer_declared",
      f"{m1.get('pricing_status')}/{m1.get('pricing_source')}")
check("credential never echoed", "api_key" not in m1 and "sk-" not in json.dumps(m1))
check("has_credential true", m1.get("has_credential") is True)

code, m2 = call("POST", "/v1/models/register", key=KEY, body={
    "model_id": "deepseek-v4-flash", "provider": "openai_compatible",
    "base_url": OC_BASE, "api_key": OC_KEY,
}, expect=201)
check("catalog pricing -> configured/catalog",
      m2.get("pricing_status") == "configured" and m2.get("pricing_source") == "catalog",
      f"{m2.get('pricing_status')}/{m2.get('pricing_source')}")

code, m3 = call("POST", "/v1/models/register", key=KEY, body={
    "model_id": f"smoke-mystery-{SUF}", "provider": "openai_compatible",
    "base_url": OC_BASE, "api_key": OC_KEY,
}, expect=201)
check("unknown pricing -> unknown/none",
      m3.get("pricing_status") == "unknown" and m3.get("pricing_source") in (None, "none"),
      f"{m3.get('pricing_status')}/{m3.get('pricing_source')}")

# --------------------------------------------------- 4. list / get / update
print("[4] List / get / update")
code, lst = call("GET", "/v1/models", key=KEY, expect=200)
ids = [m["model_id"] for m in lst.get("models", [])]
check("3 models listed", set(ids) == {"qwen3.8-flash", "deepseek-v4-flash", f"smoke-mystery-{SUF}"}, str(ids))
code, lst2 = call("GET", "/v1/models?enabled_only=true", key=KEY, expect=200)
code, one = call("GET", "/v1/models/qwen3.8-flash", key=KEY, expect=200)
check("get returns context_window", one.get("context_window") == 128000)
code, upd = call("PUT", "/v1/models/qwen3.8-flash", key=KEY,
                 body={"input_per_1M": 0.10, "output_per_1M": 0.40}, expect=200)
check("update repriced", upd.get("input_per_1M") == 0.10 and upd.get("pricing_status") == "configured")

# --------------------------------------------------- 5. live connectivity test
print("[5] POST /v1/models/{id}/test  (LIVE through customer endpoint)")
code, t = call("POST", "/v1/models/qwen3.8-flash/test", key=KEY, body={}, expect=200)
check("live test ok", t.get("ok") is True, f"detail={t.get('detail')!r} latency={t.get('latency_ms')}ms")

# --------------------------------------------------- 6. profile (live)
print("[6] POST /v1/models/{id}/profile  (LIVE capability profiling)")
code, pj = call("POST", "/v1/models/qwen3.8-flash/profile", key=KEY, body={}, expect=200)
job_id = pj.get("job_id")
check("profile job started", bool(job_id), str(pj))
if job_id:
    status = None
    job = {}
    for _ in range(90):
        time.sleep(2)
        code, job = call("GET", f"/api/models/profile/jobs/{job_id}", expect=200)
        status = job.get("status")
        if status in ("done", "error"):
            break
    check("profile job completed", status == "done", f"status={status} err={job.get('error')}")
    code, prof = call("GET", "/v1/models/qwen3.8-flash", key=KEY, expect=200)
    check("profile_status -> profiled", prof.get("profile_status") == "profiled",
          f"capability_source={prof.get('capability_source')}")

# --------------------------------------------------- 7. optimize (LIVE generation)
print("[7] POST /v1/optimize  (LIVE generation through customer models)")
code, opt = call("POST", "/v1/optimize", key=KEY, body={
    "prompt": "What is an API?",
    "max_attempts": 2,
}, expect=200)
check("answer non-empty", bool(opt.get("answer")), str(opt.get("answer", ""))[:120])
check("selected a real model", opt.get("selected_model") in ("qwen3.8-flash", "deepseek-v4-flash"),
      opt.get("selected_model"))
ms = opt.get("model_selection", {})
check("model_selection provenance present",
      ms.get("provenance") in ("nemotron_recommended", "deterministic", "nemotron_rejected_fallback"),
      f"provenance={ms.get('provenance')} reason={str(ms.get('reason'))[:80]}")
check("actual cost > 0", (opt.get("actual_cost_usd") or 0) > 0, f"${opt.get('actual_cost_usd')}")
check("baseline cost > 0", (opt.get("baseline_cost_usd") or 0) > 0, f"${opt.get('baseline_cost_usd')}")
check("control_plane_cost present", "control_plane_cost_usd" in opt, f"${opt.get('control_plane_cost_usd')}")
check("net_savings present", "net_savings_usd" in opt, f"${opt.get('net_savings_usd')}")
check("tokens measured", (opt.get("input_tokens") or 0) > 0 and (opt.get("output_tokens") or 0) > 0,
      f"in={opt.get('input_tokens')} out={opt.get('output_tokens')}")
check("quality scored", opt.get("quality_score") is not None, f"{opt.get('quality_score')}")

# --------------------------------------------------- 8. chat/completions
print("[8] POST /v1/chat/completions  (OpenAI-compatible, model=auto)")
code, chat = call("POST", "/v1/chat/completions", key=KEY, body={
    "model": "auto",
    "messages": [{"role": "user", "content": "What is an API?"}],
}, expect=200)
check("OpenAI shape: object", chat.get("object") == "chat.completion")
check("OpenAI shape: content non-empty",
      bool(chat.get("choices", [{}])[0].get("message", {}).get("content")))
gw = chat.get("gateway", {})
check("gateway block: routed_model", gw.get("routed_model") in ("qwen3.8-flash", "deepseek-v4-flash"),
      gw.get("routed_model"))
check("gateway block: cache_hit (same prompt as optimize)", gw.get("cache_hit") is True,
      f"cache_kind={gw.get('cache_kind')}")
check("gateway block: savings", "savings_usd" in gw and "net_savings_usd" in gw)

print("[8b] forced model")
code, chat2 = call("POST", "/v1/chat/completions", key=KEY, body={
    "model": "deepseek-v4-flash",
    "messages": [{"role": "user", "content": f"Reply with exactly: OK-{SUF}"}],
}, expect=200)
gw2 = chat2.get("gateway", {})
check("forced model used first (initial_model)",
      gw2.get("initial_model") == "deepseek-v4-flash", gw2.get("initial_model"))
check("forced final model valid (forced or escalated)",
      chat2.get("model") in ("deepseek-v4-flash", "qwen3.8-flash"), chat2.get("model"))
check("forced answer non-empty", bool(chat2.get("choices", [{}])[0].get("message", {}).get("content")))

print("[8c] unregistered model -> 404")
code, err3 = call("POST", "/v1/chat/completions", key=KEY, body={
    "model": "not-mine", "messages": [{"role": "user", "content": "hi"}]}, expect=404)
check("404 mentions not registered", "not registered" in str(err3.get("detail", "")))

# --------------------------------------------------- 9. tenant isolation
print("[9] Tenant isolation")
code, cust2 = call("POST", "/v1/customers", body={"customer_id": CID_B, "name": "Globex"}, expect=201)
KEY2 = cust2.get("api_key", "")
code, lst3 = call("GET", "/v1/models", key=KEY2, expect=200)
check("globex sees 0 models", lst3.get("models") == [], str(lst3.get("models")))
code, err4 = call("GET", "/v1/models/qwen3.8-flash", key=KEY2, expect=404)
check("globex cannot read acme model (404)", "not registered" in str(err4.get("detail", "")),
      str(err4.get("detail")))
code, an2 = call("GET", "/v1/analytics", key=KEY2, expect=200)
check("globex analytics scoped (0 requests)", (an2.get("analytics", {}).get("total_requests") in (0, None)),
      json.dumps(an2.get("analytics", {}))[:120])

# --------------------------------------------------- 10. analytics + health
print("[10] Analytics + health")
code, an = call("GET", "/v1/analytics", key=KEY, expect=200)
a = an.get("analytics", {})
check("acme analytics has requests", (a.get("total_requests") or 0) >= 2, f"total={a.get('total_requests')}")
check("analytics has net savings fields",
      all(k in a for k in ("savings_usd", "control_plane_cost_usd", "net_savings_usd", "net_savings_direction")),
      str({k: a.get(k) for k in ("savings_usd", "control_plane_cost_usd", "net_savings_usd")}))
code, h = call("GET", "/v1/health", key=KEY, expect=200)
check("health: models_registered=3", h.get("models_registered") == 3, str(h.get("models_registered")))
check("health: models_priced=2", h.get("models_priced") == 2, str(h.get("models_priced")))
check("health: control_plane available", h.get("control_plane", {}).get("available") is True,
      f"model={h.get('control_plane', {}).get('model')}")
check("health: pricing catalog", (h.get("pricing_catalog_size") or 0) > 0)

# --------------------------------------------------- 11. delete + cleanup
print("[11] Delete model")
code, _ = call("DELETE", f"/v1/models/smoke-mystery-{SUF}", key=KEY, expect=200)
code, err5 = call("GET", f"/v1/models/smoke-mystery-{SUF}", key=KEY, expect=404)
check("deleted model gone", "not registered" in str(err5.get("detail", "")))

# --------------------------------------------------- summary
print()
print("=" * 70)
print(f"RESULT: {'PASS' if not FAIL else 'FAIL'}  ({len(PASS)} passed, {len(FAIL)} failed)")
if FAIL:
    print("Failed checks:")
    for f in FAIL:
        print(f"  - {f}")
print("=" * 70)
sys.exit(0 if not FAIL else 1)
