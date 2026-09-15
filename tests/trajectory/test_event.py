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
#: then the five local extensions the convention has no term for.
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
