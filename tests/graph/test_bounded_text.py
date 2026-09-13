"""Bounded stored text: what the store keeps of a payload, and what it refuses to (FR-010).

The adapter already cuts a live result, but the store is what every source
writes through — a backfill reader, a second harness — so the ceiling has to
hold here too, or "at most 2 KB" is only true of the path that happens to have
been tested.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.store import (
    RESULT_CEILING,
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)
from processrecall.integrations.claude_code.hooks import capture

from .conftest import make_step

KEY = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")
SECRET = "sk-live-do-not-store-this"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteEpisodicStore]:
    """A store over a freshly created index, with the sequence under test open."""
    connection = open_index(tmp_path / "episodes.db")
    opened = SQLiteEpisodicStore(connection)
    opened.open_sequence(
        Sequence(
            key=KEY,
            project_dir_key="proj",
            started_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        )
    )
    try:
        yield opened
    finally:
        connection.close()


def _step(**overrides: object) -> EpisodicStep:
    """One recordable step of the sequence under test, with *overrides* applied."""
    return replace(make_step(dedup_key="toolu_1", sequence_key=KEY), **overrides)


def test_a_three_kilobyte_result_is_stored_at_exactly_the_ceiling(
    store: SQLiteEpisodicStore,
) -> None:
    """FR-010: 2 KB of an action result, whoever handed the step over."""
    store.record(_step(result_snippet="x" * 3072))

    (stored,) = store.steps(KEY)
    assert len(stored.result_snippet) == RESULT_CEILING


def _payload() -> dict[str, object]:
    """A ``PostToolUse`` payload whose arguments carry payload the store must not hold."""
    return {
        "hook_event_name": "PostToolUse",
        "cwd": "/work/proj",
        "session_id": KEY.conversation_id,
        "prompt_id": KEY.prompt_id,
        "tool_name": "Write",
        "tool_use_id": "toolu_1",
        "tool_input": {"file_path": "/work/proj/app.py", "content": SECRET},
        "tool_result": "ok",
        "transcript_path": "/transcripts/c1.jsonl",
    }


def test_arguments_reach_the_dedup_key_but_no_stored_value(tmp_path: Path) -> None:
    """FR-010: a pointer to the record, never a copy of the payload behind it.

    Driven through `capture`, the real event-to-step derivation, rather than a
    hand-built step: `dedup_key` is the last reader of `tool_call_arguments`
    (R3 hashes them), what the step then carries is the template and the file
    list `steps_from`/`identify_procedure` derive from them, and `record_ref`
    for whoever needs the rest.
    """
    connection = open_index(tmp_path / "episodes.db")
    with closing(connection):
        capture(_payload(), connection)
        (stored,) = SQLiteEpisodicStore(connection).steps(KEY)

    assert stored.record_ref == "/transcripts/c1.jsonl#toolu_1"
    assert not any(
        SECRET in value
        for field in fields(stored)
        if isinstance(value := getattr(stored, field.name), str)
    )
