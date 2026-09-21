"""The served form: the abstract graph as one version-stamped JSON object (R11).

`contracts/storage.md` gives the abstract graph a plain adjacency file — one
per project, one in the home directory — because a snapshot that can be read
in a diff is what makes FR-054's "safe to commit deliberately" true. This
module is that file and nothing else: it stamps a format, lands a write
atomically, and refuses a shape it does not understand.

The stamp is `graph/schema.py`'s: `SNAPSHOT_FORMAT` is imported rather than
repeated, so the two modules cannot disagree on what format they mean. The
routing rule of FR-016 is enforced at write off the declaration's own
`reference` flags, for whichever body key is spelled the way the declaration
spells its field — a reference aliased under a different key is outside what
this check can see.

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
import re
import tempfile
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby, islice
from operator import itemgetter
from pathlib import Path
from typing import Any

from processrecall.config import STORE_DIR, Counters
from processrecall.graph.schema import (
    CALLERS_PER_ENTITY,
    LAYERS,
    PRECEDES_ENTITIES,
    SNAPSHOT_FORMAT,
)

#: The snapshot's filename, under `~/.processrecall` and under
#: `<project>/.processrecall` (`contracts/storage.md`).
SNAPSHOT_NAME = "graph.json"

#: Every field name the declaration marks a reference to another identity. The
#: routing rule of FR-016 is enforced off this set, so a field that becomes a
#: reference in the declaration becomes an edge here without a second edit.
_REFERENCE_KEYS = frozenset(
    field.name
    for layer in LAYERS
    for table in layer.tables
    for field in table.fields
    if field.reference
)

#: Every field name the declaration marks stored and never projected. The
#: exclusion is enforced off the declaration for the same reason the routing
#: rule is: the served form is the form that gets traversed, and a write time
#: found there makes a from-scratch rebuild differ from an incremental
#: derivation (FR-044, FR-028).
_UNPROJECTED_KEYS = frozenset(
    field.name
    for layer in LAYERS
    for table in layer.tables
    for field in table.fields
    if not field.projected
)

#: Every field name a served body may never carry (FR-014): the content fields
#: `contracts/telemetry-records.md` never reads — the prompt and the response
#: themselves, a raw request or response body, and `result_snippet` — the
#: step's own record of what a tool call read back (`graph/record.py`) — and
#: one step's monetary amount. A procedure's `median_cost_micros` is the
#: middle of many steps' amounts and is not one of these. Spelled out here
#: rather than imported from `trajectory/telemetry.py`: that set is the
#: record reader's own, and this module is read on the hot path and stays
#: standard library only.
_PRIVATE_KEYS = frozenset(
    {"prompt", "user_prompt", "response", "body", "body_ref", "result_snippet", "cost_micros"}
)

#: An absolute path in either spelling a host writes one, drive-lettered or
#: POSIX-rooted. A node key (``"Inspection/Read/py"``) carries no separator at
#: its head and is not one; neither does `trajectory/paths.py`'s sanitised
#: forms — ``~/...`` (home-relative), ``<external>/...`` and a glob's
#: ``**/...`` — which is why those lead-in characters are excluded from the
#: lookbehind: those are the committable spellings FR-013 exists to produce.
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|(?<![\w.~>*])/)[\w.\-/\\]+")


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
        precedes: The `precedes_work_on` rows: each a procedure and an entity
            worked on around it, with the entity's callers pre-computed. `write`
            is what cuts them to the bound the hot path may parse (R17), so a
            caller hands over everything it derived and the file keeps the top
            of it. Empty for a graph whose projection nothing derived.
        episodes: The recurring step subsequences `graph.abstract._recurring_episodes`
            mined at derive time (FR-031), carried across the file the way
            `precedes` is: a reader of `frequent_episode` gets them off the
            served graph rather than off a fold it never ran. Empty for a graph
            whose fold mined none.
    """

    level: str
    episode_high_water: int
    nodes: Mapping[str, object]
    edges: Sequence[object]
    generated_at: datetime
    precedes: Sequence[Mapping[str, Any]] = ()
    episodes: Sequence[Mapping[str, Any]] = ()


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

        Raises:
            ValueError: A body sits on the wrong side of the routing rule
                (FR-016), carries a field the declaration stores and never
                projects (FR-044), or carries what a published file may not
                (FR-014). Nothing is written: the served form is the form that
                gets traversed, so such a body is refused here rather than left
                for every reader of the file to trip over.
        """
        _routed_as_declared(snapshot)
        _nothing_stored_only_is_projected(snapshot)
        _nothing_private_is_served(snapshot)
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
        # An unknown format is a refusal rather than a `KeyError` inside a hook
        # (R11), which is the whole reason the field exists.
        if document.get("format") != SNAPSHOT_FORMAT:
            self._counters.bump("snapshot_unreadable")
            return None
        try:
            return _snapshot_from(document)
        except (KeyError, TypeError, ValueError):
            self._counters.bump("snapshot_unreadable")
            return None


def _routed_as_declared(snapshot: Snapshot) -> None:
    """Refuse *snapshot* unless every body is on the side FR-016 sends it to.

    A reference to another identity is an edge, a plain value is a node
    attribute; the declaration's `reference` flag is the only thing that says
    which, so the two sides are checked off that one flag rather than a second
    list of key names.
    """
    _nodes_carry_no_reference(snapshot.nodes)
    _edges_carry_a_reference(snapshot.edges)


def _nothing_stored_only_is_projected(snapshot: Snapshot) -> None:
    """Refuse *snapshot* where a body carries a field the store keeps to itself.

    One pass over nodes and edges alike: the exclusion is of the field, not of a
    side, because the projection is what a rebuild is compared against (FR-028).
    """
    for key, body in snapshot.nodes.items():
        if projected := sorted(_UNPROJECTED_KEYS.intersection(_body_keys(body))):
            raise ValueError(
                f"node {key!r} carries {', '.join(projected)}: the declaration stores "
                "these and never projects them, so a rebuild would stop equalling a fold"
            )
    for position, body in enumerate(snapshot.edges):
        if projected := sorted(_UNPROJECTED_KEYS.intersection(_body_keys(body))):
            raise ValueError(
                f"edge {position} carries {', '.join(projected)}: the declaration stores "
                "these and never projects them, so a rebuild would stop equalling a fold"
            )


def _nothing_private_is_served(snapshot: Snapshot) -> None:
    """Refuse *snapshot* where a body carries what a published file may not (FR-014).

    Nodes, edges and both projections alike: every one of them is read back out
    of the file, so a prohibition holding on one side only would be none. A
    body's own keys are matched against the names a prohibited field is
    declared under, and its strings are read at whatever depth they sit, since
    a path lands inside a list of templates as readily as against a key of its
    own.
    """
    for label, body in _bodies(snapshot):
        if private := sorted(_PRIVATE_KEYS.intersection(_body_keys(body))):
            raise ValueError(
                f"{label} carries {', '.join(private)}: a published snapshot carries "
                "no prompt text, no file contents and no per-step monetary amount"
            )
        if rooted := _absolute_path(body):
            raise ValueError(
                f"{label} carries the absolute path {rooted!r}: a published snapshot "
                "names a project by its key, never by a path on the developer's host"
            )


def _bodies(snapshot: Snapshot) -> Iterator[tuple[str, object]]:
    """Every body *snapshot* serves, each against how a refusal names it."""
    yield from ((f"node {key!r}", body) for key, body in snapshot.nodes.items())
    yield from ((f"edge {at}", body) for at, body in enumerate(snapshot.edges))
    yield from ((f"precedes row {at}", row) for at, row in enumerate(snapshot.precedes))
    yield from ((f"episode {at}", row) for at, row in enumerate(snapshot.episodes))


def _absolute_path(body: object) -> str | None:
    """The first absolute path *body* carries, or ``None`` where it carries none."""
    for text in _nested_strings(body):
        if found := _ABSOLUTE_PATH.search(text):
            return found.group()
    return None


def _nested_strings(value: object) -> Iterator[str]:
    """Every string inside *value*, however deep, each byte-for-byte as written."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _nested_strings(item)
    elif isinstance(value, Sequence):
        for item in value:
            yield from _nested_strings(item)


def _nodes_carry_no_reference(nodes: Mapping[str, object]) -> None:
    """Refuse a node body that carries a key the declaration marks a reference."""
    for key, body in nodes.items():
        if misrouted := sorted(_REFERENCE_KEYS.intersection(_body_keys(body))):
            raise ValueError(
                f"node {key!r} carries {', '.join(misrouted)}: the declaration marks "
                "these references, and a reference is an edge, not a node attribute"
            )


def _edges_carry_a_reference(edges: Sequence[object]) -> None:
    """Refuse an edge body that carries no key the declaration marks a reference."""
    for position, body in enumerate(edges):
        keys = sorted(_body_keys(body))
        if not _REFERENCE_KEYS.intersection(keys):
            raise ValueError(
                f"edge {position} carries {', '.join(keys) or 'nothing'} and no reference "
                "the declaration marks: a plain value is a node attribute, not an edge"
            )


def _body_keys(body: object) -> Collection[str]:
    """*body*'s own keys, or none where it is not a JSON object at all.

    A body that is not a JSON object at all is not this function's shape to
    refuse: `graph/derive.py`'s `_as_mapping` is the guard that narrows a body
    to a mapping by name, wherever a caller actually reads inside one; the
    routing rule only asks which keys a body carries.
    """
    return body.keys() if isinstance(body, Mapping) else ()


def _as_document(snapshot: Snapshot) -> dict[str, Any]:
    """*snapshot* as the JSON object of `contracts/storage.md`."""
    return {
        "format": SNAPSHOT_FORMAT,
        "generated_at": snapshot.generated_at.isoformat(),
        "level": snapshot.level,
        "episode_high_water": snapshot.episode_high_water,
        "nodes": dict(snapshot.nodes),
        "edges": list(snapshot.edges),
        "precedes_work_on": _bounded_precedes(snapshot.precedes),
        "frequent_episodes": list(snapshot.episodes),
    }


def _bounded_precedes(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """*rows* cut to the projection a snapshot may carry (R17, data-model.md).

    Each procedure keeps its `PRECEDES_ENTITIES` best-supported entities and each
    of those at most `CALLERS_PER_ENTITY` callers: the file is read and parsed
    whole on every guidance call, so the size of this projection is the latency
    budget, and the bound is the hot path's entire knowledge of the call graph.

    Ordered by procedure and then by rank, and ties broken on the entity key, so
    a from-scratch rebuild and an incremental derivation land on the same rows
    whatever order they arrived in (FR-028).
    """
    ranked = sorted(rows, key=_by_procedure_then_support)
    return [
        _bounded_callers(row)
        for _, of_procedure in groupby(ranked, key=itemgetter("source"))
        for row in islice(of_procedure, PRECEDES_ENTITIES)
    ]


def _by_procedure_then_support(row: Mapping[str, Any]) -> tuple[str, int, str]:
    """*row*'s place among its procedure's: most support first, entity key on a tie."""
    return row["source"], -row["support"], row["entity_key"]


def _bounded_callers(row: Mapping[str, Any]) -> dict[str, Any]:
    """*row* with its pre-computed callers cut to `CALLERS_PER_ENTITY` (R17)."""
    return {**row, "callers": list(row["callers"])[:CALLERS_PER_ENTITY]}


def served_snapshot(project_dir: Path, counters: Counters) -> Snapshot | None:
    """The snapshot *project_dir* serves, or ``None`` when it cannot be read.

    Every reader of a project's abstract graph — `inspect`, `remember`, and
    whatever else asks what a project's graph holds — wants the same file at
    the same path; this is that one lookup rather than each reader spelling
    `SnapshotFile(project_dir / STORE_DIR / SNAPSHOT_NAME, ...)` itself.
    """
    return SnapshotFile(project_dir / STORE_DIR / SNAPSHOT_NAME, counters).read()


def _snapshot_from(document: Mapping[str, Any]) -> Snapshot:
    """The snapshot *document* describes.

    A document carrying no `precedes_work_on` or `frequent_episodes` key is
    read as carrying no projection rather than refused: a graph derived where
    no code entity was resolved, or where no run recurred often enough to be
    mined, has none, which is a servable snapshot and not an unreadable one.
    Both come back as a tuple, so a snapshot with no projection reads back
    equal to the one that was written.
    """
    return Snapshot(
        level=document["level"],
        episode_high_water=document["episode_high_water"],
        nodes=document["nodes"],
        edges=document["edges"],
        generated_at=datetime.fromisoformat(document["generated_at"]),
        precedes=tuple(document.get("precedes_work_on", ())),
        episodes=tuple(document.get("frequent_episodes", ())),
    )
