"""Shared builders for `guidance/` tests."""

from __future__ import annotations

from datetime import UTC, datetime

from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.symbolic.packs import ActivityClass


def walk(*node_keys: str) -> tuple[EpisodicStep, ...]:
    """One prompt that performed *node_keys* in the order given."""
    key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")
    return tuple(
        _step(node_key, position=position, key=key) for position, node_key in enumerate(node_keys)
    )


def _step(node_key: str, *, position: int, key: SequenceKey) -> EpisodicStep:
    """One recorded row, named by the node key the recorder derived for it."""
    activity_class, program, _ = node_key.split("/")
    return EpisodicStep(
        dedup_key=f"{key.prompt_id}-{position}",
        sequence_key=key,
        position=position,
        node_key=node_key,
        activity_class=ActivityClass(activity_class),
        program=program,
        template=f"{program} <File>",
        occurred_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        step_id=position + 1,
    )
