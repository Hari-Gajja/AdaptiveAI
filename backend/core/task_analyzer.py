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
    # --- request metadata (spec §4). Derived locally, never asked from the
    # LLM — the classifier JSON stays tiny. Both analyzer paths fill these. ---
    language: str = "text"              # text | python | javascript | sql | ...
    has_math: bool = False              # arithmetic/percent/equation signals
    has_constraints: bool = False       # explicit output constraints (N items, format)
    requires_long_context: bool = False # prompt/context exceeds a model's window
    estimated_output_tokens: int = 256  # predicted output budget (token_optimizer)
    ambiguity: float = 0.0              # 0 clear .. 1 vague (1 - confidence, floored)
    quality_requirement: str = "standard"  # low | standard | high (from difficulty)

    def metadata_view(self) -> dict:
        """Compact metadata block for API responses and the benchmark."""
        return {
            "language": self.language,
            "has_math": self.has_math,
            "has_constraints": self.has_constraints,
            "requires_long_context": self.requires_long_context,
            "estimated_output_tokens": self.estimated_output_tokens,
            "ambiguity": round(self.ambiguity, 3),
            "quality_requirement": self.quality_requirement,
        }


# ---- §4 metadata derivation (shared by legacy + opencode paths) ----------
_LANG_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("python", ("python", "def ", "import ", "pandas", "numpy", "pip")),
    ("javascript", ("javascript", "js", "node", "npm", "const ", "=>")),
    ("typescript", ("typescript", " ts ", "interface {")),
    ("sql", ("sql", "select ", "join ", "group by", "insert into")),
    ("html", ("html", "<div", "<span")),
    ("css", ("css", "flexbox", "stylesheet")),
    ("java", ("java", "public class", "system.out")),
    ("c++", ("c++", "cpp", "#include")),
    ("go", ("golang", "go func", "package main")),
    ("rust", ("rust", "fn main", "cargo build")),
    ("bash", ("bash", "shell script", "chmod", "grep ")),
]
_MATH_RE = re.compile(
    r"\d+\s*[+\-*/^%]\s*\d+|\d+\s*%|\bpercent\b|\bequation\b|\bintegral\b|"
    r"\bderivative\b|\bprobability\b|\bsolve\b|\bcalculate\b|\bcompute\b")
_CONSTRAINT_RE = re.compile(
    r"\b\d+\s+(words|sentences|items|lines|bullet|examples|steps)\b|"
    r"\bin (two|three|four|five|\d+) sentences\b|\bone paragraph\b|"
    r"\bno more than\b|\bat most\b|\bexactly\b|\bjson\b|\btable\b|\bformat\b")
_AMBIGUOUS_RE = re.compile(
    r"\bit\b|\bthis\b|\bthat\b|\bstuff\b|\bthing\b|\bsomething\b", re.IGNORECASE)


def derive_metadata(prompt: str, context: str | None = None,
                    task_type: str = "general", difficulty: float = 0.1,
                    confidence: float = 0.5) -> dict:
    """Language / math / constraints / long-context / output budget / ambiguity.

    Pure heuristics over the raw text — deterministic, free, identical for
    both analyzer backends so downstream consumers see one shape.
    """
    text = (prompt or "").strip()
    low = text.lower()
    ctx = (context or "").strip()

    language = "text"
    for lang, hints in _LANG_HINTS:
        if any(h in low for h in hints):
            language = lang
            break

    has_math = bool(_MATH_RE.search(low)) or task_type == "mathematics"
    has_constraints = bool(_CONSTRAINT_RE.search(low))

    total_chars = len(text) + len(ctx)
    # Long-context need: > ~24k chars (~6k tokens) or explicit document words.
    requires_long_context = (
        total_chars > 24_000
        or any(w in low for w in ("document", "transcript", "contract",
                                  "entire", "whole file", "attachment")))

    budget, budget_signals = _output_budget(low, task_type)

    # Ambiguity: vague pronouns with no task keywords, short signal-free
    # prompts, or low classifier confidence all raise it.
    ambiguity = max(0.0, min(1.0, 1.0 - confidence))
    vague = len(_AMBIGUOUS_RE.findall(low))
    if vague and not any(k in low for k in
                         ("code", "math", "summar", "explain", "write", "debug")):
        ambiguity = min(1.0, ambiguity + 0.15 * vague)
    if len(text.split()) < 6:
        ambiguity = min(1.0, ambiguity + 0.10)

    if difficulty >= 0.7:
        quality_requirement = "high"
    elif difficulty <= 0.3:
        quality_requirement = "low"
    else:
        quality_requirement = "standard"

    return {
        "language": language,
        "has_math": has_math,
        "has_constraints": has_constraints,
        "requires_long_context": requires_long_context,
        "estimated_output_tokens": budget,
        "ambiguity": round(ambiguity, 3),
        "quality_requirement": quality_requirement,
        "budget_signals": budget_signals,
    }


def _output_budget(low: str, task_type: str) -> tuple[int, list[str]]:
    """Small/medium/large output budget from prompt signals (spec §3)."""
    from backend.core.token_optimizer import (LARGE_BUDGET, MEDIUM_BUDGET,
                                              SMALL_BUDGET)
    signals: list[str] = []
    budget = MEDIUM_BUDGET
    brief = ("brief", "one sentence", "two sentences", "in one line",
             "short answer", "tldr", "concise", "yes or no")
    long_h = ("in detail", "comprehensive", "explain fully", "walk through",
              "step by step", "thorough")
    code_h = ("```", "def ", "class ", "function", "implement", "write a ",
              "refactor", "sql", "regex", "script")
    if any(h in low for h in brief):
        budget = SMALL_BUDGET
        signals.append("brief_output_requested")
    elif any(h in low for h in long_h):
        budget = LARGE_BUDGET
        signals.append("detailed_output_requested")
    elif any(h in low for h in code_h) or task_type == "coding":
        budget = LARGE_BUDGET
        signals.append("code_output_expected")
    wc = len(low.split())
    if wc <= 8 and budget == MEDIUM_BUDGET:
        budget = SMALL_BUDGET
        signals.append("very_short_prompt")
    elif wc > 150 and budget == MEDIUM_BUDGET:
        budget = LARGE_BUDGET
        signals.append("long_prompt_needs_room")
    return budget, signals


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
    meta = derive_metadata(prompt, context, task_type, difficulty, confidence)
    return TaskAnalysis(
        task_type=task_type,
        difficulty_score=difficulty,
        confidence=confidence,
        required_capabilities=required,
        required_thresholds=thresholds,
        detected_signals=signals,
        estimated_input_tokens=max(8, len(text) // 4) + ctx_tokens,
        word_count=wc,
        language=meta["language"],
        has_math=meta["has_math"],
        has_constraints=meta["has_constraints"],
        requires_long_context=meta["requires_long_context"],
        estimated_output_tokens=meta["estimated_output_tokens"],
        ambiguity=meta["ambiguity"],
        quality_requirement=meta["quality_requirement"],
    )
