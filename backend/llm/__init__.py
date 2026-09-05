"""LLM control plane — OpenCode-connected micro-calls for routing decisions.

The control plane is a set of TINY, budgeted LLM calls that replace/augment
deterministic heuristics where an LLM is genuinely better:

  classifier   prompt -> {t: M|C|O, d: E|M|H, c: 0.00}   (task class)
  verifier     (prompt, cached answer) -> {same: 0|1}    (cache safety net)
  evaluator    (prompt, answer) -> {c, r, s}             (subjective quality)

Design rules (spec):
  - Level 0-3 hierarchy: deterministic operations NEVER invoke an LLM.
  - Every call has a hard token budget and a timeout; on ANY failure the
    system falls back to the legacy deterministic path (never breaks).
  - Token usage is measured from the provider response and accounted
    separately from task-model spend (Total = Control Plane + Task Model).
"""
