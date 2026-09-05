"""Token Analytics — unified split accounting (spec §21/§23).

One report per request that separates every token stream honestly:

  task_model      tokens actually sent to / received from the task model
                  (measured, provider-reported; priced cost included)
  control_plane   classifier/verifier/evaluator spend (from the ledger)
  avoided         tokens that never left the building:
                    - cache hits (measured when the stored call was measured)
                    - normalization savings (estimated: original vs normalized)
  context_limit   whether the request bumped a model's context window

Aggregation for the benchmark: sum sections across per-query reports with the
same keys, so dashboards get one shape everywhere.
"""
from __future__ import annotations

from typing import Any


def build_token_report(
    attempts: list[Any],
    ledger_view: dict,
    cache_hit: bool,
    cache_kind: str,
    tokens_avoided: int,
    cache_saved_kind: str,
    normalization_view: dict | None,
    context_tokens: int = 0,
) -> dict:
    """Assemble the per-request token report.

    `attempts` are optimizer Attempt objects (input/output tokens, cost).
    `ledger_view` is ControlPlaneLedger.view(). All numbers are measured
    unless a field name says estimated.
    """
    task_in = sum(a.input_tokens for a in attempts if a.input_tokens is not None)
    task_out = sum(a.output_tokens for a in attempts if a.output_tokens is not None)
    costs = [a.cost_usd for a in attempts if a.cost_usd is not None]
    task_cost = round(sum(costs), 6) if len(costs) == len(attempts) and attempts else None

    cp = (ledger_view or {}).get("totals", {})
    cp_in = int(cp.get("input_tokens", 0))
    cp_out = int(cp.get("output_tokens", 0))
    cp_cost = float(cp.get("cost_usd", 0.0))

    norm_saved = int((normalization_view or {}).get("tokens_saved", 0))
    avoided = {
        "cache_tokens": int(tokens_avoided or 0),
        "cache_kind": cache_kind if cache_hit else None,
        "cache_savings_kind": cache_saved_kind or None,
        "normalization_tokens_saved_estimated": norm_saved,
        "context_tokens_reused": int(context_tokens or 0),
    }
    cache_avoided = avoided["cache_tokens"]
    total_in_before_opt = task_in + cache_avoided + norm_saved
    gross = task_in + task_out + cp_in + cp_out

    return {
        "task_model": {
            "calls": len(attempts),
            "input_tokens": task_in,
            "output_tokens": task_out,
            "total_tokens": task_in + task_out,
            "cost_usd": task_cost,
        },
        "control_plane": {
            "calls": int(cp.get("calls", 0)),
            "input_tokens": cp_in,
            "output_tokens": cp_out,
            "total_tokens": cp_in + cp_out,
            "cost_usd": round(cp_cost, 6),
        },
        "avoided": avoided,
        "totals": {
            "gross_tokens_incl_cp": gross,
            "input_tokens_sent": task_in,
            "input_tokens_before_optimization_estimate": total_in_before_opt,
            "input_tokens_saved_estimate": max(0, total_in_before_opt - task_in),
            "input_savings_pct": round(
                100.0 * max(0, total_in_before_opt - task_in) / total_in_before_opt, 2)
            if total_in_before_opt > 0 else 0.0,
            "total_cost_usd": round((task_cost or 0.0) + cp_cost, 6),
        },
    }


_AGG_KEYS = (
    ("task_model", "calls"),
    ("task_model", "input_tokens"),
    ("task_model", "output_tokens"),
    ("task_model", "total_tokens"),
    ("control_plane", "calls"),
    ("control_plane", "input_tokens"),
    ("control_plane", "output_tokens"),
    ("control_plane", "total_tokens"),
    ("avoided", "cache_tokens"),
    ("avoided", "normalization_tokens_saved_estimated"),
    ("avoided", "context_tokens_reused"),
    ("totals", "gross_tokens_incl_cp"),
    ("totals", "input_tokens_sent"),
    ("totals", "input_tokens_before_optimization_estimate"),
    ("totals", "input_tokens_saved_estimate"),
)


def aggregate_token_reports(reports: list[dict]) -> dict:
    """Sum the numeric leaves across per-query reports (benchmark aggregate)."""
    agg: dict = {"task_model": {}, "control_plane": {}, "avoided": {}, "totals": {}}
    for section, key in _AGG_KEYS:
        agg[section][key] = sum(
            (r or {}).get(section, {}).get(key, 0) or 0 for r in reports)
    costs = [r.get("task_model", {}).get("cost_usd") for r in reports if r]
    agg["task_model"]["cost_usd"] = round(sum(c for c in costs if c is not None), 6)
    cp_costs = [r.get("control_plane", {}).get("cost_usd", 0.0) for r in reports if r]
    agg["control_plane"]["cost_usd"] = round(sum(cp_costs), 6)
    total_costs = [r.get("totals", {}).get("total_cost_usd", 0.0) for r in reports if r]
    agg["totals"]["total_cost_usd"] = round(sum(total_costs), 6)
    sent = agg["totals"]["input_tokens_sent"]
    before = agg["totals"]["input_tokens_before_optimization_estimate"]
    agg["totals"]["input_savings_pct"] = round(
        100.0 * max(0, before - sent) / before, 2) if before > 0 else 0.0
    return agg
