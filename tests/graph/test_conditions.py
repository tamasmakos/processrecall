"""Edge conditions: deterministic context only (FR-029, FR-030).

Read through `aggregate` rather than through a condition builder of its own: a
condition is a property of a *move*, and only the aggregation knows which two
rows a move joined and which prompt it ran in.
"""

from __future__ import annotations

from datetime import UTC, datetime

from processrecall.config import ActivityClass, ProcessType
from processrecall.graph.abstract import AbstractGraph, TransitionEdge, aggregate
from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.procedures.outcome import Outcome

from .conftest import make_sequence

KEY = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")

AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def make_step(
    node_key: str,
    *,
    position: int,
    step_id: int = 1,
    files: tuple[str, ...] = (),
    outcome: str = "neutral",
    key: SequenceKey = KEY,
) -> EpisodicStep:
    """One recorded row, named by the node key the recorder derived for it."""
    activity_class, program, _ = node_key.split("/")
    return EpisodicStep(
        dedup_key=f"{key.prompt_id}-{position}",
        sequence_key=key,
        position=position,
        node_key=node_key,
        activity_class=ActivityClass(activity_class),
        program=program,
        template=f"{program} <File>",
        occurred_at=AT,
        files=files,
        outcome=outcome,
        step_id=step_id,
    )


def edge(graph: AbstractGraph, edge_key: str) -> TransitionEdge:
    """The one edge *edge_key* names, so a failure names the move and not an index."""
    return next(candidate for candidate in graph.edges if candidate.edge_key == edge_key)


def test_condition_is_the_deterministic_context_the_move_was_made_in() -> None:
    """FR-030: process type, same-file-as-previous, the previous step's outcome."""
    steps = (
        make_step("Inspection/Read/py", position=0, step_id=1, files=("a.py",), outcome="success"),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=2, files=("a.py",)),
    )

    sequences = {KEY: make_sequence(steps, process_type=ProcessType.BUG_FIX)}

    graph = aggregate(steps, level="class/program", sequences=sequences)

    moved = edge(graph, "Inspection/Read -> ChangeImplementation/Edit")
    assert moved.condition.process_type == ProcessType.BUG_FIX
    assert moved.condition.same_file_as_previous is True
    assert moved.condition.previous_outcome == Outcome.SUCCESS
    assert moved.condition.intended_activity is None


def test_a_move_onto_another_file_is_not_conditioned_on_the_same_one() -> None:
    """FR-030: same-file-as-previous is read off the two rows, not assumed."""
    steps = (
        make_step("Inspection/Read/py", position=0, step_id=1, files=("a.py",)),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=2, files=("b.py",)),
    )

    graph = aggregate(steps, level="class/program")

    moved = edge(graph, "Inspection/Read -> ChangeImplementation/Edit")
    assert moved.condition.same_file_as_previous is False
    assert moved.condition.process_type == ProcessType.UNKNOWN


def test_the_bookends_have_no_previous_row_to_be_conditioned_on() -> None:
    """FR-020, FR-030: `Start` has no previous step, and `End` lands on no row."""
    steps = (make_step("Inspection/Read/py", position=0, step_id=1, outcome="failure"),)

    sequences = {KEY: make_sequence(steps, process_type=ProcessType.INVESTIGATION)}

    graph = aggregate(steps, level="class/program", sequences=sequences)

    opening = edge(graph, "Start -> Inspection/Read").condition
    assert (opening.same_file_as_previous, opening.previous_outcome) == (None, None)
    closing = edge(graph, "Inspection/Read -> End").condition
    assert closing.process_type == ProcessType.INVESTIGATION
    assert closing.same_file_as_previous is None
    assert closing.previous_outcome == Outcome.FAILURE


def test_an_edge_carries_the_context_its_move_was_commonest_in() -> None:
    """FR-029: one edge, one condition — the typical one, not the latest."""
    elsewhere = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p2")
    read_then_edit = (
        make_step("Inspection/Read/py", position=0, step_id=1, files=("a.py",)),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=2, files=("a.py",)),
        make_step("Inspection/Read/py", position=2, step_id=3, files=("b.py",)),
        make_step("ChangeImplementation/Edit/py", position=3, step_id=4, files=("b.py",)),
    )
    read_then_edit_elsewhere = (
        make_step("Inspection/Read/py", position=0, step_id=5, files=("c.py",), key=elsewhere),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=6, files=(), key=elsewhere),
    )

    graph = aggregate(read_then_edit + read_then_edit_elsewhere, level="class/program")

    moved = edge(graph, "Inspection/Read -> ChangeImplementation/Edit")
    assert moved.support == 3
    assert moved.condition.same_file_as_previous is True


def test_a_tied_condition_breaks_by_the_condition_s_own_repr() -> None:
    """FR-032: a rebuild must resolve a tie the way the incremental fold did.

    Two sequences make the same move once each, differing only in process
    type, so the two conditions are equally supported. `Condition`'s repr
    starts with `process_type`, and ``"BugFix"`` sorts before
    ``"Investigation"`` — so that is the one the edge is required to carry.
    """
    bug_fix_key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p-bugfix")
    investigation_key = SequenceKey(
        conversation_id="c1", session_epoch=0, prompt_id="p-investigation"
    )
    steps = (
        make_step("Inspection/Read/py", position=0, step_id=1, files=("a.py",), key=bug_fix_key),
        make_step(
            "ChangeImplementation/Edit/py", position=1, step_id=2, files=("a.py",), key=bug_fix_key
        ),
        make_step(
            "Inspection/Read/py", position=0, step_id=3, files=("a.py",), key=investigation_key
        ),
        make_step(
            "ChangeImplementation/Edit/py",
            position=1,
            step_id=4,
            files=("a.py",),
            key=investigation_key,
        ),
    )
    sequences = {
        bug_fix_key: make_sequence(steps[:2], process_type=ProcessType.BUG_FIX),
        investigation_key: make_sequence(steps[2:], process_type=ProcessType.INVESTIGATION),
    }

    graph = aggregate(steps, level="class/program", sequences=sequences)

    moved = edge(graph, "Inspection/Read -> ChangeImplementation/Edit")
    assert moved.support == 2
    assert moved.condition.process_type == ProcessType.BUG_FIX
