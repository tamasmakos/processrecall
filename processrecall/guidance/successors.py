"""The moves out of one position, read off a snapshot on disk (FR-043, FR-048).

Shared by the `UserPromptSubmit` hook and the `recall` tool: both start from a
position and a snapshot path and want the same thing back — the edges leaving
that position, filtered to a process type where the caller has one to filter
by. One reading, so the missing-snapshot and unreadable-edge handling is not
retold at each call site.

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.successors import successors_from

    edges = successors_from(snapshot_path, position, counters)
"""

from __future__ import annotations

from pathlib import Path

from processrecall.config import Counters, ProcessType
from processrecall.graph.abstract import TransitionEdge, edges_from
from processrecall.graph.snapshot import SnapshotFile


def successors_from(
    path: Path,
    source: str,
    counters: Counters,
    *,
    process_type: ProcessType | None = None,
) -> tuple[TransitionEdge, ...]:
    """The moves out of *source* in the snapshot at *path*, for *process_type*.

    ``None`` means any process type — the default, for a caller with no
    prompt-start condition to restrict to.

    A snapshot that is missing or unreadable yields no move rather than a
    fault: the reader has already counted it (R11), and a project whose graph
    has never been built is the ordinary first case rather than an error. The
    format stamp only guards the document's outer shape, so `edges_from` gets
    the same guard `SnapshotFile.read` gives the rest of the document — an
    edge body it cannot parse is the same kind of unreadable snapshot, not a
    fault inside a caller.
    """
    snapshot = SnapshotFile(path, counters).read()
    if snapshot is None:
        return ()
    try:
        edges = edges_from(snapshot)
    except (KeyError, TypeError, ValueError):
        counters.bump("snapshot_unreadable")
        return ()
    return tuple(
        edge
        for edge in edges
        if edge.source == source
        and (process_type is None or edge.condition.process_type is process_type)
    )
