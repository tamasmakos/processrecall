"""`processrecall rebuild` — both snapshots re-derived from the episodic index alone.

Nothing about the abstract graph is stored: a node or an edge is an aggregation
of episodic rows, so the graph a snapshot serves can always be derived again from
the rows behind it (FR-032). This command is that derivation made explicit — the
repair for a snapshot that was deleted, doctored or written by an older build,
and the seam SC-004 is measured at.

Two files are written, because there are two: the project's own snapshot, folded
from the rows recorded against that project, and the cross-project one in the
home directory, folded from every row (`contracts/storage.md`).

The fold itself is `processrecall.graph.derive`, shared with the `close` hook
that lands a project's own snapshot in process (FR-050); what lives here is the
command around it.

Example:
    from processrecall.cli.rebuild import rebuild
    from processrecall.graph.derive import Derivation

    print(rebuild(Derivation(store=store, project_dir=Path.cwd(), level="class/program")))
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from processrecall.graph.abstract import edge_key
from processrecall.graph.derive import Derivation
from processrecall.graph.snapshot import Snapshot, SnapshotFile


def rebuild(source: Derivation) -> str:
    """Write both of *source*'s snapshots, reporting what landed where."""
    lines = []
    for path, snapshot, orphaned in source.snapshots():
        SnapshotFile(path, source.store).write(snapshot)
        lines.append(
            f"{path}  nodes={len(snapshot.nodes)}  edges={len(snapshot.edges)}"
            f"  annotations_orphaned={orphaned}"
        )
    return "\n".join(lines)


def check(source: Derivation) -> str | None:
    """The first way the snapshots on disk differ from the ones *source* derives.

    ``None`` when both agree, including on `Snapshot.generated_at`: it is
    derived from the rows folded in rather than the wall clock, so the
    incremental writer and a rebuild of the same index stamp the same instant
    and a real divergence still shows there.
    """
    for path, rebuilt, _ in source.snapshots():
        if (on_disk := SnapshotFile(path, _DiscardCounters()).read()) is None:
            return f"{path} is missing or unreadable"
        if (divergence := _divergence(on_disk, rebuilt)) is not None:
            return f"{path}: {divergence}"
    return None


class _DiscardCounters:
    """A read that counts nothing, for `--check`'s look at a snapshot.

    `SnapshotFile.read()` bumps `snapshot_unreadable` on a missing or bad
    file, a serving-failure counter meant for the path a real reader hit
    (Principle V) — a consistency check is not that reader, so its read
    must not move the counter.
    """

    def bump(self, counter: str) -> None:
        """Do nothing: `--check` reads are not counted."""


def _divergence(on_disk: Snapshot, rebuilt: Snapshot) -> str | None:
    """The first difference between the served snapshot and the rebuilt one."""
    for name, served_value, derived in (
        ("level", on_disk.level, rebuilt.level),
        ("episode_high_water", on_disk.episode_high_water, rebuilt.episode_high_water),
        ("generated_at", on_disk.generated_at, rebuilt.generated_at),
    ):
        if served_value != derived:
            return f"{name} is {served_value!r} on disk and {derived!r} rebuilt"
    return _first_unequal("node", on_disk.nodes, rebuilt.nodes) or _first_unequal(
        "edge", _by_edge(on_disk.edges), _by_edge(rebuilt.edges)
    )


def _first_unequal(
    kind: str, on_disk: Mapping[str, object], rebuilt: Mapping[str, object]
) -> str | None:
    """The first *kind* the two mappings disagree about, in key order."""
    for key in sorted({*on_disk, *rebuilt}):
        if key not in rebuilt:
            return f"{kind} {key!r} is in the snapshot and not in the rebuild"
        if key not in on_disk:
            return f"{kind} {key!r} is in the rebuild and not in the snapshot"
        if on_disk[key] != rebuilt[key]:
            return f"{kind} {key!r} is {on_disk[key]} on disk and {rebuilt[key]} rebuilt"
    return None


def _by_edge(edges: Iterable[object]) -> dict[str, object]:
    """*edges* against the name each move takes, so a difference can be named by it."""
    bodies = cast("Iterable[Mapping[str, Any]]", edges)
    return {edge_key(body["source"], body["target"]): body for body in bodies}
