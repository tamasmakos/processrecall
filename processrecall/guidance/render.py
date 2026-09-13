"""How a position becomes text: the deterministic Ψ of FR-044.

Statements arrive already made — template-derived, never prose from a model —
and rendering is nothing but assembly: one line each, the support count beside
the claim it is the evidence for, in the order the caller ranked them.

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.render import BulletRenderer, Deadline

    text = BulletRenderer(counters, Deadline(started_at=entered_at)).render(statements)
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from processrecall.graph.snapshot import Counters

#: The ceiling FR-042's ~300 tokens becomes without a tokeniser on the hot
#: path, at the conventional four characters per token (R8).
CHARACTER_BUDGET = 1200

#: The soft budget inside the hook's 5-second fence, in seconds (R9): the
#: elapsed time at which an answer is already too late to be worth serving.
SOFT_BUDGET_SECONDS = 0.25


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


@dataclass(frozen=True, slots=True)
class GuidanceStatement:
    """One thing guidance has to say, and the evidence it rests on.

    Attributes:
        text: The claim, template-derived and therefore deterministic.
        support: Episodes behind the claim, always rendered (FR-044).
    """

    text: str
    support: int


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
    return f"- {statement.text} ({statement.support} episodes)"
