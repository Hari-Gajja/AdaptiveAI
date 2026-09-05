"""Prompt Builder — task-aware minimal system prompts (spec §19/§20).

The task model receives a SHORT system prompt shaped by task type + output
budget, not a bloated generic instruction. Rules implemented:

  1. system prompt only when it adds signal (never for plain general QA)
  2. per-task templates: math / coding / debugging / reasoning /
     classification / general_qa / summarization
  3. templates state the output contract (format, length) explicitly
  4. dynamic output budget from token_optimizer.predict_output_budget
  5. reusable context goes in the system role (frees user turn for the ask)
  6. no chain-of-thought demands (burns output tokens)
  7. templates are plain strings — inspectable, testable, cheap
  8. budget hint is one clause, not a paragraph
  9. math tasks demand the final numeric answer on its own line (parseable)
 10. everything deterministic — same input, same messages, every time
"""
from __future__ import annotations

from backend.core.task_analyzer import TaskAnalysis

_BUDGET_CLAUSE = {
    128: "Be brief.",
    256: "Keep it focused.",
    512: "Be thorough but avoid padding.",
}


def _budget_clause(budget: int) -> str:
    if budget <= 128:
        return _BUDGET_CLAUSE[128]
    if budget <= 256:
        return _BUDGET_CLAUSE[256]
    return _BUDGET_CLAUSE[512]


_TEMPLATES: dict[str, str] = {
    "mathematics": (
        "Solve the problem step by step internally, then give the final "
        "numeric answer on its own last line as `Answer: <value>`. "
        "{budget}"),
    "coding": (
        "Write correct, runnable code. Output the code in a fenced block "
        "with no surrounding prose unless the ask requires explanation. "
        "{budget}"),
    "debugging": (
        "Identify the root cause first in one sentence, then give the "
        "minimal fix as code. Do not restate the whole program. {budget}"),
    "reasoning": (
        "Reason carefully, then state the conclusion first followed by at "
        "most three supporting points. {budget}"),
    "classification": (
        "Classify the input. Reply with only the label, nothing else. "
        "{budget}"),
    "general_qa": (
        "Answer the question directly. {budget}"),
    "summarization": (
        "Summarize the key points. Preserve numbers and named entities "
        "exactly. {budget}"),
    "architecture": (
        "Propose a design: components, data flow, and failure modes. "
        "Use short bullets. {budget}"),
    "analysis": (
        "Give a structured assessment: verdict first, then evidence. "
        "{budget}"),
    "long_context": (
        "Answer using the provided document. Quote sparingly. {budget}"),
}

# task_type -> template key (legacy analyzer vocabulary)
_TYPE_TO_TEMPLATE = {
    "mathematics": "mathematics",
    "coding": "coding",
    "debugging": "debugging",
    "reasoning": "reasoning",
    "architecture": "architecture",
    "analysis": "analysis",
    "long_context": "long_context",
    "summarization": "summarization",
    "general": "general_qa",
}


def build_system_prompt(analysis: TaskAnalysis, output_budget: int) -> str | None:
    """System prompt for the task model, or None when none adds signal.

    Rule 1: plain short general questions get NO system prompt — the model's
    defaults are already right and every token costs money.
    """
    key = _TYPE_TO_TEMPLATE.get(analysis.task_type, "general_qa")
    if key == "general_qa" and not analysis.has_constraints:
        return None
    template = _TEMPLATES[key]
    return template.format(budget=_budget_clause(output_budget)).strip()


def build_task_messages(prompt: str, analysis: TaskAnalysis,
                        context: str | None = None,
                        output_budget: int | None = None) -> list[dict]:
    """Messages for the task-model call.

    - context (reusable docs/policy) goes in the system role
    - task template (when any) joins it as a second system clause
    - user turn stays the raw prompt, untouched
    """
    budget = output_budget or analysis.estimated_output_tokens or 256
    system_parts: list[str] = []
    if context:
        system_parts.append(context.strip())
    sp = build_system_prompt(analysis, budget)
    if sp:
        system_parts.append(sp)
    messages: list[dict] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.append({"role": "user", "content": prompt})
    return messages


def templates() -> dict[str, str]:
    """Expose the template table (dashboard/docs/tests)."""
    return dict(_TEMPLATES)
