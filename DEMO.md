# Demo script — 6 demos, ~8 minutes. Backend on :8000, frontend on :5173.

Open the **Playground** tab for Demos 1–5, **Benchmark Lab** for Demo 6.
Narrator line for the whole arc: *"Minimum capable intelligence for every
request — and we verify, we don't trust."*

## Demo 1 — Simple request → cheap model (30s)

Prompt: `What is an API?`
Point at the trace: general · difficulty ~0.10 · flash qualifies · cheapest.
Say: *"Trivia never touches the expensive model."*

## Demo 2 — Coding → capable model (1 min)

Prompt: `Debug this Python concurrency issue: threads increment a shared
counter without a lock and the final count is too low. Explain the cause
and fix.`
Point at: coding+reasoning required, difficulty jump, cheapest qualifier wins.
Say: *"Price is compared only among models that clear the bar."*

## Demo 3 — Hard reasoning → strong model (1 min)

Prompt: `Design a fault-tolerant distributed banking ledger in four
sentences: name the consistency model, replication strategy, failure
handling, and one tradeoff.`
Point at: flash rejected on measured reasoning score, pro selected. Open the
**Models** tab: pro leads every measured category — *"measured on our own
benchmark, not marketing."*

## Demo 4 — Failure → automatic escalation (1.5 min)

Same prompt as Demo 3, but set **force first model** to
`deepseek-v4-flash`.
Point at: attempt 1 quality < 0.75 FAIL → escalates to pro → PASS.
Say: *"We don't trust our routing decision. We verify the answer and
escalate when it falls short — the failed attempt is still on the bill,
visible in the cost."*

## Demo 5 — Cache (1.5 min)

Context: `ACME return policy: 30 days with receipt. No refunds on opened
software. Extended warranty costs 15 dollars.`
1. Question `How many days do I have to return?` → MISS.
2. Question `How much is the extended warranty?` (same context) →
   CONTEXT hit, avoided tokens shown.
3. Repeat question 1 → EXACT hit, cost $0.
Say: *"Reusable context is cached, not just exact questions — measured and
estimated savings are labeled separately, never mixed."*

## Demo 6 — Benchmark (2 min)

**Benchmark Lab** → Run full benchmark (or show the stored latest result).
Read the four numbers: always-best cost vs optimizer cost, baseline quality
vs optimizer quality.
Say: *"Fifty real queries, reference-scored. This is the experiment, not a
slide — methodology is written under the button."*

## If something goes wrong live

- API slow? Every LLM call has a 75s hard deadline, then escalates.
- No Atlas? The app falls back to in-memory storage and says so on screen.
- Wrong route? `POST /api/route/preview` always explains a decision for free.
