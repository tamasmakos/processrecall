"""What guidance looks like once it is said: deterministic text, every claim counted.

Read through `BulletRenderer.render`, which is the whole seam: FR-044's support
count on every statement and R8's 1200-character ceiling, which drops whole
statements lowest-support-first rather than truncating one.
"""

from __future__ import annotations

from collections import Counter

from processrecall.guidance.render import BulletRenderer, Deadline, GuidanceStatement, Renderer


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
