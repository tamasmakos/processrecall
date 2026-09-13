"""Clean-prompt weighting: what a transition counts for (FR-027, R5, R6).

Read through `aggregate`, like the conditions and the pitfalls it shares a fold
with: cleanliness is a property of the *sequence* a move was observed in, so
only the aggregation is in a position to weigh one move against another.
"""

from __future__ import annotations

from processrecall.config import Config
from processrecall.graph.abstract import aggregate
from processrecall.graph.store import EpisodicStep

from .conftest import edge, make_aggregate_step, make_sequence, sequence

READ_THEN_EDIT = "Inspection/Read -> ChangeImplementation/Edit"


def read_then_edit(
    prompt_id: str, first_step_id: int, *, outcome: str = "success"
) -> tuple[EpisodicStep, ...]:
    """One prompt that read a file and then edited it, the edit going *outcome*."""
    key = sequence(prompt_id)
    return (
        make_aggregate_step("Inspection/Read/py", position=0, step_id=first_step_id, key=key),
        make_aggregate_step(
            "ChangeImplementation/Edit/py",
            position=1,
            step_id=first_step_id + 1,
            key=key,
            outcome=outcome,
        ),
    )


def test_a_transition_from_a_clean_prompt_counts_four_times_one_that_did_not() -> None:
    """FR-027: the same move, once cleanly and once not, weighs 4 + 1."""
    clean = read_then_edit("p1", 1)
    failed = read_then_edit("p2", 3, outcome="failure")

    graph = aggregate(
        clean + failed,
        level="class/program",
        sequences={
            clean[0].sequence_key: make_sequence(clean),
            failed[0].sequence_key: make_sequence(failed),
        },
    )

    moved = edge(graph, READ_THEN_EDIT)
    assert moved.support == 2
    assert moved.weight == 5.0


def test_a_prompt_that_never_closed_is_not_clean_however_its_steps_went() -> None:
    """R5: `clean` is derived from the status too — a crash must not look like success."""
    crashed = read_then_edit("p1", 1)

    graph = aggregate(
        crashed,
        level="class/program",
        sequences={crashed[0].sequence_key: make_sequence(crashed, status="incomplete")},
    )

    assert edge(graph, READ_THEN_EDIT).weight == 1.0


def test_a_sequence_of_only_neutral_steps_is_clean() -> None:
    """R5: absence of failure is the signal, not presence of a green summary."""
    key = sequence("p1")
    neutral = (
        make_aggregate_step(
            "Inspection/Read/py", position=0, step_id=1, key=key, outcome="neutral"
        ),
        make_aggregate_step(
            "ChangeImplementation/Edit/py", position=1, step_id=2, key=key, outcome="neutral"
        ),
    )

    graph = aggregate(
        neutral,
        level="class/program",
        sequences={key: make_sequence(neutral)},
    )

    assert edge(graph, READ_THEN_EDIT).weight == 4.0


def test_the_multiplier_is_the_configured_one() -> None:
    """R6: the 4x is a default a later measurement revises, not a constant."""
    clean = read_then_edit("p1", 1)

    graph = aggregate(
        clean,
        level="class/program",
        sequences={clean[0].sequence_key: make_sequence(clean)},
        config=Config(clean_prompt_weight=2.0),
    )

    assert edge(graph, READ_THEN_EDIT).weight == 2.0
