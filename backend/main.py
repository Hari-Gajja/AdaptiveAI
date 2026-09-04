"""Phase-6 FastAPI: + analytics, profiler, benchmark."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Allow running as `uvicorn backend.main:app` from llm-cost-optimizer/ AND as
# `uvicorn main:app` from backend/ during the hackathon.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.api.analytics import router as analytics_router  # noqa: E402
from backend.api.benchmark import router as benchmark_router  # noqa: E402
from backend.api.cache import router as cache_router  # noqa: E402
from backend.api.chat import router as chat_router  # noqa: E402
from backend.api.models import router as models_router  # noqa: E402
from backend.config import MODEL_PRICING  # noqa: E402
from backend.core.registry import get_registry  # noqa: E402
from backend.providers.opencode import GenerateResult, OpenCodeError, generate  # noqa: E402

app = FastAPI(title="Adaptive Multi-LLM Cost Optimizer — Phase 6")
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(cache_router)
app.include_router(benchmark_router)
app.include_router(analytics_router)


class GenerateRequest(BaseModel):
    model_id: str
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.2


@app.get("/health")
def health():
    reg = get_registry()
    return {"status": "ok", "phase": 6,
            "models": [m.model_id for m in reg.list()],
            "enabled": [m.model_id for m in reg.enabled()]}


@app.post("/api/test/generate")
def test_generate(req: GenerateRequest):
    try:
        entry = get_registry().get(req.model_id)
    except Exception:
        raise HTTPException(400, f"unknown model '{req.model_id}'. See GET /api/models")
    if not entry.enabled:
        raise HTTPException(400, f"model '{req.model_id}' is disabled")
    try:
        r: GenerateResult = generate(
            req.model_id,
            [{"role": "user", "content": req.prompt}],
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
    except OpenCodeError as e:
        raise HTTPException(502, str(e))
    inp, outp, _ = MODEL_PRICING.get(req.model_id, (0.0, 0.0, 0.0))
    cost = r.input_tokens / 1_000_000 * inp + r.output_tokens / 1_000_000 * outp
    return {
        "answer": r.text,
        "model_id": r.model_id,
        "endpoint_family": r.endpoint_family,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cached_tokens": r.cached_tokens,
        "latency_ms": r.latency_ms,
        "estimated_cost_usd": round(cost, 6),
    }
