"""The collector file: its persisted read offset, and which project a record belongs to (R4)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from processrecall.trajectory.offset import OFFSET_NAME, OffsetFile
from processrecall.trajectory.telemetry import (
    SESSION_ATTRIBUTE,
    ProjectAttribution,
    records_in_line,
)

#: The synthetic OTLP corpus T001 built to `contracts/telemetry-records.md`.
TELEMETRY_FIXTURES = Path(__file__).parents[1] / "fixtures" / "telemetry"


class FakeCounters:
    """A counter sink that keeps what it was bumped with, so a test reads it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


@pytest.fixture
def counters() -> FakeCounters:
    return FakeCounters()


def test_rotated_file_resets_offset_and_does_not_duplicate(
    tmp_path: Path, counters: FakeCounters
) -> None:
    collector = tmp_path / "telemetry.jsonl"
    collector.write_bytes(b'{"n": 1}\n{"n": 2}\n')
    offsets = OffsetFile(tmp_path / OFFSET_NAME, counters)

    first = list(offsets.appended_lines(collector))
    assert first == ['{"n": 1}', '{"n": 2}']
    # A pass over an unchanged file has nothing to give: the offset is honoured.
    assert list(offsets.appended_lines(collector)) == []

    # Rotated: same name, a shorter file with a different head, its last line
    # still being written.
    collector.write_bytes(b'{"n": 3}\n{"n": 4')
    rotated = list(offsets.appended_lines(collector))

    assert rotated == ['{"n": 3}']
    assert counters.counted["telemetry_offset_reset"] == 1
    recorded = offsets.read()
    assert recorded is not None
    assert recorded.offset == len('{"n": 3}\n')
    # A further pass over the unchanged (rotated) file has nothing new to give.
    assert list(offsets.appended_lines(collector)) == []

    # The mid-write line arrives whole on the next pass.
    collector.write_bytes(b'{"n": 3}\n{"n": 4}\n')
    completed = list(offsets.appended_lines(collector))

    assert completed == ['{"n": 4}']


class FakeBindings:
    """The session→project bindings a SessionStart hook would have recorded."""

    def __init__(self, bound: dict[str, str]) -> None:
        self._bound = bound

    def project_for_session(self, session_id: str) -> str | None:
        return self._bound.get(session_id)


def test_unbound_session_is_dropped_and_counted(counters: FakeCounters) -> None:
    bindings = FakeBindings({"session-opened-by-a-hook": "project-a"})
    attribution = ProjectAttribution(bindings, counters)

    records = [
        {SESSION_ATTRIBUTE: "session-opened-by-a-hook", "event.name": "claude_code.tool_result"},
        # A session the collector saw but no hook ever opened: nothing says where
        # the work happened, so it is dropped rather than stored unattributed.
        {SESSION_ATTRIBUTE: "session-no-hook-opened", "event.name": "claude_code.tool_result"},
        # OTEL_METRICS_INCLUDE_SESSION_ID off: no attribution key at all.
        {"event.name": "claude_code.tool_result"},
    ]
    attributed = list(attribution.attributed(records))

    assert [record.project_key for record in attributed] == ["project-a"]
    assert [record.attributes[SESSION_ATTRIBUTE] for record in attributed] == [
        "session-opened-by-a-hook"
    ]
    assert counters.counted["telemetry_session_unbound"] == 2


def test_batched_line_yields_every_log_record() -> None:
    line = (TELEMETRY_FIXTURES / "batched.jsonl").read_text(encoding="utf-8").strip()

    records = list(records_in_line(line))

    # One exported line, one resourceLogs, two scopeLogs, three logRecords:
    # reading the first entry of any of the three levels would silently lose the
    # tool_result that the tool_decision above it decided (FR-002).
    assert [record["event.name"] for record in records] == [
        "claude_code.api_request",
        "claude_code.tool_decision",
        "claude_code.tool_result",
    ]
    # Flattened out of OTLP key/value pairs, each value read back as the type the
    # harness wrote rather than the JSON the protobuf mapping encodes it as:
    # `intValue` arrives as a string and would not compare or sort as a number.
    assert records[0]["event.sequence"] == 11
    assert records[0]["cost_usd_micros"] == 2960
    assert records[2]["tool_use_id"] == "toolu_synthetic_0011"
    assert records[2]["success"] is True
    # Resource-level attributes are not the record's: the custom
    # OTEL_RESOURCE_ATTRIBUTES a deployment sets carry team and department names,
    # and folding them in here would attach them to every record (R11).
    assert not any("service.name" in record for record in records)


#: The attributes the contract's "Never read, under any gate" table names, plus the
#: identity attributes R11 drops at the door. None of them is on the allow-list, so
#: none of them has a way in — which is the point of binding rather than filtering.
NEVER_BOUND = (
    "prompt",
    "response",
    "body",
    "body_ref",
    "organization.id",
    "user.id",
    "user.email",
    "user.account_uuid",
    "user.account_id",
    "user.groups",
    "identity.source",
    "user_prompt",
)


def test_only_allow_listed_attributes_are_bound() -> None:
    lines = [
        (TELEMETRY_FIXTURES / name).read_text(encoding="utf-8").strip()
        for name in ("identity_attributes.jsonl", "forbidden_content.jsonl")
    ]

    records = [record for line in lines for record in records_in_line(line)]

    bound = {key for record in records for key in record}
    assert bound.isdisjoint(NEVER_BOUND)
    # Content is dropped at the door rather than made a reason to refuse the
    # record carrying it (FR-012); this reader does not yet refuse a record by
    # its `event.name` (T014), so the count below is a pre-filter count of the
    # four log records the two fixture lines carry, not a claim that
    # `assistant_response` and `api_request_body` are consumed.
    assert len(records) == 4
    assert records[0]["request_id"] == "req_synthetic_0041"
    assert records[0]["input_tokens"] == 1200
