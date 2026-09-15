"""Shared event factory: one well-formed :class:`TrajectoryEvent`, override what a test needs.

Used by ``tests/trajectory`` and ``tests/procedures`` alike — both exercise code
that reads a `TrajectoryEvent`, so both build one the same way.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

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


class _ScriptedCall(Protocol):
    """What :class:`FakeSource` needs from one call of a scripted session."""

    tool_name: str
    arguments: Any
    result: str


class FakeSource:
    """A second harness, as a trajectory source: its own tools, the one seam.

    It inherits nothing — a source is anything that yields events (FR-002) —
    and it knows nothing about the vocabulary pack that classifies its tools:
    the harness names the tool, the pack names the activity.
    """

    #: When the scripted session ran, and how far apart its calls are.
    _STARTED_AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
    _BETWEEN_CALLS = timedelta(seconds=30)

    def __init__(self, calls: Sequence[_ScriptedCall]) -> None:
        self._calls = tuple(calls)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(calls={len(self._calls)})"

    def events(self) -> Iterator[TrajectoryEvent]:
        """Yield one canonical event per scripted call, oldest first."""
        for ordinal, call in enumerate(self._calls):
            yield TrajectoryEvent(
                operation_name="execute_tool",
                conversation_id="fixture-conversation",
                agent_id="",
                agent_name="",
                tool_name=call.tool_name,
                tool_call_id=f"call-{ordinal}",
                tool_call_arguments=call.arguments,
                tool_call_result=call.result,
                prompt_id="fixture-prompt",
                project_dir="/work/demo",
                record_ref=f"fixture-session#{ordinal}",
                occurred_at=self._STARTED_AT + ordinal * self._BETWEEN_CALLS,
                source_kind=SourceKind.LIVE,
            )
