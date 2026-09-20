"""Each traversal against the baseline on a held-out temporal split (FR-036, SC-010).

FR-036 lets no traversal into the fused result until it has been measured, and
asks for the measurement to be published whichever way it falls. This is that
measurement: the prototype corpus is split by time — the prompts before the split
instant train the graph, the prompts after it are the held-out asks — and every
traversal is asked where the agent stood before each held-out step, then scored on
where it put the move actually made.

Reported per traversal (`contracts/retrieval-paths.md`): its own recall@1,
recall@5 and MRR, the baseline's — `usual_next` alone — the fused numbers with and
without it, and an exact sign test for the difference between those two. The
metric is next-move recall, which is the question the proposing paths answer;
`usually_refused` answers what to *avoid*, so its own numbers are recorded rather
than read as an admission ranking.

A maintainer script rather than shipped code: evaluation belongs neither in the
package every installing user gets nor in a gate whose result depends on a corpus
being present. It is run by hand and its output committed as the published record.

Example:
    uv run python scripts/measure_traversals.py research/corpus.db
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Container, Iterable, Iterator, Mapping
from collections.abc import Sequence as Seq
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from statistics import fmean

from processrecall.config import LEVELS, Config, load_config
from processrecall.graph.abstract import AbstractGraph, Level, TransitionEdge, aggregate
from processrecall.graph.episodic import open_index
from processrecall.graph.keys import group_by_sequence, key_at
from processrecall.graph.store import (
    EpisodicStep,
    EpisodicStore,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)
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

#: Every traversal measured, in the order the table reports them. `usual_next`
#: comes first because it is the baseline the rest are judged against (FR-036).
#: Report ordering only: `Trained.answers` groups its answers by the traversal
#: each candidate names itself (FR-034), not by this order.
TRAVERSALS = (
    USUAL_NEXT,
    GENERALISED,
    AFTER_CHANGE,
    ON_ENTITY,
    AFTER_CALLERS,
    PROMPT_START,
    USUALLY_REFUSED,
)

#: The paths a served recommendation is actually fused from. Every traversal but
#: the last: `usually_refused` answers what to *avoid*, not what to do next, so
#: its edges never enter the fused ranking or a `fused without` pool — only its
#: own alone/baseline line is published.
FUSED = TRAVERSALS[:-1]

#: The share of the corpus's prompts that trains, the rest being held out. Fixed
#: rather than a flag: the published numbers are only comparable between runs
#: when the split is taken the same way every time.
TRAIN_SHARE = 0.7

#: Width of the published table's columns, in the order `_row` lays them out.
#: One place the widths live, so the header and every data line stay aligned.
_COLUMN_WIDTHS: tuple[int, ...] = (34, 9, 9, 7, 6, 8)


def _row(*cells: str) -> str:
    """*cells* laid out at `_COLUMN_WIDTHS`: the first left-justified, the rest right."""
    first, *rest = zip(cells, _COLUMN_WIDTHS, strict=True)
    return f"{first[0]:<{first[1]}}" + "".join(f"{cell:>{width}}" for cell, width in rest)


_COLUMNS = _row("ranking", "recall@1", "recall@5", "MRR", "asks", "p")


class _Uncounted:
    """A counter sink for the measurement: nothing asked here is served to anyone.

    The traversals and the fusion count every run they make, a serving statistic
    that a measurement run has no business moving.
    """

    def bump(self, counter: str) -> None:
        """Do nothing: a traversal asked for measurement served nobody."""


#: The sink every measurement run counts against (`Trained.answers`, `measure`'s
#: `Fusion`): nothing a measurement asks is served to anyone, so there is one of
#: these rather than a field each caller would have to remember to pass.
_UNCOUNTED = _Uncounted()


@dataclass(frozen=True, slots=True)
class Session:
    """One recorded prompt of the corpus: its steps, and what the prompt was for.

    Attributes:
        steps: The rows the prompt performed, in the order it performed them.
        sequence: The prompt itself. The kind of work it was for and whether it
            ended cleanly live there rather than on any of its rows (FR-020).
    """

    steps: tuple[EpisodicStep, ...]
    sequence: Sequence

    @property
    def started_at(self) -> datetime:
        """When the prompt began, which is the side of the split it falls on."""
        return self.sequence.started_at


@dataclass(frozen=True, slots=True)
class Split:
    """The corpus cut in two by time (FR-036).

    Attributes:
        at: The instant the cut was taken at. Every training prompt began
            strictly before it and every held-out prompt at it or after, so no
            future leaks into the half the graph is folded from.
        train: The prompts the graph is folded from, earliest first.
        test: The prompts held out, earliest first.
    """

    at: datetime
    train: tuple[Session, ...]
    test: tuple[Session, ...]


@dataclass(frozen=True, slots=True)
class Ask:
    """One held-out prediction: where the agent stood, and the move it then made.

    Attributes:
        position: The working state a request at this point would have carried,
            derived from the steps already performed (FR-035, FR-043).
        taken: The key, at the serving level, of the move actually made next —
            the one answer a ranking is scored on finding.
    """

    position: Position
    taken: str


@dataclass(frozen=True, slots=True)
class Answered:
    """One held-out ask, against what every traversal offered for it.

    Attributes:
        ask: The prediction being scored.
        candidates: Each traversal's moves, against the traversal that found
            them, so a fused ranking can be taken over any subset of the paths
            without asking the graph again.
    """

    ask: Ask
    candidates: Mapping[str, tuple[TransitionEdge, ...]]


@dataclass(frozen=True, slots=True)
class Trained:
    """The graphs the training half folded to, and the tuning every traversal reads.

    Attributes:
        graph: The training prompts folded at the serving level.
        parent: The same prompts folded at the level above it, which is what
            `generalised` backs off to.
        config: The tuning the fold and the traversals read.
    """

    graph: AbstractGraph
    parent: AbstractGraph
    config: Config

    @classmethod
    def of(cls, train: Iterable[Session], config: Config) -> Trained:
        """The graphs *train* folds to, at the serving level and the level above it."""
        sessions = tuple(train)
        steps = tuple(step for session in sessions for step in session.steps)
        sequences = {session.sequence.key: session.sequence for session in sessions}
        return cls(
            graph=aggregate(steps, config.level, sequences, config),
            parent=aggregate(steps, _parent_level(config.level), sequences, config),
            config=config,
        )

    def answers(self, position: Position) -> dict[str, tuple[TransitionEdge, ...]]:
        """Every traversal's moves for *position*, keyed by the traversal that found them."""
        offered: tuple[Candidate, ...] = (
            *usual_next(position, self.graph, counters=_UNCOUNTED),
            *generalised(
                position,
                self.graph,
                self.parent,
                counters=_UNCOUNTED,
                min_support=self.config.min_support,
            ),
            *after_change_to_entity(position, self.graph, counters=_UNCOUNTED),
            *on_entity(position, self.graph, counters=_UNCOUNTED),
            *after_callers(position, self.graph, counters=_UNCOUNTED),
            *prompt_start_for_process(position, self.graph, counters=_UNCOUNTED),
            *usually_refused(position, self.graph, counters=_UNCOUNTED),
        )
        grouped: dict[str, list[TransitionEdge]] = {}
        for candidate in offered:
            grouped.setdefault(candidate.traversal, []).append(candidate.transition)
        return {traversal: tuple(edges) for traversal, edges in grouped.items()}


@dataclass(frozen=True, slots=True)
class Ranking:
    """Where the move actually made landed, over every held-out ask.

    Attributes:
        name: What was ranked — a traversal alone, the baseline, or a fusion.
        ranks: The 1-based place the taken move was offered at, one per ask, and
            ``0`` for an ask whose taken move the ranking never offered.
    """

    name: str
    ranks: tuple[int, ...]

    @property
    def queries(self) -> int:
        """How many held-out asks the metrics are taken over."""
        return len(self.ranks)

    @property
    def recall_at_1(self) -> float:
        """The share of asks whose taken move the ranking put first."""
        return self._recall(1)

    @property
    def recall_at_5(self) -> float:
        """The share of asks whose taken move the ranking put in its top five."""
        return self._recall(5)

    @property
    def mrr(self) -> float:
        """The mean reciprocal rank of the taken move over every ask."""
        return fmean(self.reciprocal_ranks) if self.ranks else 0.0

    @property
    def reciprocal_ranks(self) -> tuple[float, ...]:
        """One over each ask's rank, and zero where the taken move was never offered.

        The per-ask series the significance test pairs, rather than the mean of
        it: a sign test needs the asks the two rankings disagree about, which a
        mean has already thrown away.
        """
        return tuple(1 / rank if rank else 0.0 for rank in self.ranks)

    def _recall(self, k: int) -> float:
        """The share of asks whose taken move the ranking put in its top *k*."""
        if not self.ranks:
            return 0.0
        return sum(1 for rank in self.ranks if 0 < rank <= k) / len(self.ranks)


@dataclass(frozen=True, slots=True)
class Row:
    """One traversal's published line: itself against the baseline, and fused, and without.

    Attributes:
        alone: The traversal's own numbers.
        without: The fused numbers with this traversal left out. Compared
            against the report's fused numbers, which are the ones with it.
        p_value: The significance of that difference (FR-036): a traversal whose
            inclusion the test does not support stays out of the fused result.
        baseline_p_value: The significance of `alone` against the report's
            baseline (FR-036): the margin the traversal's own numbers beat
            `usual_next` by, which its inclusion is conditioned on as much as
            the fused difference is.
    """

    alone: Ranking
    without: Ranking
    p_value: float
    baseline_p_value: float


@dataclass(frozen=True, slots=True)
class Report:
    """What one measurement run publishes (FR-036, SC-010).

    Attributes:
        split: The temporal split the numbers were taken on.
        baseline: `usual_next` alone, which every row is judged against.
        fused: The fusable traversals (`FUSED`) fused — the numbers each row's
            `without` is compared against.
        fused_p_value: The significance of the full fused ranking against
            `baseline` (SC-010): the headline comparison the report's
            conclusion rests on gets the same sign test every other row does.
        rows: One line per traversal other than the baseline.
    """

    split: Split
    baseline: Ranking
    fused: Ranking
    fused_p_value: float
    rows: tuple[Row, ...]

    def __str__(self) -> str:
        """The published table, one ranking per line."""
        lines = [
            f"temporal split at {self.split.at.isoformat()}: "
            f"{len(self.split.train)} prompts train, {len(self.split.test)} held out, "
            f"{self.baseline.queries} asks — baseline is {self.baseline.name} alone",
            "",
            _COLUMNS,
            _line(self.baseline),
            _line(self.fused, self.fused_p_value),
        ]
        for row in self.rows:
            lines += ["", _line(row.alone, row.baseline_p_value), _line(row.without, row.p_value)]
        return "\n".join(lines)


class Measurement:
    """Every ranking one held-out split supports, over one set of answered asks."""

    def __init__(self, answered: Seq[Answered], fusion: Fusion) -> None:
        self._answered = answered
        self._fusion = fusion

    def __repr__(self) -> str:
        return f"{type(self).__name__}(asks={len(self._answered)})"

    def ranking(self, name: str, over: Container[str]) -> Ranking:
        """*name*'s ranking: where each taken move landed once *over*'s paths are fused."""
        return Ranking(
            name=name, ranks=tuple(self._rank(answered, over) for answered in self._answered)
        )

    def row(self, traversal: str, fused: Ranking, baseline: Ranking) -> Row:
        """*traversal*'s published line, against *fused* (with it) and *baseline*.

        The without-pool is taken over `FUSED`, not every traversal measured:
        `usually_refused` is not one of the paths fused into a recommendation,
        so removing it from `FUSED` changes nothing and its row's
        `without`/`p_value` come back trivial — its own `alone` line is what
        this method is called for it to report.
        """
        alone = self.ranking(traversal, {traversal})
        without = self.ranking(f"fused without {traversal}", frozenset(FUSED) - {traversal})
        return Row(
            alone=alone,
            without=without,
            p_value=significance(fused, without),
            baseline_p_value=significance(alone, baseline),
        )

    def _rank(self, answered: Answered, over: Container[str]) -> int:
        """Where *answered*'s taken move sits once the paths *over* names are fused."""
        return _rank_of(
            answered.ask.taken,
            self._fusion.fuse(
                *(
                    CandidateList(edges=edges, scope=Scope.PROJECT, traversal=traversal)
                    for traversal, edges in answered.candidates.items()
                    if traversal in over
                )
            ),
        )


def measure(corpus: Iterable[Session], config: Config) -> Report:
    """Every traversal's numbers on a temporal split of *corpus* (FR-036, SC-010).

    The graph is folded from the training half alone, and each held-out step is
    asked for from the position the steps before it left the agent at — so a
    traversal is scored on a move it had not seen made.

    The fusion measured runs with `unmeasured_traversals` on, whatever the
    shipped setting is: a dark traversal's numbers are what decide whether it
    stops being dark, and measuring it through the gate it is waiting on would
    report the gate rather than the path.
    """
    split = temporal_split(corpus)
    trained = Trained.of(split.train, config)
    measurement = Measurement(
        tuple(
            Answered(ask=ask, candidates=trained.answers(ask.position))
            for session in split.test
            for ask in _asks(session, config.level)
        ),
        Fusion(replace(config, unmeasured_traversals=True), _UNCOUNTED),
    )
    baseline = measurement.ranking(USUAL_NEXT, {USUAL_NEXT})
    fused = measurement.ranking("fused (every proposing traversal)", frozenset(FUSED))
    return Report(
        split=split,
        baseline=baseline,
        fused=fused,
        fused_p_value=significance(baseline, fused),
        rows=tuple(
            measurement.row(traversal, fused, baseline) for traversal in TRAVERSALS[1:]
        ),
    )


def temporal_split(corpus: Iterable[Session], train_share: float = TRAIN_SHARE) -> Split:
    """*corpus* cut in two by time, *train_share* of its prompts training (FR-036).

    The cut is an instant rather than an index, so two prompts that began at the
    same moment cannot land on opposite sides of it and leak one half into the
    other.

    Raises:
        ValueError: The corpus holds fewer than two prompts, or every prompt in
            it began at the same instant — neither leaves a held-out half, and a
            measurement with nothing held out is not one.
    """
    ordered = sorted(corpus, key=lambda session: session.started_at)
    if len(ordered) < 2:
        raise ValueError(f"a temporal split needs at least two prompts, got {len(ordered)}")
    boundary = min(max(int(len(ordered) * train_share), 1), len(ordered) - 1)
    at = ordered[boundary].started_at
    train = tuple(session for session in ordered if session.started_at < at)
    test = tuple(session for session in ordered if session.started_at >= at)
    if not train:
        raise ValueError(f"every prompt of the corpus began at {at.isoformat()}")
    return Split(at=at, train=train, test=test)


def significance(fused: Ranking, without: Ranking) -> float:
    """The two-sided p-value of the difference between *fused* and *without*.

    An exact sign test over the asks the two rankings placed the taken move
    differently on: the reciprocal ranks are paired by ask, and an ask both
    ranked alike says nothing about the difference, so it is not counted. Exact
    rather than normal-approximated because a corpus's held-out half is small,
    which is where the approximation is worst.
    """
    paired = tuple(zip(fused.reciprocal_ranks, without.reciprocal_ranks, strict=True))
    gained = sum(1 for with_it, without_it in paired if with_it > without_it)
    lost = sum(1 for with_it, without_it in paired if with_it < without_it)
    if (disagreements := gained + lost) == 0:
        return 1.0
    tail = sum(math.comb(disagreements, k) for k in range(min(gained, lost) + 1))
    patterns: int = 2**disagreements
    return min(1.0, 2 * tail / patterns)


def corpus_of(store: EpisodicStore) -> tuple[Session, ...]:
    """Every prompt recorded in *store*, as the sessions a split is taken over.

    Raises:
        ValueError: A prompt has steps recorded but no sequence row to name what
            it was for. Dropping it would publish a corpus smaller than the
            store without a word, which is the silent failure this refuses.
    """
    sessions: list[Session] = []
    orphaned: list[SequenceKey] = []
    for key, steps in group_by_sequence(store.iter_steps()).items():
        if (sequence := store.sequence(key)) is None:
            orphaned.append(key)
            continue
        sessions.append(Session(steps=steps, sequence=sequence))
    if orphaned:
        raise ValueError(f"{len(orphaned)} recorded prompt(s) have no sequence row: {orphaned}")
    return tuple(sessions)


def main(argv: Seq[str] | None = None) -> int:
    """Print the published table for the corpus named in *argv*; 0 when it measured one."""
    parser = argparse.ArgumentParser(description="measure each traversal against the baseline")
    parser.add_argument("corpus", type=Path, help="the episodic index holding the prototype corpus")
    path = parser.parse_args(argv).corpus
    with closing(open_index(path)) as connection:
        sessions = corpus_of(SQLiteEpisodicStore(connection))
    if len(sessions) < 2:
        print(f"{path}: {len(sessions)} recorded prompts — a temporal split needs two")
        return 1
    print(measure(sessions, load_config()))
    return 0


def _asks(session: Session, level: str) -> Iterator[Ask]:
    """Every held-out prediction *session* supports: one per step it performed.

    The ask before the first step stands at the synthetic `Start`, which is the
    only position `prompt_start_for_process` answers — dropping it would measure
    that traversal against asks it is not for.
    """
    lvl = Level.of(level)
    for index, step in enumerate(session.steps):
        stood = locate(session.steps[:index], level=level)
        yield Ask(position=_scoped(stood, session.sequence), taken=key_at(step, lvl))


def _scoped(stood: Position, sequence: Sequence) -> Position:
    """*stood*, carrying the scoping arguments a real request would have named (FR-035).

    What the corpus knows is what a requester would have known: the kind of work
    is the prompt's own (FR-020), and the symbol and file are the ones the
    previous step recorded touching — the first of its files, which for a
    single-file action is its only one. Left off, the entity-anchored traversals
    would be scored on asks that named no entity, which measures the harness that
    asked rather than the path.
    """
    if (previous := stood.previous) is None:
        return replace(stood, kind_of_work=sequence.process_type)
    return replace(
        stood,
        symbol=previous.symbol_ref,
        file=next(iter(previous.files), None),
        kind_of_work=sequence.process_type,
    )


def _rank_of(taken: str, fused: FusedCandidates) -> int:
    """Where the move *taken* names sits among *fused*'s, 1-based; ``0`` when absent.

    Ranked over the distinct moves offered rather than over the edges: two edges
    proposing the same next procedure are one prediction to a reader, and the
    second must not push the right answer down a place.
    """
    offered = tuple(dict.fromkeys(candidate.edge.target for candidate in fused.edges))
    return offered.index(taken) + 1 if taken in offered else 0


def _parent_level(level: str) -> str:
    """The coarser level `generalised` backs off to from *level*.

    Raises:
        ValueError: *level* is the coarsest there is, so nothing generalises it
            and the back-off traversal cannot be measured at all.
    """
    if (depth := LEVELS.index(level)) == 0:
        raise ValueError(f"level={level!r} is the coarsest of {list(LEVELS)}: it has no parent")
    return LEVELS[depth - 1]


def _line(ranking: Ranking, p_value: float | None = None) -> str:
    """One table row: *ranking*'s three metrics, and the p-value where it has one."""
    return _row(
        ranking.name,
        format(ranking.recall_at_1, ".3f"),
        format(ranking.recall_at_5, ".3f"),
        format(ranking.mrr, ".3f"),
        str(ranking.queries),
        "" if p_value is None else format(p_value, ".3f"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
