"""Which graph answers, and how the two are ranked into one: project first, global after.

Read through `Fusion.fuse`, which is the whole seam: FR-048's project-first
consultation, the `scope: "global"` mark on an answer this project has never
recorded, and the `guidance_fallback_global` count behind it.
"""

from __future__ import annotations

from collections import Counter

from processrecall.config import Config
from processrecall.graph.abstract import AbstractGraph, TransitionEdge, aggregate
from processrecall.guidance.fusion import CandidateList, Fusion, Scope
from processrecall.guidance.paths import USUAL_NEXT

from .conftest import walk

READ = "Inspection/Read/py"
EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"
SEARCH = "Search/Grep/py"
SHELL = "ScriptExecution/Bash/py"
COMMIT = "Checkin/git/py"
FETCH = "NetworkRetrieval/WebFetch/py"
GLOB = "Search/Glob/py"

LEVEL = "class/program"

#: Path 8 of the retrieval-paths contract: declared, run and counted, and
#: admitted to the fused result by no published measurement yet (FR-036).
UNMEASURED = "ppr_neighbourhood"


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def graph_of(*prompts: tuple[str, ...]) -> AbstractGraph:
    """The graph aggregated from one walk per prompt, named `p1`, `p2`, ..."""
    steps = tuple(
        step
        for ordinal, nodes in enumerate(prompts, start=1)
        for step in walk(*nodes, prompt=f"p{ordinal}")
    )
    return aggregate(steps, level=LEVEL)


def served(node_key: str) -> str:
    """*node_key* as the graph spells it at `LEVEL`: activity class and program."""
    return node_key.rsplit("/", 1)[0]


def out_of(graph: AbstractGraph, node_key: str) -> tuple[TransitionEdge, ...]:
    """The moves *graph* holds out of *node_key* — one position's candidates."""
    return tuple(edge for edge in graph.edges if edge.source == served(node_key))


def test_a_procedure_unseen_in_this_project_is_answered_from_the_global_graph() -> None:
    """FR-048: no project candidate falls back, marked `global` and counted."""
    global_candidates = out_of(graph_of((EDIT, TEST), (EDIT, TEST)), EDIT)
    counters = FakeCounters()

    fused = Fusion(Config(), counters).fuse(
        CandidateList(edges=(), scope=Scope.PROJECT),
        CandidateList(edges=global_candidates, scope=Scope.GLOBAL),
    )

    assert [edge.edge.target for edge in fused.edges] == [served(TEST)]
    assert [edge.scope for edge in fused.edges] == [Scope.GLOBAL]
    assert fused.scope is Scope.GLOBAL
    assert counters.counted == Counter({"guidance_fallback_global": 1})


def test_a_procedure_this_project_has_recorded_is_answered_from_its_own_graph() -> None:
    """FR-048: the project's graph is consulted first, so nothing falls back."""
    project = out_of(graph_of((EDIT, TEST), (EDIT, TEST)), EDIT)
    fallback = out_of(graph_of((EDIT, READ), (EDIT, READ)), EDIT)
    counters = FakeCounters()

    fused = Fusion(Config(), counters).fuse(
        CandidateList(edges=project, scope=Scope.PROJECT),
        CandidateList(edges=fallback, scope=Scope.GLOBAL),
    )

    assert fused.edges[0].scope is Scope.PROJECT
    assert fused.edges[0].edge.target == served(TEST)
    assert fused.scope is Scope.PROJECT
    assert counters.counted == Counter()


def test_a_move_both_graphs_rank_high_outranks_one_only_this_project_has() -> None:
    """RRF, not support: agreement across the two rankings is what lifts a move (R18)."""
    project = out_of(graph_of((EDIT, TEST), (EDIT, TEST), (EDIT, READ), (EDIT, READ)), EDIT)
    fallback = out_of(graph_of((EDIT, READ), (EDIT, READ)), EDIT)

    fused = Fusion(Config(), FakeCounters()).fuse(
        CandidateList(edges=project, scope=Scope.PROJECT),
        CandidateList(edges=fallback, scope=Scope.GLOBAL),
    )

    assert [edge.edge.target for edge in fused.edges] == [served(READ), served(TEST)]
    assert {edge.scope for edge in fused.edges} == {Scope.PROJECT}


def test_a_move_only_other_projects_have_made_is_served_last_and_marked_global() -> None:
    """FR-048: the fallback adds the procedures unseen here, without displacing ours."""
    project = out_of(graph_of((EDIT, TEST), (EDIT, TEST)), EDIT)
    fallback = out_of(graph_of((EDIT, TEST), (EDIT, TEST), (EDIT, READ), (EDIT, READ)), EDIT)
    counters = FakeCounters()

    fused = Fusion(Config(), counters).fuse(
        CandidateList(edges=project, scope=Scope.PROJECT),
        CandidateList(edges=fallback, scope=Scope.GLOBAL),
    )

    assert [edge.edge.target for edge in fused.edges] == [served(TEST), served(READ)]
    assert [edge.scope for edge in fused.edges] == [Scope.PROJECT, Scope.GLOBAL]
    assert fused.scope is Scope.PROJECT
    assert counters.counted == Counter()


def test_project_weight_does_not_gate_global_candidates() -> None:
    """FR-032: the preference is a weight, so agreement elsewhere can outrank it.

    Under a gate every project candidate precedes every global one, whatever the
    reciprocal ranks say. Here two global traversals reach the same move and it
    is served first, ahead of both of this project's own — the project weight
    leans the order, it does not partition it.
    """
    project = out_of(graph_of((EDIT, TEST), (EDIT, TEST), (EDIT, READ), (EDIT, READ)), EDIT)
    agreed = out_of(graph_of((EDIT, SEARCH), (EDIT, SEARCH)), EDIT)

    fused = Fusion(Config(), FakeCounters()).fuse(
        CandidateList(edges=project, scope=Scope.PROJECT),
        CandidateList(edges=agreed, scope=Scope.GLOBAL),
        CandidateList(edges=agreed, scope=Scope.GLOBAL),
    )

    assert [edge.edge.target for edge in fused.edges] == [
        served(SEARCH),
        served(TEST),
        served(READ),
    ]
    assert [edge.scope for edge in fused.edges] == [Scope.GLOBAL, Scope.PROJECT, Scope.PROJECT]
    assert fused.scope is Scope.PROJECT


def _measured_and_unmeasured() -> tuple[CandidateList, CandidateList]:
    """One admitted list and one dark list, over the same position (FR-036)."""
    measured = out_of(graph_of((EDIT, TEST), (EDIT, TEST)), EDIT)
    unmeasured = out_of(graph_of((EDIT, SEARCH), (EDIT, SEARCH)), EDIT)
    return (
        CandidateList(edges=measured, scope=Scope.PROJECT, traversal=USUAL_NEXT),
        CandidateList(edges=unmeasured, scope=Scope.PROJECT, traversal=UNMEASURED),
    )


def test_unmeasured_traversal_contributes_no_candidates() -> None:
    """FR-036: a traversal no published measurement has admitted stays dark.

    Its list is built and handed to the fusion — the path ran, and its own
    counter says so — and the fused answer holds nothing of it, so the
    occasion is not read as one the support floor silenced either.
    """
    counters = FakeCounters()

    fused = Fusion(Config(), counters).fuse(*_measured_and_unmeasured())

    assert [edge.edge.target for edge in fused.edges] == [served(TEST)]
    assert counters.counted == Counter()


def test_the_off_by_default_setting_is_the_only_way_to_an_unmeasured_traversal() -> None:
    """FR-036: an operator who asks for the dark candidates gets them, and only so."""
    fused = Fusion(Config(unmeasured_traversals=True), FakeCounters()).fuse(
        *_measured_and_unmeasured()
    )

    assert {edge.edge.target for edge in fused.edges} == {served(TEST), served(SEARCH)}


def _descending(*targets: str) -> tuple[TransitionEdge, ...]:
    """One position's moves to *targets*, their support descending in the order given."""
    prompts = tuple(
        (EDIT, target)
        for support, target in enumerate(reversed(targets), start=2)
        for _ in range(support)
    )
    return out_of(graph_of(*prompts), EDIT)


def test_exploration_slot_serves_outside_the_top_of_the_fused_order() -> None:
    """FR-042: the reserved slot goes to the move the fused order is least confident about.

    Six kinds of work, so the diversity rule drops none of them: what the slot
    changes is the order alone, lifting the last candidate over the ones the
    graph is surer about into the fifth place a reader still reaches.
    """
    counters = FakeCounters()

    fused = Fusion(Config(), counters).fuse(
        CandidateList(
            edges=_descending(TEST, READ, SEARCH, SHELL, COMMIT, FETCH), scope=Scope.PROJECT
        )
    )

    assert [edge.edge.target for edge in fused.edges] == [
        served(TEST),
        served(READ),
        served(SEARCH),
        served(SHELL),
        served(FETCH),
        served(COMMIT),
    ]
    assert counters.counted == Counter({"guidance_exploration_slot": 1})


def test_the_diversity_rule_drops_a_second_candidate_of_the_same_kind_of_work() -> None:
    """FR-042: two searches are one pattern, so the weaker of them is dropped and counted."""
    counters = FakeCounters()

    fused = Fusion(Config(), counters).fuse(
        CandidateList(edges=_descending(SEARCH, READ, GLOB), scope=Scope.PROJECT)
    )

    assert [edge.edge.target for edge in fused.edges] == [served(SEARCH), served(READ)]
    assert counters.counted == Counter({"guidance_diversity_dropped": 1})


def test_the_diversity_rule_does_not_drop_this_projects_move_for_another_projects() -> None:
    """FR-042 with FR-048: what this project does and what others do are two patterns.

    Two global traversals agree on a search and outrank this project's own, so a
    rule reading the kind of work alone would drop ours — and the answer would
    then report, and count, a fallback for a position this project has recorded.
    """
    project = out_of(graph_of((EDIT, SEARCH), (EDIT, SEARCH)), EDIT)
    agreed = out_of(graph_of((EDIT, GLOB), (EDIT, GLOB)), EDIT)
    counters = FakeCounters()

    fused = Fusion(Config(), counters).fuse(
        CandidateList(edges=project, scope=Scope.PROJECT),
        CandidateList(edges=agreed, scope=Scope.GLOBAL),
        CandidateList(edges=agreed, scope=Scope.GLOBAL),
    )

    assert [edge.edge.target for edge in fused.edges] == [served(GLOB), served(SEARCH)]
    assert fused.scope is Scope.PROJECT
    assert counters.counted == Counter()
