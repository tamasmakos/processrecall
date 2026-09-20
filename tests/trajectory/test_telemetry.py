"""The collector file: its persisted read offset, and which project a record belongs to (R4)."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.record import record_event
from processrecall.graph.store import (
    SequenceKey,
    SQLiteEpisodicStore,
    agent_from_record,
    inference_from_record,
)
from processrecall.guidance.locate import locate
from processrecall.trajectory.offset import OFFSET_NAME, OffsetFile
from processrecall.trajectory.telemetry import (
    SESSION_ATTRIBUTE,
    CollectorSource,
    ProjectAttribution,
    SessionGaps,
    recognised_records,
    records_in_line,
    report_span_only_gaps,
    report_tool_detail_gap,
    source_state,
    step_from_verdict,
    step_result,
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


def test_absent_and_stale_sources_are_counted_not_raised(
    tmp_path: Path, counters: FakeCounters
) -> None:
    opened_at = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)

    # The developer named a path before their collector ever wrote it: a cold
    # start, so the hook's own capture stands as the record (FR-010).
    absent = CollectorSource(tmp_path / "never-written.jsonl", opened_at)
    # A collector that stopped before this session opened: the file is there and
    # parses, but nothing in it can belong to the session being closed.
    stopped = tmp_path / "stopped.jsonl"
    stopped.write_bytes(b'{"n": 1}\n')
    os.utime(stopped, (0, (opened_at - timedelta(days=1)).timestamp()))
    # One the collector appended to while the session ran: nothing to report.
    appended = tmp_path / "telemetry.jsonl"
    appended.write_bytes(b'{"n": 2}\n')
    os.utime(appended, (0, (opened_at + timedelta(seconds=1)).timestamp()))

    assert source_state(absent, counters) == "absent"
    assert source_state(CollectorSource(stopped, opened_at), counters) == "stale"
    assert source_state(CollectorSource(appended, opened_at), counters) is None
    # A pass that names no session judges presence only: `rebuild --project`
    # closes no session, so it has nothing to call a present file stale against.
    assert source_state(CollectorSource(stopped), counters) is None
    # A directory where the file should be reads as the cold start too: the
    # pass says what it found, and an OSError would reach the fold behind it.
    assert source_state(CollectorSource(tmp_path, opened_at), counters) == "absent"
    assert counters.counted["telemetry_absent"] == 2
    assert counters.counted["telemetry_stale"] == 1


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


def test_batched_line_yields_every_log_record(counters: FakeCounters) -> None:
    line = (TELEMETRY_FIXTURES / "batched.jsonl").read_text(encoding="utf-8").strip()

    records = list(records_in_line(line, counters))

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


def test_only_allow_listed_attributes_are_bound(counters: FakeCounters) -> None:
    lines = [
        (TELEMETRY_FIXTURES / name).read_text(encoding="utf-8").strip()
        for name in ("identity_attributes.jsonl", "forbidden_content.jsonl")
    ]

    records = [record for line in lines for record in records_in_line(line, counters)]

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


def test_content_gates_on_strips_and_counts(counters: FakeCounters) -> None:
    """The content gates on: the records still arrive, stripped and counted (FR-012, SC-014)."""
    lines = [
        (TELEMETRY_FIXTURES / name).read_text(encoding="utf-8").strip()
        for name in ("forbidden_content.jsonl", "identity_attributes.jsonl")
    ]

    records = [record for line in lines for record in recognised_records(line, counters)]

    bound = {key for record in records for key in record}
    assert bound.isdisjoint(NEVER_BOUND)
    # `prompt`, `response`, `body` and `body_ref`: the four content attributes the
    # gates-on line carries, counted whether or not the record they rode in on is
    # one the memory consumes.
    assert counters.counted["telemetry_content_stripped"] == 4
    # `organization.id`, `user.id`, `user.email`, `user.account_uuid`,
    # `user.account_id`, `user.groups`, `identity.source`.
    assert counters.counted["telemetry_identity_stripped"] == 7
    # Stripping is not refusing (FR-012): the turn that carried the prompt text
    # still arrives with its length, and the call that carried the account UUID
    # still arrives with its token counts, so the session is recorded (SC-014).
    by_type = {str(record["event.name"]): record for record in records}
    assert by_type["claude_code.user_prompt"]["prompt_length"] == 31
    assert by_type["claude_code.api_request"]["input_tokens"] == 1200


def _log_record(event_name: str) -> dict[str, object]:
    """A minimal OTLP log record carrying only the attribute the reader keys on."""
    return {"attributes": [{"key": "event.name", "value": {"stringValue": event_name}}]}


def test_unknown_record_is_refused_and_counted(counters: FakeCounters) -> None:
    # Built here rather than read off a fixture: a line whose only job is to
    # carry one recognised record type among two unrecognised ones, so the
    # expected count of 2 is a property of this test, not of a fixture some
    # other test's edit could retarget.
    line = json.dumps(
        {
            "resourceLogs": [
                {
                    "scopeLogs": [
                        {
                            "logRecords": [
                                _log_record("claude_code.user_prompt"),
                                _log_record("claude_code.assistant_response"),
                                _log_record("claude_code.api_request_body"),
                            ]
                        }
                    ]
                }
            ]
        }
    )

    records = list(recognised_records(line, counters))

    # Three log records, and only `user_prompt` is one of the seven the memory
    # consumes. `assistant_response` and `api_request_body` have no episodic
    # shape to become, so they are skipped rather than read for whatever
    # attributes they happen to share with a record type that has one (FR-009).
    assert [record["event.name"] for record in records] == ["claude_code.user_prompt"]
    assert counters.counted["telemetry_record_unknown"] == 2


def test_unparsable_line_loses_its_records_not_the_pass(counters: FakeCounters) -> None:
    # A collector killed mid-write leaves a complete line that is half a JSON
    # document; a hand-edited or wrongly-configured exporter leaves a whole one
    # that is not an OTLP logs payload, or one whose shape is malformed one
    # level down. None of the three may reach the draining job as an exception
    # (FR-010).
    half_written = '{"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attribu'
    wrong_shape = '["not an OTLP logs payload"]'
    wrong_nested_shape = '{"resourceLogs": 5}'

    assert list(recognised_records(half_written, counters)) == []
    assert list(recognised_records(wrong_shape, counters)) == []
    assert list(recognised_records(wrong_nested_shape, counters)) == []
    assert counters.counted["telemetry_record_partial"] == 3


def _records_named(fixture_name: str, event_name: str) -> list[dict[str, str | int | bool]]:
    """Every record of type *event_name* the named fixture carries, flattened."""
    lines = (TELEMETRY_FIXTURES / fixture_name).read_text(encoding="utf-8").splitlines()
    counters = FakeCounters()
    return [
        dict(record)
        for line in lines
        if line.strip()
        for record in records_in_line(line, counters)
        if record["event.name"] == event_name
    ]


def _verdicts(fixture_name: str) -> list[dict[str, str | int | bool]]:
    """Every `tool_decision` record the named fixture carries, flattened."""
    return _records_named(fixture_name, "claude_code.tool_decision")


def _results(fixture_name: str) -> list[dict[str, str | int | bool]]:
    """Every `tool_result` record the named fixture carries, flattened."""
    return _records_named(fixture_name, "claude_code.tool_result")


def test_rejected_decision_becomes_refused_step_with_no_touches() -> None:
    (verdict,) = _verdicts("all_records.jsonl")
    # With `OTEL_LOG_TOOL_DETAILS` on, a verdict carries the arguments of the
    # call it refused, so the step has to drop them rather than never see them.
    refused = verdict | {"tool_parameters": '{"command": "rm -rf build"}'}

    step = step_from_verdict(refused)

    assert step.decision == "rejected"
    assert step.decision_source == "user_reject"
    assert step.tool_name == "Bash"
    assert step.tool_call_id == "toolu_synthetic_0002"
    # The call never ran: no touched edges (FR-008), and no duration or result
    # size either. The absence is what the rejection looks like, not a gap a
    # later source is expected to fill (R14).
    assert step.tool_call_arguments == {}
    assert step.duration_ms is None
    assert step.result_size_bytes is None


def test_failed_tool_result_is_failure_and_still_accepted() -> None:
    # A permitted call that exited non-zero: the harness reports the failure on
    # the `tool_result` and the permission on the verdict before it, so the two
    # axes of FR-022 must stay independent — a failure is a capability signal
    # and must not read as the refusal a rejected call would leave.
    (failed,) = _results("gate_off.jsonl")
    (verdict,) = _verdicts("gate_off.jsonl")

    # The failed record carries its own `decision_type: accept`, yet
    # `step_result` reads only the result axis and leaves it alone.
    assert failed["decision_type"] == "accept"
    assert step_result(failed) == "failure"
    assert step_from_verdict(verdict).decision == "accepted"
    # The other two readings of the axis: a call the harness said succeeded, and
    # a record carrying neither the flag nor an error type, which reports
    # nothing about the axis rather than defaulting to `ok` (R14).
    (succeeded,) = _results("batched.jsonl")
    assert step_result(succeeded) == "ok"
    assert step_result({"event.name": "claude_code.tool_result"}) is None


def test_api_records_become_inferences_with_outcome(tmp_path: Path) -> None:
    (requested,) = _records_named("all_records.jsonl", "claude_code.api_request")
    (failed,) = _records_named("all_records.jsonl", "claude_code.api_error")
    (refused,) = _records_named("all_records.jsonl", "claude_code.api_refusal")
    key = SequenceKey(
        conversation_id="session-synthetic-1", session_epoch=0, prompt_id="prompt-synthetic-1"
    )

    connection = open_index(tmp_path / "episodes.db")
    try:
        store = SQLiteEpisodicStore(connection)
        for record in (requested, failed, refused):
            store.record_inference(inference_from_record(record, key))
        recorded = store.inferences_for(key)
        counted = store.counters()["inferences_recorded"]
    finally:
        connection.close()

    # Which of the three events fired is the whole source of `outcome`, so the
    # events-only stream carries the field complete (R9): `stop_reason` and
    # `error_class` refine an outcome the model already has rather than produce it.
    assert [inference.outcome for inference in recorded] == ["ok", "error", "refusal"]
    inference, failure, refusal = recorded
    assert inference.inference_id == "req_synthetic_0001"
    assert (inference.model, inference.input_tokens, inference.output_tokens) == (
        "claude-opus-4-1-20250805",
        5231,
        412,
    )
    assert (inference.cost_micros, inference.duration_ms) == (4820, 3120)
    # The error is the terminal signal of a call that returned no body: it names
    # the status and the attempt, and carries no token counts to read (R14).
    assert (failure.status_code, failure.attempt) == (529, 2)
    assert failure.input_tokens is None
    # A refusal arrives on a stream that succeeded, so it has no status code —
    # and it is the one `stop_reason` an events-only stream ever observes (R9).
    assert (refusal.status_code, refusal.duration_ms) == (None, None)
    assert refusal.stop_reason == "refusal"
    assert counted == 3
    # Neither request id: below the `client_request_id` floor, the identity is
    # derived from the record rather than stored empty
    # (`contracts/telemetry-records.md`).
    anonymous = {name: value for name, value in requested.items() if "request_id" not in name}
    assert inference_from_record(anonymous, key).inference_id.startswith("syn-")


def test_subagent_completed_and_inference_become_agents_with_kind() -> None:
    (completed,) = _records_named("all_records.jsonl", "claude_code.subagent_completed")
    key = SequenceKey(
        conversation_id="session-synthetic-1", session_epoch=0, prompt_id="prompt-synthetic-1"
    )

    # `subagent_completed` is the only events-mode record naming an agent type
    # (`contracts/telemetry-records.md`), so a row built from it is a sub-agent's
    # by definition, carrying every attribute it names (R7).
    from_completion = agent_from_record(completed, key)
    assert from_completion.agent_id == key.agent_id
    assert from_completion.kind == "subagent"
    assert (from_completion.agent_type, from_completion.agent_source) == ("reviewer", "project")
    assert (from_completion.is_built_in, from_completion.is_async) == (False, False)

    # An inference record carries no agent fields at all, only the `query_source`
    # that separates the turn's own main thread — `repl_main_thread` or
    # `compact` — from sub-agent work without naming an instance (R7).
    main_thread = {
        "event.name": "claude_code.api_request",
        "event.timestamp": "2026-01-05T10:00:00Z",
        "query_source": "repl_main_thread",
    }
    named_subagent = {**main_thread, "query_source": "code-reviewer"}
    assert agent_from_record(main_thread, key).kind == "main"
    assert agent_from_record(named_subagent, key).kind == "subagent"


def test_gap_counter_bumps_once_per_session(counters: FakeCounters) -> None:
    gaps = SessionGaps(counters)

    # The tool-details gate is off for every record of a session, so ten
    # thousand records report one condition: the count has to say "one session
    # ran with the gate off", not how busy that session was
    # (`contracts/counters.md`).
    for _ in range(3):
        gaps.report("session-synthetic-1", "gap_tool_details")
    gaps.report("session-synthetic-1", "gap_ttft")
    # A second session with the same gate off is a second condition to fix.
    gaps.report("session-synthetic-2", "gap_tool_details")

    assert counters.counted["gap_tool_details"] == 2
    assert counters.counted["gap_ttft"] == 1
    # The set of gaps is closed, and the name reaches `bump` here as a variable
    # rather than as the literal `tests/graph/test_counters.py` scans for, so a
    # name from outside the set is refused where it is passed in.
    with pytest.raises(ValueError):
        gaps.report("session-synthetic-1", "telemetry_records_read")


def test_gate_off_still_records_and_serves(tmp_path: Path, counters: FakeCounters) -> None:
    (verdict,) = _verdicts("gate_off.jsonl")
    gaps = SessionGaps(counters)

    # `OTEL_LOG_TOOL_DETAILS` is what puts the arguments on a tool record, so a
    # record naming the tool and nothing it was given is the gate being off.
    assert "tool_parameters" not in verdict
    report_tool_detail_gap(verdict, gaps)
    connection = open_index(tmp_path / "episodes.db")
    try:
        (step,) = record_event(step_from_verdict(verdict), connection)
        store = SQLiteEpisodicStore(connection)
        recorded = store.steps(step.sequence_key)
        touches = store.touches_for(step.step_id)
    finally:
        connection.close()

    # No command line to abstract, so the tool name stands alone as the whole
    # template — the step is still recorded, and still matches a second one.
    assert step.template == "Bash"
    # Nothing named a path, so nothing was touched: the edges the semantic
    # layer would resolve are simply not there (SC-003).
    assert step.files == ()
    assert touches == ()
    assert counters.counted["gap_tool_details"] == 1
    # Guidance still has somewhere to stand: the node survives the gate, the
    # file it would have narrowed the neighbourhood by does not.
    position = locate(recorded, "class/program/ext")
    assert position.key == step.node_key
    assert position.file is None


def test_events_only_run_leaves_only_three_model_gaps(
    tmp_path: Path, counters: FakeCounters
) -> None:
    """Spans absent entirely: the model is produced whole, three fields are not (SC-006).

    The run is above every version floor and the tool-details gate is on, so the
    span side is the only thing missing from it — which leaves exactly the three
    fields R9 found no event carries. `gap_stop_reason` and `gap_error_class`
    are not among them: they refine an `outcome` the events already produce, so
    their absence degrades detail rather than the model (R9).
    """
    lines = (TELEMETRY_FIXTURES / "events_only.jsonl").read_text(encoding="utf-8").splitlines()
    records = [
        record for line in lines if line.strip() for record in recognised_records(line, counters)
    ]
    gaps = SessionGaps(counters)
    for record in records:
        report_span_only_gaps(record, gaps)

    # One turn carrying one record of each consumed type, so each part of the
    # model below is built from the record the contract names as its source.
    by_type = {str(record["event.name"]): record for record in records}
    assert len(by_type) == len(records)
    connection = open_index(tmp_path / "episodes.db")
    try:
        (step,) = record_event(step_from_verdict(by_type["claude_code.tool_decision"]), connection)
        store = SQLiteEpisodicStore(connection)
        key = step.sequence_key
        store.record_inference(inference_from_record(by_type["claude_code.api_request"], key))
        store.record_agent(agent_from_record(by_type["claude_code.subagent_completed"], key))
        sequence = store.sequence(key)
        (recorded,) = store.steps(key)
        (inference,) = store.inferences_for(key)
        agent = store.agent(key.agent_id)
    finally:
        connection.close()

    # Every part of the model is produced: the turn, the step that ran inside it,
    # the model call that asked for the step, and the actor that did the work.
    assert sequence is not None
    assert recorded.template == "Edit"
    assert (inference.inference_id, inference.outcome, inference.cost_micros) == (
        "req_synthetic_0051",
        "ok",
        3940,
    )
    assert agent is not None
    assert (agent.kind, agent.agent_type) == ("subagent", "reviewer")
    # No span, so no time to first token, no split of the step's duration into
    # waiting for the operator and running, and no parent to hang the sub-agent
    # off — the `spawned` edge the nesting gap is about.
    assert agent.parent_agent_id is None
    # `gap_stop_reason` and `gap_error_class` are excluded before the compare:
    # quickstart.md §6 allows either to also be raised, since they report absent
    # enrichment rather than an incomplete model, and an events-only run that
    # later grows one must not fail this on that account.
    model_gaps = {name for name in counters.counted if name.startswith("gap_")} - {
        "gap_stop_reason",
        "gap_error_class",
    }
    assert model_gaps == {"gap_ttft", "gap_permission_wait", "gap_agent_nesting"}
