# Architecture

One request, end to end. Every box is a module under `backend/`.

Two surfaces share one FastAPI app:

- **Gateway API (`/v1`)** — multi-tenant. Customers get a gateway API key,
  connect their OWN providers (base_url + key + model_id), and route through
  the gateway (`model="auto"`). See API.md.
- **Platform API (`/api`)** — single-tenant control center (dashboard,
  benchmark lab, org registry).

```
Customer (OpenAI SDK / HTTP, gw_ key)
  │
  ▼
POST /v1/chat/completions | /v1/optimize   (api/v1.py)
  │  auth: gw_ key → SHA-256 hash lookup (api/gateway.py, core/customers.py)
  │
  ▼
┌─────────────────────────── optimizer.run_prompt (core/optimizer.py, gateway mode) ─────────────┐
│  pool = customer's registry (core/customer_registry.py) — THEIR models only                    │
│  credentials decrypted per call (core/secret_store.py) — never logged/returned                 │
│                                                                                                │
│  1. FREE LOCAL WORK      normalize + output budget + legacy analysis (token_optimizer,          │
│                          task_analyzer)                                                        │
│  2. CACHE-FIRST          core/cache.py, namespace = customer_id (exact → semantic gates →       │
│                          veto-only verifier); hits avoid the classifier call                   │
│  3. CLASSIFY             control plane (Nemotron) or legacy fallback (llm/opencode_classifier)  │
│  4. HYBRID SELECT        llm/model_selector.py RECOMMENDS (Nemotron, ~60 tok, JSON only)        │
│                          core/gateway_router.py VALIDATES (exists/owner/enabled/context/        │
│                          capabilities/pricing/not blocked) → else deterministic route()         │
│                          provenance: nemotron_recommended | deterministic |                    │
│                                       nemotron_rejected_fallback                               │
│  5. GENERATE             provider_factory(model_id) → customer's own endpoint                   │
│                          (providers/base.py: OpenAICompatible/OpenAI/Custom adapters)           │
│  6. VERIFY               core/quality.py (+ LLM judge for subjective tasks)                     │
│  7. ESCALATE             next-best candidate, capped by max_attempts and the baseline tier;     │
│                          transient provider failures fall through, permanent → 502             │
│  8. MEASURE              core/cost.py + llm/ledger.py: actual vs always-best baseline,          │
│                          control-plane overhead, net savings                                   │
│  9. LEARN                store ONLY quality-validated answers in the customer's cache namespace │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

The single-tenant `/api/chat` pipeline is the same optimizer without gateway
mode: the org registry (`core/registry.py`) replaces the customer pool, the
platform OpenCode provider replaces `provider_factory`, and the cache uses the
empty namespace.

## Gateway modules (multi-tenant)

| Module | Responsibility |
|---|---|
| `api/v1.py` | `/v1` REST surface: customers, models, test/profile, optimize, chat/completions, analytics, health |
| `api/gateway.py` | `gw_` key auth (Bearer or x-api-key) → `Customer` dependency |
| `core/customers.py` | customer store; keys hashed (SHA-256), shown once |
| `core/customer_registry.py` | per-customer model pool; pricing resolution (declared → catalog → unknown) |
| `core/secret_store.py` | encrypted provider credentials; opaque `credential_reference`s |
| `core/pricing.py` | pricing catalog (`backend/data/pricing_registry.json`); never fabricates |
| `core/tiers.py` | dynamic cheap/mid/frontier tiers derived from the customer's pool |
| `core/gateway_router.py` | deterministic validation of Nemotron's recommendation + fallback routing |
| `llm/model_selector.py` | Nemotron recommender (step 1 of hybrid selection) |
| `providers/base.py` | BaseLLMProvider → OpenAICompatible/OpenAI/Custom adapters; redacting ProviderError |

## Single-tenant pipeline (unchanged)

```
POST /api/chat (api/chat.py)
  │
  ▼
┌─────────────────────────── optimizer.run_prompt (core/optimizer.py) ──────────────────────────┐
│                                                                                               │
│  1. FREE LOCAL WORK (no API calls)                                                            │
│     ├─ normalize_prompt (core/token_optimizer.py)   whitespace collapse, fences preserved      │
│     ├─ predict_output_budget (core/token_optimizer.py)  128/256/512 + signals                  │
│     └─ analyze (core/task_analyzer.py)              legacy heuristic classification            │
│                                                                                               │
│  2. CACHE-FIRST (core/cache.py) — BEFORE the LLM classifier (spec §13)                         │
│     ├─ exact hit      → return stored answer, ledger.calls_avoided_exact += 1                  │
│     ├─ semantic hit   → safety gates → optional LLM verifier (veto-only) → stored answer,      │
│     │                   ledger.calls_avoided_semantic += 1                                     │
│     └─ miss           → fall through                                                           │
│                                                                                               │
│  3. CLASSIFY (only on miss)                                                                   │
│     ├─ opencode classifier (llm/opencode_classifier.py)  {task_type, difficulty, confidence}   │
│     └─ fallback → legacy analyzer, ledger marked degraded                                      │
│                                                                                               │
│  4. ROUTE (core/router.py)                                                                    │
│     minimize Cost(m) subject to ExpectedQuality(m, task) ≥ Required(task)                      │
│     low confidence (<0.60) → safest qualifier; context-window guard (§11)                      │
│                                                                                               │
│  5. GENERATE (providers/opencode.py)                                                          │
│     task-aware minimal system prompt (core/prompt_builder.py) when PROMPT_TEMPLATES_ENABLED    │
│     max_tokens = explicit value or predicted budget                                            │
│                                                                                               │
│  6. VERIFY (core/quality.py)                                                                  │
│     reference scoring, or LLM judge for subjective tasks (llm/opencode_evaluator.py)           │
│     quality < threshold → escalate to next candidate (capped by max_attempts)                  │
│                                                                                               │
│  7. MEASURE (core/cost.py + llm/ledger.py)                                                    │
│     actual cost = Σ attempts; baseline = measured tokens × best-model pricing                  │
│     ControlPlaneLedger: Total Cost = Control Plane + Task Model                                │
│     token report (core/token_analytics.py): task_model / control_plane / avoided / totals      │
│                                                                                               │
│  8. LEARN — store ONLY validated responses in the cache (spec step 6)                          │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Module map

| Module | Responsibility |
|---|---|
| `api/chat.py` | REST surface; passes `max_tokens`/`auto_output_budget`; enriches response with `token_report`, `normalization`, `classifier_calls_avoided` |
| `api/benchmark.py` | Benchmark jobs incl. `POST /api/benchmark/token-efficiency` |
| `core/optimizer.py` | Orchestration; cache-first; escalation; token report assembly |
| `core/task_analyzer.py` | Deterministic classification + token estimates + output budget |
| `core/router.py` | Capability-constrained cost minimization; context-window guard |
| `core/registry.py` | Org's connected models; pricing + context windows; JSON persistence |
| `core/profiler.py` | Measures per-category capability on the 24-item test set |
| `core/quality.py` | Deterministic scoring `0.5·correct + 0.3·relevant + 0.2·complete` |
| `core/cache.py` | Exact / semantic / context tiers; safety gates; stats |
| `core/token_optimizer.py` | Normalization, chars/4 estimation, output budget prediction |
| `core/token_analytics.py` | Per-request token report + benchmark aggregation |
| `core/prompt_builder.py` | Task-aware minimal system prompts |
| `core/cost.py` | Actual + counterfactual baseline cost |
| `llm/opencode_classifier.py` | Control-plane classifier (legacy fallback) |
| `llm/cache_verifier.py` | Veto-only semantic-reuse verifier |
| `llm/opencode_evaluator.py` | LLM-as-judge for subjective tasks |
| `llm/ledger.py` | ControlPlaneLedger — CP token/call accounting |
| `providers/opencode.py` | Single provider module for task + control-plane calls |
| `benchmark/runner.py` | Full benchmark incl. routing/token aggregates |
| `benchmark/token_benchmark.py` | Naive-vs-optimized token-efficiency benchmark |
| `database/mongodb.py` | Atlas store with in-memory fallback |

## Gateway design decisions

- **Customers never label tiers.** Capability is measured by the profiler or
  conservative priors; cost tiers are derived from the customer's own pool at
  request time (`core/tiers.py`).
- **Nemotron recommends, never decides.** The deterministic validator owns the
  decision; provenance is always reported so the dashboard can show why.
- **One provider contract for customer models.** `BaseLLMProvider` adapters
  speak the OpenAI chat/completions dialect, so any OpenAI-compatible endpoint
  works without gateway changes. The platform OpenCode adapter stays untouched.
- **Cache namespacing is the isolation boundary.** `namespace = customer_id`
  on every get/put; a tenant can never read another tenant's entries.
- **Errors are honest.** Permanent provider failures surface as 502 (redacted);
  transient ones fall through the escalation ladder; unpriced pools return a
  clean 400 (`NoCapableModel`), never a fabricated cost.

## Data flow guarantees

- **No fabricated precision.** Provider-reported usage when available; chars/4
  estimate flagged `usage_estimated` otherwise. Every savings number is
  labeled `measured` or `estimated`.
- **Cache-first never lies.** A cache hit reports `analysis.backend ==
  "legacy_ml"` (the free analyzer), never a fake classifier result.
- **Verifier is veto-only.** It runs only after all deterministic gates pass
  and can only block reuse, never approve what gates blocked.
- **Store only validated responses.** `cache.put` happens only when
  `result.quality_passed` — bad answers never poison the cache.
- **New result fields have defaults.** `OptimizerResult` additions keep direct
  constructions (tests, benchmark fakes) valid.

## Why these choices

- **Why cache-first before classification?** The classifier is an LLM call; a
  cache hit makes it pure waste. Checking the cache first avoids that call and
  the saving is counted in `ledger.calls_avoided_exact/semantic`.
- **Why a legacy analyzer still runs on hits?** The dashboard needs
  analysis/routing populated; running the free heuristic keeps it honest
  without spending control-plane tokens.
- **Why store only validated responses?** A cached wrong answer is a wrong
  answer served instantly, forever. The quality gate is the cache's safety.
- **Why one provider module?** Task model and control plane share auth,
  endpoints, usage parsing, and failure semantics — duplicating it would
  guarantee drift.
