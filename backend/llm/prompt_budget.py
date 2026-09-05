"""Task-aware prompt budgeting — keep control-plane prompts tiny.

Control-plane calls see a COMPACT view of the prompt, never the full text:
  - classifier: first N chars (task signals live at the start) + tail marker
  - verifier:   full question up to budget (must judge the actual ask)
  - evaluator:  question up to budget (answer is truncated separately)

Truncation is deterministic and recorded in the result so the dashboard can
show exactly what the control plane saw (explainability requirement).
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.llm import config as cp_cfg


@dataclass
class BudgetedPrompt:
    text: str
    original_chars: int
    truncated: bool

    def view(self) -> dict:
        return {"text": self.text, "original_chars": self.original_chars,
                "truncated": self.truncated}


def _clip(text: str, max_chars: int) -> BudgetedPrompt:
    t = (text or "").strip()
    n = len(t)
    if n <= max_chars:
        return BudgetedPrompt(text=t, original_chars=n, truncated=False)
    # Keep the head (task signals concentrate there) and mark the cut.
    return BudgetedPrompt(text=t[:max_chars].rstrip() + " …[truncated]",
                          original_chars=n, truncated=True)


def budget_for_classifier(prompt: str, context: str | None = None) -> BudgetedPrompt:
    """Classifier sees the prompt head; context is summarized to a hint only
    (the task class rarely depends on the full reusable context)."""
    budget = cp_cfg.CONTROL_PLANE_PROMPT_MAX_CHARS
    ctx = (context or "").strip()
    if ctx:
        hint = f"[context: {len(ctx)} chars attached]"
        room = max(64, budget - len(hint) - 1)
        head = _clip(prompt, room)
        text = f"{hint}\n{head.text}"
        return BudgetedPrompt(text=text, original_chars=head.original_chars,
                              truncated=head.truncated)
    return _clip(prompt, budget)


def budget_for_verifier(prompt: str) -> BudgetedPrompt:
    return _clip(prompt, cp_cfg.CONTROL_PLANE_PROMPT_MAX_CHARS)


def budget_for_evaluator(prompt: str) -> BudgetedPrompt:
    return _clip(prompt, cp_cfg.CONTROL_PLANE_PROMPT_MAX_CHARS)
