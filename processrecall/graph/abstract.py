"""The abstract layer: episodic rows folded into procedures and their transitions.

The two layers share one traversal (FR-025), which only holds while the abstract
graph stays an *aggregation of* the episodic rows rather than a second copy of
them. So nothing is stored here: every node and every edge in this module is
re-derived from `steps`, which is what makes a from-scratch rebuild reproduce the
incremental one (SC-004) and a change to the taxonomy reclassify old rows instead
of needing a migration.

A node is one identity at the serving level, carrying the coarser levels as is-a
ancestors rather than existing three times (FR-018). An edge is one permissible
move between two of them, and it names the episodic rows it was derived from
(FR-026) — as integers, which point at private rows without carrying anything
out of them.

On the hot path, so the standard library only.

Example:
    from processrecall.graph.abstract import aggregate

    graph = aggregate(store.iter_steps(), level=config.level)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise

from processrecall.config import LEVELS
from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.procedures.outcome import Outcome
from processrecall.procedures.taxonomy import NO_FILE_TYPE
from processrecall.symbolic.packs import ActivityClass

#: How many of a node's action templates the graph keeps. They are what guidance
#: is rendered from (FR-044) and they are ordered by count, so the tail is both
#: the least useful and the least bounded part of a node.
TOP_TEMPLATES = 5

#: How many supporting ``step_id``s an edge keeps, most recent first to be
#: dropped last. The true count stays beside them as `TransitionEdge.support`
#: (FR-026): a bounded list nobody could compare against a total would read as
#: an edge with fifty observations however many it really had.
SUPPORTING_STEPS_KEPT = 50

#: The two synthetic nodes (FR-020). They exist per sequence rather than per
#: action: every prompt's chain begins at `START_KEY` and ends at `END_KEY`, so
#: structural validation has a single source to check reachability from and a
#: single sink to check it reaches (FR-021).
START_KEY = "Start"
END_KEY = "End"

#: The oldest a fold can be: every real observation is later, so the first one
#: replaces it.
_UNSEEN = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Level:
    """One of `LEVELS`, name and depth together.

    The two travel as one concept everywhere a fold is built, rather than as two
    parameters that must agree.
    """

    name: str
    depth: int

    @classmethod
    def of(cls, name: str) -> Level:
        """*name*'s `Level`.

        Raises:
            ValueError: *name* names none of the materialised `LEVELS`.
        """
        return cls(name=name, depth=LEVELS.index(name))


@dataclass(frozen=True, slots=True)
class Template:
    """One abstracted invocation, against how often this node was it."""

    text: str
    count: int


@dataclass(frozen=True, slots=True)
class ProcedureNode:
    """One procedure at the serving level — the symbol the graph is indexed by.

    Attributes:
        key: The identity at *level*, ``/``-joined.
        level: Which of `LEVELS` this key names.
        is_a: The ancestor keys it generalises to, coarsest last (FR-018).
        activity_class: The engineering activity it belongs to (FR-019).
        program: What performed it; ``""`` at the ``class`` level.
        file_ext: The file type it acted on; ``""`` above ``class/program/ext``.
        templates: Its commonest invocations, by count.
        support: Episodic steps carried by this node.
        outcome_counts: How those steps went.
        last_seen: The most recent of them.
    """

    key: str
    level: str
    is_a: tuple[str, ...]
    activity_class: ActivityClass
    program: str
    file_ext: str
    templates: tuple[Template, ...]
    support: int
    outcome_counts: Mapping[Outcome, int]
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class TransitionEdge:
    """One permissible move between two procedures — the predicate index.

    Attributes:
        edge_key: ``"<source> -> <target>"``; the name an annotation takes.
        source: The key moved from.
        target: The key moved to, at the same level as *source*.
        support: Distinct episodic steps supporting the move — the true count
            beside the bounded `supporting_steps` (FR-026).
        supporting_steps: The ``step_id``s it was derived from, the most recent
            `SUPPORTING_STEPS_KEPT` of them, oldest first. Integers, so an edge
            names its private rows without carrying anything out of them.
        outcome_counts: How the step each move landed on went.
        last_seen: The most recent of those steps.
    """

    edge_key: str
    source: str
    target: str
    support: int
    supporting_steps: tuple[int, ...]
    outcome_counts: Mapping[Outcome, int]
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class AbstractGraph:
    """The abstract layer as one aggregation, at one level of generality.

    Attributes:
        level: The generality every node and edge here is named at (FR-023).
        nodes: Each procedure against its body, by key, in key order.
        edges: The permissible transitions, in edge-key order.
        episode_high_water: The largest episodic ``step_id`` folded in — the
            provenance a snapshot reports staleness from (FR-041).
    """

    level: str
    nodes: Mapping[str, ProcedureNode]
    edges: tuple[TransitionEdge, ...]
    episode_high_water: int


@dataclass(slots=True)
class _NodeFold:
    """The running tally behind one :class:`ProcedureNode`."""

    key: str
    level: str
    is_a: tuple[str, ...]
    activity_class: ActivityClass
    program: str
    file_ext: str
    support: int = 0
    last_seen: datetime = _UNSEEN
    templates: Counter[str] = field(default_factory=Counter)
    outcomes: Counter[Outcome] = field(default_factory=Counter)

    def observe(self, at: datetime) -> None:
        """Count one occurrence of this node, at *at*.

        What a synthetic node gets instead of `record`: `Start` and `End` are
        passed through once per sequence and never performed, so they have a
        support and a recency but no template and no outcome of their own.
        """
        self.support += 1
        self.last_seen = max(self.last_seen, at)

    def record(self, step: EpisodicStep) -> None:
        """Fold one episodic row into this node."""
        self.observe(step.occurred_at)
        self.templates[step.template] += 1
        self.outcomes[Outcome(step.outcome)] += 1

    def finish(self) -> ProcedureNode:
        """The node this tally describes."""
        return ProcedureNode(
            key=self.key,
            level=self.level,
            is_a=self.is_a,
            activity_class=self.activity_class,
            program=self.program,
            file_ext=self.file_ext,
            templates=tuple(
                Template(text, count)
                for text, count in sorted(
                    self.templates.items(), key=lambda item: (-item[1], item[0])
                )[:TOP_TEMPLATES]
            ),
            support=self.support,
            outcome_counts=dict(self.outcomes),
            last_seen=self.last_seen,
        )


@dataclass(slots=True)
class _EdgeFold:
    """The running tally behind one :class:`TransitionEdge`."""

    source: str
    target: str
    last_seen: datetime = _UNSEEN
    step_ids: set[int] = field(default_factory=set)
    outcomes: Counter[Outcome] = field(default_factory=Counter)

    def record(self, step: EpisodicStep) -> None:
        """Fold one observation of this move, supported by episodic row *step*."""
        self.step_ids.add(step.step_id)
        self.last_seen = max(self.last_seen, step.occurred_at)
        self.outcomes[Outcome(step.outcome)] += 1

    def finish(self) -> TransitionEdge:
        """The edge this tally describes, its supporting rows bounded (FR-026)."""
        recent = sorted(self.step_ids)[-SUPPORTING_STEPS_KEPT:]
        return TransitionEdge(
            edge_key=edge_key(self.source, self.target),
            source=self.source,
            target=self.target,
            support=len(self.step_ids),
            supporting_steps=tuple(recent),
            outcome_counts=dict(self.outcomes),
            last_seen=self.last_seen,
        )


def edge_key(source: str, target: str) -> str:
    """The name the move from *source* to *target* takes."""
    return f"{source} -> {target}"


def aggregate(steps: Iterable[EpisodicStep], level: str) -> AbstractGraph:
    """Fold every episodic row in *steps* into the abstract graph at *level*.

    Raises:
        ValueError: *level* names none of the materialised `LEVELS`. A level
            nobody materialises would silently produce a graph no renderer can
            find a node in.
    """
    lvl = Level.of(level)
    nodes: dict[str, _NodeFold] = {}
    edges: dict[tuple[str, str], _EdgeFold] = {}
    high_water = 0
    for chain in _chains(steps).values():
        for step in chain:
            high_water = max(high_water, step.step_id)
            key = _key_at(step, lvl)
            if (fold := nodes.get(key)) is None:
                fold = nodes[key] = _fold_for(step, lvl)
            fold.record(step)
        nodes.setdefault(START_KEY, _synthetic_fold(START_KEY, lvl)).observe(chain[0].occurred_at)
        nodes.setdefault(END_KEY, _synthetic_fold(END_KEY, lvl)).observe(chain[-1].occurred_at)
        for source, target, support in _transitions(chain, lvl):
            edges.setdefault((source, target), _EdgeFold(source, target)).record(support)
    return AbstractGraph(
        level=level,
        nodes={key: nodes[key].finish() for key in sorted(nodes)},
        edges=tuple(edges[pair].finish() for pair in sorted(edges)),
        episode_high_water=high_water,
    )


def _transitions(chain: list[EpisodicStep], level: Level) -> Iterator[tuple[str, str, EpisodicStep]]:
    """Every move one sequence made, with the episodic row that supports it.

    The supporting row is the one the move *landed on*, which is why an edge's
    outcomes are the target's: what a transition is evidence for is how the step
    it led to went. The move into `END_KEY` lands on no row at all, so it is
    supported by the one it left — the last thing the prompt actually did.
    """
    yield START_KEY, _key_at(chain[0], level), chain[0]
    for before, after in pairwise(chain):
        yield _key_at(before, level), _key_at(after, level), after
    yield _key_at(chain[-1], level), END_KEY, chain[-1]


def _chains(steps: Iterable[EpisodicStep]) -> dict[SequenceKey, list[EpisodicStep]]:
    """The rows of *steps* grouped into sequences, each in the order carried out.

    Sorted rather than trusted: a backfill writes rows in transcript order and a
    hook writes them live, so the two interleave by ``step_id`` — and a rebuild
    that read the transitions in a different order would not reproduce the
    incremental graph (SC-004).
    """
    chains: dict[SequenceKey, list[EpisodicStep]] = {}
    for step in steps:
        chains.setdefault(step.sequence_key, []).append(step)
    for chain in chains.values():
        chain.sort(key=lambda step: step.position)
    return chains


def _keys_of(step: EpisodicStep) -> tuple[str, str, str]:
    """*step*'s identity at each of `LEVELS`, coarsest first.

    Rebuilt from the columns the recorder stored rather than by splitting
    ``node_key``: a program is free to contain a ``/`` and a positional split of
    the joined key would cut it in the wrong place.
    """
    by_program = f"{step.activity_class}/{step.program}"
    return (str(step.activity_class), by_program, step.node_key)


def _key_at(step: EpisodicStep, level: Level) -> str:
    """*step*'s node key at *level*."""
    return _keys_of(step)[level.depth]


def _synthetic_fold(key: str, level: Level) -> _NodeFold:
    """An empty tally for one of the two synthetic nodes (FR-020).

    It is spelled at *level* like every other node so that an edge into or out
    of it joins two nodes of the same generality, which is what lets one
    traversal cross the bookends.
    """
    return _NodeFold(
        key=key,
        level=level.name,
        is_a=(),
        activity_class=ActivityClass.UNKNOWN,
        program="",
        file_ext="",
    )


def _fold_for(step: EpisodicStep, level: Level) -> _NodeFold:
    """An empty tally for the node *step* belongs to at *level*.

    The identity fields come from the first row to reach a key; every later row
    with that key agrees on them, which is what the key being an identity means.
    """
    keys = _keys_of(step)
    extension = step.node_key.rsplit("/", 1)[-1]
    return _NodeFold(
        key=keys[level.depth],
        level=level.name,
        is_a=keys[: level.depth][::-1],
        activity_class=step.activity_class,
        program=step.program if level.depth >= 1 else "",
        file_ext="" if level.depth < 2 or extension == NO_FILE_TYPE else extension,
    )
