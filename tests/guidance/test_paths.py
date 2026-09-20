"""The traversals guidance is made of, each read directly (FR-031, FR-034, FR-037).

One traversal is one question put to the served graph: a position and a graph
in, candidates out, and the counter that says the path ran. `usually_refused`
answers what is turned down at this point, so its candidates are moves to warn
away from rather than moves to propose.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from processrecall.graph.abstract import PrecedesWorkOn, aggregate
from processrecall.graph.schema import DecisionSource, StepDecision
from processrecall.graph.store import EpisodicStep
from processrecall.guidance.locate import locate
from processrecall.guidance.paths import (
    after_change_to_entity,
    generalised,
    on_entity,
    usual_next,
    usually_refused,
)

from .conftest import walk

EDIT = "ChangeImplementation/Edit/py"
WRITE = "ChangeImplementation/Write/py"
BASH = "ArtifactEvaluation/Bash/py"
TEST = "ArtifactEvaluation/pytest/py"

LEVEL = "class/program"

#: The `class/program` key of `EDIT`, which is what a projection row names.
EDIT_KEY = "ChangeImplementation/Edit"

#: One code entity, spelled the way `code_entities` keys a symbol.
ENTITY = "processrecall/guidance/paths.py#after_change_to_entity"


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def edit_then_refused_command(prompt: str) -> tuple[EpisodicStep, ...]:
    """One prompt that edited a file, then had its command rejected by the user."""
    edited, commanded = walk(EDIT, BASH, prompt=prompt)
    return (
        edited,
        replace(
            commanded,
            decision=StepDecision.REJECTED,
            decision_source=DecisionSource.USER_REJECT,
        ),
    )


def test_usually_refused_returns_avoided_moves() -> None:
    """The moves out of the position that carry the refusal pitfall, and no others."""
    steps = edit_then_refused_command("p1") + edit_then_refused_command("p2") + walk(EDIT, TEST)
    graph = aggregate(steps, level=LEVEL)
    position = locate(walk(EDIT), level=LEVEL)
    counters = FakeCounters()

    candidates = usually_refused(position, graph, counters=counters)

    assert [candidate.transition.edge_key for candidate in candidates] == [
        "ChangeImplementation/Edit -> ArtifactEvaluation/Bash"
    ]
    assert counters.counted["path_usually_refused"] == 1


def test_a_candidate_names_the_counter_its_use_is_counted_under() -> None:
    """FR-034: the path that produced a statement is what the served statement is counted as."""
    steps = edit_then_refused_command("p1") + edit_then_refused_command("p2")
    graph = aggregate(steps, level=LEVEL)
    position = locate(walk(EDIT), level=LEVEL)

    (candidate,) = usually_refused(position, graph, counters=FakeCounters())

    assert candidate.traversal == "usually_refused"
    assert candidate.used_counter == "path_usually_refused_used"


def test_usual_next_returns_the_moves_out_of_the_position() -> None:
    """The transitions leaving the located procedure, and none leaving any other."""
    steps = walk(EDIT, TEST) + walk(EDIT, BASH, prompt="p2") + walk(BASH, TEST, prompt="p3")
    graph = aggregate(steps, level=LEVEL)
    position = locate(walk(EDIT), level=LEVEL)
    counters = FakeCounters()

    candidates = usual_next(position, graph, counters=counters)

    assert [candidate.transition.edge_key for candidate in candidates] == [
        "ChangeImplementation/Edit -> ArtifactEvaluation/Bash",
        "ChangeImplementation/Edit -> ArtifactEvaluation/pytest",
    ]
    assert counters.counted["path_usual_next"] == 1


def test_generalised_falls_back_when_support_below_floor() -> None:
    """FR-031: too little support here, so the parent level's own moves answer instead."""
    steps = walk(WRITE, TEST) + walk(WRITE, TEST, prompt="p2") + walk(EDIT, BASH, prompt="p3")
    graph = aggregate(steps, level=LEVEL)
    parent_graph = aggregate(steps, level="class")
    position = locate(walk(EDIT), level=LEVEL)
    counters = FakeCounters()

    candidates = generalised(position, graph, parent_graph, counters=counters, min_support=2)

    assert [candidate.transition.edge_key for candidate in candidates] == [
        "ChangeImplementation -> ArtifactEvaluation",
    ]
    assert counters.counted["path_generalised"] == 1


def test_generalised_stays_silent_when_the_position_clears_the_floor() -> None:
    """A procedure with its own evidence is answered by `usual_next`, not generalised to."""
    steps = walk(EDIT, BASH) + walk(EDIT, BASH, prompt="p2") + walk(WRITE, TEST, prompt="p3")
    graph = aggregate(steps, level=LEVEL)
    parent_graph = aggregate(steps, level="class")
    position = locate(walk(EDIT), level=LEVEL)
    counters = FakeCounters()

    assert generalised(position, graph, parent_graph, counters=counters, min_support=2) == ()
    assert counters.counted["path_generalised"] == 1


def test_after_change_to_entity_returns_candidates() -> None:
    """FR-031: the entity just changed is the anchor, so moves `usual_next` misses answer."""
    steps = walk(EDIT, TEST) + walk(BASH, WRITE, prompt="p2")
    graph = replace(
        aggregate(steps, level=LEVEL),
        precedes=(PrecedesWorkOn(source=EDIT_KEY, entity_key=ENTITY, support=2),),
    )
    counters = FakeCounters()

    candidates = after_change_to_entity(graph, ENTITY, counters=counters)

    assert [candidate.transition.edge_key for candidate in candidates] == [
        "ChangeImplementation/Edit -> ArtifactEvaluation/pytest"
    ]
    assert counters.counted["path_after_change"] == 1


def test_after_change_to_entity_answers_nothing_for_an_unprojected_entity() -> None:
    """FR-034: the projection naming no procedure still counts the path as run."""
    graph = aggregate(walk(EDIT, TEST), level=LEVEL)
    counters = FakeCounters()

    candidates = after_change_to_entity(graph, ENTITY, counters=counters)

    assert candidates == ()
    assert counters.counted["path_after_change"] == 1


def test_on_entity_returns_the_moves_usually_done_on_it() -> None:
    """FR-031: the moves that land on a procedure this entity is worked on at."""
    steps = walk(TEST, EDIT) + walk(BASH, WRITE, prompt="p2")
    graph = replace(
        aggregate(steps, level=LEVEL),
        precedes=(PrecedesWorkOn(source=EDIT_KEY, entity_key=ENTITY, support=2),),
    )
    counters = FakeCounters()

    candidates = on_entity(graph, ENTITY, counters=counters)

    assert [candidate.transition.edge_key for candidate in candidates] == [
        "ArtifactEvaluation/pytest -> ChangeImplementation/Edit"
    ]
    assert counters.counted["path_on_entity"] == 1


def test_on_entity_answers_nothing_for_an_unprojected_entity() -> None:
    """FR-034: the projection naming no procedure still counts the path as run."""
    graph = aggregate(walk(TEST, EDIT), level=LEVEL)
    counters = FakeCounters()

    candidates = on_entity(graph, ENTITY, counters=counters)

    assert candidates == ()
    assert counters.counted["path_on_entity"] == 1
