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
from processrecall.ranking.rrf import rrf_score


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
    """

    edges: tuple[TransitionEdge, ...]
    scope: Scope
    weight: float = 1.0

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

        Each list is floored, ranked and scored at its own weight; a move
        several lists reached is served once, carrying the sum. An answer left
        entirely to the cross-project lists is the fallback FR-048 allows, and
        it is counted as one. The floor emptying every list is counted once,
        the same reading `Triggers._cleared` gives it, rather than once per
        list: an occasion is silent or it is not, whatever it took to floor it.
        """
        supported = tuple(self._cleared(candidates) for candidates in lists)
        fused = FusedCandidates(
            edges=_fused(supported, _scoped(lists), self._config.project_weight)
        )
        if not fused.edges and any(candidates.edges for candidates in lists):
            self._counters.bump("guidance_below_support")
        if fused.scope is Scope.GLOBAL:
            self._counters.bump("guidance_fallback_global")
        return fused

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
        return CandidateList(edges=cleared, scope=candidates.scope, weight=candidates.weight)


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
