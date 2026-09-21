# Security

Threat model: the gateway holds **two kinds of secrets** and must never leak
either, while accepting **customer-supplied endpoints** (an SSRF surface).

| Secret | Who owns it | Stored as | Where |
|---|---|---|---|
| Gateway API key (`gw_...`) | the customer (to call OUR gateway) | SHA-256 hash | `backend/data/customers.json` |
| Provider API key (`sk-...`) | the customer (for THEIR LLM endpoint) | XOR-keystream encrypted blob | `backend/data/customer_keys.json` |

---

## Gateway API keys

- Format: `gw_` + 32 hex chars (128 bits of entropy, `secrets.token_hex`).
- Issued **exactly once** by `POST /v1/customers`; only the SHA-256 hash is
  persisted. A lost key cannot be recovered — register a new customer.
- Sent as `Authorization: Bearer gw_...` or `x-api-key: gw_...`.
- Authentication (`backend/api/gateway.py`) rejects non-`gw_` keys, unknown
  hashes, and disabled customers with `401`.
- Keys are **never** returned by any endpoint, never written to logs, and never
  included in stored analytics documents.

## Provider credentials

- Accepted once at `POST /v1/models/register` / `PUT /v1/models/{id}`.
- Encrypted at rest with a keystream derived from `GATEWAY_SECRET_KEY`
  (repeated `SHA-256(secret ‖ nonce ‖ counter)` XOR stream — adequate at-rest
  obfuscation for the MVP; a production system would use a KMS/HSM).
- Referenced everywhere by an opaque `credential_reference` (`cred_` + 16 hex
  chars) — the registry never stores or transmits the key.
- Decrypted only in memory, only at call time, only to build the provider
  adapter for the customer's own endpoint.
- **Never returned** by any API response (public views pop the field and add
  `has_credential: true/false` instead).
- **Never logged.** Error messages are redacted before raising:
  `ProviderError` strips `Bearer <token>` and `sk-...` patterns
  (`backend/providers/base.py`).
- Deleted when the model is deleted or the key is rotated (rotation stores a
  new blob and removes the old one; a failed registration deletes the orphaned
  secret).

## Redaction

`ProviderError.__init__` redacts before storing the message:

```
Authorization: Bearer sk-secret  →  Authorization: Bearer [REDACTED]
sk-AbCd1234...                   →  sk-[REDACTED]
```

The same redaction applies to messages surfaced through HTTP `502` responses
and stored `last_test_detail` (additionally truncated to 300 chars).

## SSRF caveat (documented, accepted for MVP)

Customers supply `base_url`, so the gateway will issue POSTs to
customer-controlled URLs. This is an inherent SSRF surface: a customer can
point their model at internal services and observe response shapes/timing.

Accepted for the hackathon MVP because:

- each customer can only target endpoints **they registered** (tenant
  isolation bounds the blast radius to the customer's own actions);
- the gateway sends only the customer's **own** decrypted key to their own
  endpoint — platform credentials (the OpenCode key) are never attached to
  customer-provider calls;
- responses are parsed as OpenAI-shaped JSON and truncated before storage.

Production mitigations (not implemented): URL allow/deny lists, private-range
IP blocking, egress proxies, per-customer network policies.

## Tenant isolation

- Every registry read/write, cache lookup (`namespace = customer_id`), and
  analytics query is scoped by `customer_id`.
- A foreign model id is indistinguishable from a missing one (`404`) — no
  existence oracle across tenants.
- The hybrid selector's candidate list is built only from the caller's pool;
  the deterministic validator re-checks ownership (defense in depth).
- Analytics (`GET /v1/analytics`) filters stored request documents by
  `customer_id`; a customer never sees another tenant's numbers.

## Data handling

- Analytics documents store a truncated prompt (first 2000 chars) plus
  routing/cost metadata — never credentials.
- Registry/secret files are written atomically (temp file + replace).
- `GATEWAY_SECRET_KEY` has a dev default so the demo runs out of the box;
  **must** be set to a long random value in production (see `.env.example`).
- MongoDB (optional) stores request analytics; credentials never enter it.

## Known limitations (honest list)

- XOR-keystream encryption is obfuscation, not KMS-grade secrecy.
- No rate limiting / quotas per customer yet.
- No audit log of key rotations (the registry `updated_at` is the only trace).
- The demo platform API (`/api`) is unauthenticated by design; only the
  multi-tenant `/v1` surface enforces keys.
