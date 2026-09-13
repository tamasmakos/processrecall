"""The served form: the abstract graph as one version-stamped JSON object (R11).

`contracts/storage.md` gives the abstract graph a plain adjacency file — one
per project, one in the home directory — because a snapshot that can be read
in a diff is what makes FR-054's "safe to commit deliberately" true. This
module is that file and nothing else: it stamps a format, lands a write
atomically, and refuses a shape it does not understand.

Node and edge bodies cross this seam already shaped — pre-serialised into
JSON-native values by aggregation — so the served form can change without
aggregation knowing and aggregation can change without the file format
moving.

On the hot path — every guidance lookup reads a snapshot (FR-041) — so the
standard library only.

Example:
    from processrecall.graph.snapshot import SnapshotFile

    file = SnapshotFile(project_dir / ".processrecall" / "graph.json", store)
    served = file.read()
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

#: The snapshot's filename, under `~/.processrecall` and under
#: `<project>/.processrecall` (`contracts/storage.md`).
SNAPSHOT_NAME = "graph.json"

#: The version stamp every snapshot carries. An unknown one is a refusal
#: rather than a `KeyError` inside a hook (R11), which is the whole reason the
#: field exists.
SNAPSHOT_FORMAT = 1


class Counters(Protocol):
    """The slice of the episodic store a snapshot writes to.

    Narrower than :class:`processrecall.graph.store.EpisodicStore` on purpose
    (ISP): reading and writing a file only ever counts.
    """

    def bump(self, counter: str) -> None:
        """Increment the counter named *counter*."""
        ...


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The abstract graph as it is served: adjacency, provenance, no payloads.

    Attributes:
        level: The generality this snapshot was built for (FR-023).
        episode_high_water: The largest episodic ``step_id`` folded in. It is
            provenance — what `show` reports staleness from — and never a
            trigger to rebuild (FR-041).
        nodes: Each procedure against its body, keyed by node key.
        edges: The permissible transitions, in the order they were derived.
        generated_at: When the graph behind this file was derived.
    """

    level: str
    episode_high_water: int
    nodes: Mapping[str, object]
    edges: Sequence[object]
    generated_at: datetime


class SnapshotFile:
    """One snapshot on disk, counted into *counters*.

    A project's file and the cross-project one in the home directory are two
    instances of this, which is why the path is state and not an argument
    repeated at every call (FR-053).
    """

    def __init__(self, path: Path, counters: Counters) -> None:
        self._path = path
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path!s})"

    def write(self, snapshot: Snapshot) -> None:
        """Land *snapshot* at this path, atomically (FR-055, SC-014).

        The temporary file is made in the *same directory* so `os.replace`
        stays a rename: across a filesystem boundary it degrades to
        copy-then-delete and silently stops being atomic. An interrupted write
        therefore leaves the previous snapshot readable.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(_as_document(snapshot), stream)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise
        os.replace(temporary, self._path)
        self._counters.bump("snapshot_written")

    def read(self) -> Snapshot | None:
        """The snapshot at this path, or ``None`` when it cannot be served.

        A file that is missing, half-written or carrying a format this build
        does not know is refused, counted and left alone: a hook has no error
        to show a developer, so silence plus a counter is the only honest
        answer (R11, R16). The three cases share one answer because the reader
        can do nothing different about them.
        """
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = {}
        if document.get("format") != SNAPSHOT_FORMAT:
            self._counters.bump("snapshot_unreadable")
            return None
        try:
            return _snapshot_from(document)
        except (KeyError, TypeError, ValueError):
            self._counters.bump("snapshot_unreadable")
            return None


def _as_document(snapshot: Snapshot) -> dict[str, Any]:
    """*snapshot* as the JSON object of `contracts/storage.md`."""
    return {
        "format": SNAPSHOT_FORMAT,
        "generated_at": snapshot.generated_at.isoformat(),
        "level": snapshot.level,
        "episode_high_water": snapshot.episode_high_water,
        "nodes": dict(snapshot.nodes),
        "edges": list(snapshot.edges),
    }


def _snapshot_from(document: Mapping[str, Any]) -> Snapshot:
    """The snapshot *document* describes."""
    return Snapshot(
        level=document["level"],
        episode_high_water=document["episode_high_water"],
        nodes=document["nodes"],
        edges=document["edges"],
        generated_at=datetime.fromisoformat(document["generated_at"]),
    )
