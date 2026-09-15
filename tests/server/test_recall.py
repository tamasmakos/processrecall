"""Guidance asked for by name, or for where the work already is (FR-065).

Asserted over the transport the plugin runs, like `test_inspect.py`: the
handler reads the store of the home it was launched into and the snapshots of
the directory it was launched in, so only a spawned server over a home and a
project of its own sees what a call really answers with.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from processrecall.config import STORE_DIR, ActivityClass
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import SNAPSHOT_NAME, Snapshot, SnapshotFile
from processrecall.graph.store import (
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)
from processrecall.trajectory.paths import project_key

pytestmark = pytest.mark.unit


@pytest.fixture
def homes(tmp_path: Path) -> tuple[Path, Path]:
    """A fresh `(home, project)` pair, *project* already on disk."""
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    return home, project


async def _called(home: Path, project: Path, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """One `recall` call, answered by a server serving *home* from *project*."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "processrecall.server.mcp.stdio_server"],
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        cwd=str(project),
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session,
    ):
        await session.initialize()
        answered = await session.call_tool("recall", arguments)
        assert not answered.isError, answered.content
        return answered.structuredContent


def _snapshot(source: str, target: str, support: int) -> Snapshot:
    """A graph holding the single move from *source* to *target*, in served form."""
    return Snapshot(
        level="class/program",
        episode_high_water=41,
        nodes={
            source: {
                "level": "class/program",
                "is_a": [source.split("/")[0]],
                "templates": [],
                "support": support,
                "outcome_counts": {"success": support},
                "last_seen": "2026-09-12T09:58:02Z",
            }
        },
        edges=[
            {
                "source": source,
                "target": target,
                "condition": {
                    "process_type": "BugFix",
                    "same_file_as_previous": True,
                    "previous_outcome": "success",
                    "intended_activity": None,
                },
                "support": support,
                "weight": float(support),
                "last_seen": "2026-09-12T09:58:40Z",
                "outcome_counts": {"success": support},
                "supporting_steps": [41],
                "supporting_step_count": support,
                "pitfalls": [],
                "annotations": [],
            }
        ],
        generated_at=datetime(2026, 9, 12, 10, 4, 11, tzinfo=UTC),
    )


def _written(home: Path, path: Path, snapshot: Snapshot) -> None:
    """Land *snapshot* at *path*, its read counted into *home*'s store."""
    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        SnapshotFile(path, SQLiteEpisodicStore(connection)).write(snapshot)


def test_a_named_procedure_is_answered_with_the_moves_that_follow_it(
    homes: tuple[Path, Path],
) -> None:
    """FR-065: guidance for a procedure the caller names, out of this project's graph."""
    home, project = homes
    _written(
        home,
        project / STORE_DIR / SNAPSHOT_NAME,
        _snapshot("ChangeImplementation/Edit", "ArtifactEvaluation/pytest", 38),
    )

    answered = asyncio.run(_called(home, project, {"procedure": "ChangeImplementation/Edit"}))

    assert answered is not None
    assert answered["procedure"] == "ChangeImplementation/Edit"
    assert (answered["scope"], answered["reason"]) == ("project", None)
    assert [statement["support"] for statement in answered["statements"]] == [38]
    assert "ArtifactEvaluation/pytest" in answered["statements"][0]["text"]


def _stood(home: Path, project: Path, node_key: str) -> None:
    """Leave an open turn in *home*'s store whose last step landed on *node_key*."""
    activity_class, program = node_key.split("/")
    key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")
    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        store = SQLiteEpisodicStore(connection)
        store.open_sequence(
            Sequence(
                key=key,
                project_dir_key=project_key(str(project)),
                started_at=datetime(2026, 9, 12, 9, 58, tzinfo=UTC),
            )
        )
        store.record(
            EpisodicStep(
                dedup_key="p1-0",
                sequence_key=key,
                position=0,
                node_key=node_key,
                activity_class=ActivityClass(activity_class),
                program=program,
                template=f"{program} <File>",
                occurred_at=datetime(2026, 9, 12, 9, 58, 2, tzinfo=UTC),
            )
        )


def test_an_omitted_procedure_is_answered_for_where_the_work_already_stands(
    homes: tuple[Path, Path],
) -> None:
    """FR-043: with no procedure named, the position is read off the previous step."""
    home, project = homes
    _written(
        home,
        project / STORE_DIR / SNAPSHOT_NAME,
        _snapshot("ChangeImplementation/Edit", "ArtifactEvaluation/pytest", 38),
    )
    _stood(home, project, "ChangeImplementation/Edit")

    answered = asyncio.run(_called(home, project, {}))

    assert answered is not None
    assert (answered["procedure"], answered["level"]) == (
        "ChangeImplementation/Edit",
        "class/program",
    )
    assert [statement["support"] for statement in answered["statements"]] == [38]


def _counters(home: Path) -> dict[str, int]:
    """The counter table *home*'s store kept, read back after a call."""
    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        return dict(SQLiteEpisodicStore(connection).counters())


def test_a_procedure_only_other_projects_know_is_answered_from_the_global_graph(
    homes: tuple[Path, Path],
) -> None:
    """FR-048: the project's graph is consulted first, the cross-project one as fallback."""
    home, project = homes
    _written(
        home,
        home / STORE_DIR / SNAPSHOT_NAME,
        _snapshot("ChangeImplementation/Edit", "ArtifactEvaluation/pytest", 12),
    )

    answered = asyncio.run(_called(home, project, {"procedure": "ChangeImplementation/Edit"}))

    assert answered is not None
    assert answered["scope"] == "global"
    assert [statement["support"] for statement in answered["statements"]] == [12]
    assert _counters(home)["guidance_fallback_global"] == 1


def test_this_project_outranks_every_other_for_a_move_it_has_recorded(
    homes: tuple[Path, Path],
) -> None:
    """FR-048: a move this project has recorded is served ahead of one only others have."""
    home, project = homes
    _written(
        home,
        project / STORE_DIR / SNAPSHOT_NAME,
        _snapshot("ChangeImplementation/Edit", "ArtifactEvaluation/ruff", 3),
    )
    _written(
        home,
        home / STORE_DIR / SNAPSHOT_NAME,
        _snapshot("ChangeImplementation/Edit", "ArtifactEvaluation/pytest", 900),
    )

    answered = asyncio.run(_called(home, project, {"procedure": "ChangeImplementation/Edit"}))

    assert answered is not None
    assert answered["scope"] == "project"
    assert [statement["support"] for statement in answered["statements"]] == [3, 900]


def test_a_position_nothing_is_known_about_is_answered_rather_than_refused(
    homes: tuple[Path, Path],
) -> None:
    """FR-065: an empty result is a normal answer, carrying its reason, never a fault."""
    home, project = homes

    answered = asyncio.run(_called(home, project, {"procedure": "Inspection/Read"}))

    assert answered is not None
    assert answered["statements"] == []
    assert answered["reason"] == (
        "no move out of Inspection/Read has been recorded, here or in any other project"
    )
    assert str(project.parent) not in json.dumps(answered), "a reason must not carry a path"
