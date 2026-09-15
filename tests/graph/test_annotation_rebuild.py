"""Agent-authored notes across a full rebuild of the abstract graph (FR-038).

Read through `aggregate` and `reattach` rather than through a store: the notes
live in their own table precisely because nothing about a move is stored, so
what has to be true is that a graph re-derived from the rows alone carries them
again — and says so when the move they name is no longer derived.
"""

from __future__ import annotations

from datetime import UTC, datetime

from processrecall.graph.abstract import (
    AbstractGraph,
    Annotation,
    aggregate,
    edges_from,
    reattach,
    served,
)

from .conftest import make_aggregate_step as make_step
from .conftest import sequence

#: The move the note in these tests is about, spelled as `inspect` prints it.
EDIT_AFTER_READ = "Inspection/Read -> ChangeImplementation/Edit"


def note(edge_key: str = EDIT_AFTER_READ) -> Annotation:
    """One stored note about *edge_key*, attributed and timestamped (FR-038)."""
    return Annotation(
        edge_key=edge_key,
        text="read the whole function before editing it",
        author="claude",
        written_at=datetime(2026, 9, 13, 11, 0, tzinfo=UTC),
    )


def read_then_edit() -> AbstractGraph:
    """One prompt that read a file and then edited it — the annotated move."""
    prompt = sequence("p1")
    steps = (
        make_step("Inspection/Read/py", position=0, step_id=1, key=prompt),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=2, key=prompt),
    )
    return aggregate(steps, level="class/program")


def test_a_note_is_back_on_its_move_after_a_full_rebuild() -> None:
    """FR-038: the rebuild derives the move again, and the note is re-attached to it."""
    rebuilt = reattach(read_then_edit(), [note()])

    attached = {edge.edge_key: edge.annotations for edge in rebuilt.graph.edges}
    assert attached[EDIT_AFTER_READ] == (note(),)
    assert rebuilt.orphaned == ()


def test_a_note_whose_move_vanished_is_reported_orphaned_and_kept_whole() -> None:
    """FR-038: a rebuild that no longer derives the move reports the note, never deletes it."""
    prompt = sequence("p2")
    read_then_test = aggregate(
        (
            make_step("Inspection/Read/py", position=0, step_id=1, key=prompt),
            make_step("ArtifactEvaluation/Pytest/py", position=1, step_id=2, key=prompt),
        ),
        level="class/program",
    )

    rebuilt = reattach(read_then_test, [note()])

    assert rebuilt.orphaned == (note(),)
    assert "Inspection/Read -> ArtifactEvaluation/Pytest" in {
        edge.edge_key for edge in rebuilt.graph.edges
    }
    assert all(edge.annotations == () for edge in rebuilt.graph.edges)


def test_a_note_survives_the_snapshot_it_is_served_from() -> None:
    """FR-038: guidance is read off the file, so a note only counts if the file gives it back."""
    rebuilt = reattach(read_then_edit(), [note()])

    read_back = edges_from(served(rebuilt.graph, datetime(2026, 9, 13, 12, 0, tzinfo=UTC)))

    attached = {edge.edge_key: edge.annotations for edge in read_back}
    assert attached[EDIT_AFTER_READ] == (note(),)
