"""Token Optimizer — prompt normalization, token estimation, output budgets.

Three jobs (spec §3), all deterministic and free (no API calls):

1. normalize_prompt
   Collapse redundant whitespace WITHOUT touching code blocks (indentation
   inside ``` fences is semantic). Returns measured savings so the dashboard
   can show "tokens saved by normalization" per request.

2. estimate_tokens
   Deterministic chars/4 estimator — the SAME approximation the control plane
   uses when a provider omits usage. Never fabricates precision: callers that
   need exact counts must use provider-reported usage.

3. predict_output_budget
   Small/medium/large max_tokens from prompt signals (code, lists, "brief",
   "in detail", word count). Feeds the router's cost model AND the generation
   call so we don't pay for 512 output tokens on a one-line answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


@dataclass
class NormalizedPrompt:
    text: str
    original_chars: int
    normalized_chars: int
    original_tokens_estimate: int
    normalized_tokens_estimate: int
    tokens_saved: int
    compression_ratio: float  # normalized/original (1.0 = nothing saved)

    def view(self) -> dict:
        return {
            "original_chars": self.original_chars,
            "normalized_chars": self.normalized_chars,
            "original_tokens_estimate": self.original_tokens_estimate,
            "normalized_tokens_estimate": self.normalized_tokens_estimate,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": round(self.compression_ratio, 4),
        }


def estimate_tokens(text: str | None) -> int:
    """Deterministic token estimate (chars/4). Same rule the control plane
    uses for provider-omitted usage — one estimator everywhere."""
    return max(0, len(text or "")) // 4


def normalize_prompt(prompt: str) -> NormalizedPrompt:
    """Whitespace normalization that preserves code fences verbatim.

    Outside fences: collapse runs of spaces/tabs, drop trailing spaces,
    squeeze 3+ blank lines to one. Inside fences: untouched (indentation is
    semantic in Python/YAML; reformatting code would change the task).
    """
    text = prompt or ""
    parts: list[str] = []          # (is_fence, chunk)
    pos = 0
    for m in _FENCE_RE.finditer(text):
        if m.start() > pos:
            parts.append((False, text[pos:m.start()]))
        parts.append((True, m.group(0)))
        pos = m.end()
    if pos < len(text):
        parts.append((False, text[pos:]))

    out: list[str] = []
    for is_fence, chunk in parts:
        if is_fence:
            out.append(chunk)
        else:
            c = _WS_RE.sub(" ", chunk)
            c = "\n".join(line.rstrip() for line in c.split("\n"))
            c = _BLANK_RE.sub("\n\n", c)
            out.append(c)
    normalized = "".join(out).strip()

    oc = len(text)
    nc = len(normalized)
    ot = estimate_tokens(text)
    nt = estimate_tokens(normalized)
    return NormalizedPrompt(
        text=normalized, original_chars=oc, normalized_chars=nc,
        original_tokens_estimate=ot, normalized_tokens_estimate=nt,
        tokens_saved=max(0, ot - nt),
        compression_ratio=(nc / oc) if oc else 1.0,
    )


# ---- output budget prediction -------------------------------------------
# Signals that justify larger outputs, checked against the raw prompt.
_CODE_HINTS = ("```", "def ", "class ", "function", "implement", "write a ",
               "refactor", "sql", "regex", "script")
_LIST_HINTS = ("list", "steps", "bullet", "table", "compare", "pros and cons",
               "enumerate")
_LONG_HINTS = ("in detail", "comprehensive", "explain fully", "walk through",
               "step by step", "thorough")
_BRIEF_HINTS = ("brief", "one sentence", "two sentences", "in one line",
                "short answer", "tldr", "concise", "yes or no")

SMALL_BUDGET = 128
MEDIUM_BUDGET = 256
LARGE_BUDGET = 512


def predict_output_budget(prompt: str) -> tuple[int, list[str]]:
    """(max_tokens, signals) for the generation call.

    brief beats long (explicit brevity wins); code/list/detail hints push up;
    very short prompts default small. Deterministic, no API calls.
    """
    low = (prompt or "").lower()
    signals: list[str] = []
    budget = MEDIUM_BUDGET

    if any(h in low for h in _BRIEF_HINTS):
        budget = SMALL_BUDGET
        signals.append("brief_output_requested")
    elif any(h in low for h in _LONG_HINTS):
        budget = LARGE_BUDGET
        signals.append("detailed_output_requested")
    elif any(h in low for h in _CODE_HINTS):
        budget = LARGE_BUDGET
        signals.append("code_output_expected")
    elif any(h in low for h in _LIST_HINTS):
        budget = MEDIUM_BUDGET
        signals.append("structured_output_expected")

    wc = len(low.split())
    if wc <= 8 and budget == MEDIUM_BUDGET:
        budget = SMALL_BUDGET
        signals.append("very_short_prompt")
    elif wc > 150 and budget == MEDIUM_BUDGET:
        budget = LARGE_BUDGET
        signals.append("long_prompt_needs_room")
    return budget, signals
