# Model Routing

How the router picks a model, and why it works the way it does.

## The rule

Minimize `Cost(m)` subject to `ExpectedQuality(m, task) ≥ RequiredQuality(task)`.

- `Cost(m)` = `input_per_1M · est_in/1M + output_per_1M · est_out/1M` from the
  registry (no hard-coded prices anywhere else).
- `ExpectedQuality(m, task)` = measured capability scores from the profiler
  (`core/profiler.py`, 24-item test set) when available, priors otherwise.
  `overall_source` labels the mix: `measured` / `mixed` / `estimated`.
- `RequiredQuality(task)` = per-capability thresholds derived by the task
  analyzer from task type + difficulty.

## Decision flow (`core/router.py`)

1. Filter to enabled models with configured pricing (`input_per_1M > 0`).
2. For each model compute expected quality, gaps, and estimated cost.
3. **Context-window guard (§11):** if
   `estimated_input_tokens + expected_output_tokens > context_window`, the
   model is rejected with a `context window: need ~N > W` gap no matter how
   cheap it is, and `context_limit_triggered` is set on the decision.
4. Cheapest qualifying model wins (`action="normal"`).
5. **Low confidence (< 0.60):** pick the *safest* qualifier (highest expected
   quality, cost as tiebreak) — `action="low_confidence_safety"`. A wrong
   cheap route costs an escalation; a slightly pricier safe route usually
   doesn't.
6. **Nothing qualifies:** transparent strongest-fallback — highest expected
   quality, `meets_requirements=False` so the UI can flag it.

## Escalation (`core/optimizer.py`)

Quality below threshold retries the next-best candidate, capped by
`max_attempts`. Every attempt's cost is summed — failed attempts stay on the
bill and are visible. `verification_status` distinguishes `verified`,
`escalated_and_verified`, `provider_failed`, `verification_failed`.

## Model-selection quality metrics (spec §22, benchmark)

| Metric | Definition | Caveat |
|---|---|---|
| `routing_accuracy` | routed model produced a passing answer — the router's capability prediction was right | measures prediction, not optimality |
| `unnecessary_frontier_usage` | final model == baseline (frontier) | labeled as *usage*, not proof of waste — a cheaper model might also have failed |
| `cheap_failure_rate` | escalated AND first attempt failed quality | the cost of aggressive down-routing |
| `classification_accuracy` | analyzer task_type vs query category (+ confusion matrix) | categories are the benchmark's labels |

## Why these choices

- **Why capability-constrained cost minimization instead of a cheap/expensive
  label?** Labels rot. With measured per-category scores, adding a new model
  needs no re-labeling — the router compares measured cost + capability
  dynamically, and "cheap" is just the outcome, never an input.
- **Why a low-confidence safety pick?** When the analyzer is unsure, the
  expected cost of a wrong cheap route (failed answer + escalation + retry)
  exceeds the small premium of the stronger qualifier. The action is labeled
  so the dashboard can show how often caution fires.
- **Why reject on context window instead of truncating silently?** Silent
  truncation changes the task. The guard surfaces the limit
  (`context_limit_triggered`) so callers can chunk or pick a long-window
  model deliberately.
- **Why is `unnecessary_frontier_usage` not called "waste"?** Because the
  counterfactual is unknowable from one run: the frontier model may have been
  the only one that passed. The dashboard states the caveat instead of
  claiming savings we can't prove.
- **Why expected output tokens in the cost model?** A fixed 512-token
  assumption overprices short answers and underprices essays; the analyzer's
  predicted budget (see TOKEN_OPTIMIZATION.md) keeps estimates honest in both
  directions.

## Force-model override

`force_model` pins the first attempt (demo/debug). The router's preference is
still recorded in `decision_reason` ("Demo override: first attempt forced to
X (router preferred Y)"), and escalation still applies after it.
