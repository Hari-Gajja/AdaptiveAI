# Token Optimization

How the system reduces tokens per request without lying about savings.
Everything here is deterministic and free (no API calls) unless stated.

## 1. Prompt normalization (`core/token_optimizer.py`)

Collapses redundant whitespace outside code fences: runs of spaces/tabs → one
space, trailing spaces dropped, 3+ blank lines → one. **Code fences are
preserved verbatim** — indentation inside ``` blocks is semantic in
Python/YAML; reformatting it would change the task.

Returns measured accounting: `original_chars`, `normalized_chars`,
`original_tokens_estimate`, `normalized_tokens_estimate`, `tokens_saved`,
`compression_ratio`. The Playground shows this per request ("prompt normalized
−N tokens, compression X%").

## 2. Token estimation (`estimate_tokens`)

`len(text) // 4` — the same chars/4 rule the control plane uses when a
provider omits usage. One estimator everywhere; callers needing exact counts
use provider-reported usage and say so.

## 3. Output budget prediction (`predict_output_budget`)

Predicts `max_tokens` from prompt signals instead of a fixed 512:

| Budget | Signals |
|---|---|
| 128 (small) | "brief", "one sentence", "tldr", "yes or no", very short prompt |
| 256 (medium) | default |
| 512 (large) | "in detail", "comprehensive", code hints, list/table requests |

Explicit brevity wins over length hints. The predicted budget feeds both the
generation call and the router's cost model, so cost estimates reflect the
answer length actually requested.

**Wiring:** `POST /api/chat` accepts `max_tokens: int | None` and
`auto_output_budget: bool`. `max_tokens=None` (or `auto_output_budget=true`)
means auto: the optimizer uses the predicted budget. The response reports
`estimated_output_tokens` and `output_budget_signals`.

## 4. Cache-first classifier avoidance (spec §13)

The cache is checked **before** the LLM classifier:

- **Exact hit** → stored answer returned; `ledger.calls_avoided_exact += 1`.
- **Semantic hit** (similarity ≥ threshold AND all safety gates pass AND the
  optional veto-only verifier doesn't block) → stored answer;
  `ledger.calls_avoided_semantic += 1`.

On a hit the free legacy analyzer still runs so analysis/routing stay
populated — but no classifier tokens are spent. The response reports
`classifier_calls_avoided: {"exact": n, "semantic": n}`.

**Why:** the classifier is an LLM call; on a cache hit it is pure waste.
Cache-first turns that waste into an avoided call that we can count.

## 5. Task-aware minimal system prompts (`core/prompt_builder.py`)

When `PROMPT_TEMPLATES_ENABLED=true`, generation messages get a short
task-aware system clause (math/coding/debugging/etc.); plain general
questions pass through untouched. Context goes in the system role; the user
turn stays raw. Templates only ADD a short clause — they never rewrite the
user's prompt.

## 6. Per-request token report (`core/token_analytics.py`)

One report separating every token stream honestly:

```
task_model      calls, input/output tokens, cost (measured)
control_plane   classifier/verifier/evaluator tokens + cost (ledger)
avoided         cache_tokens (+kind, +savings kind),
                normalization_tokens_saved_estimated, context_tokens_reused
totals          gross_tokens_incl_cp, input_tokens_sent,
                input_tokens_before_optimization_estimate,
                input_tokens_saved_estimate, input_savings_pct, total_cost_usd
```

`aggregate_token_reports` sums the numeric leaves across per-query reports for
the benchmark (`token_aggregate`).

## 7. Token-efficiency benchmark (`benchmark/token_benchmark.py`)

Runs the same queries in two arms:

- **naive** — raw prompt, fixed `max_tokens=TOKEN_OPT_BASELINE_OUTPUT_BUDGET`
  (default 1024), single attempt, cache off.
- **optimized** — predicted budget + templates + normalization, single
  attempt, cache off.

Reports per-arm totals/averages plus `output_tokens_saved(_pct)`,
`cost_saved_usd(_pct)`, `quality_delta`. Start with
`POST /api/benchmark/token-efficiency` (`limit`), poll
`GET /api/benchmark/token-efficiency/{job_id}`. The Benchmark Lab shows the
naive-vs-optimized table.

Smoke result (n=6, fake generator): naive output 318 tokens vs optimized 262 →
**17.6% output tokens saved, 13.9% cost saved, quality unchanged**.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `PROMPT_TEMPLATES_ENABLED` | `true` | Task-aware minimal system prompts |
| `TOKEN_OPT_BASELINE_OUTPUT_BUDGET` | `1024` | Naive-arm output budget for savings math |

## Honest limits

- chars/4 is an estimate; provider-reported usage wins wherever it exists.
- Normalization savings are real bytes sent, but "tokens saved" is the same
  chars/4 estimate — labeled `estimated`.
- The naive arm's fixed 1024 budget is a counterfactual assumption, stated in
  every result payload (`baseline_output_budget`).
- Cache-first savings are measured only when the stored call was measured;
  otherwise the kind is `unavailable`, never invented.
