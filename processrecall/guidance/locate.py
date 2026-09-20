"""Where the agent stands: the node its previous action landed on (FR-043).

Localisation is on the step *just carried out*, so what is served after action
t-1 is guidance for action t. Everything downstream — the neighbourhood, the
triggers, the rendered statements — reads the position this module derives, and
none of them ever sees the action being advised about, because at serving time
it has not happened yet.

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.locate import locate

    position = locate(store.steps(sequence_key), level=config.level)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from processrecall.config import ProcessType
from processrecall.graph.abstract import START_KEY, Level
from processrecall.graph.keys import key_at
from processrecall.graph.store import EpisodicStep


@dataclass(frozen=True, slots=True)
class Position:
    """The node guidance is served from, and the step it was derived from.

    The scoping arguments a guidance request may narrow itself by ride here too
    (FR-035): they are what the requester knows and the steps do not say — the
    symbol and file being worked on, and the kind of work the prompt is — so the
    traversals anchored on them read one position rather than a parameter each.
    Each is ``None`` when the request does not name it, which is the ordinary
    case for a request that only says where it stands.

    Attributes:
        key: The previous step's identity at the serving level, or `START_KEY`
            when the prompt has carried out no step yet.
        previous: The step that identity was read off; ``None`` at the start of
            a prompt, which has no previous step (FR-047).
        symbol: The `code_entities` key of the symbol the request is about, as
            ``path#name``; ``None`` when it names none.
        file: The `code_entities` key of the file the request is about, which is
            its path; ``None`` when it names none.
        kind_of_work: The process type the prompt is for (FR-020); ``None`` when
            the request names none.
    """

    key: str
    previous: EpisodicStep | None
    symbol: str | None = None
    file: str | None = None
    kind_of_work: ProcessType | None = None


def locate(steps: Sequence[EpisodicStep], level: str) -> Position:
    """Where *steps* leaves the agent standing, spelled at *level*.

    *steps* is the prompt's rows, in the order they were carried out; the
    position is read off the last of them. An empty *steps* is the ordinary
    start of a prompt rather than a fault: the agent stands at `START_KEY`,
    whose successors are what the prompt verb serves (FR-047).

    Raises:
        ValueError: *level* names none of the materialised `LEVELS`.
    """
    lvl = Level.of(level)
    if not steps:
        return Position(key=START_KEY, previous=None)
    previous = steps[-1]
    return Position(key=key_at(previous, lvl), previous=previous)
