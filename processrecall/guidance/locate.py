"""Where the agent stands: the working state its recent actions leave it in (FR-038).

Localisation is on the step *just carried out* (FR-043), so what is served
after action t-1 is guidance for action t. Everything downstream — the neighbourhood, the
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
from pathlib import PurePosixPath

from processrecall.config import ProcessType
from processrecall.graph.abstract import START_KEY, Level
from processrecall.graph.keys import key_at
from processrecall.graph.semantic import entity_key
from processrecall.graph.store import EpisodicStep

#: How many carried-out steps the working state keeps (FR-038). A window rather
#: than the transcript: it is the cue a traversal matches a recurring
#: subsequence against, so a long prompt narrows to its tail like a short one.
LAST_K_STEPS = 5


@dataclass(frozen=True, slots=True)
class Position:
    """The working state guidance is assembled from (FR-038).

    Built per request out of the steps the prompt has carried out, and written
    nowhere: what it holds is either derived again next request or gone.

    The scoping arguments a guidance request may narrow itself by ride here too
    (FR-035): they are what the requester knows and the steps do not say — the
    symbol and file being worked on, and the kind of work the prompt is — so the
    traversals anchored on them read one position rather than a parameter each.

    Attributes:
        key: The previous step's identity at the serving level, or `START_KEY`
            when the prompt has carried out no step yet.
        recent: The last `LAST_K_STEPS` steps, oldest first; empty at the start
            of a prompt, which has carried out none (FR-047). The previous
            step's result and decision are read off its newest entry rather
            than copied out into fields of their own.
        symbol: The `code_entities` key of the symbol the recent steps were
            attributed to, as ``path#name``; ``None`` when none names one.
        file: The `code_entities` key of the file the recent steps most
            recently touched, which is its path; ``None`` when none names one.
        kind_of_work: The process type the prompt is for (FR-020), which is the
            activity the request intends; ``None`` when it names none.
    """

    key: str
    recent: tuple[EpisodicStep, ...] = ()
    symbol: str | None = None
    file: str | None = None
    kind_of_work: ProcessType | None = None

    @property
    def previous(self) -> EpisodicStep | None:
        """The step just carried out: the newest of `recent`, or ``None`` at a prompt's start."""
        return self.recent[-1] if self.recent else None


def locate(steps: Sequence[EpisodicStep], level: str) -> Position:
    """The working state *steps* leaves the agent in, keyed at *level*.

    *steps* is the prompt's rows, in the order they were carried out; the last
    `LAST_K_STEPS` of them are the working state, and the newest is what the
    key is spelled off. An empty *steps* is the ordinary start of a prompt
    rather than a fault: the agent stands at `START_KEY`, whose successors are
    what the prompt verb serves (FR-047).

    Raises:
        ValueError: *level* names none of the materialised `LEVELS`.
    """
    lvl = Level.of(level)
    recent = tuple(steps[-LAST_K_STEPS:])
    if not recent:
        return Position(key=START_KEY)
    return Position(
        key=key_at(recent[-1], lvl),
        recent=recent,
        symbol=_active_symbol(recent),
        file=_active_file(recent),
    )


def _active_symbol(recent: Sequence[EpisodicStep]) -> str | None:
    """The `code_entities` key of the symbol *recent* was most recently working on.

    A step carries where its edit landed as ``path::qualified.name`` (FR-063);
    `entity_key` spells that same symbol as the `code_entities` key it is.
    A step attributed to a file alone names no symbol and is passed over, as is
    one nothing attributed; ``None`` when no step of the window names one.
    """
    for step in reversed(recent):
        if step.symbol_ref is not None and "::" in step.symbol_ref:
            path, _, qualified_name = step.symbol_ref.partition("::")
            return entity_key(PurePosixPath(path), qualified_name)
    return None


def _active_file(recent: Sequence[EpisodicStep]) -> str | None:
    """The `code_entities` key of the file *recent* most recently touched.

    The key of a file is its path, which is what a step records, so the
    recorded path is returned as it stands. A step that named several files is
    read for the last of them, the one it finished on; ``None`` when no step of
    the window named any.
    """
    for step in reversed(recent):
        if step.files:
            return step.files[-1]
    return None
