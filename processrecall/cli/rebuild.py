"""`processrecall rebuild` — both snapshots re-derived from the episodic index alone.

Nothing about the abstract graph is stored: a node or an edge is an aggregation
of episodic rows, so the graph a snapshot serves can always be derived again from
the rows behind it (FR-032). This command is that derivation made explicit — the
repair for a snapshot that was deleted, doctored or written by an older build,
and the seam SC-004 is measured at.

Two files are written, because there are two: the project's own snapshot, folded
from the rows recorded against that project, and the cross-project one in the
home directory, folded from every row (`contracts/storage.md`).

Example:
    from processrecall.cli.rebuild import Derivation, rebuild

    print(rebuild(Derivation(store=store, project_dir=Path.cwd(), level="class/program")))
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from processrecall.config import STORE_DIR, Config, home_dir, load_config
from processrecall.graph.abstract import aggregate, edge_key, served
from processrecall.graph.keys import group_by_sequence
from processrecall.graph.snapshot import SNAPSHOT_NAME, Snapshot, SnapshotFile
from processrecall.graph.store import EpisodicStep, EpisodicStore, Sequence, SequenceKey
from processrecall.trajectory.paths import project_key

#: The stamp an empty index derives, since `max` needs a default with none.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Derivation:
    """What one rebuild re-derives the graph from.

    Attributes:
        store: The episodic index, opened by the caller and closed by it.
        project_dir: The project whose own snapshot is rebuilt beside the
            cross-project one.
        level: The generality every node and edge is named at (FR-023).
        config: The tuning to fold with. Defaults to the project's own, loaded
            once here rather than a second time by every caller that already
            read it to resolve *level*.
    """

    store: EpisodicStore
    project_dir: Path
    level: str
    config: Config = field(default_factory=load_config)

    def snapshots(self) -> tuple[tuple[Path, Snapshot], ...]:
        """Both snapshots this derives, each against the path it belongs at.

        Derived together from one read of the index so that the two files agree
        about the rows they saw: a second read could catch a step the first
        missed and leave the project's graph ahead of the cross-project one.
        """
        steps = tuple(self.store.iter_steps())
        sequences = _sequences(self.store, steps)
        mine = _recorded_against(steps, sequences, project_key(str(self.project_dir)))
        return (
            (
                self.project_dir / STORE_DIR / SNAPSHOT_NAME,
                served(aggregate(mine, self.level, sequences, self.config), _generated_at(mine)),
            ),
            (
                home_dir() / SNAPSHOT_NAME,
                served(aggregate(steps, self.level, sequences, self.config), _generated_at(steps)),
            ),
        )


def _generated_at(steps: Iterable[EpisodicStep]) -> datetime:
    """When the graph folded from *steps* was derived.

    The most recent `occurred_at` among the rows folded in, not the wall
    clock: a pure function of the rows is what lets two rebuilds of the same
    index, or a rebuild and the incremental writer, land on the same instant
    without coordinating (FR-032).
    """
    return max((step.occurred_at for step in steps), default=_EPOCH)


def rebuild(source: Derivation) -> str:
    """Write both of *source*'s snapshots, reporting what landed where."""
    lines = []
    for path, snapshot in source.snapshots():
        SnapshotFile(path, source.store).write(snapshot)
        lines.append(f"{path}  nodes={len(snapshot.nodes)}  edges={len(snapshot.edges)}")
    return "\n".join(lines)


def _sequences(store: EpisodicStore, steps: Iterable[EpisodicStep]) -> dict[SequenceKey, Sequence]:
    """The prompt behind each of *steps*, for the rows whose prompt was recorded.

    The fold reads a sequence for its process type and for whether it ended
    cleanly (FR-020, FR-027); a row whose prompt nothing opened is left out and
    folded as unknown rather than invented here.
    """
    opened = ((key, store.sequence(key)) for key in group_by_sequence(steps))
    return {key: sequence for key, sequence in opened if sequence is not None}


def _recorded_against(
    steps: Iterable[EpisodicStep],
    sequences: Mapping[SequenceKey, Sequence],
    wanted: str,
) -> tuple[EpisodicStep, ...]:
    """The rows of *steps* whose prompt ran in the project *wanted* names.

    A row whose prompt is missing from *sequences* belongs to no project this
    can name, so it is left to the cross-project snapshot rather than filed
    under whichever project happened to ask.
    """
    return tuple(
        step
        for step in steps
        if (sequence := sequences.get(step.sequence_key)) is not None
        and sequence.project_dir_key == wanted
    )


def check(source: Derivation) -> str | None:
    """The first way the snapshots on disk differ from the ones *source* derives.

    ``None`` when both agree, including on `Snapshot.generated_at`: it is
    derived from the rows folded in rather than the wall clock, so the
    incremental writer and a rebuild of the same index stamp the same instant
    and a real divergence still shows there.
    """
    for path, rebuilt in source.snapshots():
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
