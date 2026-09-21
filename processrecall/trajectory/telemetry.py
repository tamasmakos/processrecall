"""Reading the collector's OTLP/JSON lines, and attributing them to a project (FR-001, R4).

`recognised_records` is the door every telemetry record comes through. A
collector batches, so one exported line is `resourceLogs[] → scopeLogs[] →
logRecords[]` and each level can hold several: reading the first entry of any
of them loses whole records with no sign that it did (FR-002).

No telemetry record carries a working directory, so a record cannot say which
project its work happened in. The one thing that knows is the session→project
binding the ``SessionStart`` hook already recorded, and `session.id` is the key
into it: telemetry ingest is strictly downstream of the hook rather than a
fallback for it.

A record whose session no hook ever opened is therefore dropped here, at the
door, and counted. Storing it unattributed and filtering later would leave an
excluded project's records sitting in the index in between, which is the one
thing exclusion exists to prevent.

`in_record_order` (FR-006) puts records back in the order the harness wrote
them in, by `event.timestamp` with `event.sequence` breaking a tie, and drops
what it cannot place rather than raising. Nothing in this module calls it
yet — T023 is what drains the collector and will, once it exists.

Off the hot path, but on the store's layer, so the standard library only.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from processrecall.config import Counters
from processrecall.trajectory.event import SourceKind, TrajectoryEvent
from processrecall.trajectory.records import CONSUMED_EVENT_NAMES

#: The flattened attribute naming which of the consumed record types a record
#: is (`contracts/telemetry-records.md`): the reader selects on it rather than
#: inferring a format from the attributes a record happens to carry.
RECORD_TYPE_ATTRIBUTE = "event.name"

#: The flattened attribute carrying the session a record was written under. Gated
#: by ``OTEL_METRICS_INCLUDE_SESSION_ID``: with the gate off it is absent, and
#: every record is unattributable rather than wrongly attributed.
SESSION_ATTRIBUTE = "session.id"

#: The flattened attribute carrying the harness release a record was written by.
#: Gated by ``OTEL_METRICS_INCLUDE_VERSION``: with the gate off no version floor
#: can be checked, which is why `processrecall doctor` reports on whether it
#: arrived at all.
VERSION_ATTRIBUTE = "app.version"

#: The flattened attribute carrying the user turn a record belongs to, the
#: correlation key threaded through every record type.
PROMPT_ATTRIBUTE = "prompt.id"

#: The two attributes a record's place in the read order comes from
#: (`contracts/telemetry-records.md`). `event.sequence` breaks a tie and decides
#: nothing else: it restarts per process and can go backwards inside one session
#: after a resume, so ordering by it would reorder the session it came from.
TIMESTAMP_ATTRIBUTE = "event.timestamp"
SEQUENCE_ATTRIBUTE = "event.sequence"

#: The shape every telemetry record is read as: a flattened OTel attribute set,
#: string keys onto the three JSON scalar types the collector ever emits one
#: as. Named once so the reader, the attribution and the ordering share a
#: single spelling of it rather than restating the union at each call site.
TelemetryRecord = Mapping[str, str | int | bool]

#: The attributes bound from a record whatever record it is: its identity, the
#: attribution key, the version floor, the turn's correlation key and the two
#: ordering keys (`contracts/telemetry-records.md`, "Standard attributes").
_STANDARD_ATTRIBUTES = frozenset(
    {
        RECORD_TYPE_ATTRIBUTE,
        TIMESTAMP_ATTRIBUTE,
        SEQUENCE_ATTRIBUTE,
        SESSION_ATTRIBUTE,
        VERSION_ATTRIBUTE,
        PROMPT_ATTRIBUTE,
    }
)

#: What a `user_prompt` contributes to the sequence it opens. `prompt` is absent
#: on purpose and is the whole reason this is an allow-list.
_SEQUENCE_ATTRIBUTES = frozenset({"prompt_length", "command_name", "command_source"})

#: What `tool_result` and `tool_decision` contribute to a step. The two records
#: spell the permission outcome differently — `decision_type`/`decision_source`
#: on the result, `decision`/`source` on the verdict — so both spellings are
#: bound here and reconciled where the step is written, not at the door.
_STEP_ATTRIBUTES = frozenset(
    {
        "tool_use_id",
        "tool_name",
        "success",
        "duration_ms",
        "error_type",
        "decision_type",
        "decision_source",
        "decision",
        "source",
        "tool_input_size_bytes",
        "tool_result_size_bytes",
        "tool_source",
        "mcp_server_scope",
        "tool_parameters",
        "tool_input",
        "vcs.ref.head.revision",
        "vcs.ref.head.name",
    }
)

#: What `api_request`, `api_error` and `api_refusal` contribute to an inference.
#: `cost_usd` is not among them: `cost_usd_micros` is the same quantity as an
#: integer, and a float would make cost per procedure non-reproducible.
_INFERENCE_ATTRIBUTES = frozenset(
    {
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cost_usd_micros",
        "speed",
        "effort",
        "query_source",
        "request_id",
        "client_request_id",
        "status_code",
        "attempt",
        "refusal",
    }
)

#: What `subagent_completed` contributes to an agent.
_AGENT_ATTRIBUTES = frozenset(
    {
        "agent_type",
        "agent.source",
        "is_built_in",
        "is_async",
        "total_tool_uses",
        "final_model",
        "model_swapped",
    }
)

#: Every attribute the reader will bind, and no other (R11, FR-011). Binding from
#: a list of names rather than filtering a list of forbidden ones is what makes a
#: prompt, a response body or an account UUID unreadable by default: a harness
#: release that adds one, under whatever name and whatever gate, is not on this
#: list and so never reaches anything that writes.
BOUND_ATTRIBUTES = (
    _STANDARD_ATTRIBUTES
    | _SEQUENCE_ATTRIBUTES
    | _STEP_ATTRIBUTES
    | _INFERENCE_ATTRIBUTES
    | _AGENT_ATTRIBUTES
)

#: The content attributes `contracts/telemetry-records.md`'s "Never read, under
#: any gate" table names: the prompt and the response themselves, the raw request
#: and response bodies, the host path a body was spilled to, and the span
#: attribute carrying the turn's text. None is on :data:`BOUND_ATTRIBUTES`, so
#: this set decides nothing about what is read — it is what lets a strip be
#: reported rather than pass silently (FR-012).
_CONTENT_ATTRIBUTES = frozenset({"prompt", "response", "body", "body_ref", "user_prompt"})

#: The attributes naming the human or the organisation behind a session (R11).
#: The memory keeps `session.id` and nothing else identifying, so these are
#: stripped for the same reason the content is, and counted apart from it
#: because the two conditions are fixed by different settings.
_IDENTITY_ATTRIBUTES = frozenset(
    {
        "organization.id",
        "user.id",
        "user.email",
        "user.account_uuid",
        "user.account_id",
        "user.groups",
        "identity.source",
    }
)

#: The OTel `AnyValue` fields a consumed attribute is ever written in, each with
#: the reader recovering the type the harness meant. `intValue` is a JSON string
#: in the protobuf JSON mapping, so a token count left as written would neither
#: compare nor sort as the number it is. A value in any other field — a list, a
#: nested map, bytes — is not one of these and is left unbound.
_VALUE_READERS: Mapping[str, Callable[[Any], str | int | bool]] = {
    "stringValue": str,
    "intValue": int,
    "boolValue": bool,
}

#: The decision axis as `tool_decision` spells it, mapped onto the spelling the
#: step stores it in (:class:`~processrecall.graph.schema.StepDecision`). Written
#: out here rather than imported: `processrecall/graph/store.py` already imports
#: `trajectory.event` for the event it stores, so the dependency runs graph ->
#: trajectory, and importing `graph.schema` back into this module would run the
#: same edge the other way.
_DECISION_VALUES: Mapping[str, str] = {"accept": "accepted", "reject": "rejected"}

#: The `source` spellings a verdict ever carries
#: (:class:`~processrecall.graph.schema.DecisionSource`'s closed set), spelled
#: out here for the same reason `_DECISION_VALUES` is. A spelling outside it is
#: dropped to ``None`` here rather than passed through raw only to explode much
#: later at `DecisionSource(row[21])` in `graph/store.py`.
_DECISION_SOURCE_VALUES = frozenset(
    {"config", "hook", "user_permanent", "user_temporary", "user_abort", "user_reject"}
)

#: The result axis's two spellings
#: (:class:`~processrecall.graph.schema.StepResult`), written out here for the
#: same reason `_DECISION_VALUES` is.
_RESULT_OK = "ok"
_RESULT_FAILURE = "failure"

#: Why a consumed field is not there, one name per cause because the fixes
#: differ: turn spans on, turn the tool-details gate on, or upgrade the harness
#: (`contracts/counters.md`, R9). The set is closed. Spelled out here rather than
#: read off `processrecall/graph/store.py`'s ``COUNTERS``, which this layer sits
#: below and so may not import.
GAP_COUNTERS = frozenset(
    {
        "gap_ttft",
        "gap_permission_wait",
        "gap_agent_nesting",
        "gap_stop_reason",
        "gap_error_class",
        "gap_tool_details",
        "gap_version_floor",
    }
)


#: What a pass found where the collector file was configured, when it found
#: nothing to drain (`contracts/collector-transport.md`).
_SOURCE_ABSENT = "absent"
_SOURCE_STALE = "stale"


@dataclass(frozen=True, slots=True)
class CollectorSource:
    """The collector file a pass is about to drain, and the session it closes.

    Attributes:
        path: The configured file the developer's own collector writes. Nothing
            here creates it, so "not there" is a state of the source rather than
            a failure of the pass.
        session_opened_at: When the session being closed opened. A file last
            written before that instant holds none of that session's records.
            ``None`` for a pass that names no session, such as `processrecall
            rebuild --project`, which can only tell a present source from an
            absent one.
    """

    path: Path
    session_opened_at: datetime | None = None


def source_state(source: CollectorSource, counters: Counters) -> str | None:
    """Why *source* has nothing for this pass to drain, or ``None`` when it has.

    ``"absent"`` for a configured path that is not a file to read — a cold
    start, the developer named it before their collector wrote it — and
    ``"stale"`` for one last written before the session being closed opened, so
    that none of that session's work reached it. Each bumps its counter
    (``telemetry_absent``, ``telemetry_stale``) and is returned for the pass to
    report; neither raises, because the pass that drains also folds the
    snapshots and none of that work may be lost to a telemetry source being
    late or stopped (FR-010).

    Staleness is read off the file's modification time, which is when the
    collector last appended to it, so a stale source is passed over without
    being opened.
    """
    written_at = _last_written(source.path)
    if written_at is None:
        counters.bump("telemetry_absent")
        return _SOURCE_ABSENT
    if source.session_opened_at is not None and written_at < source.session_opened_at:
        counters.bump("telemetry_stale")
        return _SOURCE_STALE
    return None


def _last_written(path: Path) -> datetime | None:
    """When *path* was last written, or ``None`` when it is no file to read.

    A path that cannot be stat'ed reads the same as one that is not there: a
    cold start is all a pass can say about either, and the error belongs to the
    collector that owns the file rather than to the job draining it.
    """
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC) if path.is_file() else None
    except OSError:
        return None


def records_in_line(line: str, counters: Counters) -> Iterator[TelemetryRecord]:
    """Every log record the batched OTLP/JSON *line* carries, flattened (FR-001, FR-002).

    Each yielded record is the log record's attribute list as a mapping. The
    resource and scope around it are not folded in: they carry the deployment's
    own `OTEL_RESOURCE_ATTRIBUTES`, which name teams and departments (R11).

    A content or identity attribute among a record's own attributes is left
    unbound like any other name off the allow-list, and bumps its counter as it
    goes: *counters* is what makes the prompt-content gates being on a condition
    an operator can read rather than a silent drop, and the record carrying one
    is yielded rather than refused for it (FR-012).

    Raises:
        TypeError: *payload* is not shaped like an OTLP logs document — not a
            mapping at the top, or `resourceLogs`/`scopeLogs`/`logRecords` not
            a list where one belongs. :func:`recognised_records` is what turns
            this into a counted refusal rather than a raise into the caller.
    """
    payload = json.loads(line)
    if not isinstance(payload, Mapping):
        raise TypeError(f"not an OTLP logs payload: {payload!r}")
    for resource_log in payload.get("resourceLogs", ()):
        for scope_log in resource_log.get("scopeLogs", ()):
            for log_record in scope_log.get("logRecords", ()):
                yield _flattened(log_record.get("attributes", ()), counters)


def _flattened(attributes: Iterable[Any], counters: Counters) -> TelemetryRecord:
    """The allow-listed part of an OTLP key/value list, as a mapping (FR-011)."""
    flat: dict[str, str | int | bool] = {}
    for attribute in attributes:
        key = attribute.get("key")
        if key not in BOUND_ATTRIBUTES:
            _report_stripped(key, counters)
            continue
        value = _scalar(attribute.get("value", {}))
        if value is not None:
            flat[key] = value
    return flat


def _report_stripped(key: Any, counters: Counters) -> None:
    """Count the unbound *key* as stripped, when it is content or identity (FR-012).

    ``telemetry_content_stripped`` for one of `_CONTENT_ATTRIBUTES`,
    ``telemetry_identity_stripped`` for one of `_IDENTITY_ATTRIBUTES`, one bump
    per attribute encountered. Any other unbound name — a resource attribute a
    deployment invented, a record field no task reads yet — is neither, and is
    left uncounted so that each counter reports the condition its name claims.
    """
    if key in _CONTENT_ATTRIBUTES:
        counters.bump("telemetry_content_stripped")
    elif key in _IDENTITY_ATTRIBUTES:
        counters.bump("telemetry_identity_stripped")


def _scalar(value: Mapping[str, Any]) -> str | int | bool | None:
    """The scalar an OTel `AnyValue` holds, or ``None`` when it holds none of them."""
    for field, read in _VALUE_READERS.items():
        if field in value:
            return read(value[field])
    return None


def recognised_records(line: str, counters: Counters) -> Iterator[TelemetryRecord]:
    """Every record of *line* the memory consumes, refusing the rest (FR-009).

    This is the door every telemetry record comes through: nothing else in this
    module drops a record by its `event.name` or a line by its shape. Nothing
    calls it yet — T023 is what drains the collector and will.

    An `event.name` outside :data:`CONSUMED_EVENT_NAMES` bumps
    ``telemetry_record_unknown`` and is skipped. Its format is not guessed at from
    the attributes it shares with a record type that has one: a harness release
    that adds a record is read once it is declared, not before.

    A complete line that will not parse, or a well-formed JSON document that is
    not shaped like an OTLP logs payload, bumps ``telemetry_record_partial`` and
    yields nothing. The batch it carried is lost with it — half an OTLP document
    names no records — but the pass is not: nothing here raises into the job that
    is draining the collector (FR-010).
    """
    try:
        records = tuple(records_in_line(line, counters))
    except (json.JSONDecodeError, TypeError):
        counters.bump("telemetry_record_partial")
        return
    for record in records:
        if record.get(RECORD_TYPE_ATTRIBUTE) in CONSUMED_EVENT_NAMES:
            yield record
        else:
            counters.bump("telemetry_record_unknown")


class SessionBindings(Protocol):
    """The slice of the episodic store telemetry attribution reads (ISP).

    Narrower than :class:`processrecall.graph.store.EpisodicStore` on purpose:
    attribution needs to learn which project a session belongs to and nothing
    else about the sequence the hook opened. T022 is what will make the hook
    keep binding a session to its project; T023 is what will drain the
    collector and call :meth:`ProjectAttribution.attributed` with an
    implementer of this protocol. Until either lands, this seam is named but
    has no implementer and no caller.
    """

    def project_for_session(self, session_id: str) -> str | None:
        """The project key *session_id* is bound to, or ``None`` when unbound."""
        ...


@dataclass(frozen=True, slots=True)
class AttributedRecord:
    """A telemetry record together with the project its session was bound to.

    Attributes:
        project_key: The project the hook bound this record's session to.
        attributes: The record's flattened attributes, unchanged.
    """

    project_key: str
    attributes: TelemetryRecord


class ProjectAttribution:
    """Telemetry records paired with the project their `session.id` names (R4).

    The bindings and the counter sink are state rather than arguments repeated at
    every call, so one attribution belongs to one store for a whole pass.
    """

    def __init__(self, bindings: SessionBindings, counters: Counters) -> None:
        self._bindings = bindings
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._bindings!r})"

    def attributed(self, records: Iterable[TelemetryRecord]) -> Iterator[AttributedRecord]:
        """Every record of *records* whose session is bound to a project, in order.

        What is not yielded is dropped: an unbound or absent `session.id` bumps
        ``telemetry_session_unbound`` and the record goes no further, so nothing
        downstream has to decide what an unattributed record means. What is
        yielded bumps ``telemetry_records_read``, the attribute completing
        `contracts/counters.md`'s "successfully parsed and attributed".
        """
        for attributes in records:
            project_key = self._project_key(attributes)
            if project_key is not None:
                self._counters.bump("telemetry_records_read")
                yield AttributedRecord(project_key=project_key, attributes=attributes)

    def _project_key(self, attributes: TelemetryRecord) -> str | None:
        """The project *attributes* is attributable to, counting the drop if none."""
        session_id = attributes.get(SESSION_ATTRIBUTE)
        bound = (
            self._bindings.project_for_session(session_id) if isinstance(session_id, str) else None
        )
        if bound is None:
            self._counters.bump("telemetry_session_unbound")
        return bound


class SessionGaps:
    """Each gap counter bumped once per field per session (`contracts/counters.md`).

    A gate that is off, a span exporter that is not running or a harness below a
    field's floor is one condition for the whole session it holds in. Bumped per
    record instead, the count would report how busy the session was rather than
    that the condition is there to fix, and would bury every other counter next
    to it.

    The sink and what has already been reported are state rather than arguments
    repeated at every call, so one instance spans one ingest pass.
    """

    def __init__(self, counters: Counters) -> None:
        self._counters = counters
        self._reported: set[tuple[str, str]] = set()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(reported={len(self._reported)})"

    def report(self, session_id: str, counter: str) -> None:
        """Bump *counter* for *session_id*, unless it was already bumped for it.

        Raises:
            ValueError: *counter* is not one of :data:`GAP_COUNTERS`. The name
                reaches ``bump`` from here as a variable rather than as the
                literal `tests/graph/test_counters.py` scans for, so the closed
                set is what keeps a name with no reader out.
        """
        if counter not in GAP_COUNTERS:
            raise ValueError(f"not a gap counter: {counter!r}")
        gap_in_session = (session_id, counter)
        if gap_in_session in self._reported:
            return
        self._reported.add(gap_in_session)
        self._counters.bump(counter)


#: The two consumed record types `OTEL_LOG_TOOL_DETAILS` has anything to put on
#: (`contracts/telemetry-records.md`): the gate is about what a tool call was
#: given, so no other record type says anything about whether it is on.
_TOOL_RECORD_NAMES = frozenset({"claude_code.tool_result", "claude_code.tool_decision"})

#: What the gate gates: the arguments of the call and the revision it ran
#: against (`contracts/telemetry-records.md`). With the gate on, every tool
#: record carries at least the arguments, so a tool record carrying none of
#: these was written with it off. Read by `processrecall doctor` too, which
#: reports the gate the records it finds imply.
TOOL_DETAIL_ATTRIBUTES = frozenset(
    {"tool_parameters", "tool_input", "vcs.ref.head.revision", "vcs.ref.head.name"}
)


def report_tool_detail_gap(record: TelemetryRecord, gaps: SessionGaps) -> None:
    """Report `gap_tool_details` when *record* was written with the gate off (SC-003).

    A tool record with none of the gated attributes on it is the only sign the
    reader gets: the harness reports no gate settings, so the gap is read off
    what the record does not carry. *gaps* is what makes that one bump for the
    session rather than one per record.

    A record of any other type is left alone, and so is a session whose
    `session.id` never arrived: the gate is about tool calls, and a gap counted
    against no session could not be bumped once per session.
    """
    if record.get(RECORD_TYPE_ATTRIBUTE) not in _TOOL_RECORD_NAMES:
        return
    if not TOOL_DETAIL_ATTRIBUTES.isdisjoint(record):
        return
    session_id = record.get(SESSION_ATTRIBUTE)
    if isinstance(session_id, str):
        gaps.report(session_id, "gap_tool_details")


#: Each span-only part of the model: the gap its absence is, and the record
#: types whose row is the event-side half of it (R9, `contracts/counters.md`).
#: Read by record type alone rather than by an attribute's absence: no span
#: reader exists yet, so no record reaching here ever carries `first_content_ms`
#: or `parent_agent_id` regardless, and `contracts/counters.md` names no
#: attribute for `gap_permission_wait` at all — the wait is inside a tool
#: span's duration, not a column of its own. Once a span reader exists, telling
#: a merged record from an events-only one is that task's guard to add, with
#: the attribute names it actually lands. `stop_reason` and `error_class` are
#: deliberately not here — they refine an `outcome` the events produce whole,
#: so their absence is absent enrichment rather than a part of the model missing.
_SPAN_ONLY_RECORD_TYPES: Mapping[str, frozenset[str]] = {
    # Only a call that returned content has a first token to time: an error
    # returned no body and a refusal no content, so neither is missing a value it
    # could have had.
    "gap_ttft": frozenset({"claude_code.api_request"}),
    # The wait for the operator is inside a tool span's duration and inside no
    # event's, so what is missing is the split of a step's `duration_ms` into
    # waiting and running.
    "gap_permission_wait": _TOOL_RECORD_NAMES,
    # A sub-agent whose parent nothing named is a `spawned` edge that cannot be
    # derived. The main thread has no parent to be missing, so this is read off
    # the one record type that names a sub-agent.
    "gap_agent_nesting": frozenset({"claude_code.subagent_completed"}),
}


def report_span_only_gaps(record: TelemetryRecord, gaps: SessionGaps) -> None:
    """Report each span-only part of the model *record*'s type carries no span for (SC-006).

    A record of a type that would carry the field is the span side not having
    reached the row it becomes: with no trace file configured that is every
    record, and these three gaps are the whole difference between an
    events-only run and a full one. A record of a type the field never sits on
    reports nothing.

    A record whose `session.id` never arrived is left alone for the same reason
    `report_tool_detail_gap` leaves it alone: a gap counted against no session
    could not be bumped once per session.

    Nothing calls this yet: the ingest pass that drains the collector will,
    once it exists, the way `recognised_records`' own docstring flags the same
    gap for itself.
    """
    session_id = record.get(SESSION_ATTRIBUTE)
    if not isinstance(session_id, str):
        return
    record_type = record.get(RECORD_TYPE_ATTRIBUTE)
    for counter, record_names in _SPAN_ONLY_RECORD_TYPES.items():
        if record_type in record_names:
            gaps.report(session_id, counter)


def in_record_order(
    records: Iterable[TelemetryRecord], counters: Counters
) -> tuple[TelemetryRecord, ...]:
    """*records* in the order the steps and sequences they become happened in (FR-006).

    By `event.timestamp`, with `event.sequence` breaking a tie between two
    records written at the same instant. The timestamp is compared as the instant
    it names rather than as the text it was written as: two processes reporting
    one session need not agree on an offset, and sorted as text they would
    interleave wrongly.

    A record whose timestamp is missing or will not parse cannot be placed in
    this order; it is dropped and bumps `telemetry_record_partial` (FR-009)
    rather than raising into the caller (FR-010).
    """
    positioned = []
    for record in records:
        try:
            position = _read_position(record)
        except (KeyError, ValueError):
            counters.bump("telemetry_record_partial")
            continue
        positioned.append((position, record))
    positioned.sort(key=lambda item: item[0])
    return tuple(record for _, record in positioned)


def parse_instant(value: str) -> datetime:
    """*value* as the instant it names, defaulting a naive reading to UTC.

    Two harnesses need not agree on whether `event.timestamp` carries an
    offset, and comparing a naive instant against an aware one raises rather
    than orders — so a naive reading is treated as UTC here, once, for every
    caller that places a record in time.
    """
    instant = datetime.fromisoformat(value)
    return instant if instant.tzinfo else instant.replace(tzinfo=UTC)


def _read_position(record: TelemetryRecord) -> tuple[datetime, int]:
    """*record*'s sort key: when it was written, then the harness's counter."""
    written_at = parse_instant(str(record[TIMESTAMP_ATTRIBUTE]))
    return written_at, int(record[SEQUENCE_ATTRIBUTE])


def step_from_verdict(record: TelemetryRecord) -> TrajectoryEvent:
    """The step a `claude_code.tool_decision` record is (FR-008).

    The only record a rejected call produces, and it says how the call was
    decided rather than how it went: the step carries the decision axis and the
    source that decided it, and carries no duration, no result size and no
    touched edges. A refused call ran nothing and touched nothing, so each of
    those absences *is* the refusal rather than a field a later source is
    expected to fill in (R14).

    The arguments are dropped even when `OTEL_LOG_TOOL_DETAILS` put them on the
    verdict, because the touched edges are derived from them and FR-008 forbids
    a refused step any. This drops them on an accepted verdict too, which for a
    compound shell command means the verdict decomposes to one sub-activity
    while the `tool_result` behind it decomposes to several (R6, T020): only
    ordinal 0 can collapse against the verdict, and the later sub-activities
    keep `decision=None`. Spreading the decision axis across every sub-activity
    of one tool-use id is the accept-verdict collapse task's to fix, not this
    one's. A verdict whose `decision` the harness spells some other way leaves
    the axis unset rather than guessed at.

    ``tool_source`` rides along even though nothing in this task asked for it,
    because `contracts/telemetry-records.md` marks `tool_decision` its only
    carrier (floor v2.1.214): no other record, and so no other task, ever
    reaches it again.

    ``project_dir`` is empty because no telemetry record carries a working
    directory (R4): the project is the one :class:`ProjectAttribution` resolved
    through `session.id`, not one this record could name. ``record_ref`` points a
    human at the record type and the tool call rather than at a collector line,
    which rotates.

    Raises:
        KeyError, ValueError: *record* carries no readable position. Records
            reach here through :func:`in_record_order`, which is where an
            unplaceable one is dropped and counted (FR-009).
    """
    occurred_at, event_sequence = _read_position(record)
    tool_call_id = str(record.get("tool_use_id", ""))
    return TrajectoryEvent(
        operation_name="execute_tool",
        conversation_id=str(record.get(SESSION_ATTRIBUTE, "")),
        agent_id="",
        agent_name="",
        tool_name=str(record.get("tool_name", "")),
        tool_call_id=tool_call_id,
        tool_call_arguments={},
        tool_call_result="",
        prompt_id=str(record.get(PROMPT_ATTRIBUTE, "")),
        project_dir="",
        record_ref=f"{record.get(RECORD_TYPE_ATTRIBUTE, '')}#{tool_call_id}",
        occurred_at=occurred_at,
        source_kind=SourceKind.LIVE,
        decision=_DECISION_VALUES.get(str(record.get("decision", ""))),
        decision_source=_validated_source(record),
        tool_source=optional_text(record, "tool_source"),
        event_sequence=event_sequence,
    )


def optional_text(record: TelemetryRecord, attribute: str) -> str | None:
    """*attribute* as text, or ``None`` when *record* did not carry it (R14)."""
    value = record.get(attribute)
    return None if value is None else str(value)


def optional_integer(record: TelemetryRecord, attribute: str) -> int | None:
    """*attribute* as a whole number, or ``None`` when *record* did not carry it (R14)."""
    value = record.get(attribute)
    return None if value is None else int(value)


def optional_bool(record: TelemetryRecord, attribute: str) -> bool | None:
    """*attribute* as a bool, or ``None`` when *record* did not carry one (R14).

    A value present but not a bool is treated the same as absent, for the reason
    :func:`step_result` guards `success` the same way: a flag spelled as
    something other than a boolean says nothing about the axis it names.
    """
    value = record.get(attribute)
    return value if isinstance(value, bool) else None


def _validated_source(record: TelemetryRecord) -> str | None:
    """*record*'s `source`, or ``None`` when absent or not one `_DECISION_SOURCE_VALUES` names."""
    source = record.get("source")
    return str(source) if isinstance(source, str) and source in _DECISION_SOURCE_VALUES else None


def step_result(record: TelemetryRecord) -> str | None:
    """The result axis a `claude_code.tool_result` *record* reports (FR-022).

    `failure` when the harness said the call did not succeed, or named the class
    of failure it hit; `ok` when it said the call succeeded and named none. Both
    readings are consulted rather than the flag alone because an `error_type` is
    only ever written on a call that hit one, so it settles the axis whatever the
    flag says.

    ``None`` when the record reports neither, the `tool_decision` a refused call
    leaves among them: a record with no error flag says nothing about this axis,
    and reading it as `ok` would count a call that never ran as one that worked
    (R14). A flag spelled as something other than a boolean, or an `error_type`
    that is not non-empty text, is unreported for the same reason rather than
    read for its presence or its truthiness.

    The decision axis is untouched here. Whether the call was allowed to try is a
    policy signal — `decision_type` on this record, `decision` on the verdict —
    and FR-022 forbids collapsing the two: a permitted call that failed and a
    refused one that never ran are different facts about different things.

    Nothing calls this yet: folding a `tool_result` into the accepted step
    T029 already collapsed onto is a later task's, not this one's — this
    function only makes the reading available.
    """
    error_type = record.get("error_type")
    if isinstance(error_type, str) and error_type:
        return _RESULT_FAILURE
    success = record.get("success")
    if not isinstance(success, bool):
        return None
    return _RESULT_OK if success else _RESULT_FAILURE
