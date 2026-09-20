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
Which is why the entity-anchored paths take a `code_entities` key in place of a
position: the entity is the whole of the working state they read, and taking a
`Position` that cannot yet carry one would be taking it to ignore it.

In the renderer layer, so the standard library only, and neither the symbol nor
the code-parsing layer is imported here (FR-037).

Example:
    from processrecall.guidance.paths import usually_refused

    avoid = usually_refused(position, graph, counters=counters)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from processrecall.config import Counters, ProcessType
from processrecall.graph.abstract import START_KEY, AbstractGraph, PitfallKind, TransitionEdge
from processrecall.guidance.locate import Position

#: The traversal reading the moves out of the position itself (FR-034).
USUAL_NEXT = "usual_next"

#: The traversal backing off to the parent level, as its candidates name it (FR-034).
GENERALISED = "generalised"

#: The traversal anchored on the entity just changed, as its candidates name it
#: (FR-034). Shorter than the function it names, because the counter the name
#: builds is the one the contract publishes: `path_after_change`.
AFTER_CHANGE = "after_change"

#: The traversal anchored on the entity in hand, as its candidates name it (FR-034).
ON_ENTITY = "on_entity"

#: The traversal reading the moves a kind of work opens with, as its candidates
#: name it (FR-034). Shorter than the function it names, because the counter the
#: name builds is the one the contract publishes: `path_prompt_start`.
PROMPT_START = "prompt_start"

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


def after_change_to_entity(
    graph: AbstractGraph, entity_key: str, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves observed around a change to *entity_key* — what follows it.

    The first traversal anchored on the code rather than on the procedure, which
    is what lets it answer where `usual_next` cannot: the `precedes_work_on`
    projection is read reversed, from the entity to the procedures it is worked
    on around, and the transitions out of those procedures are the candidates.
    A move reached this way need not leave the position the agent stands at —
    that is the point of a second anchor, and fusion is where the two anchors'
    answers meet (FR-032).

    *entity_key* is the working state's, not the position's: `Position` does not
    carry the active file or symbol yet, and the `code_entities` spelling of one
    belongs to the layer FR-037 keeps out of here, so the caller passes the key
    it already holds rather than this path deriving it.

    An entity the projection names no procedure for answers with nothing, which
    is the ordinary case for a file this project has not recorded working on;
    the path is counted as having run either way (FR-034).

    The contract calls this reading of the projection "reversed": the lookup by
    *entity_key* is the only one the projection supports either way, so what
    reverses is which side of the transition the procedure lands on — its own
    out-edges are read here, as if the procedure preceding the entity were still
    the position, rather than the in-edges `on_entity` reads instead.
    """
    return _traversal_over_precedes(
        graph, entity_key, AFTER_CHANGE, counters=counters, procedure_of=lambda edge: edge.source
    )


def on_entity(
    graph: AbstractGraph, entity_key: str, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves that land on work on *entity_key* — what is done on this file.

    The same projection as `after_change_to_entity` and the same anchor, read the
    other way round: the procedures the entity is worked on at are the *targets*
    here, so a candidate is a move by which work on this file or symbol usually
    begins rather than one that follows it. The two are separate paths because
    they are separately measurable (FR-036) and because a move can be a good
    answer to one question and a poor answer to the other.

    *entity_key* comes from the caller's working state, for the reason it does in
    `after_change_to_entity`. An entity the projection names no procedure for
    answers with nothing; the path is counted as having run either way (FR-034).
    """
    return _traversal_over_precedes(
        graph, entity_key, ON_ENTITY, counters=counters, procedure_of=lambda edge: edge.target
    )


def _traversal_over_precedes(
    graph: AbstractGraph,
    entity_key: str,
    traversal: str,
    *,
    counters: Counters,
    procedure_of: Callable[[TransitionEdge], str],
) -> tuple[Candidate, ...]:
    """The edges of *graph* whose *procedure_of* side precedes work on *entity_key*."""
    counters.bump(f"path_{traversal}")
    procedures = _procedures_preceding_work_on(graph, entity_key)
    return tuple(
        Candidate(transition=edge, traversal=traversal)
        for edge in graph.edges
        if procedure_of(edge) in procedures
    )


def _procedures_preceding_work_on(graph: AbstractGraph, entity_key: str) -> frozenset[str]:
    """The procedures *graph*'s projection says precede work on *entity_key*."""
    return frozenset(row.source for row in graph.precedes if row.entity_key == entity_key)


def prompt_start_for_process(
    graph: AbstractGraph, process_type: ProcessType, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves out of `START_KEY` made for *process_type* — how this work begins.

    The one traversal that answers before the prompt has done anything: there is
    no position to read moves out of yet, so the anchor is the synthetic `Start`
    node every sequence's chain leaves (FR-020), and what narrows the answer is
    the kind of work the prompt is for rather than where the agent stands. A bug
    fix and an investigation open differently, and an unfiltered `Start` would
    offer both.

    *process_type* is the sequence's, which is the prompt's and not any step's
    (FR-020), so the caller passes the one it already opened the sequence with.
    An edge carries the condition its move was commonest in (FR-029), so a move
    made under two kinds of work answers to the commoner of them here.

    A kind of work nothing was recorded starting answers with nothing, which is
    the ordinary case early in a project; the path is counted as having run
    either way (FR-034).
    """
    counters.bump(f"path_{PROMPT_START}")
    return tuple(
        Candidate(transition=edge, traversal=PROMPT_START)
        for edge in graph.edges
        if edge.source == START_KEY and edge.condition.process_type is process_type
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
