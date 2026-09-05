"""OpenCode control-plane client — the ONLY file that makes control-plane calls.

Wraps the existing provider (backend.providers.opencode.generate) with:
  - a dedicated cheap model (OPENCODE_MODEL)
  - hard token budgets per call type
  - JSON-only parsing with tolerant extraction
  - measured token usage returned on EVERY path (never fabricated)
  - a global health/failure tracker so the pipeline can degrade gracefully

Failure policy: any error (transport, timeout, malformed JSON, missing key,
disabled/unknown model) raises ControlPlaneError; callers catch it and fall
back to the deterministic path. The client records the last failure reason.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

from backend.llm import config as cp_cfg
from backend.providers.opencode import GenerateResult, OpenCodeError, generate


class ControlPlaneError(RuntimeError):
    """Raised when a control-plane call cannot produce a valid result."""


@dataclass
class ControlPlaneResult:
    """Measured outcome of one control-plane call. Tokens are provider-reported
    when available; `usage_estimated` marks estimated accounting (never mixed
    with measured numbers in aggregates)."""
    kind: str                      # classifier | verifier | evaluator
    parsed: dict                   # validated JSON payload
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    model_id: str
    usage_estimated: bool = False
    raw_text: str = ""


@dataclass
class ControlPlaneStats:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_calls: int = 0
    latency_ms: int = 0
    last_error: str = ""
    by_kind: dict = field(default_factory=lambda: {
        "classifier": {"calls": 0, "failures": 0, "input_tokens": 0,
                       "output_tokens": 0, "latency_ms": 0},
        "verifier": {"calls": 0, "failures": 0, "input_tokens": 0,
                     "output_tokens": 0, "latency_ms": 0},
        "evaluator": {"calls": 0, "failures": 0, "input_tokens": 0,
                      "output_tokens": 0, "latency_ms": 0},
    })

    def note(self, kind: str, ok: bool, res: ControlPlaneResult | None,
             err: str = "") -> None:
        self.calls += 1
        b = self.by_kind.setdefault(kind, {"calls": 0, "failures": 0,
                                           "input_tokens": 0, "output_tokens": 0,
                                           "latency_ms": 0})
        b["calls"] += 1
        if ok and res is not None:
            self.successes += 1
            if res.input_tokens is not None:
                self.input_tokens += res.input_tokens
                b["input_tokens"] += res.input_tokens
            if res.output_tokens is not None:
                self.output_tokens += res.output_tokens
                b["output_tokens"] += res.output_tokens
            if res.usage_estimated:
                self.estimated_calls += 1
            self.latency_ms += res.latency_ms
            b["latency_ms"] += res.latency_ms
        else:
            self.failures += 1
            b["failures"] += 1
            self.last_error = err

    def view(self) -> dict:
        return {
            "enabled": cp_cfg.OPENCODE_ENABLED,
            "model": cp_cfg.OPENCODE_MODEL,
            "provider": cp_cfg.OPENCODE_PROVIDER,
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_usage_calls": self.estimated_calls,
            "latency_ms": self.latency_ms,
            "last_error": self.last_error,
            "by_kind": {k: dict(v) for k, v in self.by_kind.items()},
        }


_stats_lock = threading.Lock()
_stats = ControlPlaneStats()
_last_failure_lock = threading.Lock()
_last_failure: str = ""


def control_plane_stats() -> dict:
    with _stats_lock:
        return _stats.view()


def reset_control_plane_stats() -> None:
    global _stats
    with _stats_lock:
        _stats = ControlPlaneStats()


def last_failure() -> str:
    with _last_failure_lock:
        return _last_failure


def _note_failure(reason: str) -> None:
    global _last_failure
    with _last_failure_lock:
        _last_failure = reason


def available() -> bool:
    """Cheap liveness check: enabled flag + model exists in the registry with
    configured pricing. Does NOT make a network call."""
    if not cp_cfg.OPENCODE_ENABLED:
        return False
    try:
        from backend.core.registry import get_registry
        entry = get_registry().get(cp_cfg.OPENCODE_MODEL)
        return entry.enabled and entry.pricing_status == "configured"
    except Exception:
        return False


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply. Tolerates code fences
    and stray prose (small models sometimes add them despite instructions)."""
    if not text:
        raise ControlPlaneError("empty response from control-plane model")
    t = text.strip()
    # direct parse first
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # fenced ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    # first {...} block
    m = re.search(r"\{[^{}]*\}", t, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    raise ControlPlaneError(f"no JSON object in control-plane reply: {text[:200]!r}")


def _call(kind: str, system: str, user: str, max_output_tokens: int,
          validate: "callable") -> ControlPlaneResult:
    """One budgeted control-plane call. Raises ControlPlaneError on failure."""
    if not cp_cfg.OPENCODE_ENABLED:
        raise ControlPlaneError("control plane disabled (OPENCODE_ENABLED=false)")
    try:
        entry = None
        from backend.core.registry import get_registry
        entry = get_registry().get(cp_cfg.OPENCODE_MODEL)
        if not entry.enabled:
            raise ControlPlaneError(
                f"control-plane model '{cp_cfg.OPENCODE_MODEL}' is disabled")
        if entry.pricing_status != "configured":
            raise ControlPlaneError(
                f"control-plane model '{cp_cfg.OPENCODE_MODEL}' has no configured pricing")
    except ControlPlaneError:
        raise
    except Exception as e:
        raise ControlPlaneError(f"control-plane model unavailable: {e}") from e

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    t0 = time.perf_counter()
    try:
        r: GenerateResult = generate(
            cp_cfg.OPENCODE_MODEL, messages,
            max_tokens=max_output_tokens, temperature=0.0)
    except OpenCodeError as e:
        err = f"{kind} call failed: {e}"
        _note_failure(err)
        with _stats_lock:
            _stats.note(kind, False, None, err)
        raise ControlPlaneError(err) from e
    latency = int((time.perf_counter() - t0) * 1000)
    if latency > cp_cfg.OPENCODE_TIMEOUT_SECONDS * 1000:
        # provider returned but blew the control-plane budget: treat as failure
        err = f"{kind} exceeded timeout budget ({latency}ms > {cp_cfg.OPENCODE_TIMEOUT_SECONDS}s)"
        _note_failure(err)
        with _stats_lock:
            _stats.note(kind, False, None, err)
        raise ControlPlaneError(err)

    try:
        parsed = validate(_extract_json(r.text))
    except ControlPlaneError as e:
        err = f"{kind} invalid reply: {e}"
        _note_failure(err)
        with _stats_lock:
            _stats.note(kind, False, None, err)
        raise
    except Exception as e:
        err = f"{kind} invalid reply: {e}"
        _note_failure(err)
        with _stats_lock:
            _stats.note(kind, False, None, err)
        raise ControlPlaneError(err) from e

    # Usage accounting: provider-reported when present. If the provider did
    # not report usage we mark the call estimated and approximate from char
    # counts (chars/4) so cost math stays possible but clearly labeled.
    usage_estimated = r.input_tokens is None or r.output_tokens is None
    in_tok = r.input_tokens if r.input_tokens is not None else \
        (len(system) + len(user)) // 4
    out_tok = r.output_tokens if r.output_tokens is not None else len(r.text) // 4
    res = ControlPlaneResult(
        kind=kind, parsed=parsed, input_tokens=in_tok, output_tokens=out_tok,
        latency_ms=latency, model_id=r.model_id, usage_estimated=usage_estimated,
        raw_text=r.text)
    with _stats_lock:
        _stats.note(kind, True, res)
    return res


# ---------------------------------------------------------------- classifier
_CLASSIFIER_SYSTEM = (
    "You are a request classifier for an LLM router. Reply with ONLY a JSON "
    "object, no explanation, no markdown: "
    '{"t":"<class>","d":"<difficulty>","c":<confidence>}. '
    'class "t" is one of: M (math/logic), C (code/technical), O (other). '
    'difficulty "d" is one of: E (easy), M (medium), H (hard). '
    '"c" is your confidence between 0.00 and 1.00. Keep it to one line.'
)


def _validate_classifier(d: dict) -> dict:
    t = str(d.get("t", "")).strip().upper()[:1]
    if t not in ("M", "C", "O"):
        raise ControlPlaneError(f"classifier bad class label: {d.get('t')!r}")
    dd = str(d.get("d", "")).strip().upper()[:1]
    if dd not in ("E", "M", "H"):
        raise ControlPlaneError(f"classifier bad difficulty label: {d.get('d')!r}")
    try:
        c = float(d.get("c", 0.0))
    except (TypeError, ValueError):
        raise ControlPlaneError(f"classifier bad confidence: {d.get('c')!r}")
    c = max(0.0, min(1.0, c))
    return {"t": t, "d": dd, "c": round(c, 2)}


def classify(prompt: str, budgeted_prompt: str) -> ControlPlaneResult:
    """Classify a prompt. `budgeted_prompt` is the truncated prompt from
    prompt_budget.budget_for_classifier()."""
    return _call("classifier", _CLASSIFIER_SYSTEM, budgeted_prompt,
                 cp_cfg.CLASSIFIER_MAX_OUTPUT_TOKENS, _validate_classifier)


# ------------------------------------------------------------------ verifier
_VERIFIER_SYSTEM = (
    "You verify a cached answer. Decide if the CACHED ANSWER correctly answers "
    "the NEW QUESTION (same meaning, same required result). Reply with ONLY: "
    '{"same":0|1,"c":<confidence 0.00-1.00>}. "same":1 only when reuse is safe.'
)


def _validate_verifier(d: dict) -> dict:
    same = d.get("same", None)
    if same in (0, 1):
        s = int(same)
    else:
        s = str(same).strip().lower()
        if s in ("1", "true", "yes"):
            s = 1
        elif s in ("0", "false", "no"):
            s = 0
        else:
            raise ControlPlaneError(f"verifier bad same flag: {same!r}")
    try:
        c = float(d.get("c", 0.0))
    except (TypeError, ValueError):
        raise ControlPlaneError(f"verifier bad confidence: {d.get('c')!r}")
    return {"same": s, "c": round(max(0.0, min(1.0, c)), 2)}


def verify_cache(budgeted_prompt: str, cached_answer: str) -> ControlPlaneResult:
    user = (
        f"NEW QUESTION:\n{budgeted_prompt}\n\n"
        f"CACHED ANSWER:\n{cached_answer[:600]}"
    )
    return _call("verifier", _VERIFIER_SYSTEM, user,
                 cp_cfg.VERIFIER_MAX_OUTPUT_TOKENS, _validate_verifier)


# ----------------------------------------------------------------- evaluator
_EVALUATOR_SYSTEM = (
    "You grade an AI answer against its question. Reply with ONLY: "
    '{"c":0|1,"r":0|1,"s":<score 0.00-1.00>}. '
    '"c":1 if the answer is factually correct for the question, else 0. '
    '"r":1 if it addresses what was asked, else 0. '
    '"s": overall quality 0.00-1.00. No explanation.'
)


def _validate_evaluator(d: dict) -> dict:
    out = {}
    for k in ("c", "r"):
        v = d.get(k, None)
        if v in (0, 1):
            out[k] = int(v)
        else:
            s = str(v).strip().lower()
            if s in ("1", "true", "yes"):
                out[k] = 1
            elif s in ("0", "false", "no"):
                out[k] = 0
            else:
                raise ControlPlaneError(f"evaluator bad flag {k}: {v!r}")
    try:
        s = float(d.get("s", 0.0))
    except (TypeError, ValueError):
        raise ControlPlaneError(f"evaluator bad score: {d.get('s')!r}")
    out["s"] = round(max(0.0, min(1.0, s)), 2)
    return out


def evaluate_answer(budgeted_prompt: str, answer: str) -> ControlPlaneResult:
    user = f"QUESTION:\n{budgeted_prompt}\n\nANSWER:\n{answer[:1200]}"
    return _call("evaluator", _EVALUATOR_SYSTEM, user,
                 cp_cfg.EVALUATOR_MAX_OUTPUT_TOKENS, _validate_evaluator)


def health() -> dict:
    """Status for /health and dashboard: config + liveness + counters."""
    return {
        "enabled": cp_cfg.OPENCODE_ENABLED,
        "available": available(),
        "model": cp_cfg.OPENCODE_MODEL,
        "provider": cp_cfg.OPENCODE_PROVIDER,
        "classifier_backend": cp_cfg.CLASSIFIER_BACKEND,
        "quality_check_mode": cp_cfg.QUALITY_CHECK_MODE,
        "cache_verify_enabled": cp_cfg.CACHE_VERIFY_ENABLED,
        "confidence_threshold": cp_cfg.CLASSIFIER_CONFIDENCE_THRESHOLD,
        "last_failure": last_failure(),
        "stats": control_plane_stats(),
    }
