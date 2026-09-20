"""What guidance looks like once it is said: deterministic text, every claim counted.

Read through `BulletRenderer.render`, which is the whole seam: FR-044's support
count on every statement and R8's 1200-character ceiling, which drops whole
statements lowest-support-first rather than truncating one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from processrecall.graph.abstract import TransitionEdge, aggregate
from processrecall.graph.annotations import Annotation
from processrecall.graph.schema import DecisionSource, StepDecision
from processrecall.graph.store import EpisodicStep
from processrecall.guidance.paths import GENERALISED, USUAL_NEXT
from processrecall.guidance.render import (
    BulletRenderer,
    Deadline,
    GuidanceStatement,
    Renderer,
    avoid_statements,
)

from .conftest import walk

EDIT = "ChangeImplementation/Edit/py"
BASH = "ArtifactEvaluation/Bash/py"
LEVEL = "class/program"


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def test_every_statement_is_rendered_with_its_support_count() -> None:
    """FR-044: the count behind a claim is served beside the claim, in order given."""
    statements = (
        GuidanceStatement(text="pytest usually follows an edit", support=7),
        GuidanceStatement(text="ruff usually follows pytest", support=3),
    )

    counters = FakeCounters()
    renderer: Renderer = BulletRenderer(counters, Deadline())

    rendered = renderer.render(statements)

    assert rendered == (
        "- pytest usually follows an edit (7 episodes)\n- ruff usually follows pytest (3 episodes)"
    )
    assert counters.counted == Counter()


def padded(support: int) -> GuidanceStatement:
    """A statement long enough that six of them cannot fit the ceiling."""
    return GuidanceStatement(text=f"run pytest after edit {'.' * 200}", support=support)


def supports(rendered: str) -> list[int]:
    """The support count read back off every line of *rendered*."""
    return [int(line.split("(")[1].split()[0]) for line in rendered.splitlines()]


def test_over_budget_drops_whole_statements_lowest_support_first() -> None:
    """R8: the ceiling costs the reader the weakest evidence, never half a claim."""
    counters = FakeCounters()

    rendered = BulletRenderer(counters, Deadline()).render([padded(n) for n in (5, 9, 2, 7, 1, 8)])

    assert supports(rendered) == [5, 9, 2, 7, 8]
    assert len(rendered) <= 1200
    assert counters.counted["guidance_over_budget"] == 1


def test_a_statement_that_alone_exceeds_the_ceiling_is_silence() -> None:
    """R8: an unfittable claim is dropped whole too, leaving nothing to serve."""
    counters = FakeCounters()

    rendered = BulletRenderer(counters, Deadline()).render(
        [GuidanceStatement(text="x" * 1300, support=9)]
    )

    assert rendered == ""
    assert counters.counted["guidance_over_budget"] == 1


def refused_command(prompt: str) -> tuple[EpisodicStep, ...]:
    """One prompt, named *prompt*, that edited a file then had a command refused."""
    edited, commanded = walk(EDIT, BASH, prompt=prompt)
    return edited, replace(
        commanded, decision=StepDecision.REJECTED, decision_source=DecisionSource.USER_REJECT
    )


def edit_then_command(steps: Sequence[EpisodicStep]) -> TransitionEdge:
    """The edit-then-command move as the fold over *steps* derived it."""
    graph = aggregate(tuple(steps), level=LEVEL)
    source, target = EDIT.rsplit("/", 1)[0], BASH.rsplit("/", 1)[0]
    return next(edge for edge in graph.edges if edge.source == source and edge.target == target)


def test_refused_move_rendered_as_avoid_with_lower_bound_rate() -> None:
    """SC-005 and FR-040: a warning, not a suggestion, and never at the naive rate."""
    below_floor = edit_then_command(refused_command("p1"))
    above_floor = edit_then_command(refused_command("p1") + refused_command("p2"))

    assert avoid_statements(below_floor) == ()

    rendered = BulletRenderer(FakeCounters(), Deadline()).render(avoid_statements(above_floor))

    assert rendered == (
        "- avoid ArtifactEvaluation/Bash after ChangeImplementation/Edit:"
        " refused at least 34% of the time (2 episodes)"
    )


def note(text: str) -> Annotation:
    """One stored note about the edit-then-verify move, as an agent wrote it."""
    return Annotation(
        edge_key=f"{EDIT} -> {BASH}",
        text=text,
        author="claude",
        written_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
    )


def test_every_statement_attributes_to_one_path_counter() -> None:
    """FR-034: one counter per served statement, naming the traversal that produced it.

    An authored note is attributed too, to the same counter as the folded
    statement beside it: `GuidanceStatement.origin` marks how the line reads
    (FR-039), not which path it is credited to. This asserts attribution by
    `traversal` alone; the transition's own `origin` — plan.md's fold-vs-
    annotated reader — has no writer yet, so this test leaves that reading
    undecided rather than settling it here.
    """
    statements = (
        GuidanceStatement(text="pytest usually follows an edit", support=7, traversal=USUAL_NEXT),
        GuidanceStatement.from_annotation(
            note("rebuild first here, the snapshot goes stale"), support=7, traversal=USUAL_NEXT
        ),
        GuidanceStatement(text="ruff usually follows pytest", support=3, traversal=GENERALISED),
    )

    counters = FakeCounters()

    rendered = BulletRenderer(counters, Deadline()).render(statements)

    assert counters.counted == Counter({"path_usual_next_used": 2, "path_generalised_used": 1})
    assert rendered.splitlines()[1] == (
        "- note: rebuild first here, the snapshot goes stale (7 episodes)"
    )
