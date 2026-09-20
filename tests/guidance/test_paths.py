"""The traversals guidance is made of, each read directly (FR-031, FR-034, FR-037).

One traversal is one question put to the served graph: a position and a graph
in, candidates out, and the counter that says the path ran. `usually_refused`
answers what is turned down at this point, so its candidates are moves to warn
away from rather than moves to propose.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from processrecall.graph.abstract import aggregate
from processrecall.graph.schema import DecisionSource, StepDecision
from processrecall.graph.store import EpisodicStep
from processrecall.guidance.locate import locate
from processrecall.guidance.paths import usually_refused

from .conftest import walk

EDIT = "ChangeImplementation/Edit/py"
BASH = "ArtifactEvaluation/Bash/py"
TEST = "ArtifactEvaluation/pytest/py"

LEVEL = "class/program"


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
