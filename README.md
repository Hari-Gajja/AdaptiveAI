# Adaptive Multi-LLM Cost Optimizer

**Minimum capable intelligence for every request — profiled, routed, verified, measured.**

Organizations connect the LLM models they already have. The system learns each
model's capabilities, understands every request, selects the cheapest model
capable of solving it, caches reusable context, verifies answer quality,
escalates when necessary, and measures the actual cost/quality trade-off
against an always-best-model baseline.

```
PROFILE → UNDERSTAND → FILTER → OPTIMIZE → GENERATE → VERIFY → ESCALATE → MEASURE → LEARN
```

## Problem

Every request sent to the strongest (most expensive) model wastes money; every
request sent to a weak model risks quality. Cost savings claimed without
quality measurement are not optimization.

## Solution

An intelligent gateway (FastAPI) in front of multiple LLM APIs (OpenCode Go)
plus a React control center:

- **Model Registry** — org configures any models (no cheap/frontier labels).
- **Model Profiler** — measures per-category capability scores on our own
  24-item test set. Scores are *benchmark performance on our set*, never
  claimed as universal intelligence scores.
- **Task Analyzer** — transparent heuristics: task type, difficulty 0–1,
  confidence, required capabilities + thresholds.
- **Smart Router** — minimize `Cost(m)` subject to
  `ExpectedQuality(m, task) ≥ RequiredQuality(task)`, driven by **task level**
  (easy → cheapest qualifier; medium → cheapest qualifier with a safe
  capability margin, else strongest; hard → strongest qualifier directly, no
  cheap-first gamble). Low confidence (< 0.60) picks the safest qualifier;
  nothing qualifies → flagged strongest-fallback. Every pick is bounded by the
  **baseline price tier** so optimized spend never exceeds the always-best
  counterfactual.
- **Quality Evaluator** — deterministic, zero extra LLM calls:
  `0.5·correctness + 0.3·relevance + 0.2·completeness`, labeled `reference`
  (grounded) or `estimated` (heuristic, never ground truth).
- **Escalation** — quality below threshold retries the next-best model
  (capped attempts, honest summed cost), preferring in-tier models and only
  reaching above-baseline-tier models as a last resort.
- **Prompt cache** — in-memory: exact-prompt hits skip the LLM (savings
  `measured`); same-context/new-question hits count avoided tokens (savings
  `estimated`). Never conflated.
- **Cost engine** — actual spend + counterfactual always-best baseline
  (measured tokens × best-model pricing; no duplicate expensive calls).
- **Token optimizer** — free, deterministic: prompt normalization (code fences
  preserved), chars/4 token estimation, and predicted output budgets
  (128/256/512) so short answers don't pay for a 512-token allowance. Cache
  hits are checked BEFORE the LLM classifier, so a hit avoids the classifier
  call entirely (`classifier_calls_avoided_exact/semantic`).
- **Benchmark Lab** — 50 reference-scored queries across 10 categories;
  baseline quality measured on a deterministic n=5 sample. Plus a
  naive-vs-optimized token-efficiency benchmark (fixed 1024-token budget vs
  predicted budget + templates + normalization).

## Architecture

```
React (Vite) ──REST──▶ FastAPI ──▶ Optimizer ──┬──▶ Cache check
                                               ├──▶ Control plane (OpenCode)
                                               │     ├─ classifier (task type/difficulty)
                                               │     ├─ cache verifier (veto-only)
                                               │     └─ LLM evaluator (subjective only)
                                               ├──▶ Task Analyzer → Router
                                               ├──▶ OpenCode Go provider
                                               ├──▶ Quality → Escalate
                                               ├──▶ Cost + baseline
                                               └──▶ MongoDB (Atlas) / memory fallback
```

### Control plane (Phase 8)

The optimizer now uses a small, cheap OpenCode model as a **control plane** for
three jobs, reusing the exact same provider module as task generation:

1. **Classifier** — replaces the keyword heuristic with an LLM call that
   returns `{task_type, difficulty, confidence}` as strict JSON. On any
   failure (timeout, malformed JSON, disabled, unpriced model) it falls back
   to the legacy deterministic analyzer and the ledger is marked `degraded`.
2. **Cache verifier** — when a semantic-cache candidate passes ALL
   deterministic safety gates, an optional LLM double-check can **veto** the
   reuse. It can never approve a reuse the gates blocked — it only runs after
   the gates pass and a veto simply falls through to normal routing.
3. **LLM evaluator** — for **subjective** tasks only (no reference answer,
   non-math), an LLM judge grades correctness/relevance. Math and
   reference-scored tasks keep deterministic scoring.

**Cost accounting is honest:** every control-plane call's tokens are recorded
in a per-request `ControlPlaneLedger` and priced with the control-plane
model's registry rates. The API reports `control_plane_cost_usd` and
`total_cost_incl_cp_usd`; the benchmark reports `net_savings_pct` = savings
minus control-plane overhead. Usage is provider-reported when available,
otherwise estimated at chars/4 and flagged `usage_estimated` — never
fabricated.

Per-request A/B knobs (used by the Benchmark Lab mode selector):
`classifier_backend` (`opencode` | `legacy_ml`), `quality_check_mode`
(`off` | `benchmark` | `live`), `cache_verify` (bool).

## Benchmark methodology (read before citing numbers)

- Optimizer runs all N queries live; costs measured.
- Baseline cost is counterfactual (measured tokens × baseline pricing).
- Baseline quality is measured on a deterministic sample (n=5), method labeled
  in every result payload.
- Cache cold-started; repeated-context items measure warm-up honestly.
- Lexical overlap scoring caps below 1.0 when prompt vocabulary differs from
  reference vocabulary by design (e.g. English prompt, code-only reference) —
  it compares models relatively on one scale. Upgrade path: LLM-as-judge.

## Latest measured result

The dashboard and this README never hard-code benchmark numbers. Every metric
shown is the latest stored run:

- **Dashboard** — Benchmark Lab tab reads `GET /api/benchmark/latest` (the most
  recent stored document) and refreshes on every new run.
- **CLI / scripts** — `GET /api/benchmark/latest` returns the full payload:
  baseline vs optimizer cost, quality (reference-scored), retention, routing
  mix, escalation and cache-hit rates.
- **Re-run any time** from Benchmark Lab; the stored document is replaced and
  every consumer updates automatically.

## Setup

```powershell
cd llm-cost-optimizer
Copy-Item .env.example .env   # then set OPENCODE_API_KEY (https://opencode.ai/auth)
pip install -r backend\requirements.txt
# Optional: set MONGODB_URI to Atlas; otherwise an in-memory store is used.

# Backend (from llm-cost-optimizer\)
uvicorn backend.main:app --reload --port 8000

# Frontend (from llm-cost-optimizer\frontend\)
npm install
npm run dev   # http://localhost:5173 (proxies /api → :8000)
```

Phase verification scripts (no API key needed except test_provider live checks):

```powershell
python backend\test_registry.py
python backend\test_router.py
python backend\test_quality.py
python backend\test_cache_cost.py
python backend\test_profiler.py
python backend\test_benchmark.py
python backend\test_control_plane.py
python backend\test_token_optimizer.py
```

## Environment variables

| Var | Purpose |
|---|---|
| `OPENCODE_API_KEY` | OpenCode Go key (never touches the frontend) |
| `OPENCODE_BASE_URL` | Default `https://opencode.ai/zen/go/v1` |
| `MODEL_A_ID` / `MODEL_B_ID` | Seed registry models |
| `OPENCODE_SESSION_ID` | Sent as `x-opencode-session` for provider caching |
| `MONGODB_URI` / `DATABASE_NAME` | Atlas (optional; memory fallback otherwise) |
| `QUALITY_THRESHOLD` | Escalation bar, default `0.75` |
| `OPENCODE_ENABLED` | Control-plane master switch (default `true`) |
| `OPENCODE_MODEL` | Control-plane model (default `deepseek-v4-flash`) |
| `OPENCODE_TIMEOUT_SECONDS` | Per-call CP timeout (default `20`) |
| `CLASSIFIER_BACKEND` | `opencode` or `legacy_ml` (default `opencode`) |
| `QUALITY_CHECK_MODE` | `off` / `benchmark` / `live` (default `live`) |
| `CACHE_VERIFY_ENABLED` | Veto-only semantic-reuse verifier (default `true`) |
| `CONTROL_PLANE_PROMPT_MAX_CHARS` | CP prompt clip (default `1200`) |
| `CLASSIFIER_MAX_OUTPUT_TOKENS` / `VERIFIER_MAX_OUTPUT_TOKENS` / `EVALUATOR_MAX_OUTPUT_TOKENS` | CP output budgets (50/40/80) |
| `PROMPT_TEMPLATES_ENABLED` | Task-aware minimal system prompts (default `true`) |
| `TOKEN_OPT_BASELINE_OUTPUT_BUDGET` | Naive-arm output budget for token-savings math (default `1024`) |

## API endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | phase + models |
| POST | `/api/chat` | full pipeline (`prompt`, `context?`, `force_model?`, `reference_answer?`, `max_attempts?`, `use_cache?`) |
| POST | `/api/route/preview` | free dry-run (no LLM call) |
| GET/POST | `/api/models` | registry list / create |
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
| POST | `/api/benchmark/token-efficiency` | start naive-vs-optimized token benchmark (`limit`) |
| GET | `/api/benchmark/token-efficiency/{id}` | token benchmark progress/result |
| POST | `/api/test/generate` | single-model smoke test |

## MongoDB schema (db `llm_optimizer`)

- `requests`: prompt, task_type, difficulty_score, confidence,
  required_capabilities, selected/initial/final model, cache_hit/kind,
  tokens, actual/baseline cost, quality_score/method, escalated, latency,
  capability_source, timestamp.
- `benchmarks`: full benchmark result documents (see runner).
- Registry/profiles stay in `backend/data/*.json` (single-writer MVP).

## Limitations

- Quality is lexical (see methodology), not semantic; strict 0.75 bar drives
  high escalation rates on terse answers.
- Capability profiles measured on 4 samples/category — expect noise; the UI
  labels measured vs estimated everywhere.
- In-memory cache + JSON registry are single-process (fine for the hackathon,
  not for multi-replica prod).
- Baseline quality from an n=5 sample — reported with n, not hidden.
- Control-plane usage is provider-reported when available, otherwise estimated
  at chars/4 and flagged `usage_estimated`; CP latency adds to per-request
  latency (bounded by `OPENCODE_TIMEOUT_SECONDS`).
- The LLM evaluator is a judge, not ground truth; subjective scores are
  labeled `llm_judge` and can disagree with human grading.

## Future improvements

- Semantic (embedding) similarity for the cache tier.
- More profiler items per category; scheduled re-profiling from live outcomes
  (the LEARN step is currently manual re-run).
- Provider-side prompt caching wired to measured cache-read billing.
- Per-model latency tracking in routing; budget-constrained routing mode.
- Auth + multi-tenant quotas.
- Aggregate control-plane spend in analytics (currently per-request ledger +
  benchmark totals).
