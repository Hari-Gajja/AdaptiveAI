"""Quality Evaluator — Phase 4. Deterministic, zero extra LLM calls.

Two methods, always labeled in the output:
  reference  benchmark question WITH a reference answer (grounded scoring)
  estimated  live playground question with NO reference (heuristic scoring,
             NEVER presented as ground truth — see `method` field)

overall = 0.5 * correctness + 0.3 * relevance + 0.2 * completeness  (0..1)

Lexical-overlap scoring is a rough proxy, not a judge of truth. It is
deliberately transparent so the benchmark methodology slide can state exactly
what was measured. A stronger judge (LLM-as-judge / embeddings) can replace
`evaluate()` behind the same signature later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "had",
    "are", "was", "were", "will", "would", "could", "should", "which", "their",
    "there", "about", "into", "your", "you", "our", "they", "them", "then",
    "than", "also", "such", "when", "what", "how", "why", "who", "not", "but",
    "all", "any", "can", "may", "its", "per", "via", "using", "used", "use",
    "one", "two", "new", "more", "most", "some", "each", "both", "between",
}

REFUSAL_PHRASES = {
    "i can't", "i cannot", "as an ai", "as a language model", "unable to help",
    "i'm sorry, but i can't", "i am sorry, but i cannot",
}

# Meta-instructions about answer SHAPE ("answer in two sentences") are not
# topic content: excluding them keeps relevance about the actual question.
META_WORDS = {
    "answer", "answers", "sentence", "sentences", "word", "words",
    "full", "briefly", "detail", "details",
}


def _stem(t: str) -> str:
    """Naive plural strip so 'requests' matches 'request', 'stands' ~ 'stand'."""
    if len(t) > 4 and t.endswith("s") and not t.endswith(("ss", "us", "is", "ous")):
        return t[:-1]
    return t


def content_tokens(text: str) -> list[str]:
    # Numbers kept at any length ("30" is meaningful in math answers).
    toks = [t for t in _TOKEN_RE.findall(text.lower())
            if (len(t) > 2 or t.isdigit()) and t not in STOPWORDS]
    return [s for s in (_stem(t) for t in toks) if s not in META_WORDS]


def _f1(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    if inter == 0:
        return 0.0
    prec = inter / len(sa)
    rec = inter / len(sb)
    return round(2 * prec * rec / (prec + rec), 3)


def _recall(needles: list[str], haystack: list[str]) -> float:
    if not needles:
        return 1.0
    s = set(haystack)
    hit = sum(1 for t in set(needles) if t in s)
    return round(hit / len(set(needles)), 3)


@dataclass
class QualityScore:
    correctness: float
    relevance: float
    completeness: float
    overall: float
    method: str  # "reference" | "estimated"
    scoring_detail: str = "lexical"


def _combine(c: float, r: float, m: float, method: str, scoring_detail: str = "lexical") -> QualityScore:
    overall = round(0.5 * c + 0.3 * r + 0.2 * m, 3)
    return QualityScore(correctness=c, relevance=r, completeness=m,
                        overall=overall, method=method, scoring_detail=scoring_detail)


def evaluate(answer: str, prompt: str, reference: str | None = None) -> QualityScore:
    answer = (answer or "").strip()
    prompt = (prompt or "").strip()
    if not answer:
        return QualityScore(0.0, 0.0, 0.0, 0.0, "reference" if reference else "estimated")
    low = answer.lower()
    if any(p in low for p in REFUSAL_PHRASES):
        # Refusal: relevant-ish (it responds) but not correct/complete.
        return _combine(0.2, 0.4, 0.1, "reference" if reference else "estimated", "refusal")

    a_toks = content_tokens(answer)
    p_toks = content_tokens(prompt)
    if reference:
        r_toks = content_tokens(reference)
        # correctness blends precision-oriented F1 with recall so short
        # factoid references ("30") still score when the answer contains them.
        correctness = round((_f1(a_toks, r_toks) + _recall(r_toks, a_toks)) / 2, 3)
        detail = "reference_lexical"
        math_prompt = any(word in prompt.lower() for word in ("calculate", "solve", "equation", "percent", "%", "how many"))
        reference_numbers = set(re.findall(r"\d+(?:\.\d+)?", reference))
        answer_numbers = set(re.findall(r"\d+(?:\.\d+)?", answer))
        if math_prompt and reference_numbers and reference_numbers == answer_numbers:
            correctness = max(correctness, 0.85)
            detail = "reference_math_numeric"
        relevance = _recall(p_toks, a_toks)
        length_ratio = min(1.0, len(a_toks) / max(1, len(r_toks)))
        completeness = round((_recall(r_toks, a_toks) + length_ratio) / 2, 3)
        return _combine(correctness, relevance, completeness, "reference", detail)

    # ---- estimated: no ground truth, heuristic signals only ----
    uniq_ratio = len(set(a_toks)) / max(1, len(a_toks))
    correctness = 0.80
    if len(a_toks) > 20 and uniq_ratio < 0.25:
        correctness = 0.30  # degenerate repetition loop
    relevance = _recall(p_toks, a_toks)
    completeness = min(1.0, len(a_toks) / max(10, len(p_toks)))
    completeness = round(completeness, 3)
    return _combine(correctness, relevance, completeness, "estimated", "estimated_heuristic")
