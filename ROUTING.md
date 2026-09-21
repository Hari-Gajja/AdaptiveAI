# Routing

How the gateway picks a model for every request — and how it proves the pick
was honest.

## The routing problem

Every request sent to the strongest (most expensive) model wastes money; every
request sent to a weak model risks quality. The router minimizes
`Cost(m)` subject to `ExpectedQuality(m, task) ≥ RequiredQuality(task)`.

## Hybrid selection (two steps)

### Step 1 — Nemotron recommends (control plane)

The control-plane model (default **Nemotron 3.5 Lightning Free** via OpenCode
Zen, `OPENCODE_MODEL` env) receives a tiny prompt — task type, difficulty,
required capabilities, a 600-char prompt clip, and the customer's candidate
list (id, derived tier, prices, context window, capability scores) — and
returns strict JSON: `{"model": "<id>", "reason": "<max 12 words>"}`.

Budget: ~60 output tokens, no chain-of-thought. Any failure (timeout,
malformed JSON, disabled, empty pool) returns `None` — graceful degradation,
never a hard error.

### Step 2 — Deterministic validator decides

`backend/core/gateway_router.py` validates the recommendation against hard
facts. A recommendation is accepted only when the model:

1. exists in the customer's registry
2. belongs to the requesting customer (isolation)
3. is enabled and not blocked
4. context window fits prompt + expected answer
5. satisfies the task's required capabilities
6. has valid pricing (`configured`; free models count as valid)
7. is not otherwise excluded

Invalid or missing recommendation → the deterministic router
(`backend/core/router.py`) routes over the customer's enabled, priced pool:
easy → cheapest qualifier; medium → cheapest qualifier with a safe capability
margin, else strongest; hard / low-confidence → strongest qualifier.

### Provenance (always reported)

| `model_selection.provenance` | Meaning |
|---|---|
| `nemotron_recommended` | the AI recommendation passed all validation and was used |
| `deterministic` | no recommendation available (control plane disabled/failed) — router decided |
| `nemotron_rejected_fallback` | a recommendation existed but failed validation — router decided |

The response also carries `reason` and `validation_notes` (the exact rejection
reasons, e.g. `capability coding: 0.55 < required 0.80`), so the dashboard can
show **why** every pick happened.

## Escalation

Quality below the threshold (`QUALITY_THRESHOLD`, default 0.75) retries the
next-best model, capped by `max_attempts` (default 2). The ladder is bounded
by the **baseline price tier**: escalating above the always-best counterfactual
tier is the only way optimized spend can exceed the baseline, so above-baseline
models go last.

Provider failures during escalation are classified
(`backend/core/optimizer._provider_failure_type`):

- **transient** (429 / 5xx / timeout / transport) → fall through to the next
  model in the ladder
- **permanent** (401, malformed response) → raise → HTTP `502` (honest
  failure, no silent model swap)

## Cache interaction

The cache is checked **before** routing and classification (spec §13), and it
is **namespaced per customer** — customer A can never hit customer B's cached
answers.

| Hit kind | Behavior |
|---|---|
| exact | stored answer returned; classifier call avoided; savings `measured` |
| semantic | similarity ≥ threshold + ALL safety gates + optional veto-only LLM verifier → stored answer; savings `measured` |
| context | reusable context seen before, new question → context tokens avoided; savings `estimated` |

Only quality-validated answers are stored in the cache.

## Cost math (honest accounting)

- `actual_cost_usd` — sum over ALL attempts (money really spent, including
  failed ones).
- `baseline_cost_usd` — counterfactual: the same measured tokens priced at the
  always-best model (frontier baseline, or the strongest measured model in the
  customer's pool). No duplicate expensive calls are made.
- `savings_usd` / `savings_direction` — `savings | loss | breakeven |
  unavailable`; never fabricated when pricing is unknown.
- `control_plane_cost_usd` — every Nemotron call (classifier, selector,
  verifier, evaluator) is priced and tracked in a per-request ledger.
- `net_savings_usd` — **gross savings minus control-plane overhead**. The
  gateway reports both, so "savings" can never hide the cost of the AI that
  produced them.

## Worked example

Customer pool: `acme-mini` ($0.12/$0.48), `deepseek-v4-flash` (catalog
$0.22/$0.66), `acme-max` ($1.00/$5.00). Prompt: "What is an API?" →
task `general`, difficulty 0.10, required `["general"]`.

1. Cache miss → classifier (Nemotron or legacy) → analysis.
2. Nemotron recommends `acme-mini` (cheapest capable) → validator confirms
   enabled, priced, context fits, capability `general` satisfied →
   `provenance: nemotron_recommended`.
3. If Nemotron is down → deterministic router picks the cheapest qualifier →
   `provenance: deterministic`.
4. If Nemotron recommends a blocked model → validator rejects → router picks →
   `provenance: nemotron_rejected_fallback` with the rejection reason.
5. Generate on the customer's endpoint → quality 0.78 ≥ 0.75 → PASS.
6. Cost: actual ≈ $0.0000058 (acme-mini) vs baseline ≈ $0.000051 (acme-max) →
   savings ≈ 88%, net of control-plane overhead.
