"""What an agent wrote, served beside what the episodes counted (FR-039).

Read through `BulletRenderer.render`, which is the whole seam: an authored note
and a counted claim travel it as the same statement, and `Origin` is the only
thing that tells them apart once they are on the page.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from processrecall.graph.annotations import Annotation
from processrecall.guidance.render import BulletRenderer, Deadline, GuidanceStatement, Origin


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def test_an_authored_note_is_marked_apart_from_a_counted_claim() -> None:
    """FR-039: both are served, in the order given, and each line says which it is."""
    statements = (
        GuidanceStatement(text="pytest usually follows an edit", support=7),
        GuidanceStatement(
            text="rebuild first here, the snapshot goes stale",
            support=7,
            origin=Origin.ANNOTATION,
        ),
    )

    rendered = BulletRenderer(FakeCounters(), Deadline()).render(statements)

    assert rendered == (
        "- pytest usually follows an edit (7 episodes)\n"
        "- note: rebuild first here, the snapshot goes stale (7 episodes)"
    )


def test_a_stored_note_becomes_a_statement_of_annotation_origin() -> None:
    """FR-038's stored note, carried to the renderer already marked as one (FR-039)."""
    annotation = Annotation(
        edge_key="Inspection/Read -> ChangeImplementation/Edit",
        text="the read only pays here when the edit narrows the file",
        author="claude",
        written_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
    )

    statement = GuidanceStatement.from_annotation(annotation, support=4)

    assert statement.origin is Origin.ANNOTATION
    assert BulletRenderer(FakeCounters(), Deadline()).render([statement]) == (
        "- note: the read only pays here when the edit narrows the file (4 episodes)"
    )
