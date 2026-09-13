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
from enum import StrEnum
from itertools import pairwise

from processrecall.config import LEVELS, Config
from processrecall.graph.store import CLOSED, EpisodicStep, Sequence, SequenceKey
from processrecall.procedures.outcome import Outcome
from processrecall.procedures.taxonomy import NO_FILE_TYPE
from processrecall.symbolic.packs import ActivityClass, ProcessType

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

#: What one observation made outside a cleanly ended prompt counts for. The
#: clean side of the ratio is configuration (`Config.clean_prompt_weight`,
#: R6); this side is the unit it is a multiple of (FR-027).
_UNCLEAN_WEIGHT = 1.0

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
class Condition:
    """When a transition applies: the context the move was made in (FR-029).

    Deterministic context only (FR-030). The first three are read off the two
    rows the move joined and the prompt they ran in, and a rebuild derives them
    again rather than reading them back.

    Attributes:
        process_type: What the prompt was for — the sequence's `Start`
            condition.
        same_file_as_previous: Whether the move stayed on a file the previous
            step touched; ``None`` at a bookend, which joins no two rows.
        previous_outcome: How the step moved away from went; ``None`` out of
            `START_KEY`, which has no previous step.
        intended_activity: A classifier's label, and only ever an enrichment
            (FR-061): ``None`` while no classifier is installed, which is the
            shipped path.
    """

    process_type: ProcessType
    same_file_as_previous: bool | None
    previous_outcome: Outcome | None
    intended_activity: ActivityClass | None = None


class PitfallKind(StrEnum):
    """What a move is known to go wrong as (FR-031)."""

    FAILURE_PRONE = "failure_prone"
    REPETITION_LOOP = "repetition_loop"


@dataclass(frozen=True, slots=True)
class Pitfall:
    """One known way a transition goes wrong — derived, never authored.

    Attributes:
        kind: Which of the two shapes FR-031 recognises this is.
        evidence: The template that failed, or the node key that repeated.
        support: Observations behind it, so nothing is rendered as a warning
            without the count that earned it (FR-044).
        failure_rate: Share of the move's observations that failed. ``0.0`` for
            `PitfallKind.REPETITION_LOOP`, which counts repetitions rather than
            failures and makes no claim about how they went.
    """

    kind: PitfallKind
    evidence: str
    support: int
    failure_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class _Baseline:
    """What an edge's pitfalls are judged against: the corpus and the tuning.

    The two travel as one because neither decides a pitfall alone — a move is
    failure-prone relative to how often every other move failed, and only once
    it clears the support floor configuration sets.
    """

    failure_rate: float
    config: Config


@dataclass(frozen=True, slots=True)
class TransitionEdge:
    """One permissible move between two procedures — the predicate index.

    Attributes:
        edge_key: ``"<source> -> <target>"``; the name an annotation takes.
        source: The key moved from.
        target: The key moved to, at the same level as *source*.
        condition: The context the move was commonest in (FR-029).
        support: Distinct episodic steps supporting the move — the true count
            beside the bounded `supporting_steps` (FR-026).
        weight: That support with every observation made inside a cleanly ended
            prompt counted `Config.clean_prompt_weight` times one that was not
            (FR-027).
        supporting_steps: The ``step_id``s it was derived from, the most recent
            `SUPPORTING_STEPS_KEPT` of them, oldest first. Integers, so an edge
            names its private rows without carrying anything out of them.
        outcome_counts: How the step each move landed on went.
        last_seen: The most recent of those steps.
        pitfalls: What this move is known to go wrong as (FR-029, FR-031).
    """

    edge_key: str
    source: str
    target: str
    condition: Condition
    support: int
    weight: float
    supporting_steps: tuple[int, ...]
    outcome_counts: Mapping[Outcome, int]
    last_seen: datetime
    pitfalls: tuple[Pitfall, ...]


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


@dataclass(frozen=True, slots=True)
class _Chain:
    """One prompt's rows in the order carried out, under the prompt's own context.

    They travel together because a move needs all three: the process type its
    condition names and the cleanliness its weight comes from are properties of
    the sequence, not of any row in it.
    """

    process_type: ProcessType
    steps: tuple[EpisodicStep, ...]
    clean: bool


@dataclass(frozen=True, slots=True)
class _Move:
    """One observed transition: where it went, what supports it, when it applied."""

    source: str
    target: str
    supporting_step: EpisodicStep
    condition: Condition


@dataclass(slots=True)
class _EdgeFold:
    """The running tally behind one :class:`TransitionEdge`."""

    source: str
    target: str
    last_seen: datetime = _UNSEEN
    weight: float = 0.0
    step_ids: set[int] = field(default_factory=set)
    outcomes: Counter[Outcome] = field(default_factory=Counter)
    conditions: Counter[Condition] = field(default_factory=Counter)
    failed_templates: Counter[str] = field(default_factory=Counter)
    repetitions: int = 0

    def record(self, move: _Move, weight: float) -> None:
        """Fold one observation of this move, counting for *weight*, into the tally."""
        step = move.supporting_step
        outcome = Outcome(step.outcome)
        self.step_ids.add(step.step_id)
        self.weight += weight
        self.last_seen = max(self.last_seen, step.occurred_at)
        self.outcomes[outcome] += 1
        self.conditions[move.condition] += 1
        if outcome is Outcome.FAILURE:
            self.failed_templates[step.template] += 1

    @property
    def support(self) -> int:
        """Distinct episodic rows behind this move."""
        return len(self.step_ids)

    def _failure_prone(self, baseline: _Baseline) -> Pitfall | None:
        """This move's `PitfallKind.FAILURE_PRONE` pitfall, where it earns one.

        Over-representation is relative (FR-031): where a third of everything
        fails, failing a third of the time is the base rate and not a pitfall.
        The support floor is what stops one unlucky prompt from warning every
        later one. The template named is the commonest of the ones that failed,
        ties broken on its own text so a rebuild names the same one (FR-032) —
        it is evidence for the transition's over-representation, not itself
        judged over-represented against a template-level base rate.
        """
        rate = self.outcomes[Outcome.FAILURE] / self.outcomes.total()
        if self.support < baseline.config.min_support or rate <= baseline.failure_rate:
            return None
        ranked = sorted(self.failed_templates.items(), key=lambda item: (-item[1], item[0]))
        return Pitfall(
            kind=PitfallKind.FAILURE_PRONE,
            evidence=ranked[0][0],
            support=self.support,
            failure_rate=rate,
        )

    def _repetition_loop(self) -> Pitfall | None:
        """This move's `PitfallKind.REPETITION_LOOP` pitfall, where it has one.

        Only a move onto itself can have one, and only where `_repetition_runs`
        counted a run long enough to be a loop rather than a retry (R7). The
        pitfall makes no claim about outcome: a loop is worth warning about
        because it is going nowhere, whatever each attempt reported.
        """
        if not self.repetitions:
            return None
        return Pitfall(
            kind=PitfallKind.REPETITION_LOOP,
            evidence=self.source,
            support=self.repetitions,
        )

    def _pitfalls(self, baseline: _Baseline) -> tuple[Pitfall, ...]:
        """Everything this move is known to go wrong as (FR-031).

        Never for a bookend: a move into `END_KEY` or out of `START_KEY` is not
        one a later prompt can be steered away from, so it earns no pitfall
        however its rate compares to the base rate.
        """
        if self.source in (START_KEY, END_KEY) or self.target in (START_KEY, END_KEY):
            return ()
        derived = (self._failure_prone(baseline), self._repetition_loop())
        return tuple(pitfall for pitfall in derived if pitfall is not None)

    @property
    def condition(self) -> Condition:
        """The context this move was commonest in.

        One edge carries one condition, so the contexts a move was observed in
        are ranked rather than kept: the commonest of them is what "when this
        transition applies" means. Ties fall back to the condition's own repr,
        so a rebuild resolves them the way the incremental fold did (FR-032).
        """
        ranked = sorted(self.conditions.items(), key=lambda item: (-item[1], repr(item[0])))
        return ranked[0][0]

    def finish(self, baseline: _Baseline) -> TransitionEdge:
        """The edge this tally describes, its supporting rows bounded (FR-026)."""
        recent = sorted(self.step_ids)[-SUPPORTING_STEPS_KEPT:]
        return TransitionEdge(
            edge_key=edge_key(self.source, self.target),
            source=self.source,
            target=self.target,
            condition=self.condition,
            support=self.support,
            weight=self.weight,
            supporting_steps=tuple(recent),
            outcome_counts=dict(self.outcomes),
            last_seen=self.last_seen,
            pitfalls=self._pitfalls(baseline),
        )


def _base_failure_rate(folds: Iterable[_EdgeFold]) -> float:
    """Share of every observed move that landed on a failed step.

    The corpus-wide rate a move has to beat to count as over-represented in
    failed prompts (FR-031). Bookend folds are excluded: the row a chain ends
    on already supports the real move that landed on it, and folding the
    `-> END_KEY` move too would count that one row's outcome twice.
    """
    outcomes: Counter[Outcome] = Counter()
    for fold in folds:
        if fold.source in (START_KEY, END_KEY) or fold.target in (START_KEY, END_KEY):
            continue
        outcomes.update(fold.outcomes)
    total = outcomes.total()
    return outcomes[Outcome.FAILURE] / total if total else 0.0


def edge_key(source: str, target: str) -> str:
    """The name the move from *source* to *target* takes."""
    return f"{source} -> {target}"


def aggregate(
    steps: Iterable[EpisodicStep],
    level: str,
    sequences: Mapping[SequenceKey, Sequence] | None = None,
    config: Config | None = None,
) -> AbstractGraph:
    """Fold every episodic row in *steps* into the abstract graph at *level*.

    *sequences* is what the rows' prompts were for and how they ended, which
    lives on the sequence and not on any of its rows (FR-020): the process type
    a condition names, and the status a weight is derived from (FR-027). A
    sequence missing from it is folded as `ProcessType.UNKNOWN` and as unclean —
    an unclassified prompt still has transitions, but a prompt nothing recorded
    the end of cannot be said to have ended cleanly. No shipped caller passes it
    yet — the task that wires the store's sequences in at the call site is T040
    (`cli/rebuild.py`).

    *config* is the tuning the fold reads: the support floor below which a move
    may not warn (FR-031), and how much a move observed in a cleanly ended
    prompt outweighs one that was not (FR-027). The shipped defaults when
    absent, so a caller with no configuration of its own still folds the same
    graph.

    Raises:
        ValueError: *level* names none of the materialised `LEVELS`. A level
            nobody materialises would silently produce a graph no renderer can
            find a node in.
    """
    lvl = Level.of(level)
    tuning = config or Config()
    nodes: dict[str, _NodeFold] = {}
    edges: dict[tuple[str, str], _EdgeFold] = {}
    high_water = 0
    for chain in _chains(steps, sequences or {}).values():
        for step in chain.steps:
            high_water = max(high_water, step.step_id)
            key = _key_at(step, lvl)
            if (fold := nodes.get(key)) is None:
                fold = nodes[key] = _fold_for(step, lvl)
            fold.record(step)
        first, last = chain.steps[0], chain.steps[-1]
        nodes.setdefault(START_KEY, _synthetic_fold(START_KEY, lvl)).observe(first.occurred_at)
        nodes.setdefault(END_KEY, _synthetic_fold(END_KEY, lvl)).observe(last.occurred_at)
        weight = tuning.clean_prompt_weight if chain.clean else _UNCLEAN_WEIGHT
        for move in _transitions(chain, lvl):
            pair = (move.source, move.target)
            edges.setdefault(pair, _EdgeFold(move.source, move.target)).record(move, weight)
        for key, runs in _repetition_runs(chain, lvl, tuning.k).items():
            edges[(key, key)].repetitions += runs
    baseline = _Baseline(_base_failure_rate(edges.values()), tuning)
    return AbstractGraph(
        level=level,
        nodes={key: nodes[key].finish() for key in sorted(nodes)},
        edges=tuple(edges[pair].finish(baseline) for pair in sorted(edges)),
        episode_high_water=high_water,
    )


def _transitions(chain: _Chain, level: Level) -> Iterator[_Move]:
    """Every move one sequence made, with what supports it and what conditioned it.

    The supporting row is the one the move *landed on*, which is why an edge's
    outcomes are the target's: what a transition is evidence for is how the step
    it led to went. The move into `END_KEY` lands on no row at all, so it is
    supported by the one it left — the last thing the prompt actually did.

    A bookend joins no two rows, so it has no file and no previous step to
    compare against; the move out of `START_KEY` has no previous outcome either.
    """
    steps = chain.steps
    first, last = steps[0], steps[-1]
    yield _Move(
        source=START_KEY,
        target=_key_at(first, level),
        supporting_step=first,
        condition=Condition(chain.process_type, None, None),
    )
    for before, after in pairwise(steps):
        yield _Move(
            source=_key_at(before, level),
            target=_key_at(after, level),
            supporting_step=after,
            condition=Condition(
                chain.process_type, _shares_a_file(before, after), Outcome(before.outcome)
            ),
        )
    yield _Move(
        source=_key_at(last, level),
        target=END_KEY,
        supporting_step=last,
        condition=Condition(chain.process_type, None, Outcome(last.outcome)),
    )


def _repetition_runs(chain: _Chain, level: Level, k: int) -> Counter[str]:
    """Each node key in *chain* against how many of its runs reached *k* in a row.

    A run is the node doing itself over, and R7 puts the threshold at three
    occurrences because two attempts at the same procedure are an ordinary
    retry. The count is of runs and not of repetitions: a run that goes on past
    *k* is still the one loop, so twenty repetitions are one thing to warn
    about rather than eighteen.

    Counted over the moves rather than the rows, which is what keeps every key
    here one the aggregation also folded a self-edge for.
    """
    runs: Counter[str] = Counter()
    length = 1
    counted = False
    for before, after in pairwise(chain.steps):
        key = _key_at(after, level)
        if key != _key_at(before, level):
            length = 1
            counted = False
            continue
        length += 1
        if length >= k and not counted:
            runs[key] += 1
            counted = True
    return runs


def _shares_a_file(before: EpisodicStep, after: EpisodicStep) -> bool:
    """Whether *after* touched any of the files *before* did (FR-030)."""
    return bool(set(before.files) & set(after.files))


def _is_clean(sequence: Sequence | None, steps: tuple[EpisodicStep, ...]) -> bool:
    """Whether the prompt behind *steps* ended cleanly (R5).

    Derived here rather than read off a stored flag, so a change to what counts
    as clean is a re-derivation and not a migration. An `incomplete` sequence —
    a prompt a crash or a later prompt ended for it — is never clean, however
    its rows went: a crash must not be able to look like success. *steps* is
    non-empty by construction — `_chains` only builds a chain for a key that
    had at least one row appended to it.
    """
    if sequence is None or sequence.status != CLOSED:
        return False
    return all(Outcome(step.outcome) is not Outcome.FAILURE for step in steps)


def _chains(
    steps: Iterable[EpisodicStep], sequences: Mapping[SequenceKey, Sequence]
) -> dict[SequenceKey, _Chain]:
    """The rows of *steps* grouped into sequences, each in the order carried out.

    Sorted rather than trusted: a backfill writes rows in transcript order and a
    hook writes them live, so the two interleave by ``step_id`` — and a rebuild
    that read the transitions in a different order would not reproduce the
    incremental graph (SC-004).
    """
    rows: dict[SequenceKey, list[EpisodicStep]] = {}
    for step in steps:
        rows.setdefault(step.sequence_key, []).append(step)
    return {key: _chain_for(sequences.get(key), rows_for_key) for key, rows_for_key in rows.items()}


def _chain_for(sequence: Sequence | None, rows: list[EpisodicStep]) -> _Chain:
    """*rows* as one prompt's chain: in the order carried out, under its context."""
    ordered = tuple(sorted(rows, key=lambda step: step.position))
    return _Chain(
        process_type=sequence.process_type if sequence else ProcessType.UNKNOWN,
        steps=ordered,
        clean=_is_clean(sequence, ordered),
    )


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
