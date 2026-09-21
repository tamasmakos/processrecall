"""The served snapshot: atomic write, format-stamped read, counted refusal."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from processrecall.graph.schema import CALLERS_PER_ENTITY, PRECEDES_ENTITIES
from processrecall.graph.snapshot import Snapshot, SnapshotFile


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


def test_snapshot_is_stamped_format_2(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """The stamp a written snapshot carries, pinned at the literal `2`.

    Spelled out rather than read back from the module's own constant: an
    assertion against the constant moves with it, so it holds whatever the
    build declares and can never report a snapshot stamped at the wrong
    format. `2` is the format `contracts/graph-schema-v2.md` names.
    """
    path = tmp_path / "graph.json"

    SnapshotFile(path, counters).write(snapshot)

    assert json.loads(path.read_text(encoding="utf-8"))["format"] == 2


def test_a_format_1_body_is_refused_rather_than_migrated(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """A body of the previous format is refused, as is one of a format ahead.

    The store carries a v1 database forward because its episodic rows cannot
    be recovered; the snapshot is a pure fold of those rows (FR-028), so a
    format-`1` body is refused for a rebuild to re-derive instead of being
    read under v2 field names. A format this build is behind is refused the
    same way, since the reader can do nothing different about either.
    """
    path = tmp_path / "graph.json"
    SnapshotFile(path, counters).write(snapshot)
    written = json.loads(path.read_text(encoding="utf-8"))

    for unserved in (1, 3):
        path.write_text(json.dumps({**written, "format": unserved}), encoding="utf-8")
        assert SnapshotFile(path, counters).read() is None

    assert counters.counted["snapshot_unreadable"] == 2


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
    path.write_text(json.dumps({"format": 2}), encoding="utf-8")

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
        stream.write('{"format": 2, "nod')
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


def test_routing_rule_rejects_reference_in_node_body(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """A node attribute that is really a reference is refused, by name (FR-016)."""
    path = tmp_path / "graph.json"
    misrouted = replace(
        snapshot,
        nodes={"ChangeImplementation/Edit": {"support": 475, "target": "ArtifactEvaluation"}},
    )

    with pytest.raises(ValueError, match="target"):
        SnapshotFile(path, counters).write(misrouted)

    assert not path.exists()
    assert counters.counted["snapshot_written"] == 0


def test_routing_rule_rejects_a_plain_value_written_as_an_edge(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """An edge that connects no identities is a node attribute, not an edge (FR-016)."""
    path = tmp_path / "graph.json"
    misrouted = replace(snapshot, edges=[{"support": 38, "weight": 0.4}])

    with pytest.raises(ValueError, match="support, weight"):
        SnapshotFile(path, counters).write(misrouted)

    assert not path.exists()
    assert counters.counted["snapshot_written"] == 0


def test_supporting_steps_are_an_edge_not_an_attribute(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """The steps behind a move name step identities, so they route as an edge (FR-025)."""
    path = tmp_path / "graph.json"
    misrouted = replace(
        snapshot,
        nodes={"ChangeImplementation/Edit": {"support": 475, "supporting_steps": [12793, 12801]}},
    )

    with pytest.raises(ValueError, match="supporting_steps"):
        SnapshotFile(path, counters).write(misrouted)

    evidenced = replace(snapshot, edges=[{"supporting_steps": [12793], "support": 38}])
    SnapshotFile(path, counters).write(evidenced)
    assert SnapshotFile(path, counters).read() == evidenced


def test_a_projected_write_time_is_refused(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """`recorded_at` is stored and never projected: a write time in a served body would
    make a from-scratch rebuild differ from an incremental derivation (FR-044, FR-028).
    The exclusion is of the field, so it holds on either side of the routing rule.
    """
    path = tmp_path / "graph.json"
    on_a_node = replace(
        snapshot,
        nodes={"ChangeImplementation/Edit": {"support": 475, "recorded_at": "2026-09-12T10:04:11"}},
    )
    on_an_edge = replace(
        snapshot,
        edges=[{"source": "ChangeImplementation/Edit", "recorded_at": "2026-09-12T10:04:11"}],
    )

    for projected in (on_a_node, on_an_edge):
        with pytest.raises(ValueError, match="recorded_at"):
            SnapshotFile(path, counters).write(projected)

    assert not path.exists()
    assert counters.counted["snapshot_written"] == 0


def test_precedes_work_on_projection_is_bounded(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """The snapshot's whole knowledge of the call graph is bounded (R17).

    The file is read and parsed whole on every guidance call, so a procedure
    carries at most `PRECEDES_ENTITIES` entities — the ones with the most
    support — and each of those at most `CALLERS_PER_ENTITY` callers. The bound
    is per procedure: a second procedure's few rows all survive.
    """
    crowded = [
        {
            "source": "ChangeImplementation/Edit",
            "entity_key": f"processrecall/graph/module_{support}.py",
            "support": support,
            "callers": [
                f"processrecall/caller_{index}.py#call" for index in range(CALLERS_PER_ENTITY + 2)
            ],
        }
        for support in range(PRECEDES_ENTITIES + 4)
    ]
    sparse = [
        {
            "source": "ArtifactEvaluation/pytest",
            "entity_key": "tests/graph/test_snapshot.py",
            "support": 3,
            "callers": [],
        }
    ]
    file = SnapshotFile(tmp_path / "graph.json", counters)

    file.write(replace(snapshot, precedes=[*crowded, *sparse]))

    served = file.read()
    assert served is not None
    kept = [row for row in served.precedes if row["source"] == "ChangeImplementation/Edit"]
    assert [row["support"] for row in kept] == list(range(PRECEDES_ENTITIES + 3, 3, -1)), (
        "the top entities by support, highest first"
    )
    assert all(len(row["callers"]) == CALLERS_PER_ENTITY for row in kept)
    assert [
        row for row in served.precedes if row["source"] == "ArtifactEvaluation/pytest"
    ] == sparse


def test_precedes_work_on_ties_break_on_entity_key(
    tmp_path: Path, counters: FakeCounters, snapshot: Snapshot
) -> None:
    """Equal support keeps the deterministic order `entity_key` gives it (FR-028).

    A from-scratch rebuild and an incremental derivation can hand the bounding
    step the same rows in a different order; support alone cannot break the tie
    between them, so the entity key does.
    """
    crowded = [
        {
            "source": "ChangeImplementation/Edit",
            "entity_key": f"processrecall/graph/module_{support}.py",
            "support": PRECEDES_ENTITIES,
            "callers": [],
        }
        for support in range(PRECEDES_ENTITIES + 2)
    ]
    file = SnapshotFile(tmp_path / "graph.json", counters)

    file.write(replace(snapshot, precedes=[*reversed(crowded)]))

    served = file.read()
    assert served is not None
    kept = [row["entity_key"] for row in served.precedes]
    assert kept == sorted(row["entity_key"] for row in crowded)[:PRECEDES_ENTITIES]
