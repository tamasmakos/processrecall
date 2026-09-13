"""The Claude Code adapter: a hook payload in, the one canonical event out (FR-001).

This is the whole of what welds the system to Claude Code. Everything downstream
reads a :class:`~processrecall.trajectory.event.TrajectoryEvent` and cannot tell
which harness produced it, so the harness's spellings — ``session_id``,
``tool_use_id``, ``tool_input`` — stop here, at the mapping table of
``contracts/trajectory-event.md``.

Two rules of that contract live in this module rather than downstream. The
result is cut to :data:`RESULT_CEILING` characters *here* (FR-010), because a
bound applied later would mean the untruncated text had already been written
somewhere; and a payload missing a field that places the action in a sequence is
counted and dropped, never raised — a hook that raises surfaces against the
developer's own action (R16).

On the hot path, so the standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from processrecall.trajectory.event import SourceKind, TrajectoryEvent

#: The 2 KB ceiling of FR-010, in characters. A snippet is for recognising what
#: happened, not for replaying it: ``record_ref`` points at the whole record.
RESULT_CEILING = 2048


class Counters(Protocol):
    """The slice of the episodic store the adapter writes to.

    Narrower than :class:`processrecall.graph.store.EpisodicStore` on purpose
    (ISP): the adapter only ever counts, so it depends on the one method it
    uses rather than on the whole store.
    """

    def bump(self, counter: str) -> None:
        """Increment the counter named *counter*."""
        ...


def _record_ref(payload: Mapping[str, Any]) -> str:
    """Where the originating record can be found, as ``"<path>#<locator>"``.

    A live ``PostToolUse`` payload carries no line ordinal — the harness is
    still writing the line this action will occupy — so the tool-call id is the
    locator, and a human greps the transcript for it. The backfill reader, which
    does know the ordinal, spells the same pointer with the number (see the
    ``record_ref`` row of ``contracts/trajectory-event.md``).
    """
    transcript = str(payload.get("transcript_path") or "")
    return f"{transcript}#{payload.get('tool_use_id') or ''}" if transcript else ""


def adapt_post_tool_use(payload: Mapping[str, Any], counters: Counters) -> TrajectoryEvent | None:
    """The event *payload* describes, or ``None`` when it describes no routable one.

    ``None`` covers two cases, neither raised into the developer's action: a
    payload for a hook event other than ``PostToolUse`` is not this adapter's to
    read and is ignored outright, uncounted; a ``PostToolUse`` payload with no
    ``session_id``, ``prompt_id`` or ``tool_name`` cannot be placed on a
    sequence, so it increments ``capture_payload_malformed`` and the caller
    moves on.
    """
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    arguments = payload.get("tool_input")
    event = TrajectoryEvent(
        operation_name="execute_tool",
        conversation_id=str(payload.get("session_id") or ""),
        agent_id=str(payload.get("agent_id") or ""),
        agent_name=str(payload.get("agent_type") or ""),
        tool_name=str(payload.get("tool_name") or ""),
        tool_call_id=str(payload.get("tool_use_id") or ""),
        tool_call_arguments=arguments if isinstance(arguments, Mapping) else {},
        tool_call_result=str(payload.get("tool_result") or "")[:RESULT_CEILING],
        prompt_id=str(payload.get("prompt_id") or ""),
        project_dir=str(payload.get("cwd") or ""),
        record_ref=_record_ref(payload),
        occurred_at=datetime.now(UTC),
        source_kind=SourceKind.LIVE,
    )
    if not event.is_routable:
        counters.bump("capture_payload_malformed")
        return None
    return event
