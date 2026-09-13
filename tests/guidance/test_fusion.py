"""Which graph answers, and how the two are ranked into one: project first, global after.

Read through `Fusion.fuse`, which is the whole seam: FR-048's project-first
consultation, the `scope: "global"` mark on an answer this project has never
recorded, and the `guidance_fallback_global` count behind it.
"""

from __future__ import annotations

from collections import Counter

from processrecall.graph.abstract import AbstractGraph, TransitionEdge, aggregate
from processrecall.guidance.fusion import Fusion, Scope

from .conftest import walk

READ = "Inspection/Read/py"
EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"

LEVEL = "class/program"


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

    fused = Fusion(counters).fuse((), global_candidates)

    assert [edge.edge.target for edge in fused.edges] == [served(TEST)]
    assert [edge.scope for edge in fused.edges] == [Scope.GLOBAL]
    assert fused.scope is Scope.GLOBAL
    assert counters.counted == Counter({"guidance_fallback_global": 1})


def test_a_procedure_this_project_has_recorded_is_answered_from_its_own_graph() -> None:
    """FR-048: the project's graph is consulted first, so nothing falls back."""
    project = out_of(graph_of((EDIT, TEST), (EDIT, TEST)), EDIT)
    fallback = out_of(graph_of((EDIT, READ), (EDIT, READ)), EDIT)
    counters = FakeCounters()

    fused = Fusion(counters).fuse(project, fallback)

    assert fused.edges[0].scope is Scope.PROJECT
    assert fused.edges[0].edge.target == served(TEST)
    assert fused.scope is Scope.PROJECT
    assert counters.counted == Counter()


def test_a_move_both_graphs_rank_high_outranks_one_only_this_project_has() -> None:
    """RRF, not support: agreement across the two rankings is what lifts a move (R18)."""
    project = out_of(graph_of((EDIT, TEST), (EDIT, TEST), (EDIT, READ), (EDIT, READ)), EDIT)
    fallback = out_of(graph_of((EDIT, READ), (EDIT, READ)), EDIT)

    fused = Fusion(FakeCounters()).fuse(project, fallback)

    assert [edge.edge.target for edge in fused.edges] == [served(READ), served(TEST)]
    assert {edge.scope for edge in fused.edges} == {Scope.PROJECT}


def test_a_move_only_other_projects_have_made_is_served_last_and_marked_global() -> None:
    """FR-048: the fallback adds the procedures unseen here, without displacing ours."""
    project = out_of(graph_of((EDIT, TEST), (EDIT, TEST)), EDIT)
    fallback = out_of(graph_of((EDIT, TEST), (EDIT, TEST), (EDIT, READ), (EDIT, READ)), EDIT)
    counters = FakeCounters()

    fused = Fusion(counters).fuse(project, fallback)

    assert [edge.edge.target for edge in fused.edges] == [served(TEST), served(READ)]
    assert [edge.scope for edge in fused.edges] == [Scope.PROJECT, Scope.GLOBAL]
    assert fused.scope is Scope.PROJECT
    assert counters.counted == Counter()
