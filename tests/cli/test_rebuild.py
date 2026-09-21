"""`processrecall rebuild`: the graph re-derived from the episodic index alone (SC-004).

SC-004 also requires the rebuild to match a graph the *incremental* writer
maintained; no incremental writer exists yet, so every test below compares a
rebuild against another rebuild, not against an incrementally written
snapshot. That half of the criterion is for the task that lands the
incremental writer to cover.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from processrecall.graph.schema import STORE_SCHEMA_VERSION
from tests.cli.conftest import record_turn, run_cli, snapshot_document


def run_rebuild(home: Path, project: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    """What `python -m processrecall.cli rebuild` did for the store under *home*."""
    return run_cli(home, "rebuild", "--project", str(project), *flags)


def test_rebuild_writes_both_snapshots_from_the_episodic_index(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read", "ChangeImplementation/Edit")

    finished = run_rebuild(home, project)

    assert finished.returncode == 0, finished.stderr
    for path in (project / ".processrecall" / "graph.json", home / ".processrecall" / "graph.json"):
        document = snapshot_document(path)
        assert document["format"] == 2
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


#: The frozen v1 episodic index the forward migration runs against
#: (`tests/fixtures/stores/v1.db`), copied in rather than recorded here so the
#: rows folded below are rows a schema-version-`1` build wrote.
V1_STORE = Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "v1.db"


def _install_v1_store(home: Path) -> None:
    """Put a copy of the frozen v1 index where the CLI under *home* opens one."""
    episodes = home / ".processrecall" / "episodes.db"
    episodes.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(V1_STORE, episodes)


def _schema_stamp(home: Path) -> str:
    """The schema version the index under *home* is stamped with."""
    with closing(sqlite3.connect(home / ".processrecall" / "episodes.db")) as connection:
        stamped = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        return str(stamped.fetchone()[0])


def test_check_reports_zero_divergence_on_migrated_store(tmp_path: Path) -> None:
    """A migrated store folds to the graph already on disk, exactly (FR-028, SC-007).

    The v1 rows were all recorded against one project of their own, so the
    cross-project snapshot is the one they reach here; asserting it carries
    nodes and edges is what keeps the zero divergence a statement about them.
    """
    home, project = tmp_path / "home", tmp_path / "project"
    _install_v1_store(home)
    assert run_rebuild(home, project).returncode == 0
    assert _schema_stamp(home) == STORE_SCHEMA_VERSION
    migrated = snapshot_document(home / ".processrecall" / "graph.json")
    assert migrated["nodes"] and migrated["edges"], migrated

    finished = run_rebuild(home, project, "--check")

    assert finished.returncode == 0, finished.stdout
