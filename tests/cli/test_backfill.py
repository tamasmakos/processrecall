"""`processrecall backfill`: past sessions replayed through the live seam (FR-015).

The command reads the harness's own transcripts for a project and records what
they contain as episodic steps, so a memory can be seeded from work already
done. A record it cannot read costs its own record and is reported grouped by
reason (FR-016); the fixture session is the one small anonymised transcript
that ever enters the repository (FR-073).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from processrecall.graph.episodic import open_index
from processrecall.graph.store import SQLiteEpisodicStore
from processrecall.trajectory.transcript import transcript_directory

#: The anonymised session every test below replays.
SESSION = Path(__file__).resolve().parents[1] / "fixtures" / "session.jsonl"

#: The project directory the fixture session was recorded in.
PROJECT = "/work/demo"

#: What one pass over the fixture session records, in the order it recorded it.
ONE_PASS = ["Inspection/Read/py", "ArtifactEvaluation/pytest/py", "ChangeImplementation/Edit/py"]


def place_transcript(home: Path, project: str, name: str = "sess-fixture.jsonl") -> Path:
    """Put the fixture session where the harness keeps *project*'s transcripts."""
    directory = transcript_directory(Path(project), home=home)
    directory.mkdir(parents=True, exist_ok=True)
    placed = directory / name
    shutil.copyfile(SESSION, placed)
    return placed


def run_backfill(
    home: Path, project: str, *flags: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """What `python -m processrecall.cli backfill` did for the store under *home*."""
    return subprocess.run(
        [sys.executable, "-m", "processrecall.cli", "backfill", "--project", project, *flags],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
    )


def recorded_node_keys(home: Path) -> list[str]:
    """The node key of every step in the episodic index under *home*, in order."""
    connection = open_index(home / ".processrecall" / "episodes.db")
    try:
        steps = sorted(SQLiteEpisodicStore(connection).iter_steps(), key=lambda s: s.occurred_at)
    finally:
        connection.close()
    return [step.node_key for step in steps]


def test_backfill_records_the_actions_the_session_completed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    place_transcript(home, PROJECT)

    finished = run_backfill(home, PROJECT)

    assert finished.returncode == 0, finished.stderr
    assert recorded_node_keys(home) == ONE_PASS
    assert "sessions=1" in finished.stdout
    assert "steps=3" in finished.stdout


def test_the_summary_groups_skipped_records_by_reason(tmp_path: Path) -> None:
    """FR-016: an unreadable record is reported and counted, never aborted on."""
    home = tmp_path / "home"
    place_transcript(home, PROJECT)
    place_transcript(home, PROJECT, name="sess-again.jsonl")

    finished = run_backfill(home, PROJECT)

    assert finished.returncode == 0, finished.stderr
    assert "sessions=2" in finished.stdout
    assert "skipped=4" in finished.stdout
    assert "  2  not readable as a JSON record" in finished.stdout
    assert "  2  unrecognised record type 'x-future-record'" in finished.stdout


def test_a_relative_project_resolves_before_it_is_mangled(tmp_path: Path) -> None:
    """`--project .` must find the same transcripts an absolute path would."""
    home = tmp_path / "home"
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    place_transcript(home, str(project_dir))

    finished = run_backfill(home, ".", cwd=project_dir)

    assert finished.returncode == 0, finished.stderr
    assert "sessions=1" in finished.stdout
    assert "steps=3" in finished.stdout


def test_dry_run_reports_what_it_would_write_and_writes_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    place_transcript(home, PROJECT)

    finished = run_backfill(home, PROJECT, "--dry-run")

    assert finished.returncode == 0, finished.stderr
    assert "steps=3" in finished.stdout
    assert recorded_node_keys(home) == []


def test_since_records_only_the_actions_after_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    place_transcript(home, PROJECT)

    finished = run_backfill(home, PROJECT, "--since", "2026-01-05T09:30:19+00:00")

    assert finished.returncode == 0, finished.stderr
    assert "steps=2" in finished.stdout
    assert recorded_node_keys(home) == [
        "ArtifactEvaluation/pytest/py",
        "ChangeImplementation/Edit/py",
    ]


def test_a_bare_date_is_read_as_the_start_of_that_day_in_utc(tmp_path: Path) -> None:
    """An action's time is offset-aware; a naive `--since` cannot be compared with it."""
    home = tmp_path / "home"
    place_transcript(home, PROJECT)

    finished = run_backfill(home, PROJECT, "--since", "2026-01-05")

    assert finished.returncode == 0, finished.stderr
    assert "steps=3" in finished.stdout


def test_replaying_the_same_session_twice_writes_it_once(tmp_path: Path) -> None:
    """SC-003: the dedup key is a pure function of the record, so a rerun adds nothing."""
    home = tmp_path / "home"
    place_transcript(home, PROJECT)
    assert run_backfill(home, PROJECT).returncode == 0
    once = recorded_node_keys(home)

    finished = run_backfill(home, PROJECT)

    assert finished.returncode == 0, finished.stderr
    assert "steps=0" in finished.stdout
    assert recorded_node_keys(home) == once
