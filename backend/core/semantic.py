"""Semantic cache similarity + safety gates — Phase 7.

Embedding-free semantic similarity: transparent bag-of-character-3-grams
vectors compared with cosine similarity. No external embedding model, no API
cost, deterministic, fast enough for a 256-entry cache scan. Safety gates
then veto unsafe reuse.

Gate philosophy (from the spec): similarity alone is NOT enough. Two prompts
can be 95% similar and mean different things ("2 + 2" vs "2 x 2"). Gates
check the *decision-relevant* features:
  numbers         operands must match exactly (25% of 400 != 25% of 500)
  operators       + - * / % ^ must match (2 + 2 != 2 * 2)
  ordinals        first/second/third... must match (first vs second item)
  units           kg/lb, celsius/fahrenheit, usd/eur must match
  directions      increase/decrease, max/min, asc/desc must match
  language        programming language mentions must match
  data structures list/dict/tree/graph mentions must match
  function names  snake_case/camelCase identifiers must match
  intent          question vs command shape must match
  negation        don't/never/without must check (polarity flip = unsafe)
  context         different reusable context -> never reuse
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_NGRAM_RE = re.compile(r"[a-z0-9]+")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_OP_RE = re.compile(r"[+\-*/%^=<>]")
_IDENT_RE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b|\b[a-z]+[A-Z][a-zA-Z0-9]*\b")
_ORDINALS = {"first", "second", "third", "fourth", "fifth", "sixth", "seventh",
             "eighth", "ninth", "tenth", "last", "final", "next", "previous"}
_UNITS = {"kg", "lb", "lbs", "miles", "km", "celsius", "fahrenheit", "usd",
          "eur", "gb", "mb", "minutes", "hours", "days", "weeks", "years"}
_DIRECTIONS = {"increase", "decrease", "max", "min", "maximum", "minimum",
               "ascending", "descending", "asc", "desc", "sort", "reverse"}
_LANGS = {"python", "javascript", "typescript", "html", "java", "c++", "c#", "go",
          "rust", "sql", "bash", "ruby", "php", "swift", "kotlin", "r"}
_STRUCTS = {"list", "array", "dict", "dictionary", "set", "tuple", "stack",
            "queue", "tree", "graph", "heap", "linked", "hashmap", "string"}
_NEGATIONS = {"not", "no", "dont", "never", "without", "except",
              "avoid", "exclude", "skip", "cant", "wont"}
_QUESTION_WORDS = {"what", "why", "how", "when", "who", "where", "which",
                   "is", "are", "can", "does", "do", "should"}
_CODE_COMMANDS = {"write", "create", "build", "make", "generate", "implement",
                  "refactor", "fix", "convert", "translate"}
# Math-execution verbs are intent-neutral: "Calculate 15% of 200" and
# "What is 15% of 200?" expect the SAME output, so they must not veto.
_MATH_VERBS = {"calculate", "compute", "solve", "evaluate"}
_COMMANDS = {"explain", "summarize", "show", "give", "tell", "list",
             "describe"} | _CODE_COMMANDS
# Words ignored by the salient-words gate (function words + command/math
# verbs: rephrasings may swap these freely; content words may not).
_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
              "of", "to", "in", "on", "for", "with", "and", "or", "but", "if",
              "then", "than", "as", "at", "by", "from", "up", "about", "into",
              "over", "after", "what", "whats", "how", "why", "when", "who",
              "where", "which", "that", "this", "these", "those", "it", "its",
              "i", "you", "we", "they", "he", "she", "do", "does", "did", "can",
              "could", "should", "would", "will", "shall", "may", "might",
              "must", "please", "me", "my", "your", "our", "there", "here",
              "using", "use", "used"} | _COMMANDS | _MATH_VERBS

_APOSTROPHE_FIXES = (("what's", "what is"), ("it's", "it is"),
                     ("that's", "that is"), ("there's", "there is"))


def canonicalize(text: str) -> str:
    """Normalize wording variants so gates compare like with like.

    "What is 15 percent of 200?" and "What is 15% of 200?" must produce
    IDENTICAL features — otherwise the operator gate vetoes a safe paraphrase.
    """
    t = (text or "").lower()
    for a, b in _APOSTROPHE_FIXES:
        t = t.replace(a, b)
    t = t.replace("'", "")            # don't -> dont
    t = re.sub(r"\bpercent\b", "%", t)  # 15 percent -> 15 %
    return t


def _tokens(text: str) -> list[str]:
    return _NGRAM_RE.findall((text or "").lower())


def _ngrams(text: str, n: int = 3) -> list[str]:
    """Character 3-grams over word characters — robust to typos/word order."""
    grams: list[str] = []
    for t in _tokens(text):
        padded = f"  {t}  "
        grams.extend(padded[i:i + n] for i in range(len(padded) - n + 1))
    return grams


def _vec(text: str) -> dict[str, int]:
    v: dict[str, int] = {}
    for g in _ngrams(text):
        v[g] = v.get(g, 0) + 1
    return v


def cosine(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    if len(b) < len(a):
        a, b = b, a
    dot = sum(x * b.get(g, 0) for g, x in a.items())
    norms = math.sqrt(sum(x * x for x in a.values())) * math.sqrt(sum(x * x for x in b.values()))
    return dot / norms if norms else 0.0


def similarity(a: str, b: str) -> float:
    return cosine(_vec(a), _vec(b))


@dataclass
class Features:
    """Decision-relevant features for the safety gates."""
    numbers: tuple = ()
    operators: tuple = ()
    ordinals: tuple = ()
    units: tuple = ()
    directions: tuple = ()
    langs: tuple = ()
    structs: tuple = ()
    idents: tuple = ()
    negations: tuple = ()
    intent: str = "general"  # question | command | general
    word_count: int = 0
    raw: str = ""


@dataclass
class GateResult:
    safe: bool
    reasons: list[str] = field(default_factory=list)


def extract_features(text: str) -> Features:
    low = canonicalize(text)
    words = set(_tokens(low))
    return Features(
        numbers=tuple(sorted(_NUM_RE.findall(low))),
        operators=tuple(sorted(set(_OP_RE.findall(low)))),
        ordinals=tuple(sorted(words & _ORDINALS)),
        units=tuple(sorted(words & _UNITS)),
        directions=tuple(sorted(words & _DIRECTIONS)),
        langs=tuple(sorted(words & _LANGS)),
        structs=tuple(sorted(words & _STRUCTS)),
        idents=tuple(sorted(set(_IDENT_RE.findall(low)))),
        negations=tuple(sorted(words & _NEGATIONS)),
        intent=("question" if words & _QUESTION_WORDS else
                "command" if words & _CODE_COMMANDS else "general"),
        word_count=len(_tokens(low)),
        raw=low,
    )


def _salient_words(text: str) -> set[str]:
    """Content words after canonicalization, minus stopwords/command verbs.

    Catches meaning flips that no other gate covers, e.g. "capital of
    France" vs "capital of Germany" (identical numbers/operators/intent).
    """
    return set(_tokens(canonicalize(text))) - _STOPWORDS


def check_gates(a: Features, b: Features, same_context: bool = True) -> GateResult:
    """Veto reuse when any decision-relevant feature differs.

    Every gate is a hard veto: high similarity with a different operator,
    operand, ordinal, unit, direction, language, structure, identifier,
    intent, negation polarity, or salient content word is a MISS.
    """
    reasons: list[str] = []
    if not same_context:
        reasons.append("different context")
    if a.numbers != b.numbers:
        reasons.append(f"numbers {a.numbers} != {b.numbers}")
    if a.operators != b.operators:
        reasons.append(f"operators {a.operators} != {b.operators}")
    if a.ordinals != b.ordinals:
        reasons.append(f"ordinals {a.ordinals} != {b.ordinals}")
    if a.units != b.units:
        reasons.append(f"units {a.units} != {b.units}")
    if a.directions != b.directions:
        reasons.append(f"directions {a.directions} != {b.directions}")
    if a.langs != b.langs:
        reasons.append(f"language {a.langs} != {b.langs}")
    if a.structs != b.structs:
        reasons.append(f"data structures {a.structs} != {b.structs}")
    if a.idents != b.idents:
        reasons.append(f"identifiers {a.idents} != {b.idents}")
    if a.negations != b.negations:
        reasons.append(f"negation {a.negations} != {b.negations}")
    if a.intent != b.intent and "general" not in (a.intent, b.intent):
        # Only a true question<->command mismatch vetoes; "general" (no
        # markers either way) is compatible with both.
        reasons.append(f"intent {a.intent} != {b.intent}")
    sa, sb = _salient_words(a.raw), _salient_words(b.raw)
    if sa != sb:
        reasons.append(f"salient words {sorted(sa)} != {sorted(sb)}")
    return GateResult(safe=not reasons, reasons=reasons)
