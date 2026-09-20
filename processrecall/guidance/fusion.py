"""Many candidate lists ranked into one, by weighted reciprocal rank (FR-032).

Every traversal answers a position with its own list, and the project's graph
and the cross-project one are each traversed, so a served answer is one ranking
made of many lists — not two. Reciprocal Rank Fusion, lifted from the forked
recall path (R18), is what makes it one: a move several lists know ranks above a
move only one of them does, without any list's support counts having to be
comparable with another's.

The project's preference is a **weight**, not a gate. A gate — every project
candidate ahead of every global one — leaves reciprocal rank deciding order only
*within* a scope, which is not fusion: it discards exactly the evidence fusion
exists to read, that several independent traversals agreed. So a list from this
project's own graph is scored at `Config.project_weight`, which leans the order
towards what this project has recorded while still letting a move two other
traversals reached outrank it. An answer holding nothing of this project's is still marked
`Scope.GLOBAL` and counted, because guidance from somewhere else is a different
claim from guidance from here (FR-048).

Candidates are deduplicated across lists before the order is taken, and the
duplicate is what earns the rank: one candidate, one score, summed over every
list that reached it — the multi-path boost. The per-list support floor is
applied here, before the ranks are read, because a list is where the floor is
meaningful: an edge below it exists without being evidence enough to rank
(FR-045a), and a traversal has no business applying it to itself.

The order the lists agree on is not quite the order served. One candidate per
kind of work survives the fused ranking — a second move into the same activity
class is the same claim made twice — and the fifth place is kept for the
candidate the order is least confident about, so the set a reader acts on cannot
collapse onto the one pattern the graph is surest of, which on this corpus is a
thrash loop (FR-042).

The measurement gate is applied here for the same reason (FR-036): a traversal
whose numbers the published measurement does not support runs and counts where
it runs, and is excluded where its candidates would have been served — so the
path a reader never sees is still the path `inspect` can show keeps losing.

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.fusion import CandidateList, Fusion, Scope

    fused = Fusion(config, counters).fuse(
        CandidateList(edges=project_edges, scope=Scope.PROJECT),
        CandidateList(edges=global_edges, scope=Scope.GLOBAL),
    )
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from processrecall.config import Config, Counters
from processrecall.graph.abstract import TransitionEdge
from processrecall.graph.keys import class_of
from processrecall.guidance.paths import USUAL_NEXT
from processrecall.ranking.rrf import rrf_score

#: The traversals admitted to the fused result (FR-036). A traversal this set
#: does not name contributes no candidates however well it ran: it stays dark
#: until its measurement is published and it is named here, so declaring a path
#: can never put it in front of a reader on its own. `usual_next` is in it as
#: the baseline every other traversal's numbers are judged against, not a
#: measured entrant itself. Every other name is added by hand, one at a time,
#: as `docs/measurements/007.md` (T050) publishes it clearing the margin —
#: `usually_refused` never joins, because `FUSED` in `measure_traversals.py`
#: excludes it from the fused ranking it would have to clear.
ADMITTED: frozenset[str] = frozenset({USUAL_NEXT})

#: Where in the served order the exploration slot sits (FR-042): the fifth
#: place, so the four candidates the fused order is most confident about keep
#: theirs and the reserved one still lands inside the top five the published
#: measurement reports recall over. Filled from the end of the order rather
#: than by a random draw, because the served answer stays deterministic
#: (FR-044) — and the least-confident candidate is the furthest thing from the
#: pattern an order left to itself keeps reinforcing.
EXPLORATION_SLOT = 4


class Scope(StrEnum):
    """Which graph a candidate — or a whole answer — came from (FR-048)."""

    PROJECT = "project"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class CandidateList:
    """One traversal's candidates for a position, from one graph.

    Attributes:
        edges: The moves the traversal found, carried whole from the graph.
        scope: `PROJECT` where the graph traversed is this project's own,
            `GLOBAL` where it is the cross-project one.
        weight: How much this traversal's ranks count against the others', on
            top of the scope's own weight. A plain multiplier, not a switch:
            a weight of zero still serves the list's edges, at a fused score
            of zero, rather than excluding them.
        traversal: Which path found the edges, as `paths` names it (FR-034).
            The measurement gate is what reads it, and the baseline is the
            default: a caller that takes a position's moves straight off the
            graph is answering `usual_next`'s question, and the gate admits it.
    """

    edges: tuple[TransitionEdge, ...]
    scope: Scope
    weight: float = 1.0
    traversal: str = USUAL_NEXT

    def fused_weight(self, project_weight: float) -> float:
        """The weight this list's ranks are scored at: its own, times the scope's."""
        return self.weight * (project_weight if self.scope is Scope.PROJECT else 1.0)


@dataclass(frozen=True, slots=True)
class FusedEdge:
    """One candidate move, with where it came from and how it ranked.

    Attributes:
        edge: The transition itself, carried from the graph it was found in.
        scope: `PROJECT` where any project list reached the move, `GLOBAL`
            where only cross-project ones did.
        score: The fused RRF score the candidate was ordered by.
    """

    edge: TransitionEdge
    scope: Scope
    score: float


@dataclass(frozen=True, slots=True)
class FusedCandidates:
    """One position's candidates as a single ranking, best first."""

    edges: tuple[FusedEdge, ...]

    @property
    def scope(self) -> Scope:
        """`GLOBAL` where nothing in the answer came from this project."""
        if self.edges and all(edge.scope is Scope.GLOBAL for edge in self.edges):
            return Scope.GLOBAL
        return Scope.PROJECT


class Fusion:
    """The weighted fusion of every traversal's candidates, asked once per position."""

    def __init__(self, config: Config, counters: Counters) -> None:
        self._config = config
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def fuse(self, *lists: CandidateList) -> FusedCandidates:
        """*lists*' candidates as one ranking, best first.

        Each admitted list is floored, ranked and scored at its own weight; a
        move several lists reached is served once, carrying the sum. An answer
        left entirely to the cross-project lists is the fallback FR-048 allows,
        and it is counted as one. The floor emptying every list is counted once,
        the same reading `Triggers._cleared` gives it, rather than once per
        list: an occasion is silent or it is not, whatever it took to floor it.

        The gate runs before the floor and everything after reads the admitted
        lists alone, so a dark traversal decides neither an answer's scope nor
        whether the occasion was one the floor silenced: it contributed nothing
        to weigh in either reading.

        The diversity rule and the exploration slot shape the order last
        (FR-042), and the two readings below are still readings of the evidence:
        the rule keeps the best-ranked candidate of every pattern, so it cannot
        empty a ranking that had candidates, nor leave an answer holding none of
        this project's where a list reached one.
        """
        admitted = tuple(candidates for candidates in lists if self._admits(candidates))
        supported = tuple(self._cleared(candidates) for candidates in admitted)
        ranked = _fused(supported, _scoped(admitted), self._config.project_weight)
        fused = FusedCandidates(edges=self._explored(self._diverse(ranked)))
        if not fused.edges and any(candidates.edges for candidates in admitted):
            self._counters.bump("guidance_below_support")
        if fused.scope is Scope.GLOBAL:
            self._counters.bump("guidance_fallback_global")
        return fused

    def _admits(self, candidates: CandidateList) -> bool:
        """Whether *candidates*' traversal may reach the fused result (FR-036).

        A traversal the measurement has not admitted still runs and is still
        counted where it ran; what the gate withholds is a reader. Reading
        `Config.unmeasured_traversals` here rather than at any call site is what
        makes the setting the only way to those candidates, and it ships off.
        """
        return candidates.traversal in ADMITTED or self._config.unmeasured_traversals

    def _diverse(self, ranked: tuple[FusedEdge, ...]) -> tuple[FusedEdge, ...]:
        """*ranked* with every candidate repeating a kind of work already kept dropped.

        One candidate per activity class per scope, the best-ranked of them,
        because a second move into the same kind of work is the same claim made
        twice and a set of them is the collapse FR-042 forbids. The scope is
        part of the pattern: what this project does and what other projects do
        are two claims, and a rule reading the kind of work alone would drop
        this project's own move for somebody else's and leave the answer
        reporting a fallback for a position we have recorded (FR-048). Each drop
        is counted, so how much of an order the rule is taking stays readable.
        """
        kept: list[FusedEdge] = []
        patterns: set[tuple[Scope, str]] = set()
        for candidate in ranked:
            pattern = (candidate.scope, class_of(candidate.edge.target))
            if pattern in patterns:
                self._counters.bump("guidance_diversity_dropped")
                continue
            patterns.add(pattern)
            kept.append(candidate)
        return tuple(kept)

    def _explored(self, ranked: tuple[FusedEdge, ...]) -> tuple[FusedEdge, ...]:
        """*ranked* with :data:`EXPLORATION_SLOT` given to a candidate from outside its top.

        The last of the order — the move the graph is least confident about —
        is served in the reserved place and the candidates it passed follow it,
        so an order the graph is very sure about cannot fill every slot a
        reader reaches (FR-042). Nothing is dropped here: the slot is a
        position, and what a reader has room for stays the budget's to decide.
        An order no longer than the top is served as it is, there being no
        outside for the slot to draw from, and is not counted as reserved.
        """
        if len(ranked) <= EXPLORATION_SLOT + 1:
            return ranked
        self._counters.bump("guidance_exploration_slot")
        return (*ranked[:EXPLORATION_SLOT], ranked[-1], *ranked[EXPLORATION_SLOT:-1])

    def _cleared(self, candidates: CandidateList) -> CandidateList:
        """*candidates* reduced to the moves clearing the support floor (FR-045a).

        Filtered silently: an edge seen once exists without being evidence
        enough to rank against edges seen often, but whether the occasion as a
        whole earns a `guidance_below_support` count is `fuse`'s to decide,
        once, after every list has had its say.
        """
        cleared = tuple(
            edge for edge in candidates.edges if edge.support >= self._config.min_support
        )
        return CandidateList(
            edges=cleared,
            scope=candidates.scope,
            weight=candidates.weight,
            traversal=candidates.traversal,
        )


def _fused(
    lists: Sequence[CandidateList], scopes: Mapping[str, Scope], project_weight: float
) -> tuple[FusedEdge, ...]:
    """*lists* as one ranking: one entry per move, ordered by weighted RRF.

    A move several lists reached is served once and carries the sum of what each
    list's rank was worth — the multi-path boost — under the scope *scopes*
    names, so what this project has recorded is never served twice. Where two
    lists of the same scope hold different copies of the same move, the
    best-supported one is served, so which list happened to run last never
    decides what the reader sees.
    """
    scores = _fused_scores(lists, project_weight)
    best: dict[str, TransitionEdge] = {}
    for candidates in lists:
        for edge in candidates.edges:
            if scopes[edge.edge_key] is not candidates.scope:
                continue
            kept = best.get(edge.edge_key)
            if kept is None or edge.support > kept.support:
                best[edge.edge_key] = edge
    fused = {
        key: FusedEdge(edge=edge, scope=scopes[key], score=scores[key])
        for key, edge in best.items()
    }
    return tuple(sorted(fused.values(), key=_order))


def _scoped(lists: Sequence[CandidateList]) -> Mapping[str, Scope]:
    """Which scope each move is served under: this project's own, where any list is.

    The deduplication rule, and read off the lists as given rather than off the
    floored ones: a move this project has recorded is this project's however
    many others made it too, because the cross-project graph counts this
    project's own episodes among its support and so is no independent evidence
    of a move already seen here. A move whose own count is below the floor is
    therefore suppressed rather than resurrected as somebody else's — FR-048's
    fallback is for a procedure unseen in this project, and one this project
    has recorded, even once, is seen here.
    """
    scopes: dict[str, Scope] = {}
    for candidates in lists:
        for edge in candidates.edges:
            if scopes.get(edge.edge_key) is not Scope.PROJECT:
                scopes[edge.edge_key] = candidates.scope
    return scopes


def _fused_scores(lists: Sequence[CandidateList], project_weight: float) -> Mapping[str, float]:
    """Each candidate's RRF score, summed over the ranking of every list.

    Rank, not support: the lists count different numbers of episodes, so a move
    several of them agree on earns its place from being near the top of each
    rather than from support counts that were never comparable.
    """
    scores: dict[str, float] = {}
    for candidates in lists:
        for rank, edge in enumerate(_by_support(candidates.edges)):
            scores[edge.edge_key] = scores.get(edge.edge_key, 0.0) + rrf_score(
                rank, boost=candidates.fused_weight(project_weight)
            )
    return scores


def _by_support(edges: Sequence[TransitionEdge]) -> tuple[TransitionEdge, ...]:
    """*edges* as one traversal ranks them: best-supported first, edge key breaking ties."""
    return tuple(sorted(edges, key=lambda edge: (-edge.support, edge.edge_key)))


def _order(candidate: FusedEdge) -> tuple[float, str]:
    """The order guidance is served in: fused score first, edge key breaking ties."""
    return -candidate.score, candidate.edge.edge_key
