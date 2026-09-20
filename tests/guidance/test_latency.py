"""What a fused guidance answer costs while the agent waits (SC-009).

SC-009's budget of 250 ms (plan.md's Performance Goals) is for the whole of
retrieval rather than for one traversal, so what is timed here is the most
expensive answer the layer can be asked for: every traversal is asked for the
same position and their lists are fused into one ranking, at every position of
every prompt the fixture corpus recorded. Marked `slow` because the corpus is
replayed through the real capture path before a single answer is timed.

Two of the nine traversals the contract lays out are not written yet —
`ppr_neighbourhood` (T098) and `frequent_episode` (T099) — so seven are asked,
and the counters the run bumped are read back afterwards to prove each of them
ran: a path this test forgets to ask is a path whose cost the budget never
covered. Each of the two joins `_candidates` with the task that adds it.

The measurement gate is opened here, because a gate that drops six of the seven
lists before they are ranked would time a fusion nobody will run once the
published measurements admit them (FR-036).

What is clocked is the traversals and the fusion over a graph already folded,
never the fold itself and never a snapshot read off disk: nothing yet
reconstructs that graph from a written snapshot for a served answer to walk
(T043 and T045 are still open), so timing that read here would time a
composition no request makes. `tests/guidance/test_no_leaks.py`'s
`test_hot_path_never_opens_semantic_store` is where the code structure staying
unread is proved against the path that is wired, against real queries that can
catch it failing; this file does not repeat that proof, only the budget.

Over this corpus the entity-anchored paths answer with nothing: the
`precedes_work_on` projection they read is derived from the code structure,
which a replayed corpus has none of. They are asked and counted all the same,
because what the budget covers is the cost of asking them.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace

import pytest

from processrecall.config import LEVELS, Config
from processrecall.graph.abstract import AbstractGraph, TransitionEdge, aggregate
from processrecall.graph.derive import _sequences
from processrecall.graph.keys import group_by_sequence
from processrecall.graph.store import EpisodicStep, Sequence, SequenceKey, SQLiteEpisodicStore
from processrecall.guidance.fusion import CandidateList, FusedCandidates, Fusion, Scope
from processrecall.guidance.locate import Position, locate
from processrecall.guidance.paths import (
    AFTER_CALLERS,
    AFTER_CHANGE,
    GENERALISED,
    ON_ENTITY,
    PROMPT_START,
    USUAL_NEXT,
    USUALLY_REFUSED,
    Candidate,
    after_callers,
    after_change_to_entity,
    generalised,
    on_entity,
    prompt_start_for_process,
    usual_next,
    usually_refused,
)

#: SC-009's soft budget, in seconds: what the 95th-percentile answer stays inside.
BUDGET_SECONDS = 0.25

#: The share of answers the budget is read at, the other twentieth being the
#: tail SC-009 leaves outside its promise.
PERCENTILE = 0.95

#: Asks the percentile is only read off above. A floor rather than the corpus's
#: own count: what it catches is a replay that recorded next to nothing, which
#: would otherwise pass this budget by never having asked anything.
MIN_ASKS = 10

#: Every traversal asked for each position, as `paths` names them (FR-034).
ASKED = frozenset(
    {USUAL_NEXT, GENERALISED, AFTER_CHANGE, ON_ENTITY, AFTER_CALLERS, PROMPT_START, USUALLY_REFUSED}
)


class _Counted:
    """A counter sink of this test's own: the store's would write while the clock runs."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


@dataclass(frozen=True, slots=True)
class Folded:
    """What a served answer is traversed over, and what it is read with.

    Attributes:
        graph: The corpus folded at the serving level.
        parent: The same corpus folded at the level above it, which is what
            `generalised` backs off to.
        config: The tuning every traversal and the fusion read.
        counters: Where each traversal's run is counted, so the paths the timed
            answers actually asked can be read back off the sink.
    """

    graph: AbstractGraph
    parent: AbstractGraph
    config: Config
    counters: _Counted

    @classmethod
    def of(
        cls,
        steps: tuple[EpisodicStep, ...],
        sequences: Mapping[SequenceKey, Sequence],
        config: Config,
    ) -> Folded:
        """*steps* folded at the serving level and at the one above it."""
        return cls(
            graph=aggregate(steps, config.level, sequences, config),
            parent=aggregate(steps, _parent_level(config.level), sequences, config),
            config=config,
            counters=_Counted(),
        )


@dataclass(frozen=True, slots=True)
class Answer:
    """One position's fused guidance, and what the layer spent reaching it.

    Attributes:
        fused: The single ranking every traversal's list fused to.
        seconds: How long the traversals and the fusion took over this one
            position — the budget's own reading, one ask at a time.
    """

    fused: FusedCandidates
    seconds: float

    @classmethod
    def timed(cls, position: Position, folded: Folded) -> Answer:
        """*position* asked of every traversal and fused, with the clock on it."""
        started_at = time.perf_counter()
        fused = Fusion(folded.config, folded.counters).fuse(*_candidates(position, folded))
        return cls(fused=fused, seconds=time.perf_counter() - started_at)


def _parent_level(level: str) -> str:
    """The coarser level `generalised` backs off from *level* to (FR-023)."""
    index = LEVELS.index(level)
    if index == 0:
        raise ValueError(f"level={level!r} is already the coarsest of {list(LEVELS)}")
    return LEVELS[index - 1]


def _candidates(position: Position, folded: Folded) -> tuple[CandidateList, ...]:
    """Every traversal's moves for *position*, one list each, as fusion ranks them."""
    counters = folded.counters
    offered: tuple[Candidate, ...] = (
        *usual_next(position, folded.graph, counters=counters),
        *generalised(
            position,
            folded.graph,
            folded.parent,
            counters=counters,
            min_support=folded.config.min_support,
        ),
        *after_change_to_entity(position, folded.graph, counters=counters),
        *on_entity(position, folded.graph, counters=counters),
        *after_callers(position, folded.graph, counters=counters),
        *prompt_start_for_process(position, folded.graph, counters=counters),
        *usually_refused(position, folded.graph, counters=counters),
    )
    return _lists(offered)


def _lists(offered: tuple[Candidate, ...]) -> tuple[CandidateList, ...]:
    """*offered* as one list per traversal, each from this project's own graph."""
    grouped: dict[str, list[TransitionEdge]] = {}
    for candidate in offered:
        grouped.setdefault(candidate.traversal, []).append(candidate.transition)
    return tuple(
        CandidateList(edges=tuple(edges), scope=Scope.PROJECT, traversal=traversal)
        for traversal, edges in grouped.items()
    )


def _positions(
    steps: tuple[EpisodicStep, ...], sequences: Mapping[SequenceKey, Sequence], config: Config
) -> tuple[Position, ...]:
    """Where the corpus left the agent standing, once per step of every prompt."""
    return tuple(
        _scoped(locate(carried_out, config.level), sequences.get(key))
        for key, rows in group_by_sequence(steps).items()
        for carried_out in _prefixes(rows)
    )


def _scoped(position: Position, sequence: Sequence | None) -> Position:
    """*position* narrowed by the kind of work its prompt was opened under (FR-035).

    The request is what carries the kind of work, never the steps (FR-020), so
    without this the one traversal reading it would be asked a question no
    request had named and would answer with nothing.
    """
    if sequence is None:
        return position
    return replace(position, kind_of_work=sequence.process_type)


def _prefixes(rows: tuple[EpisodicStep, ...]) -> Iterator[tuple[EpisodicStep, ...]]:
    """The prompt as guidance sees it at each position: nothing done yet, then more."""
    return (rows[:carried_out] for carried_out in range(len(rows) + 1))


def _ran(counted: Mapping[str, int]) -> frozenset[str]:
    """The traversals *counted* says ran, whatever each of them answered (FR-034)."""
    return frozenset(counter for counter in counted if counter.startswith("path_"))


def _percentile(seconds: tuple[float, ...]) -> float:
    """The slowest of *seconds* once the slowest twentieth of them is cut."""
    ordered = sorted(seconds)
    return ordered[math.ceil(PERCENTILE * len(ordered)) - 1]


@pytest.fixture
def config() -> Config:
    """The serving tuning, with every declared traversal let through to the fusion."""
    return Config(unmeasured_traversals=True)


@pytest.mark.slow
def test_fused_guidance_p95_under_250ms(store: SQLiteEpisodicStore, config: Config) -> None:
    """SC-009: every traversal, fused, at every position of the corpus, inside 250 ms."""
    steps = tuple(store.iter_steps())
    sequences = _sequences(store, steps)
    folded = Folded.of(steps, sequences, config)
    positions = _positions(steps, sequences, config)

    answers = tuple(Answer.timed(position, folded) for position in positions)

    assert len(answers) >= MIN_ASKS, "a percentile needs asks to be taken over"
    assert any(answer.fused.edges for answer in answers), "the corpus earned some guidance"
    assert _ran(folded.counters.counted) == frozenset(f"path_{name}" for name in ASKED)
    assert _percentile(tuple(answer.seconds for answer in answers)) <= BUDGET_SECONDS
