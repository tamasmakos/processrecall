"""When guidance may speak at all: the four triggers and the silence between them.

Read through `Triggers.fire`, which is the whole seam: FR-045's four triggers,
the per-(node, sequence) latch that keeps a loop to one warning (SC-013), and
FR-045a's support floor, below which a move is silence rather than
low-confidence guidance.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from processrecall.config import Config
from processrecall.graph.abstract import START_KEY, AbstractGraph, PrecedesWorkOn, aggregate
from processrecall.graph.store import EpisodicStep
from processrecall.guidance.locate import locate
from processrecall.guidance.neighborhood import extract
from processrecall.guidance.triggers import Firing, Trigger, Triggers, hot_path

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


def fire(
    triggers: Triggers, graph: AbstractGraph, steps: tuple[EpisodicStep, ...]
) -> Firing | None:
    """What *triggers* makes of an agent that has carried out *steps* so far."""
    position = locate(steps, level=LEVEL)
    return triggers.fire(steps, extract(graph, position, h=1))


def moves(firing: Firing) -> set[tuple[str, str]]:
    """Every edge the firing is made of, as source and target."""
    return {(edge.source, edge.target) for edge in firing.edges}


def test_prompt_start_serves_the_successors_of_start() -> None:
    """A prompt that has carried out no step fires on the start node (FR-045)."""
    graph = aggregate(walk(READ, EDIT) + walk(READ, EDIT, prompt="p2"), level=LEVEL)
    triggers = Triggers(Config(), FakeCounters())

    firing = fire(triggers, graph, ())

    assert firing is not None
    assert firing.trigger is Trigger.PROMPT_START
    assert moves(firing) == {(START_KEY, "Inspection/Read")}


def test_twenty_repetitions_yield_one_repetition_warning() -> None:
    """The per-(node, sequence) latch, not `k` alone, is what bounds it (SC-013)."""
    steps = walk(*[EDIT] * 20)
    graph = aggregate(steps, level=LEVEL)
    triggers = Triggers(Config(), FakeCounters())

    fired = [
        firing for firing in (fire(triggers, graph, steps[:i]) for i in range(1, 21)) if firing
    ]

    assert [firing.trigger for firing in fired] == [Trigger.REPETITION]
    assert moves(fired[0]) == {("ChangeImplementation/Edit", "ChangeImplementation/Edit")}


def test_a_write_that_verification_usually_follows_fires_after_write() -> None:
    """The second of FR-045's occasions: a write whose likeliest successor verifies it."""
    steps = walk(EDIT, TEST, EDIT, TEST)
    graph = aggregate(steps, level=LEVEL)
    triggers = Triggers(Config(), FakeCounters())

    firing = fire(triggers, graph, steps[:1])

    assert firing is not None
    assert firing.trigger is Trigger.AFTER_WRITE
    assert moves(firing) == {("ChangeImplementation/Edit", "ArtifactEvaluation/pytest")}


def test_a_write_that_verification_does_not_follow_stays_silent() -> None:
    """Silence is the default: an ordinary move onward is not an occasion (FR-045)."""
    steps = walk(EDIT, READ, EDIT, READ)
    graph = aggregate(steps, level=LEVEL)
    triggers = Triggers(Config(), FakeCounters())

    assert fire(triggers, graph, steps[:1]) is None


def read_edit_then_failing_test(prompt: str) -> tuple[EpisodicStep, ...]:
    """One prompt that read, edited, then ran a test suite that failed."""
    steps = walk(READ, EDIT, TEST, prompt=prompt)
    return (*steps[:-1], replace(steps[-1], outcome="failure"))


def test_a_pitfall_on_the_likeliest_next_action_warns_before_it_is_taken() -> None:
    """FR-046: the warning is served on the step before the risky move, not after it."""
    steps = read_edit_then_failing_test("p1") + read_edit_then_failing_test("p2")
    graph = aggregate(steps, level=LEVEL)
    triggers = Triggers(Config(), FakeCounters())

    firing = fire(triggers, graph, steps[:2])

    assert firing is not None
    assert firing.trigger is Trigger.PITFALL
    assert moves(firing) == {("ChangeImplementation/Edit", "ArtifactEvaluation/pytest")}


def test_a_move_seen_once_is_silence_and_counts_as_a_suppression() -> None:
    """FR-045a: a single observation is silence, never low-confidence guidance (SC-006)."""
    graph = aggregate(walk(READ, EDIT), level=LEVEL)
    counters = FakeCounters()
    triggers = Triggers(Config(), counters)

    assert fire(triggers, graph, ()) is None
    assert counters.counted["guidance_below_support"] == 1


#: One code entity, spelled the way `code_entities` keys a symbol.
ENTITY = "processrecall/guidance/triggers.py#hot_path"

#: The `class/program` key of `EDIT`, which is what a projection row names.
EDIT_KEY = "ChangeImplementation/Edit"

#: The `class/program` key of `READ`, which is what a projection row names.
READ_KEY = "Inspection/Read"


def test_hot_path_runs_only_the_three_declared_traversals() -> None:
    """FR-033: `usual_next`, `after_change_to_entity`, `usually_refused`, and no fourth."""
    steps = walk(EDIT, TEST) + walk(EDIT, TEST, prompt="p2")
    graph = replace(
        aggregate(steps, level=LEVEL),
        precedes=(PrecedesWorkOn(source=EDIT_KEY, entity_key=ENTITY, support=2),),
    )
    position = replace(locate(steps[:1], level=LEVEL), symbol=ENTITY)
    counters = FakeCounters()

    candidates = hot_path(position, graph, counters=counters)

    assert {counter for counter in counters.counted if counter.startswith("path_")} == {
        "path_usual_next",
        "path_after_change",
        "path_usually_refused",
    }
    assert {candidate.traversal for candidate in candidates} == {"usual_next", "after_change"}


def test_after_a_read_the_entity_anchored_traversal_does_not_run() -> None:
    """FR-033: a change is what puts that question, so after a read it is not asked."""
    steps = walk(READ, EDIT) + walk(READ, EDIT, prompt="p2")
    graph = replace(
        aggregate(steps, level=LEVEL),
        precedes=(PrecedesWorkOn(source=READ_KEY, entity_key=ENTITY, support=2),),
    )
    position = replace(locate(steps[:1], level=LEVEL), symbol=ENTITY)
    counters = FakeCounters()

    candidates = hot_path(position, graph, counters=counters)

    assert "path_after_change" not in counters.counted
    assert {candidate.traversal for candidate in candidates} == {"usual_next"}
