"""Control-plane cost accounting — Total Cost = Control Plane + Task Model.

Every control-plane call's measured tokens are priced with the SAME pricing
table as task models (the control-plane model is a normal registry model).
Aggregates keep measured and estimated usage strictly separate so nothing is
ever fabricated:

  measured  : provider-reported token counts
  estimated : chars/4 approximation when the provider omitted usage
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.core.registry import get_registry
from backend.llm import config as cp_cfg


@dataclass
class ComponentUsage:
    """Token usage for one control-plane component."""
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    measured_calls: int = 0
    estimated_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def add(self, input_tokens: int | None, output_tokens: int | None,
            latency_ms: int = 0, usage_estimated: bool = False) -> None:
        self.calls += 1
        if usage_estimated:
            self.estimated_calls += 1
        else:
            self.measured_calls += 1
        if input_tokens is not None:
            self.input_tokens += input_tokens
        if output_tokens is not None:
            self.output_tokens += output_tokens
        self.latency_ms += latency_ms

    def price(self, input_per_1M: float, output_per_1M: float) -> None:
        self.cost_usd = round(
            self.input_tokens / 1_000_000 * input_per_1M
            + self.output_tokens / 1_000_000 * output_per_1M, 6)

    def view(self) -> dict:
        return {
            "calls": self.calls,
            "measured_calls": self.measured_calls,
            "estimated_calls": self.estimated_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
        }


@dataclass
class ControlPlaneLedger:
    """Per-request ledger of control-plane spend. Attached to OptimizerResult
    and serialized into the API response + benchmark aggregates."""
    classifier: ComponentUsage = field(default_factory=ComponentUsage)
    cache_verifier: ComponentUsage = field(default_factory=ComponentUsage)
    evaluator: ComponentUsage = field(default_factory=ComponentUsage)
    model_id: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""
    status: str = "disabled"   # disabled | active | degraded
    # §13 cache-first: classifier calls avoided by checking the cache BEFORE
    # classification. Tracked so dashboards/benchmarks can show control-plane
    # savings, not just task-model savings.
    calls_avoided_exact: int = 0
    calls_avoided_semantic: int = 0

    # ---- recording ------------------------------------------------------
    def record(self, kind: str, input_tokens: int | None, output_tokens: int | None,
               latency_ms: int = 0, usage_estimated: bool = False) -> None:
        comp = {"classifier": self.classifier,
                "cache_verifier": self.cache_verifier,
                "evaluator": self.evaluator}.get(kind)
        if comp is None:
            return
        comp.add(input_tokens, output_tokens, latency_ms, usage_estimated)

    # ---- pricing --------------------------------------------------------
    def finalize(self) -> None:
        """Price all components with the control-plane model's pricing."""
        self.model_id = self.model_id or cp_cfg.OPENCODE_MODEL
        try:
            entry = get_registry().get(self.model_id)
            for comp in (self.classifier, self.cache_verifier, self.evaluator):
                comp.price(entry.input_per_1M, entry.output_per_1M)
        except Exception:
            # unpriced control-plane model: leave costs at 0.0 but mark status
            self.status = "degraded" if self.status == "active" else self.status

    # ---- aggregates -----------------------------------------------------
    @property
    def total_input_tokens(self) -> int:
        return (self.classifier.input_tokens + self.cache_verifier.input_tokens
                + self.evaluator.input_tokens)

    @property
    def total_output_tokens(self) -> int:
        return (self.classifier.output_tokens + self.cache_verifier.output_tokens
                + self.evaluator.output_tokens)

    @property
    def total_cost_usd(self) -> float:
        return round(self.classifier.cost_usd + self.cache_verifier.cost_usd
                     + self.evaluator.cost_usd, 6)

    @property
    def total_latency_ms(self) -> int:
        return (self.classifier.latency_ms + self.cache_verifier.latency_ms
                + self.evaluator.latency_ms)

    @property
    def total_calls(self) -> int:
        return (self.classifier.calls + self.cache_verifier.calls
                + self.evaluator.calls)

    @property
    def estimated_calls(self) -> int:
        return (self.classifier.estimated_calls + self.cache_verifier.estimated_calls
                + self.evaluator.estimated_calls)

    def view(self) -> dict:
        return {
            "model_id": self.model_id,
            "status": self.status,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "components": {
                "classifier": self.classifier.view(),
                "cache_verifier": self.cache_verifier.view(),
                "evaluator": self.evaluator.view(),
            },
            "totals": {
                "calls": self.total_calls,
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "latency_ms": self.total_latency_ms,
                "cost_usd": self.total_cost_usd,
                "estimated_usage_calls": self.estimated_calls,
                "classifier_calls_avoided_exact": self.calls_avoided_exact,
                "classifier_calls_avoided_semantic": self.calls_avoided_semantic,
            },
        }


def empty_ledger() -> ControlPlaneLedger:
    """Ledger for requests that never touched the control plane."""
    return ControlPlaneLedger(model_id=cp_cfg.OPENCODE_MODEL, status="disabled")
