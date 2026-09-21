"""``processrecall doctor`` — the readiness check on the collector's file (FR-004).

The memory never installs, starts or supervises a collector: the file it reads is
written by one the developer runs. So the only help this verb can offer is a
report on the file they named, and a path that is not there yet reads as "no
telemetry source" rather than as a failure — naming the file before the
collector has written it is the ordinary cold start
(`contracts/collector-transport.md`).

A read and nothing else: no counter the store keeps is bumped here, and no read
offset is moved. `telemetry_absent` counts the drain passes that found nothing to
take (`processrecall.cli.derive.drain`), and a human asking whether their
collector works is not one of those passes; what the borrowed reader bumps as it
goes is tallied for the length of one report and thrown away.

The collector the developer runs is theirs to configure, so the other thing this
verb offers is the example configuration, shipped as package data and printed on
request: an installed wheel has no checkout to read it from.

Example:
    from processrecall.cli.doctor import collector_config, readiness
    from processrecall.config import load_config
    from processrecall.graph.store import SQLiteEpisodicStore

    print(readiness(load_config().telemetry_path, SQLiteEpisodicStore(connection)))
    print(collector_config())
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from processrecall.config import Counters, home_dir
from processrecall.graph.store import EpisodicStore, counter_table
from processrecall.trajectory.offset import OFFSET_NAME, OffsetFile
from processrecall.trajectory.telemetry import (
    SESSION_ATTRIBUTE,
    TOOL_DETAIL_ATTRIBUTES,
    VERSION_ATTRIBUTE,
    TelemetryRecord,
    recognised_records,
)

#: What the report says in place of a path when no collector file is configured,
#: which is the shipped state (`processrecall.config.Config.telemetry_path`).
NO_SOURCE = "no telemetry source"

#: The example collector configuration, beside this module so that it ships with
#: it (`tests/test_packaging.py`) and prints from an install (FR-004).
COLLECTOR_CONFIG = Path(__file__).parent / "data" / "otel-collector.yaml"

#: What the report calls each gate the records it read imply, one row apiece: the
#: content gates the prompt and the response are readable under, and the
#: tool-details gate the arguments of a call are.
_CONTENT_GATE = "content gate"
_DETAIL_GATE = "tool details gate"

#: The counter the reader bumps where it strips a content attribute. A content
#: attribute is never bound, so this bump is the only sign a content gate is on
#: (FR-012).
_CONTENT_STRIPPED = "telemetry_content_stripped"

#: What every counter of the telemetry path is named with, and so what the report
#: prints of `processrecall.graph.store.COUNTERS`.
_TELEMETRY_PREFIX = "telemetry_"

#: What the report says of an attribute or a gate no record evidenced either way.
_UNOBSERVED = "not observed"

#: What the report says of a file the collector has written nothing to, in place
#: of a modification time there is none of.
_UNWRITTEN = "(never written)"


class _Tally:
    """The counters one report implies, kept for as long as it takes to print.

    `doctor` asks a question; it does not drain. The reader it borrows bumps as
    it goes, and those bumps land here instead of in the store, where they would
    count a human's question as a pass over the collector file. What the report
    reads back off them is the content gate: an attribute the allow-list strips
    is unreadable by design, so the bump is the only sign it was ever there.
    """

    def __init__(self) -> None:
        self._counted: Counter[str] = Counter()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict(self._counted)})"

    def __contains__(self, counter: str) -> bool:
        """Whether *counter*'s condition was met while this report was read."""
        return counter in self._counted

    def bump(self, counter: str) -> None:
        """Count *counter*, for this report to read back and then drop."""
        self._counted[counter] += 1


def collector_config() -> str:
    """The shipped example collector configuration, verbatim.

    Printed for the developer to review and place themselves: nothing here writes
    it into a collector directory, and no collector is started.
    """
    return COLLECTOR_CONFIG.read_text(encoding="utf-8")


def readiness(telemetry_path: str, store: EpisodicStore) -> str:
    """What `doctor` reports about the collector file at *telemetry_path*.

    In the order `contracts/collector-transport.md` fixes: the path as configured
    and whether it is there, when it was last written, how many bytes lie between
    the persisted offset and the end, whether each gated attribute was observed,
    which gates the observed records imply, and every telemetry counter *store*
    kept. The path is printed rather than only whether a file was found: the
    commonest reading of an empty report is a collector writing somewhere else.
    """
    if not telemetry_path:
        return NO_SOURCE
    source = Path(telemetry_path)
    rows: Mapping[str, object] = {
        "last modified": _last_modified(source),
        "unread bytes": _unread_bytes(source),
        **_implied_by_records(source),
        **_telemetry_counters(store),
    }
    reported = (f"{name}  {value}" for name, value in rows.items())
    return "\n".join([f"{source}  exists={source.is_file()}", *reported])


def _last_modified(source: Path) -> str:
    """When the collector last appended to *source*, or that it never has.

    The modification time, which is what
    `processrecall.trajectory.telemetry.source_state` judges staleness on: read
    here too so that an operator sees the same instant the drain pass will.
    """
    try:
        return datetime.fromtimestamp(source.stat().st_mtime, UTC).isoformat()
    except OSError:
        return _UNWRITTEN


def _unread_bytes(source: Path) -> int:
    """How much of *source* lies past the offset the last pass over it persisted.

    The whole file where the recorded offset belongs to another path, which is
    where the next pass starts from. Whether the offset is still trusted is the
    drain pass's own fingerprint check (`processrecall.trajectory.offset`) and
    not re-run here; nothing in this report writes an offset back.
    """
    recorded = OffsetFile(home_dir() / OFFSET_NAME, _Tally()).read()
    consumed = recorded.offset if recorded is not None and recorded.path == str(source) else 0
    try:
        return max(source.stat().st_size - consumed, 0)
    except OSError:
        return 0


def _implied_by_records(source: Path) -> dict[str, str]:
    """What the records now in *source* imply, one row each, in the report's order.

    `app.version` and `session.id` are reported as observed or not because each
    is gated at the harness (``OTEL_METRICS_INCLUDE_VERSION``,
    ``OTEL_METRICS_INCLUDE_SESSION_ID``): without the first no version floor can
    be checked, and without the second no record can be attributed to a project.

    Each gate is read off the records the way ingest reads it. A content
    attribute is stripped at the door, so the strip is what says a content gate
    is on (FR-012); a tool record carries the arguments of the call whenever the
    detail gate is on, so those attributes arriving is what says that gate is
    (SC-003). A file holding no record implies neither gate, and both rows say
    that instead of naming a state nothing evidenced.
    """
    tally = _Tally()
    observed: set[str] = set()
    read = 0
    for record in _records(source, tally):
        read += 1
        observed.update(record)
    if not read:
        return dict.fromkeys(
            (VERSION_ATTRIBUTE, SESSION_ATTRIBUTE, _CONTENT_GATE, _DETAIL_GATE), _UNOBSERVED
        )
    return {
        VERSION_ATTRIBUTE: _observed(VERSION_ATTRIBUTE in observed),
        SESSION_ATTRIBUTE: _observed(SESSION_ATTRIBUTE in observed),
        _CONTENT_GATE: _gate(_CONTENT_STRIPPED in tally),
        _DETAIL_GATE: _gate(not TOOL_DETAIL_ATTRIBUTES.isdisjoint(observed)),
    }


def _records(source: Path, counters: Counters) -> Iterator[TelemetryRecord]:
    """Every record *source* holds now, read from the top through the one door.

    From the top rather than from the persisted offset, because the question the
    report answers is what the collector writes rather than what the next pass
    will take, and a file whose records were all drained would otherwise imply
    nothing at all. A trailing line with no newline is mid-write and is left to
    its writer, the reading `processrecall.trajectory.offset` gives it too.

    A file that cannot be read stops the iteration rather than raising: the
    collector owns the file, and the rows above this one already say what the
    memory could see of it.
    """
    try:
        with source.open(encoding="utf-8") as handle:
            for line in handle:
                if line.endswith("\n"):
                    yield from recognised_records(line, counters)
    except OSError:
        return


def _telemetry_counters(store: EpisodicStore) -> dict[str, int]:
    """Every counter the telemetry path can keep, against what *store* kept.

    Names still at zero are printed for the reason `counter_table` keeps them: a
    drain that never ran reads like one that found nothing, unless the name is
    there to read (R16).
    """
    return {
        name: value
        for name, value in counter_table(store).items()
        if name.startswith(_TELEMETRY_PREFIX)
    }


def _observed(seen: bool) -> str:
    """How the report spells an attribute the records carried, or did not."""
    return "observed" if seen else _UNOBSERVED


def _gate(on: bool) -> str:
    """How the report spells a gate the records imply is on, or off."""
    return "on" if on else "off"
