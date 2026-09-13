"""The internal edit API: add, delete, revise, and what they refuse (FR-021, FR-066).

Read through `GraphEditor` rather than through `aggregate`: an edit is authored
rather than derived, and the question every test here asks is whether the edited
graph still runs from `Start` to `End` through every node it names.
"""

from __future__ import annotations

from processrecall.graph.abstract import AbstractGraph, aggregate
from processrecall.graph.edit import GraphEditor, Rejection, RejectionReason

from .conftest import make_aggregate_step as make_step
from .conftest import sequence


def base_graph() -> AbstractGraph:
    """Two prompts: one read, edited and ran the tests, the other stopped at the edit.

    The second prompt is what puts a branch into `End`, without which no single
    delete can strand a node one way rather than both ways at once.
    """
    ran_the_tests, stopped_at_the_edit = sequence("p1"), sequence("p2")
    steps = (
        make_step("Inspection/Read/py", position=0, step_id=1, key=ran_the_tests),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=2, key=ran_the_tests),
        make_step("ArtifactEvaluation/Pytest/py", position=2, step_id=3, key=ran_the_tests),
        make_step("Inspection/Read/py", position=0, step_id=4, key=stopped_at_the_edit),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=5, key=stopped_at_the_edit),
    )
    return aggregate(steps, level="class/program")


def edge_keys(graph: AbstractGraph) -> tuple[str, ...]:
    """Every move in *graph*, so a failure names the moves and not an index."""
    return tuple(edge.edge_key for edge in graph.edges)


def test_an_added_move_between_known_nodes_joins_the_graph() -> None:
    """FR-066: `add` is how the refiner authors a move nothing was observed for."""
    editor = GraphEditor(base_graph())

    assert editor.add("Inspection/Read", "ArtifactEvaluation/Pytest") is None
    assert "Inspection/Read -> ArtifactEvaluation/Pytest" in edge_keys(editor.graph)


def test_a_move_to_a_node_the_graph_does_not_have_is_refused() -> None:
    """FR-021: a move onto an unknown node is one no sequence could run through."""
    editor = GraphEditor(base_graph())
    before = edge_keys(editor.graph)

    rejection = editor.add("Inspection/Read", "Delegation/Task")

    assert rejection is not None
    assert rejection.reason is RejectionReason.UNKNOWN_NODE
    assert rejection.edge_key == "Inspection/Read -> Delegation/Task"
    assert edge_keys(editor.graph) == before


def test_a_deleted_move_leaves_the_graph() -> None:
    """FR-066: `delete` retracts a move, here the one the refiner just authored."""
    editor = GraphEditor(base_graph())
    editor.add("Inspection/Read", "ArtifactEvaluation/Pytest")

    assert editor.delete("Inspection/Read -> ArtifactEvaluation/Pytest") is None
    assert "Inspection/Read -> ArtifactEvaluation/Pytest" not in edge_keys(editor.graph)


def test_a_delete_that_puts_a_node_out_of_reach_of_start_is_refused() -> None:
    """FR-021: every node must still be reachable from `Start` after the edit."""
    editor = GraphEditor(base_graph())
    before = edge_keys(editor.graph)

    rejection = editor.delete("Inspection/Read -> ChangeImplementation/Edit")

    assert rejection is not None
    assert rejection.reason is RejectionReason.UNREACHABLE
    assert edge_keys(editor.graph) == before


def test_a_delete_that_leaves_a_node_leading_nowhere_is_refused() -> None:
    """FR-021: every node must still reach `End` after the edit."""
    editor = GraphEditor(base_graph())
    before = edge_keys(editor.graph)

    rejection = editor.delete("ArtifactEvaluation/Pytest -> End")

    assert rejection is not None
    assert rejection.reason is RejectionReason.DEAD_END
    assert edge_keys(editor.graph) == before


def test_the_editor_remembers_the_edits_it_refused_in_order() -> None:
    """FR-066: a refiner's next proposal is chosen against what was already refused."""
    editor = GraphEditor(base_graph())

    editor.add("Inspection/Read", "Delegation/Task")
    editor.delete("ArtifactEvaluation/Pytest -> End")

    assert editor.rejections == (
        Rejection("Inspection/Read -> Delegation/Task", RejectionReason.UNKNOWN_NODE),
        Rejection("ArtifactEvaluation/Pytest -> End", RejectionReason.DEAD_END),
    )


def test_a_move_the_graph_already_has_is_refused_rather_than_doubled() -> None:
    """FR-038a: an edge key names one move — an annotation hangs off it."""
    editor = GraphEditor(base_graph())
    before = edge_keys(editor.graph)

    rejection = editor.add("Inspection/Read", "ChangeImplementation/Edit")

    assert rejection is not None
    assert rejection.reason is RejectionReason.DUPLICATE_MOVE
    assert edge_keys(editor.graph) == before


def test_deleting_a_move_the_graph_does_not_have_is_refused_rather_than_ignored() -> None:
    """Principle V: a delete that changed nothing must not read as one that worked."""
    editor = GraphEditor(base_graph())

    rejection = editor.delete("Inspection/Read -> End")

    assert rejection is not None
    assert rejection.reason is RejectionReason.UNKNOWN_MOVE


def test_a_revised_move_keeps_its_source_and_leads_somewhere_else() -> None:
    """FR-066: `revise` retargets a move the refiner judged to lead to the wrong node."""
    editor = GraphEditor(base_graph())

    assert editor.revise("ChangeImplementation/Edit -> End", "Inspection/Read") is None
    assert "ChangeImplementation/Edit -> End" not in edge_keys(editor.graph)
    assert "ChangeImplementation/Edit -> Inspection/Read" in edge_keys(editor.graph)


def test_revising_a_move_onto_a_node_the_graph_does_not_have_is_refused() -> None:
    """FR-021: a retarget is held to what an authored move is held to."""
    editor = GraphEditor(base_graph())
    before = edge_keys(editor.graph)

    rejection = editor.revise("ChangeImplementation/Edit -> End", "Delegation/Task")

    assert rejection is not None
    assert rejection.reason is RejectionReason.UNKNOWN_NODE
    assert edge_keys(editor.graph) == before


def test_a_revise_that_strands_the_retargeted_nodes_only_incoming_move_is_refused() -> None:
    """FR-021: `revise` removes the old move, so what it strands refuses it too."""
    editor = GraphEditor(base_graph())
    before = edge_keys(editor.graph)

    rejection = editor.revise("Inspection/Read -> ChangeImplementation/Edit", "ArtifactEvaluation/Pytest")

    assert rejection is not None
    assert rejection.reason is RejectionReason.UNREACHABLE
    assert edge_keys(editor.graph) == before
