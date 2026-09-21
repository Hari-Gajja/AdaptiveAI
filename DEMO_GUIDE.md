# Demo Guide

End-to-end walkthrough of the multi-tenant gateway: create a customer, connect
models, route a request, read the honest numbers. No frontend needed —
everything is `curl`/PowerShell against `http://localhost:8000`.

## 0. Start the backend

```powershell
cd llm-cost-optimizer
Copy-Item .env.example .env      # set OPENCODE_API_KEY for live control plane
pip install -r backend\requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Optional: `MONGODB_URI` (Atlas) for persistent analytics; otherwise an
in-memory store is used and reported in `/api/analytics`.

Swagger UI: <http://localhost:8000/docs>.

## 1. Provision a customer (key shown once!)

```powershell
$r = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/customers `
     -ContentType "application/json" -Body '{"customer_id":"acme","name":"Acme Corp"}'
$r.api_key                       # gw_... — copy it NOW, it is never shown again
$key = $r.api_key
$H = @{ Authorization = "Bearer $key" }
```

## 2. Connect the customer's own models

Declare prices (or rely on the catalog, or leave unknown — never fabricated):

```powershell
# 1) cheap model, prices declared by the customer
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/models/register `
  -Headers $H -ContentType "application/json" -Body (@{
    model_id="acme-mini"; provider="openai_compatible"
    base_url="https://api.acme.ai/v1"; api_key="sk-acme-1"
    input_per_1M=0.12; output_per_1M=0.48 } | ConvertTo-Json)

# 2) strong model (becomes the frontier tier + baseline)
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/models/register `
  -Headers $H -ContentType "application/json" -Body (@{
    model_id="acme-max"; provider="openai_compatible"
    base_url="https://api.acme.ai/v1"; api_key="sk-acme-2"
    input_per_1M=1.00; output_per_1M=5.00 } | ConvertTo-Json)

# 3) catalog-priced model (id exists in backend/data/pricing_registry.json)
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/models/register `
  -Headers $H -ContentType "application/json" -Body (@{
    model_id="deepseek-v4-flash"; base_url="https://api.acme.ai/v1" } | ConvertTo-Json)

# 4) unpriced model -> pricing_status "unknown" (excluded from cost routing)
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/models/register `
  -Headers $H -ContentType "application/json" -Body (@{
    model_id="acme-mystery"; base_url="https://api.acme.ai/v2" } | ConvertTo-Json)
```

Verify: `Invoke-RestMethod http://localhost:8000/v1/models -Headers $H`

## 3. Test connectivity + auto-profile (never manual tiers)

```powershell
# live ping through the customer's endpoint + decrypted key
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/models/acme-mini/test -Headers $H

# capability profiling job (runs the 24-item test set on THEIR endpoint)
$job = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/models/acme-mini/profile -Headers $H
$job.job_id
Invoke-RestMethod "http://localhost:8000/api/models/profile/jobs/$($job.job_id)"
```

## 4. Route a request — OpenAI-compatible

```powershell
$body = @{
  model = "auto"
  messages = @(
    @{ role="system"; content="You are terse." },
    @{ role="user";   content="What is an API?" })
  max_tokens = 256
} | ConvertTo-Json -Depth 5

$r = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/chat/completions `
     -Headers $H -ContentType "application/json" -Body $body
$r.choices[0].message.content    # the answer
$r.model                          # which of THEIR models was routed
$r.gateway | ConvertTo-Json       # routing + honest cost block
```

The `gateway` block shows: `routed_model`, `requested_model`, cache hit/kind,
`actual_cost_usd`, `baseline_model`/`baseline_cost_usd` (always-best
counterfactual), `savings_usd`/`savings_direction`,
`control_plane_cost_usd`, `net_savings_usd`, `quality_score`, `escalated`,
`decision_reason`.

Drop-in for OpenAI SDKs:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="gw_...")  # gateway key
resp = client.chat.completions.create(model="auto",
    messages=[{"role": "user", "content": "What is an API?"}])
print(resp.model, resp.choices[0].message.content)
```

## 5. Full pipeline with provenance

```powershell
$o = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/optimize `
     -Headers $H -ContentType "application/json" -Body (@{
       prompt="What is an API?"; max_tokens=256 } | ConvertTo-Json)
$o.model_selection.provenance     # nemotron_recommended | deterministic | nemotron_rejected_fallback
$o.model_selection.validation_notes
$o.net_savings_usd                # gross savings MINUS control-plane overhead
```

## 6. Cache + escalation, observed honestly

- Repeat step 4 with the same messages → `gateway.cache_hit: true`,
  `cache_kind: "exact"`, cost `0` — savings labeled `measured`.
- Ask something the cheap model fails (or set `reference_answer` and watch
  quality) → escalation to the next model, `escalated: true`, cost = sum of
  ALL attempts (nothing hidden).
- Customer A's cache is namespaced; customer B never hits A's entries.

## 7. Tenant isolation in 30 seconds

```powershell
$r2 = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/customers `
       -ContentType "application/json" -Body '{"customer_id":"beta","name":"Beta"}'
$H2 = @{ Authorization = "Bearer $($r2.api_key)" }
Invoke-RestMethod http://localhost:8000/v1/models -Headers $H2      # count: 0
Invoke-RestMethod http://localhost:8000/v1/models/acme-mini -Headers $H2   # 404
Invoke-RestMethod http://localhost:8000/v1/analytics -Headers $H2   # only beta's docs
```

## 8. Analytics + health

```powershell
Invoke-RestMethod http://localhost:8000/v1/analytics -Headers $H | ConvertTo-Json -Depth 5
Invoke-RestMethod http://localhost:8000/v1/health      -Headers $H
```

## 9. Dashboard (control center)

```powershell
cd frontend; npm install; npm run dev    # http://localhost:5173 (proxies /api)
```

The dashboard covers the single-tenant platform pipeline (registry, profiler,
benchmark lab, cache, analytics). Gateway tenancy is API-first in this MVP.

## What is real vs mocked in this demo

| Component | Status |
|---|---|
| Customer provisioning, key hashing, auth | real |
| Credential encryption at rest + redaction | real |
| Pricing resolution (declared / catalog / unknown) | real |
| Hybrid routing (Nemotron → validator → fallback) | real (needs `OPENCODE_API_KEY`; without it → `provenance: deterministic`) |
| Cache (exact/semantic/context, per-customer namespace) | real (fakeredis/in-memory) |
| Quality scoring + escalation + cost math | real |
| Analytics (per-customer scoping) | real (memory or MongoDB) |
| **Customer provider calls** | **point at YOUR endpoint** — the demo scripts use `api.acme.ai` placeholders; register a real OpenAI-compatible endpoint (OpenAI, vLLM, LiteLLM, OpenRouter…) to see live generation |
| Profiling against customer endpoints | real job machinery; needs a reachable endpoint |

Nothing is fabricated: unpriced models stay `unknown`, savings are labeled
`measured`/`estimated`, and control-plane overhead is subtracted from savings.
