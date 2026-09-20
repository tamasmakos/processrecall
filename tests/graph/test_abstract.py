"""Aggregation: episodic rows in, the abstract procedural graph out (FR-025).

Everything here goes through `aggregate` rather than reaching for a fold of its
own — the abstract layer is an aggregation *of* the episodic rows, and a test
that rebuilt the tally itself would only prove the tally twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from processrecall.config import ActivityClass
from processrecall.graph.abstract import (
    END_KEY,
    START_KEY,
    SUPPORTING_STEPS_KEPT,
    AbstractGraph,
    TransitionEdge,
    aggregate,
    served,
)
from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.guidance.render import next_statement
from processrecall.procedures.outcome import Outcome

KEY = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")

AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def make_step(
    node_key: str,
    *,
    position: int,
    step_id: int = 1,
    template: str = "",
    outcome: str = "neutral",
    key: SequenceKey = KEY,
    at: datetime = AT,
    duration_ms: int | None = None,
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
        template=template or f"{program} <File>",
        occurred_at=at,
        outcome=outcome,
        step_id=step_id,
        duration_ms=duration_ms,
    )


def edge(graph: AbstractGraph, edge_key: str) -> TransitionEdge:
    """The one edge *edge_key* names, so a failure names the move and not an index."""
    return next(candidate for candidate in graph.edges if candidate.edge_key == edge_key)


def test_node_carries_the_coarser_levels_as_is_a_ancestors() -> None:
    """FR-018: one node per identity, the coarser levels hanging off it as is-a."""
    steps = (make_step("Inspection/Read/py", position=0, step_id=7),)

    graph = aggregate(steps, level="class/program")

    node = graph.nodes["Inspection/Read"]
    assert node.level == "class/program"
    assert node.is_a == ("Inspection",)
    assert node.support == 1


@pytest.mark.parametrize(
    ("level", "key", "is_a", "program", "file_ext"),
    [
        ("class", "Inspection", (), "", ""),
        ("class/program", "Inspection/Read", ("Inspection",), "Read", ""),
        (
            "class/program/ext",
            "Inspection/Read/py",
            ("Inspection/Read", "Inspection"),
            "Read",
            "py",
        ),
    ],
)
def test_node_identity_is_materialised_at_every_level(
    level: str, key: str, is_a: tuple[str, ...], program: str, file_ext: str
) -> None:
    """FR-018: all three levels are materialised, each with its own is-a chain."""
    steps = (make_step("Inspection/Read/py", position=0, step_id=1),)

    graph = aggregate(steps, level=level)

    node = graph.nodes[key]
    assert node.level == level
    assert node.is_a == is_a
    assert node.program == program
    assert node.file_ext == file_ext


def test_file_ext_maps_the_no_file_type_sentinel_to_empty() -> None:
    """`--` marks a non-file action, not a file extension of its own."""
    steps = (make_step("ArtifactEvaluation/pytest/--", position=0, step_id=1),)

    graph = aggregate(steps, level="class/program/ext")

    assert graph.nodes["ArtifactEvaluation/pytest/--"].file_ext == ""


def test_aggregate_rejects_an_unmaterialised_level() -> None:
    """A level nobody materialises must fail loudly, not build a dead graph."""
    with pytest.raises(ValueError):
        aggregate((), level="nonsense")


def test_edge_names_the_episodic_steps_that_support_it() -> None:
    """FR-026: a transition carries its support count, recency and outcomes."""
    steps = (
        make_step("Inspection/Read/py", position=0, step_id=11),
        make_step("ArtifactEvaluation/pytest/--", position=1, step_id=12, outcome="failure"),
    )

    graph = aggregate(steps, level="class/program")

    moved = edge(graph, "Inspection/Read -> ArtifactEvaluation/pytest")
    assert (moved.source, moved.target) == ("Inspection/Read", "ArtifactEvaluation/pytest")
    assert moved.support == 1
    assert moved.supporting_steps == (12,)
    assert moved.outcome_counts == {Outcome.FAILURE: 1}
    assert moved.last_seen == AT


def test_every_sequence_is_bookended_by_the_synthetic_start_and_end() -> None:
    """FR-020: a prompt's chain begins at `Start` and ends at `End`, once each."""
    other = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p2")
    steps = (
        make_step("Inspection/Read/py", position=0, step_id=1),
        make_step("Inspection/Read/py", position=0, step_id=2, key=other),
    )

    graph = aggregate(steps, level="class/program")

    assert set(graph.nodes) == {START_KEY, "Inspection/Read", END_KEY}
    assert graph.nodes[START_KEY].support == 2
    assert edge(graph, f"{START_KEY} -> Inspection/Read").supporting_steps == (1, 2)
    assert edge(graph, f"Inspection/Read -> {END_KEY}").supporting_steps == (1, 2)


def test_supporting_steps_are_capped_at_the_most_recent_with_the_true_count_beside() -> None:
    """FR-026: the bounded list is the 50 newest; `support` stays the real total."""
    keys = ("Inspection/Read/py", "ArtifactEvaluation/pytest/--")
    steps = tuple(
        make_step(keys[position % 2], position=position, step_id=position + 1)
        for position in range(120)
    )

    graph = aggregate(steps, level="class/program")

    moved = edge(graph, "Inspection/Read -> ArtifactEvaluation/pytest")
    assert moved.support == 60
    assert len(moved.supporting_steps) == SUPPORTING_STEPS_KEPT
    assert moved.supporting_steps[0] == 22
    assert moved.supporting_steps[-1] == 120


def test_mutual_pair_scores_near_zero_and_is_not_phrased_as_usual_next() -> None:
    """FR-041: a pair run as often one way as the other is co-occurrence, not sequence."""
    keys = ("Inspection/Read/py", "ArtifactEvaluation/pytest/--")
    steps = tuple(
        make_step(keys[position % 2], position=position, step_id=position + 1)
        for position in range(5)
    )

    graph = aggregate(steps, level="class/program")

    mutual = edge(graph, "Inspection/Read -> ArtifactEvaluation/pytest")
    assert mutual.dependency_measure == pytest.approx(0.0)
    assert mutual.lift == pytest.approx(2.0)
    said = next_statement(mutual).text
    assert "co-occur" in said
    assert "usually" not in said
    one_way = edge(graph, f"{START_KEY} -> Inspection/Read")
    assert one_way.dependency_measure == pytest.approx(1.0)
    assert one_way.lift == pytest.approx(2.0)
    assert "usually" in next_statement(one_way).text


def test_activation_ranks_fresh_above_stale_at_equal_support() -> None:
    """FR-039: at equal support, the procedure still being run outranks the stale one, and
    the stale one does not close the gap by racking up more support instead."""
    stale_key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p0")
    busier_stale_key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p2")
    long_ago = AT - timedelta(days=90)
    steps = (
        *(
            make_step("Inspection/Read/py", position=n, step_id=n + 1, at=long_ago, key=stale_key)
            for n in range(3)
        ),
        *(
            make_step(
                "ChangeImplementation/Write/py",
                position=n,
                step_id=n + 10,
                at=long_ago,
                key=busier_stale_key,
            )
            for n in range(5)
        ),
        *(
            make_step("ArtifactEvaluation/pytest/--", position=n, step_id=n + 20)
            for n in range(3)
        ),
    )

    graph = aggregate(steps, level="class/program")

    fresh = graph.nodes["ArtifactEvaluation/pytest"]
    stale = graph.nodes["Inspection/Read"]
    busier_stale = graph.nodes["ChangeImplementation/Write"]
    assert fresh.support == stale.support
    assert fresh.activation == pytest.approx(3.0)
    assert stale.activation < fresh.activation
    assert busier_stale.support > fresh.support
    assert busier_stale.activation < fresh.activation


def test_procedure_reports_median_cost() -> None:
    """FR-024, FR-014: what its steps cost reaches the node as one median, never per step."""
    steps = tuple(
        make_step("Inspection/Read/py", position=position, step_id=position + 1)
        for position in range(3)
    )

    graph = aggregate(steps, level="class/program", costs={1: 100, 2: 200, 3: 900})

    assert graph.nodes["Inspection/Read"].median_cost_micros == 200
    body = served(graph, AT).nodes["Inspection/Read"]
    assert 100 not in body.values()
    assert 900 not in body.values()


def test_procedure_reports_median_duration() -> None:
    """FR-024: how long its steps took reaches the node as one median too."""
    steps = tuple(
        make_step("Inspection/Read/py", position=position, step_id=position + 1, duration_ms=took)
        for position, took in enumerate((10, 30, 500))
    )

    graph = aggregate(steps, level="class/program")

    assert graph.nodes["Inspection/Read"].median_duration_ms == 30
