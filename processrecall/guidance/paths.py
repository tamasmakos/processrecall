"""The traversals guidance is made of: one question each, one shape (FR-031).

Every traversal takes the working state and a served graph and answers with
candidates — nothing else beyond a keyword tuning knob a path cannot do without,
the way `neighborhood.extract`'s radius and `generalised`'s support floor are —
so the fusion layer above them grows no special case per path and a path can be
measured, weighted or switched off on its own (FR-036). The working state is
`Position`, built by `locate`, because a position
is what a traversal is asked *from* and there is one of it; the answer is
`Candidate`, which carries the transition together with the traversal that found
it, so a rendered statement can say which path earned it (FR-034). `Position`
is defined once, in `locate.py`, and imported here rather than redeclared; it
is still `key` and `previous` there, not yet the fuller working state the
contract describes, and widening it is `locate`'s task to do, not this one's.

In the renderer layer, so the standard library only, and neither the symbol nor
the code-parsing layer is imported here (FR-037).

Example:
    from processrecall.guidance.paths import usually_refused

    avoid = usually_refused(position, graph, counters=counters)
"""

from __future__ import annotations

from dataclasses import dataclass

from processrecall.config import Counters
from processrecall.graph.abstract import AbstractGraph, PitfallKind, TransitionEdge
from processrecall.guidance.locate import Position

#: The traversal reading the moves out of the position itself (FR-034).
USUAL_NEXT = "usual_next"

#: The traversal backing off to the parent level, as its candidates name it (FR-034).
GENERALISED = "generalised"

#: The traversal reading the refusal pitfall, as its candidates name it (FR-034).
USUALLY_REFUSED = "usually_refused"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One move a traversal offers, and which traversal offered it.

    Attributes:
        transition: The move itself, carried whole from the graph, so whatever
            renders it reads the support and the pitfalls off the evidence
            rather than off a copy made here.
        traversal: The name of the path that produced it — the attribution a
            served statement carries (FR-034), and what tells the fusion layer
            that a candidate two paths reached was reached twice.
    """

    transition: TransitionEdge
    traversal: str

    @property
    def used_counter(self) -> str:
        """The counter bumped when this candidate reaches the rendered output.

        A path's pair of counters is how it earns its place: one says it ran,
        this one says it contributed, and the gap between them is the continuous
        form of FR-036's measurement.
        """
        return f"path_{self.traversal}_used"


def usual_next(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves out of *position* in *graph* — what usually comes next here.

    The plainest of the traversals: the transitions the fold derived for this
    procedure, carried whole. No support floor of its own, because an edge is
    already the evidence of having been observed and what warrants rendering it
    is the renderer's decision, not this path's — the per-list support floor is
    applied before fusion, not inside a traversal (FR-032). Unordered for the
    reason `usually_refused` is.

    A *position* the graph holds no move out of answers with nothing, which is
    the ordinary case at a procedure this project has not recorded moving on
    from; the path is counted as having run either way (FR-034).
    """
    counters.bump(f"path_{USUAL_NEXT}")
    return tuple(
        Candidate(transition=edge, traversal=USUAL_NEXT)
        for edge in graph.edges
        if edge.source == position.key
    )


def generalised(
    position: Position,
    graph: AbstractGraph,
    parent_graph: AbstractGraph,
    *,
    counters: Counters,
    min_support: int,
) -> tuple[Candidate, ...]:
    """The moves out of this *kind* of place, when *position* itself has too few.

    The back-off traversal, and the one the `subsumes` relation is load-bearing
    for: the position's node names the parent it generalises to (`is_a[0]`), and
    *parent_graph* — the coarser level's own aggregation, served alongside
    *graph* rather than re-derived from it — is read for that parent's own
    transitions. So a procedure seen once answers with what the whole activity
    class it belongs to usually does next, at the parent's own level of
    confidence; fusion is where a move two paths reached earns its rank
    (FR-032), not here.

    *min_support* is the floor the position is judged against, a parameter for
    the reason `neighborhood.extract`'s radius is one: the caller passes
    `Config.min_support`. A position already clearing it needs no generalising
    and answers with nothing, which is `usual_next`'s answer standing on its own.

    A *position* the graph holds no node for is answered with nothing too: there
    is no ancestor to read off an absent node, and an unrecorded procedure is
    what `Fusion`'s fall back to the global graph is for, not this path.
    """
    counters.bump(f"path_{GENERALISED}")
    node = graph.nodes.get(position.key)
    if node is None or node.support >= min_support or not node.is_a:
        return ()
    parent_key = node.is_a[0]
    return tuple(
        Candidate(transition=edge, traversal=GENERALISED)
        for edge in parent_graph.edges
        if edge.source == parent_key
    )


def usually_refused(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves out of *position* in *graph* that are usually turned down.

    These are candidates to *avoid* rather than to take: a transition earns the
    `PitfallKind.REFUSED` pitfall in the fold once its refusals cross
    the support floor, so what is returned is already evidenced and needs no
    threshold of its own here (FR-027). Unordered for now: the recency-weighted,
    lift-scaled order the contract asks each traversal for needs an activation
    `ProcedureNode` does not yet carry, so the sort lands with whichever task
    adds that field, not here.

    A position with no refused moves answers with nothing, which is the ordinary
    case; the path is counted as having run either way (FR-034).
    """
    counters.bump(f"path_{USUALLY_REFUSED}")
    return tuple(
        Candidate(transition=edge, traversal=USUALLY_REFUSED)
        for edge in graph.edges
        if edge.source == position.key and _is_usually_refused(edge)
    )


def _is_usually_refused(edge: TransitionEdge) -> bool:
    """Whether *edge* carries the refusal pitfall the traversal reads."""
    return any(pitfall.kind is PitfallKind.REFUSED for pitfall in edge.pitfalls)
