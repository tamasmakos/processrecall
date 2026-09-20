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
is defined once, in `locate.py`, and imported here rather than redeclared; the
scoping arguments a request narrows itself by — a symbol, a file and a kind of
work — are carried there (FR-035), so the entity-anchored paths and the opening
path read them off the position they are asked from rather than taking a
parameter each. The rest of the working state the contract describes is still
`locate`'s task to build, not this one's.

In the renderer layer, so the standard library only, and neither the symbol nor
the code-parsing layer is imported here (FR-037).

Example:
    from processrecall.guidance.paths import usually_refused

    avoid = usually_refused(position, graph, counters=counters)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from processrecall.config import Counters
from processrecall.graph.abstract import (
    START_KEY,
    AbstractGraph,
    Level,
    PitfallKind,
    TransitionEdge,
)
from processrecall.graph.keys import key_at
from processrecall.graph.schema import PPR_ITERATIONS
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

#: The traversal anchored on the callers of the entity just changed, as its
#: candidates name it (FR-034).
AFTER_CALLERS = "after_callers"

#: The traversal reading the moves a kind of work opens with, as its candidates
#: name it (FR-034). Shorter than the function it names, because the counter the
#: name builds is the one the contract publishes: `path_prompt_start`.
PROMPT_START = "prompt_start"

#: The traversal reading the refusal pitfall, as its candidates name it (FR-034).
USUALLY_REFUSED = "usually_refused"

#: The traversal walking out over the snapshot's own adjacency, as its candidates
#: name it (FR-034).
PPR_NEIGHBOURHOOD = "ppr_neighbourhood"

#: The traversal completing the working state's window against the mined runs,
#: as its candidates name it (FR-034).
FREQUENT_EPISODE = "frequent_episode"


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


def used_counter(traversal: str) -> str:
    """The counter *traversal* is credited under once its candidate is served.

    Named here rather than at the renderer, where the attribution is made
    (FR-034): the traversal names this module publishes are what the counter
    name is built out of, so both sides spell it the same way.
    """
    return f"path_{traversal}_used"


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
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves observed around a change to *position*'s entities — what follows them.

    The first traversal anchored on the code rather than on the procedure, which
    is what lets it answer where `usual_next` cannot: the `precedes_work_on`
    projection is read reversed, from the entity to the procedures it is worked
    on around, and the transitions out of those procedures are the candidates.
    A move reached this way need not leave the position the agent stands at —
    that is the point of a second anchor, and fusion is where the two anchors'
    answers meet (FR-032).

    The entities are the position's symbol and file, whichever of them the
    request named (FR-035): the `code_entities` spelling of one belongs to the
    layer FR-037 keeps out of here, so the requester carries the keys in and this
    path only reads them.

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
        graph,
        _scope_of(position),
        AFTER_CHANGE,
        counters=counters,
        procedure_of=lambda edge: edge.source,
    )


def on_entity(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves that land on work on *position*'s entities — what is done on them.

    The same projection as `after_change_to_entity` and the same anchor, read the
    other way round: the procedures the entity is worked on at are the *targets*
    here, so a candidate is a move by which work on this file or symbol usually
    begins rather than one that follows it. The two are separate paths because
    they are separately measurable (FR-036) and because a move can be a good
    answer to one question and a poor answer to the other.

    The entities are the position's, for the reason they are in
    `after_change_to_entity`. An entity the projection names no procedure for
    answers with nothing; the path is counted as having run either way (FR-034).
    """
    return _traversal_over_precedes(
        graph,
        _scope_of(position),
        ON_ENTITY,
        counters=counters,
        procedure_of=lambda edge: edge.target,
    )


def after_callers(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves that follow work on the callers of *position*'s symbol — the blast radius.

    `after_change_to_entity` one anchor further out: a symbol is rarely changed
    without the code calling it being looked at next, and this is the path that
    answers with those moves. The callers are read off the projection rows
    themselves, pre-computed at derivation time and bounded there (R17), so the
    hot path never walks a call graph and this layer stays free of the code
    layer FR-037 keeps out of it.

    What is anchored on is the position's symbol alone, not its file: FR-031
    asks for the callers of a modified *symbol*, and a file is not a symbol the
    projection carries callers for. A request naming no symbol, or a symbol the
    projection carries no callers for, answers with nothing — the ordinary case
    for one nothing calls, or one the projection's bound cut the callers of. The
    path is counted as having run either way (FR-034).
    """
    return _traversal_over_precedes(
        graph,
        _callers_of(graph, _symbol_of(position)),
        AFTER_CALLERS,
        counters=counters,
        procedure_of=lambda edge: edge.source,
    )


def _traversal_over_precedes(
    graph: AbstractGraph,
    entity_keys: frozenset[str],
    traversal: str,
    *,
    counters: Counters,
    procedure_of: Callable[[TransitionEdge], str],
) -> tuple[Candidate, ...]:
    """The edges of *graph* whose *procedure_of* side precedes work on *entity_keys*."""
    counters.bump(f"path_{traversal}")
    procedures = _procedures_preceding_work_on(graph, entity_keys)
    return tuple(
        Candidate(transition=edge, traversal=traversal)
        for edge in graph.edges
        if procedure_of(edge) in procedures
    )


def _procedures_preceding_work_on(
    graph: AbstractGraph, entity_keys: frozenset[str]
) -> frozenset[str]:
    """The procedures *graph*'s projection says precede work on any of *entity_keys*."""
    return frozenset(row.source for row in graph.precedes if row.entity_key in entity_keys)


def _callers_of(graph: AbstractGraph, entity_keys: frozenset[str]) -> frozenset[str]:
    """The callers *graph*'s projection carries pre-computed for *entity_keys* (R17)."""
    return frozenset(
        caller for row in graph.precedes if row.entity_key in entity_keys for caller in row.callers
    )


def _scope_of(position: Position) -> frozenset[str]:
    """The `code_entities` keys *position* scopes the entity-anchored paths to (FR-035).

    A request names a symbol, a file, both or neither, and a path anchored on the
    code answers from all of what it was given: work on the symbol and work on
    the file are both work the request is about.
    """
    return frozenset(key for key in (position.symbol, position.file) if key is not None)


def _symbol_of(position: Position) -> frozenset[str]:
    """The `code_entities` key of *position*'s symbol alone (FR-031).

    A file is not a symbol and the projection carries no callers for one, so
    `after_callers` anchors on this rather than on `_scope_of`'s wider set.
    """
    return frozenset(key for key in (position.symbol,) if key is not None)


def prompt_start_for_process(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves out of `START_KEY` made for *position*'s kind of work — how it begins.

    The one traversal that answers before the prompt has done anything: there is
    no position to read moves out of yet, so the anchor is the synthetic `Start`
    node every sequence's chain leaves (FR-020), and what narrows the answer is
    the kind of work the prompt is for rather than where the agent stands. A bug
    fix and an investigation open differently, and an unfiltered `Start` would
    offer both.

    The kind of work is the position's, carried there by the request that scoped
    itself by it (FR-035); it is the sequence's and not any step's (FR-020), so
    what is filtered on is the one the prompt was opened under. An edge carries
    the condition its move was commonest in (FR-029), so a move made under two
    kinds of work answers to the commoner of them here.

    A kind of work nothing was recorded starting answers with nothing, which is
    the ordinary case early in a project, and so does a request that named no
    kind of work: an unfiltered `Start` would offer every kind at once.
    The path is counted as having run either way (FR-034).
    """
    counters.bump(f"path_{PROMPT_START}")
    return tuple(
        Candidate(transition=edge, traversal=PROMPT_START)
        for edge in graph.edges
        if edge.source == START_KEY and edge.condition.process_type is position.kind_of_work
    )


def usually_refused(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves out of *position* in *graph* that are usually turned down.

    These are candidates to *avoid* rather than to take: a transition earns the
    `PitfallKind.REFUSED` pitfall in the fold once its refusals cross
    the support floor, so what is returned is already evidenced and needs no
    threshold of its own here (FR-027). Unordered for now: the procedure carries
    the recency-weighted activation the contract's lift-scaled order reads
    (FR-039), but nothing sorts by it yet, here or in any other traversal.

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


def ppr_neighbourhood(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves out of the neighbourhood *position* sits in — what a walk out of it turns up.

    The one traversal that does not stay where it was asked from: the working
    state's own procedure is walked outward over the snapshot's adjacency for
    `PPR_ITERATIONS` hops, and the moves out of every procedure reached are the
    candidates. So a move two procedures away from where the agent stands can
    answer, which is the multi-hop reach FR-031 asks for. The budget is the
    constant `graph/schema.py` declares rather than a knob here, because what it
    bounds is the snapshot read whose other half that module already bounds.

    Nothing outside *graph* is read: both kinds of hop are edges the snapshot
    carries, so the walk costs the one parse every guidance call already makes
    and reaches no store (R17). Unordered, for the reason `usually_refused` is.

    Dark until the gate clears it: the path runs and is counted either way
    (FR-034), but `fusion.ADMITTED` does not name it, so its candidates reach no
    reader until its measurement against the baseline is published (FR-036).
    """
    counters.bump(f"path_{PPR_NEIGHBOURHOOD}")
    reached = _walk_out_of(graph, frozenset({position.key}))
    return tuple(
        Candidate(transition=edge, traversal=PPR_NEIGHBOURHOOD)
        for edge in graph.edges
        if edge.source in reached
    )


def _walk_out_of(graph: AbstractGraph, seeds: frozenset[str]) -> frozenset[str]:
    """The procedures within `PPR_ITERATIONS` hops of *seeds*, *seeds* themselves included.

    Breadth first, expanding each procedure once, so a cycle costs no more than
    a chain does and a hop that reaches nothing new ends the walk early.
    """
    reached = seeds
    frontier = seeds
    for _ in range(PPR_ITERATIONS):
        frontier = _adjacent_to(graph, frontier) - reached
        if not frontier:
            break
        reached |= frontier
    return reached


def _adjacent_to(graph: AbstractGraph, keys: frozenset[str]) -> frozenset[str]:
    """The procedures one hop from *keys* over the two adjacencies the snapshot carries.

    A transition out of one of *keys* is the procedural hop; a projection row
    naming an entity one of *keys* is worked on around, followed on to the other
    procedures that same entity is worked on around, is the semantic one (FR-031).
    """
    onward = frozenset(edge.target for edge in graph.edges if edge.source in keys)
    entities = frozenset(row.entity_key for row in graph.precedes if row.source in keys)
    return onward | _procedures_preceding_work_on(graph, entities)


def frequent_episode(
    position: Position, graph: AbstractGraph, *, counters: Counters
) -> tuple[Candidate, ...]:
    """The moves that completed a run like the one *position*'s window just ran.

    The one traversal that reads more of the working state than the procedure the
    agent stands at: the last-k window is matched against the recurring step
    subsequences the fold mined into *graph* (FR-031), longest suffix first, and
    the moves out of the position that the matched runs went on to make are the
    candidates. So where `usual_next` answers with everything ever seen after
    this one procedure, this path answers with what followed the *sequence* of
    procedures that led here — the same move reached by a longer cue.

    The runs are mined at derivation time, so what happens here is a lookup and
    a suffix comparison rather than a walk over the episodic chains (R17). A
    candidate is still an edge of *graph*, so a completion nothing recorded as a
    move out of the position is offered by neither path.

    A window matching no mined run answers with nothing, which is the ordinary
    case at the start of a prompt, whose window is empty; the path is counted as
    having run either way (FR-034).

    Dark until the gate clears it: the path runs and is counted either way
    (FR-034), but `fusion.ADMITTED` does not name it, so its candidates reach no
    reader until its measurement against the baseline is published (FR-036).
    """
    counters.bump(f"path_{FREQUENT_EPISODE}")
    completions = _completions_of(graph, _window_of(position, graph.level))
    matches = (
        Candidate(transition=edge, traversal=FREQUENT_EPISODE)
        for edge in graph.edges
        if edge.source == position.key and edge.target in completions
    )
    return tuple(sorted(matches, key=lambda candidate: -completions[candidate.transition.target]))


def _window_of(position: Position, level: str) -> tuple[str, ...]:
    """*position*'s last-k steps as the procedure keys at *level* they were (FR-038)."""
    lvl = Level.of(level)
    return tuple(key_at(step, lvl) for step in position.recent)


def _completions_of(graph: AbstractGraph, window: tuple[str, ...]) -> dict[str, int]:
    """What *graph*'s mined runs continue the longest matched suffix of *window* with.

    Longest suffix first, and the first suffix any run begins with is the answer:
    a shorter suffix matches more runs, so continuing to it would drown the
    specific cue in the moves `usual_next` already offers.

    Keyed by the run's own `support` — the best-attested run behind a
    continuation, where more than one matches it — so `frequent_episode` can
    read the better-supported completion before the merely eligible one
    instead of treating every match as equally likely.
    """
    for length in range(len(window), 0, -1):
        suffix = window[-length:]
        continued: dict[str, int] = {}
        for episode in graph.episodes:
            if len(episode.steps) > length and episode.steps[:length] == suffix:
                target = episode.steps[length]
                continued[target] = max(continued.get(target, 0), episode.support)
        if continued:
            return continued
    return {}
