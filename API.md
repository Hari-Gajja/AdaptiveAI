# API Reference

Two API surfaces share one FastAPI app:

| Surface | Prefix | Auth | Purpose |
|---|---|---|---|
| **Gateway API** | `/v1` | Gateway API key (`gw_...`) | Multi-tenant: customers connect their own LLM providers and route through the gateway |
| **Platform API** | `/api` (+ `/health`) | none (demo) | Single-tenant control center: dashboard, benchmark lab, org-level registry |

Interactive docs: `http://localhost:8000/docs` (Swagger UI, generated from the code).

---

## Authentication (Gateway API)

Every `/v1` request (except `POST /v1/customers`) requires a gateway API key:

```
Authorization: Bearer gw_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

or equivalently the header `x-api-key: gw_...`.

- Keys are issued **once** by `POST /v1/customers` and stored only as a
  SHA-256 hash. A lost key cannot be recovered — create a new customer or
  rotate (see SECURITY.md).
- Disabled customers get `401` on every endpoint.

Error shape: `{"detail": "<human-readable reason>"}`.

---

## Gateway API (`/v1`)

### Customers

#### `POST /v1/customers` — provision a customer (no auth)

```json
{"customer_id": "acme", "name": "Acme Corp"}
```

`customer_id`: `[a-z0-9][a-z0-9._-]{2,63}` (lowercased/stripped server-side).

`201` — the gateway API key is returned **exactly once**:

```json
{
  "customer": {"customer_id": "acme", "name": "Acme Corp",
               "enabled": true, "created_at": "...", "updated_at": "..."},
  "api_key": "gw_0f3c...c1",
  "note": "Store this key securely — it is shown only once."
}
```

Errors: `409` duplicate id · `422` invalid id shape.

#### `GET /v1/customers/me`

```json
{"customer": {"customer_id": "acme", "name": "Acme Corp", "enabled": true}}
```

### Models (the customer's own pool)

#### `POST /v1/models/register`

Connect one of the customer's own models. The gateway never labels tiers —
capability is auto-profiled (`POST /v1/models/{id}/profile`).

```json
{
  "model_id": "acme-mini",
  "provider": "openai_compatible",          // openai | openai_compatible | custom
  "base_url": "https://api.acme.ai/v1",     // http(s), trailing "/" stripped
  "api_key": "sk-...",                      // optional; encrypted at rest, never returned
  "display_name": "Acme Mini",
  "input_per_1M": 0.10,                     // optional declared pricing (USD / 1M tokens)
  "output_per_1M": 0.40,
  "cached_per_1M": 0.02,                    // optional
  "context_window": 200000                  // optional, default 200000
}
```

Pricing resolution (never fabricated):

1. **customer_declared** — both `input_per_1M` and `output_per_1M` present and `> 0` → `pricing_status: "configured"`
2. **catalog** — model id found in `backend/data/pricing_registry.json` → `configured`
3. **unknown** — otherwise `pricing_status: "unknown"`, `priced: false`. Unpriced
   models are excluded from cost-optimal routing until prices exist.

`201` → public view (no `api_key`, no `credential_reference`):

```json
{"customer_id": "acme", "model_id": "acme-mini", "provider": "openai_compatible",
 "base_url": "https://api.acme.ai/v1", "enabled": true, "display_name": "Acme Mini",
 "input_per_1M": 0.10, "output_per_1M": 0.40, "pricing_status": "configured",
 "pricing_source": "customer_declared", "priced": true, "has_credential": true,
 "context_window": 200000, "profile_status": "unprofiled", "blocked": false,
 "last_test_status": "", "created_at": "...", "updated_at": "..."}
```

Errors: `409` duplicate model id for this customer · `422` bad shape.

#### `GET /v1/models?enabled_only=false`

`{"models": [...public views...], "count": n}` — always scoped to the caller.

#### `GET /v1/models/{model_id}` · `404` if not the caller's model.

#### `PUT /v1/models/{model_id}`

Partial update. `api_key` present → re-encrypt and swap the credential
reference (old secret deleted). Prices present → pricing re-derived.

```json
{"input_per_1M": 0.12, "output_per_1M": 0.48, "api_key": "sk-rotated"}
```

#### `DELETE /v1/models/{model_id}`

`{"deleted": "acme-mini"}` — the stored credential is deleted too.

#### `POST /v1/models/{model_id}/test`

Live connectivity test through the customer's own endpoint + decrypted key.
Runs a tiny `ping` generation. Records `last_test_status`/`last_test_detail`.

```json
{"ok": true, "detail": "reply: 'pong'", "latency_ms": 412, "model_id": "acme-mini"}
```

Errors: `400` model has no `base_url` · `404` unknown · `502` provider error.

#### `POST /v1/models/{model_id}/profile`

Auto-profile capability on the customer's model (never manual tiers). Runs the
standard capability test set through the customer's endpoint in a background
job; sets `profile_status: "profiling"`.

```json
{"job_id": "prof_...", "models": ["acme-mini"],
 "note": "poll GET /api/models/profile/jobs/{job_id}"}
```

Errors: `400` disabled or no `base_url` · `404` unknown.

### Inference

#### `POST /v1/optimize` — full pipeline

```json
{
  "prompt": "What is an API?",
  "max_tokens": 512,            // null = auto output budget (128/256/512)
  "temperature": 0.2,
  "context": null,              // optional reusable context
  "reference_answer": null,     // optional; enables grounded quality scoring
  "max_attempts": 2,
  "use_cache": true,
  "force_model": null           // pin a specific customer model
}
```

Pipeline: cache check (namespaced per customer) → classify → hybrid selection
(Nemotron recommends, deterministic validator decides) → generate on the
customer's endpoint → quality check → escalate → honest cost math.

`200` response (abridged):

```json
{
  "customer_id": "acme",
  "answer": "...",
  "selected_model": "acme-mini",
  "initial_model": "acme-mini",
  "escalated": false,
  "cache_hit": false, "cache_kind": "miss",
  "tokens_avoided": 0, "cache_saved_usd": 0.0,
  "input_tokens": 10, "output_tokens": 10,
  "quality_score": 0.78, "quality_passed": true,
  "verification_status": "verified",
  "attempts": [{"model_id": "acme-mini", "quality": 0.78, "passed": true,
                "cost_usd": 0.0000058, "latency_ms": 12}],
  "actual_cost_usd": 0.0000058,
  "baseline_model": "acme-max", "baseline_cost_usd": 0.000051,
  "savings_usd": 0.0000452, "savings_pct": 88.6, "savings_direction": "savings",
  "control_plane_cost_usd": 0.0,
  "net_savings_usd": 0.0000452, "net_savings_pct": 88.6,
  "net_savings_direction": "savings",
  "latency_ms": 12,
  "analysis": {"task_type": "general", "difficulty_score": 0.1,
               "confidence": 0.85, "required_capabilities": ["general"]},
  "routing": {"selected_model": "acme-mini", "decision_reason": [...],
              "capability_source": "priors"},
  "model_selection": {"provenance": "deterministic", "reason": "...",
                      "recommendation": null, "validation_notes": []},
  "token_report": {"task_model": {...}, "control_plane": {...}},
  "control_plane": {"status": "disabled", "calls": []},
  "request_id": "..."
}
```

`model_selection.provenance` — see ROUTING.md:
`nemotron_recommended` | `deterministic` | `nemotron_rejected_fallback`.

Errors: `400` empty prompt · no models registered · unpriced pool
(`NoCapableModel`) · `502` permanent provider failure (e.g. bad provider key).

#### `POST /v1/chat/completions` — OpenAI-compatible surface

Drop-in for OpenAI SDK clients. `model: "auto"` lets the gateway route over
the customer's pool; a specific model id must belong to the customer.

```json
{"model": "auto",
 "messages": [{"role": "system", "content": "You are terse."},
              {"role": "user", "content": "What is an API?"}],
 "max_tokens": 512, "temperature": 0.2}
```

`200` — OpenAI `chat.completion` shape plus a `gateway` extension block
(OpenAI clients ignore unknown fields):

```json
{
  "id": "gwchat-acme-mini-12345678",
  "object": "chat.completion",
  "created": 1757100000,
  "model": "acme-mini",
  "choices": [{"index": 0,
               "message": {"role": "assistant", "content": "..."},
               "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
  "gateway": {
    "customer_id": "acme", "routed_model": "acme-mini", "requested_model": "auto",
    "cache_hit": false, "cache_kind": "miss",
    "actual_cost_usd": 0.0000058,
    "baseline_model": "acme-max", "baseline_cost_usd": 0.000051,
    "savings_usd": 0.0000452, "savings_direction": "savings",
    "control_plane_cost_usd": 0.0, "net_savings_usd": 0.0000452,
    "quality_score": 0.78, "escalated": false,
    "decision_reason": [...]
  }
}
```

Errors: `400` empty messages · no models · unpriced pool · `404` model not
registered for this customer · `502` permanent provider failure.

### Analytics & health

#### `GET /v1/analytics`

Customer-scoped only — a customer never sees another tenant's numbers.

```json
{"customer_id": "acme",
 "analytics": {"total_requests": 3, "total_cost_usd": ..., "savings_usd": ...,
               "cache_hit_rate": ..., "escalation_rate": ...},
 "routing": {"total_requests": 3, "by_model": {"acme-mini": 2, "acme-max": 1}}}
```

#### `GET /v1/health`

```json
{"status": "ok", "customer_id": "acme", "customer_enabled": true,
 "models_registered": 5, "models_priced": 3, "models_pricing_unknown": 2,
 "control_plane": {"enabled": true, "model": "nemotron-3.5-lightning-free",
                   "available": true},
 "pricing_catalog_size": 12, "cache_backend": "memory"}
```

---

## Platform API (`/api`) — single-tenant control center

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | phase + models |
| POST | `/api/chat` | full pipeline (`prompt`, `context?`, `force_model?`, `reference_answer?`, `max_attempts?`, `use_cache?`) |
| POST | `/api/route/preview` | free dry-run (no LLM call) |
| GET/POST | `/api/models` | org registry list / create |
| GET/PUT/DELETE | `/api/models/{id}` | read / update / delete |
| GET | `/api/models/profiles` | measured + priors per model |
| POST | `/api/models/profile` | profile all (background job) |
| POST | `/api/models/{id}/profile` | profile one (background job) |
| GET | `/api/models/profile/jobs/{id}` | profiler progress |
| GET | `/api/models/control-plane` | CP config, budgets, health, lifetime stats |
| GET | `/api/analytics` | totals from request history |
| GET | `/api/routing-stats` | per-model / per-task distribution |
| GET | `/api/requests/{id}` | stored decision record |
| GET | `/api/cache/stats` | hits, avoided tokens, measured vs estimated savings |
| POST | `/api/cache/clear` | reset cache |
| GET | `/api/benchmark/queries` | dataset inventory |
| POST | `/api/benchmark/run` | start benchmark job (`limit`, `baseline_sample_n`, `mode`) |
| GET | `/api/benchmark/jobs/{id}` | progress |
| GET | `/api/benchmark/latest` | last completed result |
| POST | `/api/benchmark/token-efficiency` | naive-vs-optimized token benchmark (`limit`) |
| GET | `/api/benchmark/token-efficiency/{id}` | token benchmark progress/result |
| POST | `/api/test/generate` | single-model smoke test |

---

## Status codes (conventions)

| Code | Meaning |
|---|---|
| `400` | client-fixable request problem (empty prompt, no models, unpriced pool, missing base_url) |
| `401` | missing/invalid/disabled gateway key |
| `404` | unknown resource (always tenant-scoped — a foreign id looks like a missing one) |
| `409` | duplicate (customer id, model id) |
| `422` | request shape validation (FastAPI/Pydantic) |
| `502` | permanent provider failure surfaced honestly (auth, malformed response) |

Transient provider failures (429/5xx/timeout/transport) are NOT surfaced —
the optimizer falls through to the next model in the escalation ladder.
