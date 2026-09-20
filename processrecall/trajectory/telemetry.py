"""Attributing the collector's telemetry records to the project their session belongs to (R4, FR-004).

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

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

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
