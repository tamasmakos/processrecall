"""The canonical event seam: one schema, whatever harness produced the action.

The field names are the contract (`contracts/trajectory-event.md`): every source
adapts onto them and nothing downstream knows which harness it came from, so a
rename here is a breaking change and is asserted as a literal list.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import datetime

import pytest

from processrecall.trajectory.event import SourceKind, TrajectoryEvent
from tests.trajectory.factories import make_event

#: The schema table of ``contracts/trajectory-event.md``, in its order: the nine
#: OpenTelemetry GenAI names first (``gen_ai.`` dropped, dots to underscores),
#: then the five local extensions the convention has no term for, then the
#: optional telemetry fields (FR-022).
CONTRACT_FIELDS = [
    "operation_name",
    "conversation_id",
    "agent_id",
    "agent_name",
    "tool_name",
    "tool_call_id",
    "tool_call_arguments",
    "tool_call_result",
    "prompt_id",
    "project_dir",
    "record_ref",
    "occurred_at",
    "source_kind",
    "decision",
    "decision_source",
    "duration_ms",
    "error_type",
    "input_size_bytes",
    "result_size_bytes",
    "tool_source",
    "event_sequence",
]


def test_event_carries_exactly_the_contract_field_names() -> None:
    """The schema is the contract table — no more fields, no fewer, in order."""
    assert [field.name for field in fields(TrajectoryEvent)] == CONTRACT_FIELDS


def test_naive_occurred_at_is_rejected_rather_than_coerced() -> None:
    """A naive timestamp is a bug in the adapter, not a value to assume UTC for."""
    with pytest.raises(ValueError, match="occurred_at"):
        make_event(occurred_at=datetime(2026, 1, 1, 9, 30))


@pytest.mark.parametrize("routing_field", ["conversation_id", "prompt_id", "tool_name"])
def test_a_missing_routing_field_is_reported_rather_than_raised(routing_field: str) -> None:
    """A malformed payload is counted and dropped, never raised into the agent.

    The adapter needs the verdict as a value it can act on: constructing the
    event must survive so the caller can increment ``capture_payload_malformed``
    and move on.
    """
    event = make_event(**{routing_field: ""})

    assert not event.is_routable


def test_a_complete_event_is_routable() -> None:
    """Every routing field present is the ordinary case, and it passes."""
    assert make_event().is_routable


def test_source_kind_is_the_closed_pair_of_provenances() -> None:
    """Live capture and backfill, spelled as the contract spells them.

    The spelling is what a stored row and a counter carry, so a third kind or a
    renamed one is a schema change and has to be made here first.
    """
    assert [kind.value for kind in SourceKind] == ["live", "backfill"]


def test_an_event_is_frozen_and_slotted() -> None:
    """The event travels the pipeline unchanged, and carries no room to grow.

    Frozen: an adapter, not a later stage, decides what an action was. Slotted:
    on the hot path, and a misspelt attribute must fail where it is written.
    """
    event = make_event()

    with pytest.raises(FrozenInstanceError):
        event.tool_name = "Edit"  # type: ignore[misc]
    assert not hasattr(event, "__dict__")


def test_event_carries_telemetry_fields() -> None:
    """Every telemetry field is carried, and every one of them is optional (FR-022).

    Only a `claude_code.tool_result` or `claude_code.tool_decision` record reports
    these; a replayed transcript reports none of them, so an absent field is the
    ordinary case and reads as ``None`` rather than as a zero that would count.
    The tool-use identity is not among them: it is the event's existing
    ``tool_call_id``, which is what the store deduplicates a step on.
    """
    observed = {
        "decision": "rejected",
        "decision_source": "user_reject",
        "duration_ms": 412,
        "error_type": "timeout",
        "input_size_bytes": 128,
        "result_size_bytes": 4096,
        "tool_source": "mcp",
        "event_sequence": 17,
    }
    event = make_event(**observed)

    assert {name: getattr(event, name) for name in observed} == observed
    assert all(getattr(make_event(), name) is None for name in observed)
