"""Request Optimizer — Phase 5 orchestration.

Pipeline: cache-check -> analyze -> route -> generate -> quality-check
-> escalate (max N) -> cost + counterfactual baseline. MongoDB hooks in later.

Actual cost = SUM over attempts (money really spent, including failed ones).
Exact cache hits skip the LLM entirely (cost 0, measured savings).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backend.config import settings
from backend.core.cache import CacheEntry, get_cache
from backend.core.cost import actual_cost_usd, baseline_method, baseline_model, cost_summary
from backend.core.quality import QualityScore, evaluate
from backend.core.registry import ModelEntry, get_registry
from backend.core.router import CandidateView, RoutingDecision, route
from backend.core.task_analyzer import TaskAnalysis, analyze
from backend.providers.opencode import GenerateResult, generate
from backend.providers.opencode import OpenCodeError


@dataclass
class Attempt:
    model_id: str
    answer: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int
    cost_usd: float | None
    quality: QualityScore | None
    passed: bool
    failure_type: str | None = None
    error: str | None = None


@dataclass
class OptimizerResult:
    answer: str
    analysis: TaskAnalysis
    routing: RoutingDecision
    attempts: list[Attempt] = field(default_factory=list)
    escalated: bool = False
    total_cost_usd: float | None = 0.0
    total_latency_ms: int = 0
    # cache
    cache_hit: bool = False
    cache_kind: str = "miss"  # miss | exact | context
    tokens_avoided: int = 0
    cache_saved_usd: float | None = 0.0
    cache_saved_kind: str = ""  # "" | "measured" | "estimated"
    # baseline
    baseline_model: str = ""
    baseline_method: str = ""
    baseline_cost_usd: float | None = 0.0
    savings_usd: float | None = 0.0
    savings_pct: float | None = 0.0
    cost_status: str = "measured"
    savings_status: str = "measured"
    quality_passed: bool = False
    verification_status: str = "not_run"
    max_attempts_reached: bool = False

    @property
    def initial_model(self) -> str:
        return self.attempts[0].model_id if self.attempts else ""

    @property
    def final_model(self) -> str:
        if self.attempts:
            return self.attempts[-1].model_id
        return self.routing.selected_model if self.routing else ""

    @property
    def final_quality(self) -> QualityScore | None:
        return self.attempts[-1].quality if self.attempts else None


def escalation_order(candidates: list[CandidateView], tried: set[str]) -> list[str]:
    """Untried models, best expected quality first, cheapest tiebreak."""
    rest = [c for c in candidates if c.model_id not in tried]
    rest.sort(key=lambda c: (-c.expected_quality, c.expected_cost_usd))
    return [c.model_id for c in rest]


def _finish_costs(result: OptimizerResult, enabled: list[ModelEntry]) -> None:
    base = baseline_model(enabled)
    in_tok = sum(a.input_tokens for a in result.attempts if a.input_tokens is not None) if all(a.input_tokens is not None for a in result.attempts) else None
    out_tok = sum(a.output_tokens for a in result.attempts if a.output_tokens is not None) if all(a.output_tokens is not None for a in result.attempts) else None
    s = cost_summary(result.total_cost_usd, base, in_tok, out_tok, baseline_method(enabled))
    result.baseline_model = s["baseline_model"]
    result.baseline_method = s["baseline_method"]
    result.baseline_cost_usd = s["baseline_cost_usd"]
    result.savings_usd = s["savings_usd"]
    result.savings_pct = s["savings_pct"]
    result.cost_status = s["actual_cost_status"]
    result.savings_status = s["savings_status"]


def run_prompt(
    prompt: str,
    max_tokens: int = 512,
    temperature: float = 0.2,
    reference: str | None = None,
    force_model: str | None = None,
    max_attempts: int = 2,
    threshold: float | None = None,
    context: str | None = None,
    use_cache: bool = True,
    _generate: Callable = generate,
    _evaluate: Callable = evaluate,
) -> OptimizerResult:
    """Full pipeline. _generate/_evaluate are injectable for keyless tests."""
    if not prompt or not prompt.strip():
        raise ValueError("prompt must not be empty")
    threshold = settings.quality_threshold if threshold is None else threshold
    max_attempts = max(1, max_attempts)
    context = (context or "").strip()

    registry = get_registry()
    enabled_list = registry.enabled()
    enabled = {m.model_id: m for m in enabled_list}
    if not enabled:
        raise ValueError("no enabled models in registry")
    if force_model is not None and force_model not in enabled:
        raise ValueError(f"force_model '{force_model}' is unknown or disabled")

    cache = get_cache()
    analysis = analyze(prompt, context or None)
    routing = route(analysis, enabled_list)
    reasons = list(routing.decision_reason)

    # ---- exact cache hit: skip the LLM entirely (analysis/routing still shown) ----
    if use_cache:
        hit = cache.get_exact(prompt, context)
        if hit is not None:
            cache.note_exact_hit(hit)
            reasons.append(f"Cache EXACT hit: returning stored answer from {hit.model_id}, "
                           f"skipped LLM call (measured savings ${hit.cost_usd:.6f}).")
            routing.decision_reason = reasons
            result = OptimizerResult(
                answer=hit.answer, analysis=analysis, routing=routing,
                cache_hit=True, cache_kind="exact",
                tokens_avoided=hit.input_tokens + hit.output_tokens,
                cache_saved_usd=hit.cost_usd, cache_saved_kind="measured" if hit.cost_usd is not None else "unavailable")
            _finish_costs_free_hit(result, enabled_list, hit)
            return result

        # ---- semantic cache hit: similar prompt + ALL safety gates pass ----
        sem_hit, sem_score, vetoes = cache.lookup_semantic(prompt, context)
        if sem_hit is not None:
            cache.note_semantic_hit(sem_hit, sem_score)
            reasons.append(
                f"Cache SEMANTIC hit (similarity {sem_score:.2f} >= {cache._sem_threshold}): "
                f"all safety gates passed (numbers, operators, ordinals, units, directions, "
                f"language, structures, identifiers, intent, negation, context). "
                f"Returning stored answer from {sem_hit.model_id}, skipped LLM call "
                f"(measured savings ${sem_hit.cost_usd:.6f}).")
            routing.decision_reason = reasons
            result = OptimizerResult(
                answer=sem_hit.answer, analysis=analysis, routing=routing,
                cache_hit=True, cache_kind="semantic",
                tokens_avoided=sem_hit.input_tokens + sem_hit.output_tokens,
                cache_saved_usd=sem_hit.cost_usd,
                cache_saved_kind="measured" if sem_hit.cost_usd is not None else "unavailable")
            _finish_costs_free_hit(result, enabled_list, sem_hit)
            return result
        if vetoes:
            cache.note_semantic_veto(vetoes)
            reasons.append(
                f"Semantic candidate found (similarity {sem_score:.2f}) but safety gates "
                f"VETOED reuse: {'; '.join(vetoes)}. Falling through to routing.")
        elif sem_score > 0:
            reasons.append(f"Semantic tier: best similarity {sem_score:.2f} "
                           f"below threshold {cache._sem_threshold}.")

    # ---- context hit: reusable context seen before, new question ----
    ctx_tokens = len(context) // 4 if context else 0
    ctx_hit = cache.get_context(context) if (use_cache and context) else None

    first = force_model or routing.selected_model
    order = [first] + [m for m in escalation_order(routing.candidates, {first})]
    order = order[:max_attempts]
    if force_model and force_model != routing.selected_model:
        reasons.append(f"Demo override: first attempt forced to {force_model} "
                       f"(router preferred {routing.selected_model}).")

    result = OptimizerResult(answer="", analysis=analysis, routing=routing)
    tried: set[str] = set()
    for i, mid in enumerate(order):
        entry = enabled[mid]
        messages = ([{"role": "system", "content": context}] if context else []) + \
                   [{"role": "user", "content": prompt}]
        try:
            r: GenerateResult = _generate(mid, messages,
                                          max_tokens=max_tokens, temperature=temperature)
        except OpenCodeError as e:
            failure_type = _provider_failure_type(e)
            if failure_type == "permanent":
                raise
            result.attempts.append(Attempt(
                model_id=mid, answer="", input_tokens=None, output_tokens=None,
                cached_tokens=None, latency_ms=0, cost_usd=None, quality=None,
                passed=False, failure_type=failure_type, error=str(e)))
            reasons.append(f"Attempt {i + 1} ({mid}) provider failure: {failure_type}.")
            if i + 1 < len(order):
                reasons.append(f"Provider failure fallback to {order[i + 1]}.")
                continue
            break
        q = _evaluate(r.text, prompt, reference)
        passed = q.overall >= threshold
        cost = actual_cost_usd(entry, r.input_tokens, r.output_tokens)
        result.attempts.append(Attempt(
            model_id=mid, answer=r.text, input_tokens=r.input_tokens,
            output_tokens=r.output_tokens, cached_tokens=r.cached_tokens,
            latency_ms=r.latency_ms, cost_usd=round(cost, 6),
            quality=q, passed=passed))
        tried.add(mid)
        if passed:
            reasons.append(f"Attempt {i + 1} ({mid}): quality {q.overall} >= {threshold} PASS.")
            break
        reasons.append(f"Attempt {i + 1} ({mid}): quality {q.overall} < {threshold} FAIL.")
        if i + 1 < len(order):
            reasons.append(f"Escalating to {order[i + 1]}.")
    routing.decision_reason = reasons
    result.escalated = len(result.attempts) > 1
    result.answer = next((a.answer for a in reversed(result.attempts) if a.answer), "")
    result.total_cost_usd = (round(sum(a.cost_usd for a in result.attempts), 6)
                             if all(a.cost_usd is not None for a in result.attempts) else None)
    result.total_latency_ms = sum(a.latency_ms for a in result.attempts)
    final = result.attempts[-1] if result.attempts else None
    result.quality_passed = bool(final and final.passed and final.quality is not None)
    result.max_attempts_reached = bool(result.attempts) and not result.quality_passed and len(result.attempts) >= len(order)
    if result.quality_passed:
        result.verification_status = "escalated_and_verified" if result.escalated else "verified"
    elif any(a.failure_type for a in result.attempts):
        result.verification_status = "provider_failed"
    else:
        result.verification_status = "verification_failed"

    # ---- cache bookkeeping: store ONLY validated responses (spec step 6) ----
    if use_cache and result.quality_passed and result.answer:
        last = result.attempts[-1]
        cache.put(CacheEntry(prompt=prompt, context=context, answer=last.answer,
                             model_id=last.model_id, input_tokens=last.input_tokens,
                             output_tokens=last.output_tokens, cost_usd=last.cost_usd,
                             context_tokens=ctx_tokens))
        if ctx_hit is not None:
            price = enabled[last.model_id].input_per_1M
            cache.note_context_hit(ctx_tokens, price)
            result.cache_hit = True
            result.cache_kind = "context"
            result.tokens_avoided = ctx_tokens
            result.cache_saved_usd = round(ctx_tokens / 1_000_000 * price, 6)
            result.cache_saved_kind = "estimated"
            reasons.append(f"Cache CONTEXT hit: reusable context seen before "
                           f"({ctx_tokens} tokens, estimated savings ${result.cache_saved_usd:.6f}).")
            routing.decision_reason = reasons
        else:
            cache.note_miss()
    elif not use_cache:
        result.cache_kind = "miss"

    _finish_costs(result, enabled_list)
    return result


def _provider_failure_type(error: OpenCodeError) -> str:
    message = str(error).lower()
    import re
    if (re.search(r"\b429\b", message) or re.search(r"\b5\d\d\b", message)
            or "timeout" in message or "timed out" in message or "transport error" in message):
        return "transient"
    return "permanent"


def _finish_costs_free_hit(result: OptimizerResult, enabled: list[ModelEntry],
                           hit: CacheEntry) -> None:
    """Baseline math for exact hits: actual spend 0; baseline computed on the
    avoided token usage so the counterfactual stays honest."""
    base = baseline_model(enabled)
    s = cost_summary(0.0 if hit.cost_usd is not None else None, base,
                     hit.input_tokens, hit.output_tokens, baseline_method(enabled))
    result.baseline_model = s["baseline_model"]
    result.baseline_method = s["baseline_method"]
    result.baseline_cost_usd = s["baseline_cost_usd"]
    result.savings_usd = s["savings_usd"]
    result.savings_pct = s["savings_pct"]
    result.cost_status = s["actual_cost_status"]
    result.savings_status = s["savings_status"]
    result.quality_passed = False
    result.verification_status = "cached"
