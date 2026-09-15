"""What a slow hot path is served as: nothing, and a counted breach.

Read through `BulletRenderer(counters, deadline)`: the 250 ms soft budget of R9
runs from hook entry, so a snapshot read that outruns it costs the injection
whole — never a late one, never a truncated one (FR-042, SC-001).
"""

from __future__ import annotations

import time
from collections import Counter

from processrecall.guidance.render import BulletRenderer, Deadline, GuidanceStatement


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def statements() -> tuple[GuidanceStatement, ...]:
    """Guidance that would be served happily inside the budget."""
    return (GuidanceStatement(text="pytest usually follows an edit", support=7),)


def test_a_render_past_the_soft_budget_is_silence() -> None:
    """R9: the budget is crossed before rendering, so the injection is dropped whole."""
    counters = FakeCounters()
    spent = Deadline(started_at=time.perf_counter() - 0.3)

    rendered = BulletRenderer(counters, spent).render(statements())

    assert rendered == ""
    assert counters.counted["guidance_deadline_exceeded"] == 1


def test_guidance_inside_the_budget_is_served_untouched() -> None:
    """R9: the budget catches a pathological read, it does not silence the hot path."""
    counters = FakeCounters()

    rendered = BulletRenderer(counters, Deadline()).render(statements())

    assert rendered == "- pytest usually follows an edit (7 episodes)"
    assert counters.counted == Counter()
