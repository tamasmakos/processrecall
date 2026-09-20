"""Edge pitfalls: what a move is known to go wrong as (FR-031, R7).

Read through `aggregate` for the same reason the conditions are: a pitfall is a
property of a *move* seen against every other move, and only the aggregation
knows the base failure rate to call one over-represented relative to.
"""

from __future__ import annotations

from dataclasses import replace

from processrecall.graph.abstract import PitfallKind, aggregate
from processrecall.graph.schema import DecisionSource, StepDecision
from processrecall.graph.store import EpisodicStep

from .conftest import edge, sequence
from .conftest import make_aggregate_step as make_step


def edit_then_failing_test(prompt_id: str, first_step_id: int) -> tuple[EpisodicStep, ...]:
    """One prompt that read, edited, then ran a test suite that failed."""
    key = sequence(prompt_id)
    return (
        make_step("Inspection/Read/py", position=0, step_id=first_step_id, key=key),
        make_step("ChangeImplementation/Edit/py", position=1, step_id=first_step_id + 1, key=key),
        make_step(
            "ArtifactEvaluation/Pytest/py",
            position=2,
            step_id=first_step_id + 2,
            key=key,
            outcome="failure",
        ),
    )


def test_a_move_failing_more_than_the_base_rate_is_a_tool_error() -> None:
    """FR-031: over-represented in failed prompts, and clearing `min_support`."""
    steps = edit_then_failing_test("p1", 1) + edit_then_failing_test("p2", 4)

    graph = aggregate(steps, level="class/program")

    moved = edge(graph, "ChangeImplementation/Edit -> ArtifactEvaluation/Pytest")
    (pitfall,) = moved.pitfalls
    assert pitfall.kind is PitfallKind.TOOL_ERROR
    assert round(pitfall.failure_rate, 3) == 0.342
    assert pitfall.support == 2
    assert pitfall.evidence == "Pytest <File>"


def test_a_move_into_end_carries_no_pitfall_however_it_compares_to_the_base_rate() -> None:
    """FR-031: a move into `End` is not one a later prompt can be steered away from."""
    steps = edit_then_failing_test("p1", 1) + edit_then_failing_test("p2", 4)

    graph = aggregate(steps, level="class/program")

    into_end = edge(graph, "ArtifactEvaluation/Pytest -> End")
    assert into_end.pitfalls == ()


def test_a_move_seen_once_is_below_support_and_warns_about_nothing() -> None:
    """FR-031: clearing `min_support` is a condition, not a tiebreak.

    One prompt, so the move that failed failed every time it was seen — which
    is one prompt's bad luck and not something to warn the next one about.
    """
    graph = aggregate(edit_then_failing_test("p1", 1), level="class/program")

    moved = edge(graph, "ChangeImplementation/Edit -> ArtifactEvaluation/Pytest")
    assert moved.support == 1
    assert moved.pitfalls == ()


def repeated(node_key: str, times: int, *, prompt_id: str) -> tuple[EpisodicStep, ...]:
    """One prompt that did the same thing *times* times running."""
    key = sequence(prompt_id)
    return tuple(
        make_step(node_key, position=position, step_id=position + 1, key=key)
        for position in range(times)
    )


def test_a_node_following_itself_k_times_is_step_repetition() -> None:
    """R7: two attempts at a procedure are retries, `k` of them are a loop."""
    steps = repeated("ArtifactEvaluation/Pytest/py", 3, prompt_id="p1")

    graph = aggregate(steps, level="class/program")

    looped = edge(graph, "ArtifactEvaluation/Pytest -> ArtifactEvaluation/Pytest")
    (pitfall,) = looped.pitfalls
    assert pitfall.kind is PitfallKind.STEP_REPETITION
    assert pitfall.evidence == "ArtifactEvaluation/Pytest"
    assert pitfall.support == 1
    assert pitfall.failure_rate == 0.0


def test_two_attempts_at_the_same_procedure_are_a_retry_and_not_a_loop() -> None:
    """R7: below `k` there is no loop to warn about, only ordinary rework."""
    steps = repeated("ArtifactEvaluation/Pytest/py", 2, prompt_id="p1")

    graph = aggregate(steps, level="class/program")

    looped = edge(graph, "ArtifactEvaluation/Pytest -> ArtifactEvaluation/Pytest")
    assert looped.pitfalls == ()


def edit_then_refused_command(
    prompt_id: str, first_step_id: int, *, outcome: str = "neutral"
) -> tuple[EpisodicStep, ...]:
    """One prompt that edited a file, then had its command rejected by the user."""
    key = sequence(prompt_id)
    return (
        make_step("ChangeImplementation/Edit/py", position=0, step_id=first_step_id, key=key),
        make_step(
            "ArtifactEvaluation/Bash/py",
            position=1,
            step_id=first_step_id + 1,
            key=key,
            outcome=outcome,
            decision=StepDecision.REJECTED,
            decision_source=DecisionSource.USER_REJECT,
        ),
    )


def edit_then_accepted_command(prompt_id: str, first_step_id: int) -> tuple[EpisodicStep, ...]:
    """One prompt that edited a file, then had its command let through."""
    key = sequence(prompt_id)
    return (
        make_step("ChangeImplementation/Edit/py", position=0, step_id=first_step_id, key=key),
        make_step(
            "ArtifactEvaluation/Bash/py",
            position=1,
            step_id=first_step_id + 1,
            key=key,
            decision=StepDecision.ACCEPTED,
        ),
    )


def test_refused_pitfall_counts_refusals() -> None:
    """FR-027: a move refused more often than it was allowed, named with how often."""
    steps = (
        edit_then_refused_command("p1", 1)
        + edit_then_refused_command("p2", 3)
        + edit_then_accepted_command("p3", 5)
    )

    graph = aggregate(steps, level="class/program")

    moved = edge(graph, "ChangeImplementation/Edit -> ArtifactEvaluation/Bash")
    (pitfall,) = moved.pitfalls
    assert pitfall.kind is PitfallKind.REFUSED
    assert pitfall.evidence == "Bash <File>"
    assert pitfall.support == 3


def edit_then_test_erroring(
    prompt_id: str, first_step_id: int, *, error_type: str
) -> tuple[EpisodicStep, ...]:
    """`edit_then_failing_test`, with what the run that failed reported."""
    *before, failed = edit_then_failing_test(prompt_id, first_step_id)
    return (*before, replace(failed, error_type=error_type))


def edit_then_read_on(prompt_id: str, first_step_id: int) -> tuple[EpisodicStep, ...]:
    """One prompt that edited a file and read on, never evaluating what it wrote."""
    key = sequence(prompt_id)
    return (
        make_step("ChangeImplementation/Edit/py", position=0, step_id=first_step_id, key=key),
        make_step("Inspection/Read/py", position=1, step_id=first_step_id + 1, key=key),
    )


def rows_deriving_each_kind() -> dict[PitfallKind, tuple[EpisodicStep, ...]]:
    """The episodic rows each kind of the closed vocabulary is derived from (FR-027)."""
    return {
        PitfallKind.TOOL_ERROR: edit_then_failing_test("p1", 1) + edit_then_failing_test("p2", 4),
        PitfallKind.TIMEOUT: edit_then_test_erroring("p1", 1, error_type="TimeoutError")
        + edit_then_test_erroring("p2", 4, error_type="TimeoutError"),
        PitfallKind.RESOURCE_NOT_FOUND: edit_then_test_erroring(
            "p1", 1, error_type="FileNotFoundError"
        )
        + edit_then_test_erroring("p2", 4, error_type="FileNotFoundError"),
        PitfallKind.REFUSED: edit_then_refused_command("p1", 1)
        + edit_then_refused_command("p2", 3),
        PitfallKind.STEP_REPETITION: repeated("ArtifactEvaluation/Pytest/py", 3, prompt_id="p1"),
        PitfallKind.NO_VERIFICATION: edit_then_read_on("p1", 1) + edit_then_read_on("p2", 3),
    }


def test_every_declared_pitfall_kind_derives_from_rows() -> None:
    """FR-027: a closed set, with nothing in it no episodic row produces."""
    derived = {
        kind: {
            pitfall.kind
            for edge in aggregate(steps, level="class/program").edges
            for pitfall in edge.pitfalls
        }
        for kind, steps in rows_deriving_each_kind().items()
    }

    assert set(derived) == set(PitfallKind)
    assert all(kind in kinds for kind, kinds in derived.items())


def test_reported_rate_is_the_lower_bound_not_the_proportion() -> None:
    """FR-040: two refusals in three observations is not served as two thirds."""
    steps = (
        edit_then_refused_command("p1", 1)
        + edit_then_refused_command("p2", 3)
        + edit_then_accepted_command("p3", 5)
    )

    graph = aggregate(steps, level="class/program")

    moved = edge(graph, "ChangeImplementation/Edit -> ArtifactEvaluation/Bash")
    (pitfall,) = moved.pitfalls
    assert pitfall.refusal_rate < 2 / 3
    assert round(pitfall.refusal_rate, 3) == 0.208
