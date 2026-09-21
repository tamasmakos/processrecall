"""`processrecall show`: the reader Principle V makes the rest of the package honest by."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import processrecall
from processrecall.config import ActivityClass, Config, ProcessType
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import Snapshot, SnapshotFile
from processrecall.graph.store import (
    COUNTERS,
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)
from processrecall.trajectory.paths import project_key


def run_show(
    subject: str,
    home: Path,
    project: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """What `python -m processrecall.cli show <subject>` prints for the store under *home*."""
    command = [sys.executable, "-m", "processrecall.cli", "show", subject]
    if project is not None:
        command += ["--project", str(project)]
    finished = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home), **(environment or {})},
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


def _bumped_counter_names() -> set[str]:
    """Every counter literal the package bumps, found in the source and not in `COUNTERS`.

    Comparing `show counters`' output against `COUNTERS` itself would be
    circular — a `bump("...")` call site missing from that constant would
    pass either way. Grepping the source is the independent check.
    """
    package_root = Path(processrecall.__file__).parent
    pattern = re.compile(r'\.bump\(\s*"([^"]+)"\s*\)')
    return {
        name
        for path in package_root.rglob("*.py")
        for name in pattern.findall(path.read_text(encoding="utf-8"))
    }


def test_show_counters_prints_every_counter_the_package_can_increment(tmp_path: Path) -> None:
    home = tmp_path / "home"
    connection = open_index(home / ".processrecall" / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    store.bump("steps_recorded")
    store.bump("steps_recorded")
    connection.close()

    counted = dict(line.split(maxsplit=1) for line in run_show("counters", home).splitlines())

    assert counted["steps_recorded"] == "2"
    assert set(counted) >= _bumped_counter_names(), "a counter the package increments has no reader"
    assert counted["guidance_silent"] == "0", "an unseen counter must print, not be omitted"


def test_show_counters_prints_every_gap_name(tmp_path: Path) -> None:
    """Every gap counter reads back with the cause an operator can act on (SC-012).

    The counter table names all seven whatever they stand at, and a count alone
    says nothing about the fix: the causes differ — turn spans on, turn the
    tool-details gate on, upgrade the harness. A name still at zero carries its
    cause too, because the gap nobody has hit yet is the one an operator has
    still to be told about.
    """
    home = tmp_path / "home"
    connection = open_index(home / ".processrecall" / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    store.bump("gap_tool_details")
    connection.close()

    reported = {
        name: (count, cause)
        for name, count, cause in (
            line.split(maxsplit=2)
            for line in run_show("counters", home).splitlines()
            if line.startswith("gap_")
        )
    }

    expected = {name for name in COUNTERS if name.startswith("gap_")}
    assert set(reported) == expected, "a gap counter has no reader"
    assert reported["gap_tool_details"] == ("1", "OTEL_LOG_TOOL_DETAILS is off")
    assert reported["gap_ttft"] == ("0", "first_content_ms is span-only"), (
        "a gap still at zero must print, and print its cause"
    )


def test_show_config_names_where_every_value_came_from(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".processrecall").mkdir(parents=True)
    (home / ".processrecall" / "config.json").write_text(json.dumps({"k": 5}), encoding="utf-8")

    printed = run_show("config", home, environment={"PROCESSRECALL_H": "4"})

    resolved = {
        name: (value, source) for name, value, source in map(str.split, printed.splitlines())
    }
    assert resolved["k"] == ("5", "file")
    assert resolved["h"] == ("4", "environment")
    assert resolved["level"] == ("class/program", "default")
    assert resolved["telemetry_path"] == ("(unset)", "default"), "an unset value must still print"
    assert set(resolved) == {field.name for field in fields(Config)}


def test_show_graph_prints_the_shape_of_the_served_snapshot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    connection = open_index(home / ".processrecall" / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    SnapshotFile(project / ".processrecall" / "graph.json", store).write(
        Snapshot(
            level="class/program",
            episode_high_water=12841,
            nodes={"ChangeImplementation/Edit": {"support": 475}},
            edges=[
                {
                    "source": "ChangeImplementation/Edit",
                    "target": "ArtifactEvaluation/pytest",
                    "condition": {"process_type": "BugFix", "same_file_as_previous": True},
                    "support": 38,
                }
            ],
            generated_at=datetime(2026, 9, 12, 10, 4, 11, tzinfo=UTC),
        )
    )
    connection.close()

    printed = run_show("graph", home, project=project)

    shape = dict(line.split() for line in printed.splitlines() if len(line.split()) == 2)
    assert shape["nodes"] == "1"
    assert shape["edges"] == "1"
    assert shape["level"] == "class/program"
    assert shape["episode_high_water"] == "12841"
    assert "ChangeImplementation/Edit -> ArtifactEvaluation/pytest" in printed
    assert "process_type=BugFix" in printed


def test_show_sequences_prints_each_turn_shape_and_outcome_without_its_payloads(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    connection = open_index(home / ".processrecall" / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    key = SequenceKey(conversation_id="c1", session_epoch=1, prompt_id="p1")
    store.open_sequence(
        Sequence(
            key=key,
            project_dir_key=project_key(str(project)),
            started_at=datetime(2026, 9, 12, 9, 0, tzinfo=UTC),
            process_type=ProcessType.BUG_FIX,
        )
    )
    store.record(
        EpisodicStep(
            dedup_key="d1",
            sequence_key=key,
            position=0,
            node_key="Inspection/Read",
            activity_class=ActivityClass.INSPECTION,
            template="Read file_path",
            occurred_at=datetime(2026, 9, 12, 9, 0, 1, tzinfo=UTC),
            result_snippet="ghp_secret_token_the_reader_must_never_print",
        )
    )
    connection.close()

    printed = run_show("sequences", home, project=project)

    assert "c1/1/p1" in printed
    assert "steps=1" in printed
    assert "process=BugFix" in printed
    assert "derived=neutral" in printed
    assert "ghp_secret" not in printed, "a payload reached the operator surface"
