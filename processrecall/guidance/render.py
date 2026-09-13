"""How a position becomes text: the deterministic Ψ of FR-044.

Statements arrive already made — template-derived, never prose from a model —
and rendering is nothing but assembly: one line each, the support count beside
the claim it is the evidence for, in the order the caller ranked them.

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.render import BulletRenderer

    text = BulletRenderer(counters).render(statements)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from processrecall.graph.snapshot import Counters

#: The ceiling FR-042's ~300 tokens becomes without a tokeniser on the hot
#: path, at the conventional four characters per token (R8).
CHARACTER_BUDGET = 1200


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

    def __init__(self, counters: Counters) -> None:
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def render(self, statements: Sequence[GuidanceStatement]) -> str:
        """*statements* as one string, in the order they were ranked in.

        Bounded at :data:`CHARACTER_BUDGET`: what does not fit is dropped
        whole, weakest evidence first, and the render is counted (R8).
        """
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
