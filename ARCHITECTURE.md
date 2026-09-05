# Architecture

One request, end to end. Every box is a module under `backend/`.

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
