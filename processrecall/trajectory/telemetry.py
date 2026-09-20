"""Reading the collector's OTLP/JSON lines, and attributing them to a project (FR-001, R4).

`records_in_line` is the door every telemetry record comes through. A collector
batches, so one exported line is `resourceLogs[] → scopeLogs[] → logRecords[]`
and each level can hold several: reading the first entry of any of them loses
whole records with no sign that it did (FR-002).

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
from typing import Any, Protocol

from processrecall.config import Counters

#: The flattened attribute carrying the session a record was written under. Gated
#: by ``OTEL_METRICS_INCLUDE_SESSION_ID``: with the gate off it is absent, and
#: every record is unattributable rather than wrongly attributed.
SESSION_ATTRIBUTE = "session.id"

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
        "event.name",
        TIMESTAMP_ATTRIBUTE,
        SEQUENCE_ATTRIBUTE,
        SESSION_ATTRIBUTE,
        "app.version",
        "prompt.id",
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


def records_in_line(line: str) -> Iterator[TelemetryRecord]:
    """Every log record the batched OTLP/JSON *line* carries, flattened (FR-001, FR-002).

    Each yielded record is the log record's attribute list as a mapping. The
    resource and scope around it are not folded in: they carry the deployment's
    own `OTEL_RESOURCE_ATTRIBUTES`, which name teams and departments (R11).
    """
    payload = json.loads(line)
    for resource_log in payload.get("resourceLogs", ()):
        for scope_log in resource_log.get("scopeLogs", ()):
            for log_record in scope_log.get("logRecords", ()):
                yield _flattened(log_record.get("attributes", ()))


def _flattened(attributes: Iterable[Any]) -> TelemetryRecord:
    """The allow-listed part of an OTLP key/value list, as a mapping (FR-011)."""
    flat: dict[str, str | int | bool] = {}
    for attribute in attributes:
        key = attribute.get("key")
        if key not in BOUND_ATTRIBUTES:
            continue
        value = _scalar(attribute.get("value", {}))
        if value is not None:
            flat[key] = value
    return flat


def _scalar(value: Mapping[str, Any]) -> str | int | bool | None:
    """The scalar an OTel `AnyValue` holds, or ``None`` when it holds none of them."""
    for field, read in _VALUE_READERS.items():
        if field in value:
            return read(value[field])
    return None


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
        bound = self._bindings.project_for_session(session_id) if isinstance(session_id, str) else None
        if bound is None:
            self._counters.bump("telemetry_session_unbound")
        return bound


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


def _read_position(record: TelemetryRecord) -> tuple[datetime, int]:
    """*record*'s sort key: when it was written, then the harness's counter.

    `event.timestamp` is parsed and, when it carries no offset, treated as UTC:
    two harnesses need not agree on whether to emit one, and comparing a naive
    instant against an aware one raises rather than orders.
    """
    written_at = datetime.fromisoformat(str(record[TIMESTAMP_ATTRIBUTE]))
    if written_at.tzinfo is None:
        written_at = written_at.replace(tzinfo=UTC)
    return written_at, int(record[SEQUENCE_ATTRIBUTE])
