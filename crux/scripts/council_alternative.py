"""The withdrawn council alternative, as patterns one implementation owns.

Older cycle templates let a conductor replace the council with harness-native
agents ("or, equivalently, dispatch 5 parallel review agents"), gave each seat
one dimension, named council models in prose ("Claude + Gemini + GPT"), let a
deep-review or deep-architecture agent settle findings, and offered a
parallel-reviewer fallback when no key was configured. Council deliberation is
now always the council runner through the gateway, so every one of those offers
is withdrawn.

Two readers share these patterns:

* the gate-information query in `advance-run.py`, which prints a correction
  notice when a prompt of a book that already exists still carries one. The book
  itself is never rewritten; the notice corrects it when it executes.
* the shipped-surface content scan, which asserts no template, skill or agent
  still offers one.

Matching runs over whitespace-normalized text with backticks removed, so a
phrase broken across lines of a YAML block scalar still matches. The patterns
are anchored on the offer, not on the bare words: a sentence that names a
reviewer agent, or the three providers, without offering them as the council
matches nothing.

Stdlib only.
"""
from __future__ import annotations

import re
from typing import NamedTuple


class WithdrawnPattern(NamedTuple):
    """One withdrawn offer: a stable id, its compiled pattern, and what it offers."""

    id: str
    pattern: "re.Pattern[str]"
    offers: str


_I = re.IGNORECASE

WITHDRAWN_ALTERNATIVE_PATTERNS: tuple[WithdrawnPattern, ...] = (
    WithdrawnPattern(
        "native-agents-equivalent",
        re.compile(r"\bequivalently,?\s+dispatch\s+(?:\d+|three|five)\s+parallel\s+review(?:er)?\s+agents\b", _I),
        "harness-native review agents as an equivalent of the council",
    ),
    WithdrawnPattern(
        "native-agents-alternative",
        re.compile(r"\bor\s+(?:dispatch\s+)?(?:\d+|three|five)\s+parallel\s+review(?:er)?\s+agents\b", _I),
        "harness-native review agents as an alternative to the council",
    ),
    WithdrawnPattern(
        "parallel-reviewer-fallback",
        re.compile(r"\b(?:use|uses|using)\s+the\s+(?:sanctioned\s+)?parallel[- ]reviewer\s+fallback\b"
                   r"|\bparallel[- ]reviewer\s+fallback\s+covers\b", _I),
        "reviewer agents as a fallback when the council cannot run",
    ),
    WithdrawnPattern(
        "deep-review-agent-settles",
        # "Agent" is the harness tool name, so it is matched case-sensitively.
        re.compile(r"\b[Dd]eep[- ][Rr]eview\s+Agent\b"),
        "a deep-review agent settling council findings in place of a council round",
    ),
    WithdrawnPattern(
        "deep-architecture-agent",
        # Matches "deep-architecture Agent" and "deep-architecture-review Agent".
        re.compile(r"\b[Dd]eep[- ]architecture(?:[- ]review)?\s+Agent\b"),
        "a deep-architecture agent rendering a binding verdict in place of a council round",
    ),
    WithdrawnPattern(
        "council-models-in-prose",
        re.compile(r"\bClaude\s*\+\s*Gemini\s*\+\s*GPT\b", _I),
        "council models named in prose, which a conductor reads as seat models",
    ),
    WithdrawnPattern(
        "one-dimension-per-seat",
        re.compile(r"\beach\s+(?:seat\s+)?own(?:s|ing)\s+one\s+(?:quality\s+)?dimension\b", _I),
        "one dimension per seat, which makes the dimension count set the seat count",
    ),
)


CORRECTION_NOTICE = (
    "CORRECTION: this prompt carries a withdrawn council alternative. Council "
    "deliberation is the council runner (run-council.py) obtaining verdicts from "
    "three providers through the gateway; it is never harness-native agents, a "
    "reviewer fallback, or a deep-review agent. Every seat assesses every "
    "dimension the prompt names. Convene the council with the council runner and "
    "attach its council record. When the configured council cannot run, the "
    "council runner writes a DEFER_TO_HUMAN record and the gate stops for the "
    "owner. The book's "
    "text is left unchanged; this notice is the correction. The procedure is in "
    "the run-promptbook skill's references/gates.md section 4: commit every "
    "subject, run run-council.py with --round set as that section states, and act "
    "on its exit code as that section's table says. The council runner commits "
    "the attempt record and the council record itself."
)


def normalize(text: str) -> str:
    """Collapse runs of whitespace to one space and drop backticks."""
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


def find_withdrawn_alternative(text: str) -> list[str]:
    """The ids of every withdrawn offer `text` carries, in pattern order.

    An empty list means the text carries none. Non-text input carries none.
    """
    if not isinstance(text, str) or not text:
        return []
    flat = normalize(text)
    return [p.id for p in WITHDRAWN_ALTERNATIVE_PATTERNS if p.pattern.search(flat)]


__all__ = [
    "WithdrawnPattern",
    "WITHDRAWN_ALTERNATIVE_PATTERNS",
    "CORRECTION_NOTICE",
    "normalize",
    "find_withdrawn_alternative",
]
