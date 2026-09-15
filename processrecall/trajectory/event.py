"""The canonical trajectory event: one schema, every source (FR-001).

A harness adapter's only job is to produce a :class:`TrajectoryEvent`; nothing
downstream knows whether it came from a hook payload or a replayed transcript.
The field names follow the OpenTelemetry GenAI semantic conventions wherever one
exists — ``gen_ai.`` dropped, dots turned into underscores — and the five that
the convention has no term for are named plainly rather than given invented
``gen_ai.``-shaped names (R1, ``contracts/trajectory-event.md``).

On the hot path, so the standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SourceKind(StrEnum):
    """Where an event came from. Provenance only; it never changes behaviour."""

    LIVE = "live"
    BACKFILL = "backfill"


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    """One completed agent action, in the single format the pipeline reads.

    Attributes:
        operation_name: ``gen_ai.operation.name`` — ``"execute_tool"`` here.
        conversation_id: ``gen_ai.conversation.id``.
        agent_id: ``gen_ai.agent.id``; ``""`` for the main agent.
        agent_name: ``gen_ai.agent.name``; ``""`` when the harness reports none.
        tool_name: ``gen_ai.tool.name``, as the harness spells it.
        tool_call_id: ``gen_ai.tool.call.id``.
        tool_call_arguments: ``gen_ai.tool.call.arguments``; read for the
            template and the file list, never persisted whole.
        tool_call_result: ``gen_ai.tool.call.result``, already truncated by the
            adapter (FR-010) — nothing downstream may assume it is complete.
        prompt_id: The user turn this action belongs to.
        project_dir: Absolute working directory; the anchor for path
            normalisation.
        record_ref: ``"<source path>#<line ordinal>"`` for a backfilled event; a
            live payload has no ordinal yet, so its adapter spells the locator
            with the tool-call id instead. Either way, a pointer for a human,
            never a copy and never re-read by the pipeline.
        occurred_at: When the action completed, timezone-aware.
        source_kind: Live capture or backfill.
    """

    operation_name: str
    conversation_id: str
    agent_id: str
    agent_name: str
    tool_name: str
    tool_call_id: str
    tool_call_arguments: Mapping[str, object] = field(hash=False, compare=False)
    tool_call_result: str
    prompt_id: str
    project_dir: str
    record_ref: str
    occurred_at: datetime
    source_kind: SourceKind

    def __post_init__(self) -> None:
        """Refuse a naive ``occurred_at``.

        Two harnesses on two machines produce one ordering only if every
        timestamp carries its offset; a naive datetime silently assumed to be
        UTC would reorder a sequence rather than fail. It is a bug in the
        adapter that produced it, not a value to coerce.
        """
        if self.occurred_at.utcoffset() is None:
            raise ValueError(f"occurred_at={self.occurred_at!r} is not timezone-aware")

    @property
    def is_routable(self) -> bool:
        """Whether the fields that place this action in a sequence are all present.

        A malformed payload must not raise into the agent's path: the caller
        drops the event and increments ``capture_payload_malformed`` (R16), so
        the verdict is a value rather than an exception.
        """
        return bool(self.conversation_id and self.prompt_id and self.tool_name)
