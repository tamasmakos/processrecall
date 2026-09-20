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

Off the hot path, but on the store's layer, so the standard library only.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from processrecall.config import Counters

#: The flattened attribute carrying the session a record was written under. Gated
#: by ``OTEL_METRICS_INCLUDE_SESSION_ID``: with the gate off it is absent, and
#: every record is unattributable rather than wrongly attributed.
SESSION_ATTRIBUTE = "session.id"


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
    attributes: Mapping[str, str | int | bool]


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

    def attributed(
        self, records: Iterable[Mapping[str, str | int | bool]]
    ) -> Iterator[AttributedRecord]:
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

    def _project_key(self, attributes: Mapping[str, str | int | bool]) -> str | None:
        """The project *attributes* is attributable to, counting the drop if none."""
        session_id = attributes.get(SESSION_ATTRIBUTE)
        bound = self._bindings.project_for_session(session_id) if isinstance(session_id, str) else None
        if bound is None:
            self._counters.bump("telemetry_session_unbound")
        return bound
