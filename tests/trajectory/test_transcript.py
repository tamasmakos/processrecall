"""Backfill over the harness's own session records, behind the live seam (FR-015).

The transcript reader is a :class:`TrajectorySource` like the hook adapter is,
so the steps it produces are the same shape as live-captured ones, and a record
whose format it does not understand is skipped and counted rather than aborted
on (FR-016). It reads one small anonymised session; real transcripts never
enter the repository (FR-073).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from processrecall.trajectory.event import SourceKind
from processrecall.trajectory.transcript import SkippedRecord, TranscriptSource

pytestmark = pytest.mark.unit

SESSION = Path(__file__).resolve().parents[1] / "fixtures" / "session.jsonl"


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def _user_turn(uuid: str) -> dict[str, Any]:
    """A prompt record: the turn every action after it belongs to."""
    return {"type": "user", "uuid": uuid, "message": {"content": "fix the failing test"}}


def _tool_call(call_id: str, tool_name: str) -> dict[str, Any]:
    """An assistant record opening the call *call_id* on *tool_name*."""
    block = {"type": "tool_use", "id": call_id, "name": tool_name, "input": {}}
    return {
        "type": "assistant",
        "sessionId": "sess-changed",
        "cwd": "/work/demo",
        "message": {"content": [block]},
    }


def _tool_result(call_id: str, stamp: str) -> dict[str, Any]:
    """A user record answering *call_id*, timed by *stamp*."""
    block = {"type": "tool_result", "tool_use_id": call_id, "content": "done"}
    return {"type": "user", "timestamp": stamp, "message": {"content": [block]}}


def test_the_session_replays_as_the_actions_it_completed() -> None:
    """One event per completed tool call, in the format live capture produces."""
    events = list(TranscriptSource(SESSION, FakeCounters()).events())

    assert [event.tool_name for event in events] == ["Read", "Bash", "Edit"]
    read, run, _edit = events
    assert read.conversation_id == "sess-fixture"
    assert read.prompt_id == "u-prompt-1"
    assert read.tool_call_id == "toolu_01"
    assert read.tool_call_arguments == {"file_path": "/work/demo/src/app.py"}
    assert read.project_dir == "/work/demo"
    assert read.operation_name == "execute_tool"
    assert read.source_kind is SourceKind.BACKFILL
    assert read.record_ref == f"{SESSION}#2"
    assert read.occurred_at == datetime(2026, 1, 5, 9, 30, 5, tzinfo=UTC)
    assert run.tool_call_result == "1 failed, 2 passed in 0.31s"


def test_a_record_the_reader_cannot_read_is_skipped_counted_and_reported() -> None:
    """FR-016: an unknown format costs its own record, never the rest of the file."""
    counters = FakeCounters()
    source = TranscriptSource(SESSION, counters)

    events = list(source.events())

    assert len(events) == 3
    assert counters.counted["backfill_records_skipped"] == 2
    unknown_type, unreadable = source.skipped
    assert isinstance(unknown_type, SkippedRecord)
    assert (unknown_type.ordinal, unreadable.ordinal) == (7, 8)
    assert "x-future-record" in unknown_type.reason
    assert "JSON" in unreadable.reason


def test_a_changed_record_shape_costs_its_own_action_only(tmp_path: Path) -> None:
    """Three shapes the reader cannot place, then a call it can: it never aborts."""
    transcript = tmp_path / "changed.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                _user_turn("p-1"),
                {"type": "assistant", "uuid": "a-1", "message": {"content": "not blocks"}},
                _tool_result("toolu_orphan", stamp="2026-01-05T10:00:01+00:00"),
                _tool_call("toolu_naive", "Read"),
                _tool_result("toolu_naive", stamp="2026-01-05T10:00:03"),
                _tool_call("toolu_ok", "Bash"),
                _tool_result("toolu_ok", stamp="2026-01-05T10:00:05+00:00"),
            )
        ),
        encoding="utf-8",
    )
    counters = FakeCounters()
    source = TranscriptSource(transcript, counters)

    events = list(source.events())

    assert [event.tool_name for event in events] == ["Bash"]
    assert counters.counted["backfill_records_skipped"] == 3
    assert [skip.ordinal for skip in source.skipped] == [2, 3, 5]
    assert "not a list of blocks" in source.skipped[0].reason
    assert "toolu_orphan" in source.skipped[1].reason
    assert "offset-aware" in source.skipped[2].reason
