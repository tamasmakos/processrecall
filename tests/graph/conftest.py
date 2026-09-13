"""Shared builders for `graph/store.py` tests."""

from __future__ import annotations

from datetime import UTC, datetime

from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.symbolic.packs import ActivityClass


def make_step(*, dedup_key: str, sequence_key: SequenceKey, position: int = 0) -> EpisodicStep:
    """One recordable step, with only the fields a test varies exposed."""
    return EpisodicStep(
        dedup_key=dedup_key,
        sequence_key=sequence_key,
        position=position,
        node_key="Inspection/Read",
        activity_class=ActivityClass.INSPECTION,
        template="Read file_path",
        occurred_at=datetime(2026, 9, 13, 10, 0, 1, tzinfo=UTC),
    )
