"""Chat orchestration — Phase 4: analyze -> route -> generate -> verify -> escalate.

Cache (Phase 5) and MongoDB (Phase 6) attach inside optimizer.run_prompt later.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import settings
from backend.core.optimizer import run_prompt
from backend.core.registry import get_registry
from backend.core.router import NoCapableModel, route
from backend.core.task_analyzer import analyze
from backend.providers.opencode import OpenCodeError

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.2
    # Demo/testing aid: force the FIRST attempt to this (enabled) model.
    # Escalation then proceeds normally — used to demo fallback live.
    force_model: str | None = None
    reference_answer: str | None = None  # benchmark-style grounded scoring
    max_attempts: int = 2
    context: str | None = None  # reusable context (docs/policy) for cache demo
    use_cache: bool = True


class PreviewRequest(BaseModel):
    prompt: str


def _analysis_view(a) -> dict:
    return {
        "task_type": a.task_type,
        "difficulty_score": a.difficulty_score,
        "confidence": a.confidence,
        "required_capabilities": a.required_capabilities,
        "required_thresholds": a.required_thresholds,
        "detected_signals": a.detected_signals,
        "estimated_input_tokens": a.estimated_input_tokens,
        "word_count": a.word_count,
    }


def _decision_view(d) -> dict:
    return {
        "selected_model": d.selected_model,
        "candidates": [c.__dict__ for c in d.candidates],
        "expected_cost_usd": d.expected_cost_usd,
        "meets_requirements": d.meets_requirements,
        "confidence_action": d.confidence_action,
        "capability_source": d.capability_source,
        "decision_reason": d.decision_reason,
    }


@router.post("/api/route/preview")
def route_preview(req: PreviewRequest):
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(400, "prompt must not be empty")
    analysis = analyze(req.prompt)
    try:
        decision = route(analysis, get_registry().enabled())
    except NoCapableModel as e:
        raise HTTPException(400, str(e))
    return {"analysis": _analysis_view(analysis), "routing": _decision_view(decision),
            "capability_source": decision.capability_source}


@router.post("/api/chat")
def chat(req: ChatRequest):
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(400, "prompt must not be empty")
    try:
        res = run_prompt(
            req.prompt,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            reference=req.reference_answer,
            force_model=req.force_model,
            max_attempts=req.max_attempts,
            context=req.context,
            use_cache=req.use_cache,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OpenCodeError as e:
        raise HTTPException(502, str(e))
    final = res.attempts[-1] if res.attempts else None
    if final is None:  # exact cache hit: no LLM call, no fresh evaluation
        quality_score, quality_method, quality_breakdown = None, "cached", {
            "correctness": None, "relevance": None, "completeness": None}
    else:
        quality_score = final.quality.overall if final.quality else None
        quality_method = final.quality.method if final.quality else "unavailable"
        quality_breakdown = {
            "correctness": final.quality.correctness if final.quality else None,
            "relevance": final.quality.relevance if final.quality else None,
            "completeness": final.quality.completeness if final.quality else None,
        }
    quality_detail = final.quality.scoring_detail if final and final.quality else "cached" if final is None else "unavailable"
    resp = {
        "answer": res.answer,
        "task_type": res.analysis.task_type,
        "difficulty_score": res.analysis.difficulty_score,
        "confidence": res.analysis.confidence,
        "required_capabilities": res.analysis.required_capabilities,
        "selected_model": res.final_model,  # final model after any escalation
        "initial_model": res.initial_model,
        "final_model": res.final_model,
        "cache_hit": res.cache_hit,
        "cache_kind": res.cache_kind,  # miss | exact | semantic | context
        "tokens_avoided": res.tokens_avoided,
        "cache_saved_usd": res.cache_saved_usd,
        "cache_saved_kind": res.cache_saved_kind,  # "" | measured | estimated
        "input_tokens": sum(a.input_tokens for a in res.attempts),
        "output_tokens": sum(a.output_tokens for a in res.attempts),
        "quality_score": quality_score,
        "quality_method": quality_method,
        "quality_detail": quality_detail,
        "quality_breakdown": quality_breakdown,
        "quality_threshold": settings.quality_threshold,
        "quality_passed": res.quality_passed,
        "verification_status": res.verification_status,
        "max_attempts_reached": res.max_attempts_reached,
        "escalated": res.escalated,
        "attempts": [
            {"model_id": a.model_id, "quality": a.quality.overall if a.quality else None,
             "method": a.quality.method if a.quality else "unavailable",
             "scoring_detail": a.quality.scoring_detail if a.quality else "unavailable", "passed": a.passed,
             "cost_usd": a.cost_usd, "latency_ms": a.latency_ms}
            for a in res.attempts
        ],
        "actual_cost_usd": res.total_cost_usd,  # SUM over attempts (honest spend)
        "cost_status": res.cost_status,
        "baseline_model": res.baseline_model,  # counterfactual always-best
        "baseline_method": res.baseline_method,
        "baseline_cost_usd": res.baseline_cost_usd,
        "savings_usd": res.savings_usd,
        "savings_pct": res.savings_pct,
        "savings_status": res.savings_status,
        "latency_ms": res.total_latency_ms,
        "analysis": _analysis_view(res.analysis),
        "routing": _decision_view(res.routing),
        "decision_reason": res.routing.decision_reason,
        "capability_source": res.routing.capability_source,
    }
    # Persist history for analytics/benchmark (never breaks the response).
    try:
        from backend.database.mongodb import get_store
        rid = get_store().store_request({
            "prompt": req.prompt[:2000],
            "task_type": res.analysis.task_type,
            "difficulty_score": res.analysis.difficulty_score,
            "confidence": res.analysis.confidence,
            "required_capabilities": res.analysis.required_capabilities,
            "selected_model": res.routing.selected_model,
            "initial_model": res.initial_model,
            "final_model": res.final_model,
            "cache_hit": res.cache_hit,
            "cache_kind": res.cache_kind,
            "input_tokens": sum(a.input_tokens for a in res.attempts) if all(a.input_tokens is not None for a in res.attempts) else None,
            "output_tokens": sum(a.output_tokens for a in res.attempts) if all(a.output_tokens is not None for a in res.attempts) else None,
            "actual_cost_usd": res.total_cost_usd,
            "baseline_model": res.baseline_model,
            "baseline_cost_usd": res.baseline_cost_usd,
            "quality_score": quality_score,
            "quality_method": quality_method,
            "escalated": res.escalated,
            "latency_ms": res.total_latency_ms,
            "capability_source": res.routing.capability_source,
            "verification_status": res.verification_status,
            "cost_status": res.cost_status,
        })
        resp["request_id"] = rid
    except Exception:
        resp["request_id"] = None
    return resp
