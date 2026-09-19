"""The synthetic OTLP corpus checks itself: every line parses and is the record it claims.

The telemetry fixtures are built to `contracts/telemetry-records.md`, never captured
from a real session — a captured line carries an account UUID and an email address,
and the repository's standing rule is that real transcripts never enter it. Because
nothing else validates the corpus, a file filed under ``gate_off`` that carries the
gated attributes anyway would make every test reading it green for the wrong reason.

These tests read each file the way the reader of `contracts/collector-transport.md`
will — `resourceLogs` → `scopeLogs` → `logRecords`, attributes flattened — and assert
the trait each file's name promises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

TELEMETRY = Path(__file__).parent / "telemetry"

#: The seven records the memory consumes, in the order the contract lists them.
CONSUMED = (
    "claude_code.user_prompt",
    "claude_code.api_request",
    "claude_code.api_error",
    "claude_code.api_refusal",
    "claude_code.tool_result",
    "claude_code.tool_decision",
    "claude_code.subagent_completed",
)

#: Every fixture file, against the ``event.name`` sequence its name claims to carry.
CLAIMS: dict[str, tuple[str, ...]] = {
    "all_records.jsonl": CONSUMED,
    "batched.jsonl": (
        "claude_code.api_request",
        "claude_code.tool_decision",
        "claude_code.tool_result",
    ),
    "truncated.jsonl": ("claude_code.user_prompt", "claude_code.tool_result"),
    "forbidden_content.jsonl": (
        "claude_code.user_prompt",
        "claude_code.assistant_response",
        "claude_code.api_request_body",
    ),
    "identity_attributes.jsonl": ("claude_code.api_request",),
    "events_only.jsonl": (
        "claude_code.user_prompt",
        "claude_code.api_request",
        "claude_code.tool_decision",
        "claude_code.tool_result",
        "claude_code.subagent_completed",
    ),
    "gate_off.jsonl": (
        "claude_code.user_prompt",
        "claude_code.tool_decision",
        "claude_code.tool_result",
        "claude_code.subagent_completed",
    ),
}

#: The content attributes the reader never binds, under any gate (FR-011): each sits on
#: the record or span the contract names for it, and the line's union is what SC-002 is
#: measured against.
FORBIDDEN = ("prompt", "response", "body", "body_ref", "user_prompt")

#: The identity attributes dropped at the door (R11), on whatever record carries them.
IDENTITY = (
    "organization.id",
    "user.id",
    "user.email",
    "user.account_uuid",
    "user.account_id",
    "user.groups",
    "identity.source",
)

#: The attributes `OTEL_LOG_TOOL_DETAILS` is the only source of; with it off the
#: harness emits none of them and collapses the names it still emits.
GATED = ("tool_parameters", "tool_input", "vcs.ref.head.revision", "vcs.ref.head.name")


def _text(name: str) -> str:
    """One fixture file, byte for byte as a pass would read it."""
    return (TELEMETRY / name).read_text(encoding="utf-8")


def _consumable_lines(name: str) -> list[str]:
    """The lines a pass consumes: a trailing line with no newline is mid-write."""
    text = _text(name)
    lines = text.splitlines()
    return lines[:-1] if lines and not text.endswith("\n") else lines


def _log_records(line: str) -> list[dict[str, Any]]:
    """Every ``logRecords`` entry of one OTLP line, at whatever batch size it arrived."""
    payload = json.loads(line)
    return [
        record
        for resource in payload["resourceLogs"]
        for scope in resource["scopeLogs"]
        for record in scope["logRecords"]
    ]


def _records(name: str) -> list[dict[str, Any]]:
    """Every record of one fixture file, in arrival order."""
    return [record for line in _consumable_lines(name) for record in _log_records(line)]


def _attributes(record: dict[str, Any]) -> dict[str, Any]:
    """The record's OTLP key/value pairs, flattened the way the reader flattens them."""
    return {pair["key"]: next(iter(pair["value"].values())) for pair in record["attributes"]}


def _event_names(name: str) -> list[str]:
    """The ``event.name`` of every record in one fixture file — what selects the type."""
    return [_attributes(record)["event.name"] for record in _records(name)]


@pytest.mark.parametrize("name", sorted(CLAIMS))
def test_every_fixture_parses_and_carries_the_records_it_claims(name: str) -> None:
    """`event.name` selects the record type, so a fixture is only usable if it carries it."""
    assert tuple(_event_names(name)) == CLAIMS[name]


def test_the_batched_file_delivers_several_records_on_one_line() -> None:
    """Batch size is the developer's collector configuration, so a line may carry many."""
    lines = _consumable_lines("batched.jsonl")
    assert len(lines) == 1
    assert len(_log_records(lines[0])) == len(CLAIMS["batched.jsonl"])


def test_the_truncated_file_ends_in_a_line_no_pass_may_consume() -> None:
    """A trailing line with no newline is mid-write, so the offset stops before it."""
    text = _text("truncated.jsonl")
    assert not text.endswith("\n")
    partial = text.splitlines()[-1]
    assert partial not in _consumable_lines("truncated.jsonl")
    with pytest.raises(json.JSONDecodeError):
        json.loads(partial)


def test_the_forbidden_content_line_carries_every_field_the_reader_may_never_bind() -> None:
    """Each field sits on the record or span the contract names for it (`prompt` on
    `user_prompt`, `response` on `assistant_response`, `body`/`body_ref` on
    `api_request_body`, `user_prompt` on the interaction span) — their union is what the
    strip allow-list is measured against.
    """
    log_content: set[str] = set()
    for record in _records("forbidden_content.jsonl"):
        log_content |= set(_attributes(record))
    payload = json.loads(_consumable_lines("forbidden_content.jsonl")[0])
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span_content = {pair["key"] for pair in span["attributes"]}
    assert set(FORBIDDEN) <= log_content | span_content


def test_the_identity_line_carries_every_attribute_dropped_at_the_door() -> None:
    """Identity arrives on the record and, as a team name, on the resource around it."""
    assert set(IDENTITY) <= set(_attributes(_records("identity_attributes.jsonl")[0]))
    payload = json.loads(_consumable_lines("identity_attributes.jsonl")[0])
    assert "team.name" in _attributes(payload["resourceLogs"][0]["resource"])


def test_the_events_only_file_is_one_whole_turn_and_carries_no_span_payload() -> None:
    """SC-006's input: spans absent entirely, so nothing span-derived can be borrowed."""
    lines = _consumable_lines("events_only.jsonl")
    assert all(set(json.loads(one)) == {"resourceLogs"} for one in lines)
    turn = {_attributes(record)["prompt.id"] for record in _records("events_only.jsonl")}
    assert len(turn) == 1


def test_the_gate_off_file_carries_nothing_the_tool_details_gate_gates() -> None:
    """SC-003's input: the gate off degrades the records, and never silently."""
    records = [_attributes(record) for record in _records("gate_off.jsonl")]
    assert not any(set(GATED) & set(one) for one in records)
    collapsed = [
        one[key] for one in records for key in ("command_name", "agent_type") if key in one
    ]
    assert collapsed and set(collapsed) <= {"custom", "mcp"}
