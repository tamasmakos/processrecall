"""Which graph answers: the project's first, the cross-project one after (FR-048).

Two graphs hold candidates for the same position — the project's own, and the
cross-project one every project writes into — and a served answer is one
ranking, not two. Reciprocal Rank Fusion, lifted from the forked recall path
(R18), is what makes it one: a move both graphs know ranks above a move only
one of them does, without either graph's support counts having to be
comparable with the other's.

Project-first is a precedence, not a weight: what this project has recorded
outranks what it has not, however often other projects did it. The global graph
is consulted for the procedures this project has never seen, and an answer made
only of those is marked `Scope.GLOBAL` and counted, because guidance from
somewhere else is a different claim from guidance from here.

Precedence governs *scope*, not *order within it*: a move this project has
recorded is always served ahead of one only the global graph has, but where
two of the project's own moves land relative to each other is decided by the
fused RRF score across both graphs, not by the project graph alone — a move
the global graph also ranks highly is itself evidence, the same way a second
episode inside this project would be. This is a deliberate reading of FR-048's
"consult ... first, fall back ... for procedures unseen": it is silent on
whether the fallback ranking may also inform the order of what the project
already has, and R18 asks for a fusion of both, not a project-only order with
fallback appended.

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.fusion import Fusion

    fused = Fusion(counters).fuse(project_edges, global_edges)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from processrecall.config import Counters
from processrecall.graph.abstract import TransitionEdge
from processrecall.ranking.rrf import rrf_score


class Scope(StrEnum):
    """Which graph a candidate — or a whole answer — came from (FR-048)."""

    PROJECT = "project"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class FusedEdge:
    """One candidate move, with where it came from and how it ranked.

    Attributes:
        edge: The transition itself, carried from the graph it was found in.
        scope: `PROJECT` where this project has recorded the move, `GLOBAL`
            where only the cross-project graph has.
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
    """The project-first fusion of two graphs' candidates, asked once per position."""

    def __init__(self, counters: Counters) -> None:
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def fuse(
        self, project: Sequence[TransitionEdge], fallback: Sequence[TransitionEdge]
    ) -> FusedCandidates:
        """*project*'s candidates for a position, falling back to *fallback*'s.

        A move this project has recorded is served ahead of every move only
        other projects have, and the two rankings decide the order within each
        scope. An answer left entirely to *fallback* is the fallback FR-048
        allows, and it is counted as one.
        """
        scores = _fused_scores(project, fallback)
        recorded = {edge.edge_key for edge in project}
        candidates = [
            FusedEdge(edge=edge, scope=Scope.PROJECT, score=scores[edge.edge_key])
            for edge in project
        ] + [
            FusedEdge(edge=edge, scope=Scope.GLOBAL, score=scores[edge.edge_key])
            for edge in fallback
            if edge.edge_key not in recorded
        ]
        fused = FusedCandidates(edges=tuple(sorted(candidates, key=_precedence)))
        if fused.scope is Scope.GLOBAL:
            self._counters.bump("guidance_fallback_global")
        return fused


def _fused_scores(
    project: Sequence[TransitionEdge], fallback: Sequence[TransitionEdge]
) -> Mapping[str, float]:
    """Each candidate's RRF score, summed over the ranking of both graphs.

    Rank, not support: the two graphs count different numbers of episodes, so
    a move agreed on by both earns its place from being near the top of each
    rather than from support counts that were never comparable.
    """
    scores: dict[str, float] = {}
    for ranking in (_by_support(project), _by_support(fallback)):
        for rank, edge in enumerate(ranking):
            scores[edge.edge_key] = scores.get(edge.edge_key, 0.0) + rrf_score(rank)
    return scores


def _by_support(edges: Sequence[TransitionEdge]) -> tuple[TransitionEdge, ...]:
    """*edges* as one graph ranks them: best-supported first, edge key breaking ties."""
    return tuple(sorted(edges, key=lambda edge: (-edge.support, edge.edge_key)))


def _precedence(candidate: FusedEdge) -> tuple[bool, float, str]:
    """The order guidance is served in: project scope first, then the fused score."""
    return candidate.scope is Scope.GLOBAL, -candidate.score, candidate.edge.edge_key
