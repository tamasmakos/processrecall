"""When guidance speaks: the four triggers, and silence everywhere else (FR-045).

Silence is the default. A located agent with a neighbourhood of moves in front
of it is not reason enough to speak; one of exactly four occasions is:

- the start of a prompt, where the empty step history is the occasion — the
  successors of `START_KEY` are served as-is, unfiltered by process type;
- a write after which verification usually follows;
- the same procedure repeated `k` times;
- a pitfall on the action about to be taken (FR-046).

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.triggers import Triggers

    firing = Triggers(config, counters).fire(steps, neighborhood)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from processrecall.config import Config
from processrecall.graph.abstract import Level, PitfallKind, TransitionEdge
from processrecall.graph.keys import ARTIFACT_EVALUATION, CHANGE_IMPLEMENTATION, class_of, key_at
from processrecall.graph.snapshot import Counters
from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.guidance.neighborhood import Neighborhood


class Trigger(StrEnum):
    """The four occasions FR-045 lets guidance fire on, and no fifth."""

    PROMPT_START = "prompt_start"
    AFTER_WRITE = "after_write"
    REPETITION = "repetition"
    PITFALL = "pitfall"


@dataclass(frozen=True, slots=True)
class Firing:
    """One occasion to speak, with the moves the statement may be made of.

    Attributes:
        trigger: Which of the four occasions this is.
        edges: The transitions guidance is served from, every one of them
            clearing `Config.min_support` (FR-045a).
    """

    trigger: Trigger
    edges: tuple[TransitionEdge, ...]


#: A trigger that has matched, with the edges it would fire on and the
#: (node, sequence) latch to set once those edges clear the support floor —
#: `None` for triggers that do not latch.
_Candidate = tuple[Trigger, tuple[TransitionEdge, ...], tuple[str, SequenceKey] | None]


class Triggers:
    """The four triggers, asked once per served position.

    Stateful on purpose: the repetition trigger latches per (node, sequence),
    which is what turns twenty repetitions into one warning (SC-013).
    """

    def __init__(self, config: Config, counters: Counters) -> None:
        self._config = config
        self._counters = counters
        self._warned: set[tuple[str, SequenceKey]] = set()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(level={self._config.level!r}, k={self._config.k})"

    def fire(self, steps: Sequence[EpisodicStep], neighborhood: Neighborhood) -> Firing | None:
        """The occasion *neighborhood* is an answer to, or ``None`` for silence.

        *steps* is the prompt's rows so far, in the order carried out — the
        same rows `locate` read the position off.
        """
        if not steps:
            return self._cleared(Trigger.PROMPT_START, neighborhood.edges)
        candidate = (
            self._repetition(steps, neighborhood)
            or self._pitfall(neighborhood)
            or self._after_write(steps, neighborhood)
        )
        if candidate is None:
            return None
        trigger, edges, latch = candidate
        firing = self._cleared(trigger, edges)
        if firing is not None and latch is not None:
            self._warned.add(latch)
        return firing

    def _repetition(self, steps: Sequence[EpisodicStep], neighborhood: Neighborhood) -> _Candidate | None:
        """The warning for a procedure *steps* has just done `k` times running.

        Latched per (node, sequence): once a node has been warned about inside
        a sequence it cannot be warned about again there, so a loop of twenty
        costs the reader one statement rather than eighteen (R7, SC-013).
        """
        latch = (neighborhood.center, steps[-1].sequence_key)
        if latch in self._warned or self._repeats(steps) < self._config.k:
            return None
        loop = tuple(edge for edge in _out_of(neighborhood) if edge.target == neighborhood.center)
        if not loop:
            return None
        return Trigger.REPETITION, loop, latch

    def _pitfall(self, neighborhood: Neighborhood) -> _Candidate | None:
        """The warning for a known-bad move the agent is about to make.

        Only the likeliest move is warned about, and only for what it is known
        to fail as: a loop is the repetition trigger's business, latched so a
        procedure that goes round twenty times is not warned about twenty
        times (SC-013). Served here, one step before the move is taken, which
        is where a warning can still change it (FR-046).
        """
        likeliest = _likeliest(_out_of(neighborhood))
        if likeliest is None or not _fails_often(likeliest):
            return None
        return Trigger.PITFALL, (likeliest,), None

    def _after_write(
        self, steps: Sequence[EpisodicStep], neighborhood: Neighborhood
    ) -> _Candidate | None:
        """What usually follows the write *steps* has just carried out.

        Only where verification is what usually follows it: a write whose
        likeliest successor is another write is ordinary work, and FR-045 makes
        silence the answer to ordinary work.
        """
        if steps[-1].activity_class != CHANGE_IMPLEMENTATION:
            return None
        onward = _out_of(neighborhood)
        likeliest = _likeliest(onward)
        if likeliest is None or not _verifies(likeliest):
            return None
        return Trigger.AFTER_WRITE, onward, None

    def _repeats(self, steps: Sequence[EpisodicStep]) -> int:
        """How many times running *steps* ends on the procedure it ends on."""
        level = Level.of(self._config.level)
        last = key_at(steps[-1], level)
        run = 0
        for step in reversed(steps):
            if key_at(step, level) != last:
                break
            run += 1
        return run

    def _cleared(self, trigger: Trigger, edges: Sequence[TransitionEdge]) -> Firing | None:
        """*trigger* on the *edges* that clear the support floor, or silence.

        An occasion left without evidence by the floor is silence and counts as
        one suppression, not as low-confidence guidance (FR-045a).
        """
        supported = tuple(edge for edge in edges if edge.support >= self._config.min_support)
        if not supported:
            if edges:
                self._counters.bump("guidance_below_support")
            return None
        return Firing(trigger=trigger, edges=supported)


def _out_of(neighborhood: Neighborhood) -> tuple[TransitionEdge, ...]:
    """The moves *neighborhood* holds out of the position itself, nearest hop only."""
    return tuple(edge for edge in neighborhood.edges if edge.source == neighborhood.center)


def _likeliest(edges: Sequence[TransitionEdge]) -> TransitionEdge | None:
    """The best-supported of *edges* — the action about to be taken, or ``None``."""
    return max(edges, key=lambda edge: edge.support, default=None)


def _verifies(edge: TransitionEdge) -> bool:
    """Whether *edge* lands on a procedure that evaluates what was just built.

    Read off the key's leading segment, which every serving level spells the
    same way however much of the rest of the key it keeps.
    """
    return class_of(edge.target) == ARTIFACT_EVALUATION


def _fails_often(edge: TransitionEdge) -> bool:
    """Whether *edge* is known to go wrong as a failure, rather than as a loop."""
    return any(pitfall.kind is PitfallKind.FAILURE_PRONE for pitfall in edge.pitfalls)
