"""`processrecall rebuild`: the graph re-derived from the episodic index alone (SC-004).

SC-004 also requires the rebuild to match a graph the *incremental* writer
maintained; no incremental writer exists yet, so every test below compares a
rebuild against another rebuild, not against an incrementally written
snapshot. That half of the criterion is for the task that lands the
incremental writer to cover.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from processrecall.graph.episodic import open_index
from processrecall.graph.store import EpisodicStep, Sequence, SequenceKey, SQLiteEpisodicStore
from processrecall.symbolic.packs import ActivityClass, ProcessType
from processrecall.trajectory.paths import project_key

#: When the fixture turn was carried out; fixed so a rebuild of it is comparable.
STARTED_AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def record_turn(home: Path, project: Path, *node_keys: str, prompt_id: str = "p1") -> None:
    """Record one closed turn under *home*, whose steps are *node_keys* in order."""
    connection = open_index(home / ".processrecall" / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id=prompt_id)
    store.open_sequence(
        Sequence(
            key=key,
            project_dir_key=project_key(str(project)),
            started_at=STARTED_AT,
            process_type=ProcessType.BUG_FIX,
            status="closed",
        )
    )
    for position, node_key in enumerate(node_keys):
        activity_class, program = node_key.split("/")
        store.record(
            EpisodicStep(
                dedup_key=f"{prompt_id}-{position}",
                sequence_key=key,
                position=position,
                node_key=node_key,
                activity_class=ActivityClass(activity_class),
                program=program,
                template=f"{program} <File>",
                occurred_at=STARTED_AT + timedelta(seconds=position),
                files=("src/app.py",),
                outcome="success",
            )
        )
    connection.close()


def run_rebuild(home: Path, project: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    """What `python -m processrecall.cli rebuild` did for the store under *home*."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "processrecall.cli",
            "rebuild",
            "--project",
            str(project),
            *flags,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
    )


def snapshot_document(path: Path) -> dict[str, Any]:
    """The snapshot written at *path*."""
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_rebuild_writes_both_snapshots_from_the_episodic_index(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read", "ChangeImplementation/Edit")

    finished = run_rebuild(home, project)

    assert finished.returncode == 0, finished.stderr
    for path in (project / ".processrecall" / "graph.json", home / ".processrecall" / "graph.json"):
        document = snapshot_document(path)
        assert document["format"] == 1
        assert document["level"] == "class/program"
        nodes = document["nodes"]
        assert isinstance(nodes, dict)
        assert set(nodes) == {"Start", "Inspection/Read", "ChangeImplementation/Edit", "End"}
        assert nodes["Inspection/Read"]["support"] == 1
        edges = document["edges"]
        assert isinstance(edges, list)
        assert {(edge["source"], edge["target"]) for edge in edges} == {
            ("Start", "Inspection/Read"),
            ("Inspection/Read", "ChangeImplementation/Edit"),
            ("ChangeImplementation/Edit", "End"),
        }


def test_check_accepts_snapshots_the_episodic_index_still_derives(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read", "ChangeImplementation/Edit")
    assert run_rebuild(home, project).returncode == 0

    finished = run_rebuild(home, project, "--check")

    assert finished.returncode == 0, finished.stdout


def test_check_names_the_first_divergence_and_exits_non_zero(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read", "ChangeImplementation/Edit")
    assert run_rebuild(home, project).returncode == 0
    doctored = project / ".processrecall" / "graph.json"
    document = snapshot_document(doctored)
    document["nodes"].pop("ChangeImplementation/Edit")
    doctored.write_text(json.dumps(document), encoding="utf-8")

    finished = run_rebuild(home, project, "--check")

    assert finished.returncode != 0
    assert str(doctored) in finished.stdout
    assert "ChangeImplementation/Edit" in finished.stdout


def test_another_project_reaches_the_cross_project_snapshot_only(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read")
    record_turn(home, tmp_path / "elsewhere", "Search/Grep", prompt_id="p2")

    assert run_rebuild(home, project).returncode == 0

    mine = snapshot_document(project / ".processrecall" / "graph.json")["nodes"]
    everything = snapshot_document(home / ".processrecall" / "graph.json")["nodes"]
    assert "Search/Grep" not in mine
    assert "Search/Grep" in everything
    assert "Inspection/Read" in mine


def test_rebuilding_twice_derives_the_same_graph(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read", "ChangeImplementation/Edit")
    assert run_rebuild(home, project).returncode == 0
    first = snapshot_document(project / ".processrecall" / "graph.json")

    assert run_rebuild(home, project).returncode == 0

    second = snapshot_document(project / ".processrecall" / "graph.json")
    assert first == second
