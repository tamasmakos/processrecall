"""How a position becomes text: the deterministic Ψ of FR-044.

Statements arrive already made — template-derived, never prose from a model —
and rendering is nothing but assembly: one line each, the support count beside
the claim it is the evidence for, in the order the caller ranked them. An
agent-authored note is the one statement nobody derived, so it travels the same
seam under a marker of its own rather than a channel of its own (FR-039).

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.render import BulletRenderer, Deadline

    text = BulletRenderer(counters, Deadline(started_at=entered_at)).render(statements)
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import sqrt
from typing import Protocol

from processrecall.config import Counters
from processrecall.graph.abstract import Pitfall, PitfallKind, TransitionEdge
from processrecall.graph.annotations import Annotation

#: The ceiling FR-042's ~300 tokens becomes without a tokeniser on the hot
#: path, at the conventional four characters per token (R8).
CHARACTER_BUDGET = 1200

#: The soft budget inside the hook's 5-second fence, in seconds (R9): the
#: elapsed time at which an answer is already too late to be worth serving.
SOFT_BUDGET_SECONDS = 0.25

#: The standard score of a 95% interval, the confidence FR-040 states every
#: served rate at.
_CONFIDENCE_Z = 1.96


@dataclass(frozen=True, slots=True)
class Deadline:
    """How much of the soft budget rendering may still spend (R9).

    Attributes:
        started_at: `time.perf_counter` as it read when the hook was entered.
            Entry, not render time, is the origin: the snapshot read happens
            in between, and a slow one is exactly what the budget catches.
    """

    started_at: float = field(default_factory=time.perf_counter)

    @property
    def exceeded(self) -> bool:
        """Whether the budget is spent, so that anything served now is late."""
        return time.perf_counter() - self.started_at > SOFT_BUDGET_SECONDS


class Origin(StrEnum):
    """Where a statement came from, which is what sets its line apart (FR-039).

    An authored note and a counted claim carry different authority — one is a
    person's reading of the move, the other is what the episodes did — so the
    reader is told which is which rather than left to infer it from wording.
    """

    STATISTICAL = "statistical"
    ANNOTATION = "annotation"


#: The label each origin is served under, ahead of its bullet. One table
#: rather than a branch in :func:`_line`: the label is the whole of what
#: FR-039 asks for, and an origin without one would render as indistinguishable
#: from a counted claim.
_LABELS: Mapping[Origin, str] = {
    Origin.STATISTICAL: "",
    Origin.ANNOTATION: "note:",
}


@dataclass(frozen=True, slots=True)
class GuidanceStatement:
    """One thing guidance has to say, and the evidence it rests on.

    Attributes:
        text: The claim, template-derived and therefore deterministic.
        support: Episodes behind the claim, always rendered (FR-044).
        origin: What kind of claim it is, and so how it is marked (FR-039).
            Statistical by default: every statement this phase derives is
            counted, and a note has to say so.
    """

    text: str
    support: int
    origin: Origin = Origin.STATISTICAL

    @classmethod
    def from_annotation(cls, annotation: Annotation, support: int) -> GuidanceStatement:
        """*annotation* as the statement it is served as, marked as authored.

        *support* is the move's, not the note's: a note is not evidence of
        itself, and FR-044 wants every line to carry the episodes behind the
        transition the reader is being told about.
        """
        return cls(text=annotation.text, support=support, origin=Origin.ANNOTATION)


def avoid_statements(edge: TransitionEdge) -> tuple[GuidanceStatement, ...]:
    """*edge* as the one avoid-this warning it has earned, where it has earned one (SC-005).

    Empty for a move whose refusals never crossed the support floor:
    `_EdgeFold._refused` attaches the `REFUSED` pitfall only
    above `Config.min_support`, so a refused move is served as something to
    steer away from, never as a next step, and below the floor it is not
    served at all.
    """
    return tuple(
        _avoidance(edge, pitfall)
        for pitfall in edge.pitfalls
        if pitfall.kind is PitfallKind.REFUSED
    )


class Renderer(Protocol):
    """The seam FR-044 requires: rendering is substitutable, and pure."""

    def render(self, statements: Sequence[GuidanceStatement]) -> str:
        """The text *statements* are served as, support counts included."""
        ...


class BulletRenderer:
    """The one renderer this phase ships: a bullet and a count per statement."""

    def __init__(self, counters: Counters, deadline: Deadline) -> None:
        self._counters = counters
        self._deadline = deadline

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def render(self, statements: Sequence[GuidanceStatement]) -> str:
        """*statements* as one string, in the order they were ranked in.

        Bounded at :data:`CHARACTER_BUDGET`: what does not fit is dropped
        whole, weakest evidence first, and the render is counted (R8). Past
        the soft budget the answer is silence rather than a late one (R9).
        """
        if self._deadline.exceeded:
            self._counters.bump("guidance_deadline_exceeded")
            return ""
        kept = _within_budget(statements)
        if len(kept) != len(statements):
            self._counters.bump("guidance_over_budget")
        return _assemble(kept)


def _within_budget(statements: Sequence[GuidanceStatement]) -> tuple[GuidanceStatement, ...]:
    """As many of *statements* as the ceiling allows, in the order given.

    Dropping runs in ascending support, so the reader loses the weakest claim
    before a better-evidenced one; equal support drops the later statement,
    which is the less immediate of the two.
    """
    kept = list(statements)
    lengths = [len(_line(statement)) for statement in kept]
    total = sum(lengths) + max(len(kept) - 1, 0)
    while kept and total > CHARACTER_BUDGET:
        index = _weakest(kept)
        total -= lengths[index] + (1 if len(kept) > 1 else 0)
        del kept[index]
        del lengths[index]
    return tuple(kept)


def _weakest(statements: Sequence[GuidanceStatement]) -> int:
    """Where in *statements* the one to drop next is."""
    return min(
        range(len(statements)),
        key=lambda index: (statements[index].support, -index),
    )


def _assemble(statements: Sequence[GuidanceStatement]) -> str:
    """*statements* as the one string they are served as, one line each."""
    return "\n".join(_line(statement) for statement in statements)


def _line(statement: GuidanceStatement) -> str:
    """*statement* as the single line it is served on, claim then evidence."""
    label = _LABELS[statement.origin]
    prefix = f"- {label} " if label else "- "
    return f"{prefix}{statement.text} ({statement.support} episodes)"


def _avoidance(edge: TransitionEdge, pitfall: Pitfall) -> GuidanceStatement:
    """*pitfall* as the warning it is served as: the move, then how often it is refused."""
    rate = _lower_bound(pitfall.refusal_rate, pitfall.observations)
    return GuidanceStatement(
        text=f"avoid {edge.target} after {edge.source}: refused at least {rate:.0%} of the time",
        support=pitfall.support,
    )


def _lower_bound(rate: float, observations: int) -> float:
    """The Wilson lower bound of *rate* over *observations*, at 95% (FR-040).

    What few observations buy is a weak claim, and the bound is where that is
    spent: two refusals out of two are not a certainty, so they are not served
    as one. Wilson rather than the normal approximation because the rates worth
    warning about sit near 1, where the symmetric interval leaves the unit
    interval altogether.
    """
    z_squared = _CONFIDENCE_Z**2
    margin = _CONFIDENCE_Z * sqrt(
        rate * (1 - rate) / observations + z_squared / (4 * observations**2)
    )
    return (rate + z_squared / (2 * observations) - margin) / (1 + z_squared / observations)
