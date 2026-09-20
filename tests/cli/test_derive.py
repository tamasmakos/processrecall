"""The end-of-unit-of-work pass: the collector file drained before the fold (FR-004).

The pass is the detached job `SessionEnd` already spawns, which is why the drain
is tested through `processrecall rebuild` rather than through a reader of its
own: FR-004 puts the read in that pass and nowhere else, so what the pass does
first is the property, not what the drain would do if called alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.store import SQLiteEpisodicStore
from processrecall.trajectory.offset import OFFSET_NAME
from tests.cli.conftest import record_turn, run_cli

#: Lines written by a real collector, as the fixture corpus spells them.
TELEMETRY = Path(__file__).resolve().parents[1] / "fixtures" / "telemetry" / "events_only.jsonl"


def collector_file(path: Path, lines: int) -> Path:
    """A collector file at *path* holding the first *lines* of the fixture corpus."""
    written = TELEMETRY.read_text(encoding="utf-8").splitlines()[:lines]
    path.write_text("".join(f"{line}\n" for line in written), encoding="utf-8")
    return path


def counter(home: Path, name: str) -> int:
    """How many times the store under *home* has bumped *name*."""
    connection = open_index(home / ".processrecall" / "episodes.db")
    try:
        return SQLiteEpisodicStore(connection).counters().get(name, 0)
    finally:
        connection.close()


def test_drain_runs_before_fold_in_end_of_unit_of_work_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read", "ChangeImplementation/Edit")
    collector = collector_file(tmp_path / "collector.jsonl", lines=3)
    monkeypatch.setenv("PROCESSRECALL_TELEMETRY_PATH", str(collector))

    finished = run_cli(home, "rebuild", "--project", str(project))

    assert finished.returncode == 0, finished.stderr
    reported = finished.stdout.splitlines()
    assert reported[0] == f"{collector}  lines=3"
    assert [line for line in reported[1:] if "graph.json" in line]
    recorded = json.loads((home / ".processrecall" / OFFSET_NAME).read_text(encoding="utf-8"))
    assert recorded["path"] == str(collector)
    assert recorded["offset"] == collector.stat().st_size


def test_a_configured_collector_file_that_is_absent_is_counted_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_turn(home, project, "Inspection/Read")
    monkeypatch.setenv("PROCESSRECALL_TELEMETRY_PATH", str(tmp_path / "never-written.jsonl"))

    finished = run_cli(home, "rebuild", "--project", str(project))

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.splitlines()[0] == f"{tmp_path / 'never-written.jsonl'}  absent"
    assert counter(home, "telemetry_absent") == 1
