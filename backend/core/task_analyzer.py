"""Task Analyzer — Phase 3. Transparent heuristics, no ML model.

For every prompt returns task_type, difficulty 0-1, confidence 0-1,
required_capabilities (+ per-capability thresholds), detected signals.
Modular: later phases can swap in an ML classifier behind the same function.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[a-zA-Z_#+\.]+")

KEYWORDS: dict[str, set[str]] = {
    "summarization": {"summarize", "summary", "tldr", "recap", "brief", "outline"},
    "coding": {"code", "function", "class", "bug", "debug", "refactor", "implement",
               "python", "javascript", "typescript", "java", "sql", "compile",
               "deploy", "script", "program", "rest api", "sdk"},
    "debugging": {"debug", "traceback", "stacktrace", "race", "deadlock", "segfault",
                  "exception", "error", "fix", "reproduce"},
    "reasoning": {"why", "prove", "explain", "reason", "logic", "infer", "deduce",
                  "analyze", "compare", "evaluate", "tradeoff", "trade-off"},
    "mathematics": {"math", "equation", "integral", "derivative", "probability",
                    "theorem", "calculate", "solve", "algebra", "matrix", "statistics"},
    "architecture": {"architecture", "architect", "distributed", "scalab", "fault",
                     "microservice", "kubernetes", "system design", "banking",
                     "infrastructure", "design a system"},
    "analysis": {"analyze", "analysis", "report", "review", "assess", "critique",
                 "pros", "cons", "benchmark"},
    "long_context": {"document", "paper", "contract", "transcript", "book",
                     "manual", "policy", "attachment"},
}
MULTI_STEP_HINTS = {"step by step", "first", "then", "finally", "plan", "steps",
                    "stage", "phase", "roadmap"}
# "What is X?" / "Define X" with no other task signals = unambiguous general
# question, even though it is short. Without this, short prompts sink into
# low-confidence safety routing and waste the strong model on trivia.
SIMPLE_QUESTION_RE = re.compile(
    r"^\s*(what is|what are|what's|whats|define|who is|who was|explain)\b")


@dataclass
class TaskAnalysis:
    task_type: str
    difficulty_score: float
    confidence: float
    required_capabilities: list[str]
    required_thresholds: dict[str, float]
    detected_signals: list[str]
    estimated_input_tokens: int
    word_count: int
    # --- control-plane fields (Phase 8). Legacy analyzer leaves defaults. ---
    backend: str = "legacy_ml"          # legacy_ml | opencode
    fallback_used: bool = False         # LLM classifier failed -> legacy ran
    fallback_reason: str = ""
    control_plane: dict | None = None   # raw labels + measured token usage


def _hits(prompt: str, words: set[str]) -> int:
    low = prompt.lower()
    return sum(1 for w in words if w in low)


def analyze(prompt: str, context: str | None = None) -> TaskAnalysis:
    text = (prompt or "").strip()
    low = text.lower()
    words = _WORD_RE.findall(low)
    wc = len(words)
    ctx = (context or "").strip()
    ctx_tokens = len(ctx) // 4 if ctx else 0

    scores: dict[str, int] = {k: _hits(low, v) for k, v in KEYWORDS.items()}
    # architecture multi-word phrases counted via substring already in _hits
    task_type = max(scores, key=lambda k: scores[k])
    if scores[task_type] == 0:
        task_type = "general"
    simple_question = (
        wc <= 25 and all(s == 0 for s in scores.values())
        and SIMPLE_QUESTION_RE.match(low) is not None
    )
    if simple_question:
        task_type = "general"

    signals: list[str] = []
    difficulty = 0.10  # base: every request needs some intelligence
    if wc > 100:
        difficulty += 0.15
        signals.append("long_prompt")
    if wc > 300:
        difficulty += 0.15
        signals.append("very_long_prompt")
    if scores["reasoning"] > 0 or scores["analysis"] > 0:
        difficulty += 0.20
        signals.append("reasoning")
    if scores["coding"] > 0 or scores["debugging"] > 0:
        difficulty += 0.15
        signals.append("coding")
    if scores["architecture"] > 0:
        difficulty += 0.20
        signals.append("architecture")
    if scores["mathematics"] > 0:
        difficulty += 0.15
        signals.append("math")
    if any(h in low for h in MULTI_STEP_HINTS):
        difficulty += 0.10
        signals.append("multi_step")
    if len(text) > 4000:
        difficulty += 0.15
        signals.append("large_context")
    if ctx_tokens > 1000:
        difficulty += 0.10
        signals.append("large_reusable_context")
    difficulty = round(min(1.0, difficulty), 3)

    # Required capabilities: primary axis + supporting axis for hard tasks.
    primary = {
        "summarization": ["summarization"],
        "coding": ["coding"],
        "debugging": ["coding", "reasoning"],
        "reasoning": ["reasoning"],
        "mathematics": ["math", "reasoning"],
        "architecture": ["reasoning", "coding"],
        "analysis": ["reasoning"],
        "long_context": ["long_context", "summarization"],
        "general": ["general"],
    }[task_type]
    required = list(primary)
    if difficulty >= 0.6 and "reasoning" not in required:
        required.append("reasoning")
    # Threshold scales with difficulty: easy tasks accept ~0.55, hard need ~0.85.
    thresh = round(0.50 + 0.40 * difficulty, 3)
    thresholds = {c: (thresh if c != "general" else round(0.40 + 0.30 * difficulty, 3))
                  for c in required}

    # Confidence: strong single-category signal + enough words = high.
    # Short prompts are penalized ONLY when they carry no signal at all
    # ("Explain this."). Short but clear prompts keep their confidence.
    ranked = sorted(scores.values(), reverse=True)
    confidence = 0.55
    if simple_question:
        confidence = 0.85
        signals.append("simple_question")
    else:
        if scores.get(task_type, 0) >= 2:
            confidence += 0.15
        elif scores.get(task_type, 0) == 1:
            confidence += 0.05
        if wc >= 20:
            confidence += 0.10
        if len(text) < 30 and all(s == 0 for s in scores.values()):
            confidence -= 0.10  # short AND signal-free = genuinely ambiguous
    if len(ranked) > 1 and ranked[0] > 0 and ranked[0] == ranked[1]:
        confidence -= 0.15  # tie between categories
        signals.append("ambiguous_category")
    if task_type == "general" and wc >= 10:
        confidence += 0.10
    confidence = round(max(0.30, min(0.97, confidence)), 3)

    signals = sorted(set(signals + ([task_type] if task_type != "general" else [])))
    return TaskAnalysis(
        task_type=task_type,
        difficulty_score=difficulty,
        confidence=confidence,
        required_capabilities=required,
        required_thresholds=thresholds,
        detected_signals=signals,
        estimated_input_tokens=max(8, len(text) // 4) + ctx_tokens,
        word_count=wc,
    )
