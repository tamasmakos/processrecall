"""The internal edit API: authored changes to the abstract graph (FR-066).

Every node and edge of `abstract.py` is derived from episodic rows, which leaves
no way to say a move *should* exist that nothing was observed for. This module is
that way, and it is a Python interface only: no tool, hook or manifest entry
reaches it (FR-066). It exists for the deferred refiner and for tests.

An edit is refused rather than raised on, because the caller is a program that
must decide what to try next: a rejection carries the reason it was refused, and
the editor records every one of them for the caller to consult — a refusal is
read rather than enforced, so nothing here stops the same proposal from being
made again.

Example:
    from processrecall.graph.edit import GraphEditor

    editor = GraphEditor(aggregate(store.iter_steps(), level=config.level))
    editor.add("Inspection/Read", "ArtifactEvaluation/Pytest")
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from processrecall.graph.abstract import (
    END_KEY,
    START_KEY,
    UNSEEN,
    AbstractGraph,
    Condition,
    TransitionEdge,
    edge_key,
)
from processrecall.symbolic.packs import ProcessType

#: The context an authored edge applies in: every one of them. No observation
#: narrowed it, and a condition invented here would claim a context nothing was
#: seen in (FR-030). Uses `ProcessType.UNKNOWN` rather than a wildcard of its
#: own, so a consumer that filters edges by `condition.process_type` reads an
#: authored edge as belonging to prompts of undetermined type only — narrower
#: than "every prompt" until `Condition` grows a real wildcard.
_UNCONDITIONAL = Condition(
    process_type=ProcessType.UNKNOWN,
    same_file_as_previous=None,
    previous_outcome=None,
)


class RejectionReason(StrEnum):
    """Why an edit was refused, in terms the caller can act on."""

    UNKNOWN_NODE = "unknown_node"
    UNREACHABLE = "unreachable"
    DEAD_END = "dead_end"
    DUPLICATE_MOVE = "duplicate_move"
    UNKNOWN_MOVE = "unknown_move"


@dataclass(frozen=True, slots=True)
class Rejection:
    """One refused edit: the move it named, and what was wrong with it.

    Attributes:
        edge_key: The move the edit named, whether or not the graph has it.
        reason: Which of `RejectionReason` refused it.
    """

    edge_key: str
    reason: RejectionReason


def _authored_edge(source: str, target: str) -> TransitionEdge:
    """The move from *source* to *target*, as an edit rather than a fold.

    Zero support and zero weight: an authored move has no episodic rows behind
    it, and guidance that rendered one as though it had would be the invented
    count FR-044 forbids.
    """
    return TransitionEdge(
        edge_key=edge_key(source, target),
        source=source,
        target=target,
        condition=_UNCONDITIONAL,
        support=0,
        weight=0.0,
        supporting_steps=(),
        outcome_counts={},
        last_seen=UNSEEN,
        pitfalls=(),
    )


class GraphEditor:
    """One abstract graph under authored edits, and what it refused.

    Holds the graph rather than returning a new one from every call because the
    refiner's next proposal depends on what became of the last: an editor is a
    sequence of edits against one graph, which is the state this object is.
    """

    def __init__(self, graph: AbstractGraph) -> None:
        self._graph = graph
        self._rejections: list[Rejection] = []

    def __repr__(self) -> str:
        return f"{type(self).__name__}(graph={self._graph!r})"

    @property
    def graph(self) -> AbstractGraph:
        """The graph as the accepted edits have left it."""
        return self._graph

    @property
    def rejections(self) -> tuple[Rejection, ...]:
        """Every edit refused so far, oldest first.

        Read rather than enforced: a refusal is a fact about the graph the edit
        was proposed against, and a later edit can make the same proposal valid.
        So the memory tells a refiner what has already been tried and why,
        instead of refusing a proposal a second time on its own authority.
        """
        return tuple(self._rejections)

    def add(self, source: str, target: str) -> Rejection | None:
        """Author the move from *source* to *target*, or say why it was refused.

        ``None`` on acceptance.
        """
        if (rejection := self._proposal_rejection(source, target)) is not None:
            return self._refuse(rejection)
        return self._apply(
            (*self._graph.edges, _authored_edge(source, target)), edge_key(source, target)
        )

    def delete(self, key: str) -> Rejection | None:
        """Retract the move *key* names, or say why it was refused.

        ``None`` on acceptance.
        """
        if self._edge(key) is None:
            return self._refuse(Rejection(key, RejectionReason.UNKNOWN_MOVE))
        return self._apply(tuple(edge for edge in self._graph.edges if edge.edge_key != key), key)

    def revise(self, key: str, target: str) -> Rejection | None:
        """Point the move *key* names at *target* instead, or say why it was refused.

        ``None`` on acceptance. One edit rather than a `delete` and an `add`,
        because the graph between the two would be missing a move it is about to
        have again, and FR-021 would refuse a retarget that leaves every node as
        it found it.
        """
        if (edge := self._edge(key)) is None:
            return self._refuse(Rejection(key, RejectionReason.UNKNOWN_MOVE))
        if (rejection := self._proposal_rejection(edge.source, target)) is not None:
            return self._refuse(rejection)
        revised = _authored_edge(edge.source, target)
        remaining = tuple(other for other in self._graph.edges if other.edge_key != key)
        return self._apply((*remaining, revised), revised.edge_key)

    def _apply(self, edges: tuple[TransitionEdge, ...], key: str) -> Rejection | None:
        """Take *edges* as the graph's own, unless FR-021 refuses what they make.

        The one place an edit lands, so every operation is validated by the same
        structural check rather than each carrying its own reading of it.
        """
        if (reason := _structural_reason(self._graph, edges)) is not None:
            return self._refuse(Rejection(key, reason))
        self._graph = _replace_edges(self._graph, edges)
        return None

    def _proposal_rejection(self, source: str, target: str) -> Rejection | None:
        """What refuses the move from *source* to *target* before FR-021 sees it.

        Both ends must already be nodes of the graph: a move onto a procedure
        nothing was ever folded into is one no sequence could run through, and
        inventing the node here would put a symbol in the abstract layer that no
        episodic row stands behind (FR-025). And the graph must not have the move
        already, because an edge key names exactly one move — it is the name an
        annotation hangs off (FR-038a).
        """
        key = edge_key(source, target)
        if any(end not in self._graph.nodes for end in (source, target)):
            return Rejection(key, RejectionReason.UNKNOWN_NODE)
        if self._edge(key) is not None:
            return Rejection(key, RejectionReason.DUPLICATE_MOVE)
        return None

    def _edge(self, key: str) -> TransitionEdge | None:
        """The move *key* names, or ``None`` when the graph has no such move."""
        return next((edge for edge in self._graph.edges if edge.edge_key == key), None)

    def _refuse(self, rejection: Rejection) -> Rejection:
        """Remember *rejection* and hand it back to the caller that earned it."""
        self._rejections.append(rejection)
        return rejection


def _adjacency(moves: Iterable[tuple[str, str]]) -> Mapping[str, set[str]]:
    """*moves* as what each key leads to, so a traversal reads one hop at a time."""
    adjacency: dict[str, set[str]] = {}
    for origin, destination in moves:
        adjacency.setdefault(origin, set()).add(destination)
    return adjacency


def _reachable(adjacency: Mapping[str, set[str]], origin: str) -> set[str]:
    """Every key *origin* leads to through *adjacency*, *origin* included."""
    seen = {origin}
    frontier = [origin]
    while frontier:
        for destination in adjacency.get(frontier.pop(), ()):
            if destination not in seen:
                seen.add(destination)
                frontier.append(destination)
    return seen


def _structural_reason(
    graph: AbstractGraph, edges: tuple[TransitionEdge, ...]
) -> RejectionReason | None:
    """Which half of FR-021 *edges* would break in *graph*, or ``None`` for neither.

    Both halves, because they fail differently: a node nothing leads to can
    never be entered, and a node leading nowhere ends a prompt that the graph
    says has not finished. Read from the synthetic bookends (FR-020), which is
    why they exist.
    """
    from_start = _reachable(_adjacency((e.source, e.target) for e in edges), START_KEY)
    if not graph.nodes.keys() <= from_start:
        return RejectionReason.UNREACHABLE
    to_end = _reachable(_adjacency((e.target, e.source) for e in edges), END_KEY)
    if not graph.nodes.keys() <= to_end:
        return RejectionReason.DEAD_END
    return None


def _replace_edges(graph: AbstractGraph, edges: tuple[TransitionEdge, ...]) -> AbstractGraph:
    """*graph* with *edges* in place of its own, in the fold's edge order.

    Sorted the way `aggregate` sorts, so an edited graph and a rebuilt one
    compare equal rather than differing by the order an edit happened to land in
    (SC-004).
    """
    return AbstractGraph(
        level=graph.level,
        nodes=graph.nodes,
        edges=tuple(sorted(edges, key=lambda edge: (edge.source, edge.target))),
        episode_high_water=graph.episode_high_water,
    )
