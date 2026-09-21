# Model Registry

Two registries, one principle: **prices and capabilities come from data, never
from labels.**

| Registry | File | Scope |
|---|---|---|
| Platform registry | `backend/data/models.json` | single-tenant `/api` pipeline (the demo org's models) |
| Customer registry | `backend/data/customer_models.json` | multi-tenant `/v1` gateway — each customer's own pool |

Supporting stores:

| Store | File | Contents |
|---|---|---|
| Customers | `backend/data/customers.json` | customer_id, name, `api_key_hash` (SHA-256), enabled |
| Credentials | `backend/data/customer_keys.json` | `credential_reference` → encrypted provider key |
| Pricing catalog | `backend/data/pricing_registry.json` | curated global prices with `source` provenance |
| Capability profiles | `backend/data/profiles.json` (or `LLMO_PROFILES_FILE`) | measured per-category scores |

---

## Customer model entry (`/v1/models`)

```json
{
  "customer_id": "acme",
  "model_id": "acme-mini",
  "provider": "openai_compatible",
  "base_url": "https://api.acme.ai/v1",
  "credential_reference": "cred_1a2b3c4d",   // internal only — never returned
  "enabled": true,
  "display_name": "Acme Mini",
  "input_per_1M": 0.10,
  "output_per_1M": 0.40,
  "cached_per_1M": null,
  "pricing_status": "configured",
  "pricing_source": "customer_declared",
  "context_window": 200000,
  "profile_status": "profiled",
  "blocked": false,
  "last_test_status": "ok",
  "last_test_detail": "reply: 'pong'",
  "created_at": "...", "updated_at": "..."
}
```

### Field rules

- `model_id` — `[A-Za-z0-9][A-Za-z0-9._-]{0,79}`; unique **per customer**
  (two tenants may both register `my-model`).
- `provider` — `openai` (defaults base_url to `https://api.openai.com/v1`),
  `openai_compatible` (any `/chat/completions` endpoint: vLLM, LiteLLM,
  OpenRouter, private gateways), `custom` (same wire shape, base_url required).
- `base_url` — must be `http(s)://...`; trailing `/` stripped. Empty is allowed
  at registration but the model cannot be tested or profiled until set.
- `credential_reference` — opaque id into the encrypted secret store. The
  plaintext key is accepted **once** at register/update time and never leaves
  the store (see SECURITY.md).
- `blocked` — admin/health flag; a blocked model is excluded from routing but
  stays visible in the registry.

## Pricing resolution (never fabricated)

Priority order:

1. **`customer_declared`** — the customer declared BOTH `input_per_1M` and
   `output_per_1M` (each `> 0`) at register/update time →
   `pricing_status: "configured"`.
2. **`catalog`** — the model id exists in `backend/data/pricing_registry.json`
   → `configured`, prices copied from the catalog.
3. **`unknown`** — otherwise. `pricing_status: "unknown"`, `priced: false`,
   prices stay `null`.

Consequences of `unknown`:

- The model is **excluded from cost-optimal routing** (`NoCapableModel` → 400
  when the whole pool is unpriced) because savings math would be fabricated.
- It can still be tested, profiled, and listed.
- The fix is data, not guessing: declare prices (`PUT /v1/models/{id}`) or add
  the model to the catalog file.

Every catalog entry carries a `source` string (e.g. provider pricing page,
date) so the dashboard can show provenance. Free models are allowed with
price `0.0` and `free: true` (the Nemotron control-plane model is free).

## Capability profiling (auto, never manual)

Customers **never** label models cheap/mid/frontier. Two mechanisms replace
manual tiers:

### 1. Measured profiles

`POST /v1/models/{id}/profile` runs the standard capability test set
(`backend/core/profiler.py`, 24 items across categories) through the
customer's own endpoint and stores per-category scores in
`backend/data/profiles.json`. `profile_status` moves
`unprofiled → profiling → profiled` (or `stale` after re-registration).

### 2. Priors for known ids

`backend/core/capabilities.py` carries conservative priors for well-known
model ids (e.g. deepseek family) and a neutral default (`0.70` per category)
for unknown ids. Routing prefers **measured** scores over priors; the
`capability_source` field in every routing decision says which was used
(`measured` | `priors`).

## Dynamic cost tiers

Cost tiers are **derived from the customer's own pool** at request time
(`backend/core/tiers.py`) — never hard-coded:

| Pool size | Tiers |
|---|---|
| 1 model | single tier (`mid`) |
| 2 models | `cheap` / `frontier` split at the median price |
| 3+ models | `cheap` / `mid` / `frontier` by price terciles |

Tier ordering uses the blended price `(input + output) / 2` per 1M tokens.
Free models (price 0) sort as cheapest. Unpriced models get tier `unknown`
and are excluded from tier-based reasoning (but can still be validated on
capability alone by the hybrid selector).

Tiers are used to sanity-check the Nemotron recommendation and to bound
escalation (escalating above the baseline tier is the one way optimized spend
can exceed the always-best counterfactual).

## Lifecycle

```
register ──▶ test (connectivity) ──▶ profile (capability) ──▶ routeable
    │                                     │
    ├── update (prices / rotate key)      └── re-profile after model changes
    ├── block / unblock (admin flag)
    └── delete (credential deleted too)
```

A model is **routeable** when: `enabled` ∧ ¬`blocked` ∧
`pricing_status == "configured"` ∧ context window fits the request ∧
capabilities satisfy the task's requirements.
