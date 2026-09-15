"""One read of the episodic index, folded into the snapshots it derives (FR-032).

Nothing about the abstract graph is stored, so both files it is served from are
a pure function of the rows behind them: this module is that function. It lives
in the graph layer rather than beside `processrecall rebuild` because two
writers derive the same two files — the operator command, and the `close` hook
folding a project's own graph at the end of a piece of work (FR-050) — and a
second fold written to agree with the first is a copy that can drift (SC-004).

On the hot path, so the standard library only.

Example:
    from processrecall.graph.derive import Derivation

    path, snapshot, orphaned = Derivation(store, project_dir, level).project_snapshot()
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from collections.abc import Sequence as Seq
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from processrecall.config import STORE_DIR, Config, home_dir, load_config
from processrecall.graph.abstract import (
    SUPPORTING_STEPS_KEPT,
    TOP_TEMPLATES,
    aggregate,
    edge_key,
    reattach,
    served,
)
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

    def snapshots(self) -> tuple[tuple[Path, Snapshot, int], ...]:
        """Both snapshots this derives, each against the path it belongs at.

        Derived together from one read of the index so that the two files agree
        about the rows they saw: a second read could catch a step the first
        missed and leave the project's graph ahead of the cross-project one.

        The third element of each tuple is how many stored annotations
        `reattach` (FR-038) could not find a move for on this rebuild —
        reported by the caller rather than dropped, since an orphan is a fact
        about the rebuild and not a defect to run silently.
        """
        steps = tuple(self.store.iter_steps())
        sequences = _sequences(self.store, steps)
        every_reattached = reattach(
            aggregate(steps, self.level, sequences, self.config),
            self.store.annotations_for(None),
        )
        return (
            self._own_snapshot(steps, sequences),
            (
                home_dir() / SNAPSHOT_NAME,
                served(every_reattached.graph, _generated_at(steps)),
                len(every_reattached.orphaned),
            ),
        )

    def project_snapshot(self) -> tuple[Path, Snapshot, int]:
        """This project's own snapshot alone, folded onto the file already on disk.

        The half the `close` hook lands in process at the end of a piece of
        work: rather than re-deriving the whole project's history on every
        `Stop` — cost that would grow with every row ever recorded against the
        project instead of with the turn just closed — this reads only the
        episodic rows recorded since the file's own high-water mark
        (`EpisodicStore.iter_steps`'s *since*) and counts them onto what is
        already there (FR-050). The cross-project file is left to the
        detached session-end job, the only writer of a file every session of
        the machine shares.

        A project with no snapshot yet, or one built at a different *level*,
        has nothing to count onto, so the first fold — and only the first —
        still derives from every row the project has (`_own_snapshot`).

        Counting is exact for every running total (support, weight, outcome
        counts): addition does not care what order the rows arrive in. What it
        is not exact for is a field that names a single winner rather than a
        total — an edge's condition, a pitfall's evidence — because judging
        those again needs the edge's whole history and not just this turn's.
        A key already on the file keeps its own winner; only a key new to this
        fold takes the one this turn derived. That is a deliberate, bounded
        gap from reproducing a from-scratch rebuild bit for bit, closed by the
        next `rebuild` or session-end fold rather than by this one (SC-004 is
        asserted at `rebuild` and `prune`, not continuously).
        """
        path = self.project_dir / STORE_DIR / SNAPSHOT_NAME
        existing = SnapshotFile(path, self.store).read()
        if existing is None or existing.level != self.level:
            steps = tuple(self.store.iter_steps())
            return self._own_snapshot(steps, _sequences(self.store, steps))
        steps = tuple(self.store.iter_steps(since=existing.episode_high_water))
        sequences = _sequences(self.store, steps)
        mine_key = project_key(str(self.project_dir))
        mine = _recorded_against(steps, sequences, mine_key)
        reattached = reattach(
            aggregate(mine, self.level, sequences, self.config),
            self.store.annotations_for(mine_key),
        )
        delta = served(reattached.graph, _generated_at(mine))
        return path, _folded_onto(existing, delta), len(reattached.orphaned)

    def _own_snapshot(
        self, steps: tuple[EpisodicStep, ...], sequences: Mapping[SequenceKey, Sequence]
    ) -> tuple[Path, Snapshot, int]:
        """The snapshot the rows of *steps* recorded against this project derive."""
        mine_key = project_key(str(self.project_dir))
        mine = _recorded_against(steps, sequences, mine_key)
        reattached = reattach(
            aggregate(mine, self.level, sequences, self.config),
            self.store.annotations_for(mine_key),
        )
        return (
            self.project_dir / STORE_DIR / SNAPSHOT_NAME,
            served(reattached.graph, _generated_at(mine)),
            len(reattached.orphaned),
        )


def _generated_at(steps: Iterable[EpisodicStep]) -> datetime:
    """When the graph folded from *steps* was derived.

    The most recent `occurred_at` among the rows folded in, not the wall
    clock: a pure function of the rows is what lets two rebuilds of the same
    index, or a rebuild and the incremental writer, land on the same instant
    without coordinating (FR-032).
    """
    return max((step.occurred_at for step in steps), default=_EPOCH)


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


def _folded_onto(existing: Snapshot, delta: Snapshot) -> Snapshot:
    """*delta*'s rows, counted onto *existing* rather than replacing it (FR-050).

    Every field that is a running total merges by addition — the one thing
    pure counting can always get right regardless of what has already been
    folded in. A field that instead names a single winner carries forward from
    *existing* for a key it already had, and is taken from *delta* only for a
    key new to it (see `Derivation.project_snapshot`).
    """
    return Snapshot(
        level=existing.level,
        episode_high_water=max(existing.episode_high_water, delta.episode_high_water),
        nodes=_merged_nodes(existing.nodes, delta.nodes),
        edges=_merged_edges(existing.edges, delta.edges),
        generated_at=max(existing.generated_at, delta.generated_at),
    )


def _as_mapping(body: object) -> Mapping[str, Any]:
    """*body*, narrowed from the ``object`` a snapshot's own typing leaves it as.

    `graph/snapshot.py` types a node or edge body as ``object`` because it
    never looks inside one; this module does, so it is the one that narrows —
    with `isinstance` rather than a bare cast, since a document read back off
    disk is exactly the untrusted input FR-014 asks not to trust blindly.

    Raises:
        TypeError: *body* is not a JSON object. Every node and edge a snapshot
            carries is one, so this names a caller bug rather than a shape a
            snapshot on disk could legitimately have.
    """
    if not isinstance(body, Mapping):
        raise TypeError(f"expected a node or edge object, got {type(body).__name__}")
    return cast("Mapping[str, Any]", body)


def _merged_nodes(existing: Mapping[str, object], delta: Mapping[str, object]) -> dict[str, Any]:
    """Every node of *existing*, counted against what *delta* adds to it, by key."""
    merged = {key: dict(_as_mapping(body)) for key, body in existing.items()}
    for key, body in delta.items():
        incoming = _as_mapping(body)
        merged[key] = _merge_node(merged[key], incoming) if key in merged else dict(incoming)
    return merged


def _merge_node(existing: Mapping[str, Any], delta: Mapping[str, Any]) -> dict[str, Any]:
    """One node's body, *delta*'s occurrences counted onto *existing*'s own."""
    return {
        "level": existing["level"],
        "is_a": existing["is_a"],
        "templates": _merged_templates(existing["templates"], delta["templates"]),
        "support": int(existing["support"]) + int(delta["support"]),
        "outcome_counts": _merged_counts(existing["outcome_counts"], delta["outcome_counts"]),
        "last_seen": _later(existing["last_seen"], delta["last_seen"]),
    }


def _merged_edges(existing: Seq[object], delta: Seq[object]) -> list[dict[str, Any]]:
    """Every edge of *existing*, counted against what *delta* adds to it, in key order."""
    merged = {
        edge_key(str(body["source"]), str(body["target"])): dict(body)
        for body in (_as_mapping(edge) for edge in existing)
    }
    for edge in delta:
        incoming = _as_mapping(edge)
        key = edge_key(str(incoming["source"]), str(incoming["target"]))
        merged[key] = _merge_edge(merged[key], incoming) if key in merged else dict(incoming)
    return [merged[key] for key in sorted(merged)]


def _merge_edge(existing: Mapping[str, Any], delta: Mapping[str, Any]) -> dict[str, Any]:
    """One edge's body, *delta*'s occurrences counted onto *existing*'s own.

    ``condition``, ``pitfalls`` and ``annotations`` carry forward from
    *existing* unchanged: each names a single winner — or, for annotations, a
    table already reattached at the last full fold — that needs the edge's
    whole history to judge again, not just this turn's. Recomputing them from
    one turn alone would replace an established fact with a guess; a full
    `rebuild` re-derives them for real, from every row again.
    """
    return {
        "source": existing["source"],
        "target": existing["target"],
        "condition": existing["condition"],
        "guidance": existing["guidance"],
        "pitfalls": existing["pitfalls"],
        "annotations": existing["annotations"],
        "support": int(existing["support"]) + int(delta["support"]),
        "weight": float(existing["weight"]) + float(delta["weight"]),
        "last_seen": _later(existing["last_seen"], delta["last_seen"]),
        "outcome_counts": _merged_counts(existing["outcome_counts"], delta["outcome_counts"]),
        "supporting_steps": _merged_supporting_steps(
            existing["supporting_steps"], delta["supporting_steps"]
        ),
        "supporting_step_count": (
            int(existing["supporting_step_count"]) + int(delta["supporting_step_count"])
        ),
    }


def _merged_templates(existing: Any, delta: Any) -> list[list[Any]]:
    """The top `TOP_TEMPLATES` of *existing*'s and *delta*'s templates, counts summed."""
    counts: Counter[str] = Counter()
    for text, count in (*existing, *delta):
        counts[text] += int(count)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [[text, count] for text, count in ranked[:TOP_TEMPLATES]]


def _merged_counts(existing: Mapping[str, int], delta: Mapping[str, int]) -> dict[str, int]:
    """*existing*'s outcome counts, *delta*'s added on by matching key."""
    counts: Counter[str] = Counter(existing)
    counts.update(delta)
    return dict(counts)


def _merged_supporting_steps(existing: Any, delta: Any) -> list[int]:
    """The `SUPPORTING_STEPS_KEPT` most recent of *existing*'s and *delta*'s step ids."""
    ids = sorted({int(value) for value in (*existing, *delta)})
    return ids[-SUPPORTING_STEPS_KEPT:]


def _later(existing: str, delta: str) -> str:
    """Whichever of two ISO timestamps names the later instant."""
    return existing if datetime.fromisoformat(existing) >= datetime.fromisoformat(delta) else delta
