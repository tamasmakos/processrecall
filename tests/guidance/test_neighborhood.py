"""The h-hop neighbourhood of the located position (FR-043, R7).

Read through `extract`, which is the whole seam: what a located agent may do
next, out to the radius configuration allows, and nothing else.
"""

from __future__ import annotations

from processrecall.graph.abstract import aggregate
from processrecall.guidance.locate import Position, locate
from processrecall.guidance.neighborhood import Neighborhood, extract

from .conftest import walk

READ = "Inspection/Read/py"
EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"
COMMIT = "Checkin/git/py"

LEVEL = "class/program"


def moves(neighborhood: Neighborhood) -> set[tuple[str, str]]:
    """Every extracted transition as source and target, which is what is asserted."""
    return {(edge.source, edge.target) for edge in neighborhood.edges}


def test_one_hop_is_what_may_follow_the_position() -> None:
    """h=1 extracts the moves out of the located node, and no others."""
    steps = walk(READ, EDIT, TEST, COMMIT)
    graph = aggregate(steps, level=LEVEL)
    position = locate(steps[:2], level=LEVEL)

    neighborhood = extract(graph, position, h=1)

    assert neighborhood.center == "ChangeImplementation/Edit"
    assert moves(neighborhood) == {("ChangeImplementation/Edit", "ArtifactEvaluation/pytest")}


def test_two_hops_reach_what_follows_what_follows() -> None:
    """h=2 extracts the second hop as well, and stops there."""
    steps = walk(READ, EDIT, TEST, COMMIT)
    graph = aggregate(steps, level=LEVEL)
    position = locate(steps[:2], level=LEVEL)

    neighborhood = extract(graph, position, h=2)

    assert moves(neighborhood) == {
        ("ChangeImplementation/Edit", "ArtifactEvaluation/pytest"),
        ("ArtifactEvaluation/pytest", "Checkin/git"),
    }


def test_a_procedure_that_loops_yields_each_move_once() -> None:
    """A radius wider than the loop extracts the cycle's moves, not repeats of them."""
    steps = walk(READ, EDIT, READ, EDIT)
    graph = aggregate(steps, level=LEVEL)
    position = locate(steps[:2], level=LEVEL)

    neighborhood = extract(graph, position, h=3)

    assert [(edge.source, edge.target) for edge in neighborhood.edges] == [
        ("ChangeImplementation/Edit", "End"),
        ("ChangeImplementation/Edit", "Inspection/Read"),
        ("Inspection/Read", "ChangeImplementation/Edit"),
    ]


def test_a_position_the_graph_holds_no_node_for_yields_silence() -> None:
    """An unrecorded procedure has no outgoing moves — an empty neighbourhood, not a fault."""
    graph = aggregate(walk(READ, EDIT), level=LEVEL)
    position = Position(key="Unseen/Thing", previous=None)

    neighborhood = extract(graph, position, h=1)

    assert neighborhood.edges == ()
