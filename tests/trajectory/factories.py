"""Shared event factory: one well-formed :class:`TrajectoryEvent`, override what a test needs.

Used by ``tests/trajectory`` and ``tests/procedures`` alike — both exercise code
that reads a `TrajectoryEvent`, so both build one the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from processrecall.trajectory.event import SourceKind, TrajectoryEvent


def make_event(**overrides: Any) -> TrajectoryEvent:
    """A well-formed event, with *overrides* applied — one edit case per test."""
    values: dict[str, Any] = {
        "operation_name": "execute_tool",
        "conversation_id": "conv-1",
        "agent_id": "",
        "agent_name": "",
        "tool_name": "Bash",
        "tool_call_id": "toolu_01",
        "tool_call_arguments": {"command": "pytest -q"},
        "tool_call_result": "1 passed",
        "prompt_id": "prompt-1",
        "project_dir": "/app",
        "record_ref": "/transcripts/conv-1.jsonl#12",
        "occurred_at": datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
        "source_kind": SourceKind.LIVE,
    }
    return TrajectoryEvent(**(values | overrides))
