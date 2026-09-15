"""The served snapshot: atomic write, format-stamped read, counted refusal."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from processrecall.graph.snapshot import SNAPSHOT_FORMAT, Snapshot, SnapshotFile


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


@pytest.fixture
def counters() -> FakeCounters:
    return FakeCounters()


@pytest.fixture
def snapshot() -> Snapshot:
    """One node and the edge leaving it — the adjacency form of `storage.md`."""
    return Snapshot(
        level="class/program",
        episode_high_water=12841,
        nodes={
            "ChangeImplementation/Edit": {
                "level": "class/program",
                "is_a": ["ChangeImplementation"],
                "support": 475,
            }
        },
        edges=[
            {
                "source": "ChangeImplementation/Edit",
                "target": "ArtifactEvaluation/pytest",
                "support": 38,
            }
        ],
        generated_at=datetime(2026, 9, 12, 10, 4, 11, tzinfo=UTC),
    )


def test_a_written_snapshot_reads_back_whole_and_counts_the_write(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    file = SnapshotFile(tmp_path / ".processrecall" / "graph.json", counters)

    file.write(snapshot)

    assert file.read() == snapshot
    assert counters.counted["snapshot_written"] == 1


def test_an_unknown_format_is_refused_rather_than_crashed_on(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    path = tmp_path / "graph.json"
    SnapshotFile(path, counters).write(snapshot)
    written = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**written, "format": SNAPSHOT_FORMAT + 1}), encoding="utf-8")

    assert SnapshotFile(path, counters).read() is None
    assert counters.counted["snapshot_unreadable"] == 1


def test_a_truncated_snapshot_is_refused_rather_than_crashed_on(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    path = tmp_path / "graph.json"
    SnapshotFile(path, counters).write(snapshot)
    whole = path.read_text(encoding="utf-8")
    path.write_text(whole[: len(whole) // 2], encoding="utf-8")

    assert SnapshotFile(path, counters).read() is None
    assert counters.counted["snapshot_unreadable"] == 1


def test_a_missing_snapshot_is_refused_rather_than_crashed_on(
    tmp_path: Path, counters: FakeCounters
) -> None:
    assert SnapshotFile(tmp_path / "graph.json", counters).read() is None
    assert counters.counted["snapshot_unreadable"] == 1


def test_a_well_formed_but_incomplete_snapshot_is_refused_rather_than_crashed_on(
    tmp_path: Path, counters: FakeCounters
) -> None:
    path = tmp_path / "graph.json"
    path.write_text(json.dumps({"format": SNAPSHOT_FORMAT}), encoding="utf-8")

    assert SnapshotFile(path, counters).read() is None
    assert counters.counted["snapshot_unreadable"] == 1


def test_a_write_interrupted_part_way_leaves_the_previous_snapshot_readable(
    tmp_path: Path,
    counters: FakeCounters,
    snapshot: Snapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = SnapshotFile(tmp_path / "graph.json", counters)
    file.write(snapshot)

    def die_half_way(document: object, stream: Any) -> None:
        stream.write('{"format": 1, "nod')
        raise OSError("no space left on device")

    monkeypatch.setattr(json, "dump", die_half_way)
    replacement = Snapshot(
        level="class",
        episode_high_water=1,
        nodes={},
        edges=[],
        generated_at=snapshot.generated_at,
    )
    with pytest.raises(OSError, match="no space"):
        file.write(replacement)

    monkeypatch.undo()
    assert file.read() == snapshot
    assert counters.counted["snapshot_written"] == 1
    assert list(tmp_path.glob("*.tmp")) == []
