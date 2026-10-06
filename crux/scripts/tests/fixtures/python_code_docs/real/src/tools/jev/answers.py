"""Validated answer types.

The one structural decision worth stating: **`NoulAnswer` has no `confidence`
attribute at all** — not an `Optional[float]` that is always `None`. The
interface documents no confidence field on a noul answer, and the accepted
decision forbids fabricating one. Modelling the absence as a missing attribute
makes a caller that reaches for it raise `AttributeError` at the call site,
rather than read a plausible `None` (or, worse, a default) and carry on. An
absence you can accidentally paper over is not an absence.

`ChoiceAnswer.confidence` and `ScoreAnswer.confidence` are `float | None`, where
`None` means **unavailable** and never a default. Nothing in this package
supplies a fallback for it, and nothing thresholds on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence


@dataclass(frozen=True)
class NoulAnswer:
    """A probability in [0, 1] that the statement is true.

    The vendor documents near-1 as a strong yes, near-0 a strong no, and near-0.5
    as uncertain. An uncertain answer is a VALID answer: it is returned, not
    raised, which is what keeps model ambiguity distinct from transport failure.
    """

    question_id: str
    noul: float

    @property
    def is_uncertain(self) -> bool:
        """True near the midpoint. A reporting convenience, never a gate.

        Deliberately not a threshold anybody may act on: no code in this package
        branches on it, and the accepted decision forbids a threshold gating
        anything.
        """
        return 0.4 <= self.noul <= 0.6


@dataclass(frozen=True)
class ChoiceAnswer:
    """The selected option, checked for membership in the declared option set."""

    question_id: str
    choice: str
    confidence: float | None = None
    probabilities: Mapping[str, float] | None = None


@dataclass(frozen=True)
class ScoreAnswer:
    """A position along the ordered rubric levels.

    `level_count` is the N the request declared. `observed_convention` records
    what the RESPONSE suggests about the index base — `zero-based`, `one-based`,
    or `indeterminate` — and is a report, never an assertion: the interface does
    not state the base, so this package never normalizes the value and never maps
    it to a level name.
    """

    question_id: str
    score: float
    level_count: int
    observed_convention: str
    confidence: float | None = None
    probabilities: Mapping[str, float] | None = None
    legend: Sequence[str] | None = None


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class Usage:
    """Token counts, and cost when the gateway returns one.

    `cost` stays `None` when absent rather than defaulting to 0.0 — a missing
    cost and a free call are different facts, and collapsing them would let a
    spend report understate silently.
    """

    input_tokens: int
    output_tokens: int
    cost: float | None = None


@dataclass(frozen=True)
class DecisionsResult:
    """One validated Decisions response.

    `notes` carries observations that are RECORDED rather than refused — a
    probability distribution that does not sum to 1, or a response `model`
    differing from the pinned request. Neither is a documented invariant, so
    refusing on them would be inventing a spec; dropping them silently would
    hide a real signal. They are surfaced as text for a human to read.
    """

    model: str
    answers: Mapping[str, Answer]
    usage: Usage
    id: str | None = None
    provider: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "NoulAnswer",
    "ChoiceAnswer",
    "ScoreAnswer",
    "Answer",
    "Usage",
    "DecisionsResult",
]
