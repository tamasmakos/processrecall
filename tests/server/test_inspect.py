"""What the graph actually holds, read back by the agent itself (FR-065, R16).

Asserted over the transport the plugin runs, like `test_mark_outcome.py`: the
handler reads the store of the home it was launched into and the snapshot of
the directory it was launched in, so only a spawned server over a home and a
project of its own sees what a call really answers with.

`inspect` is the read half of Principle V — a counter nothing reads is dead
weight — so the counter table is asserted against the full named set rather
than against whatever this store happened to bump.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from processrecall.config import STORE_DIR
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import SNAPSHOT_NAME, Snapshot, SnapshotFile
from processrecall.graph.store import COUNTERS, SQLiteEpisodicStore

pytestmark = pytest.mark.unit


@pytest.fixture
def homes(tmp_path: Path) -> tuple[Path, Path]:
    """A fresh `(home, project)` pair, *project* already on disk."""
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    return home, project


async def _called(home: Path, project: Path) -> dict[str, Any] | None:
    """One `inspect` call, answered by a server serving *home* from *project*."""
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
        return (await session.call_tool("inspect", {})).structuredContent


def test_every_counter_the_package_can_increment_is_reported(
    homes: tuple[Path, Path],
) -> None:
    """R16: a counter still at zero is exactly the case a reader needs shown.

    Asserted against :data:`COUNTERS` itself, so this only checks that
    `inspect` echoes the constant faithfully; the independent check, that
    every `.bump("...")` call site names something in `COUNTERS`, lives in
    `tests/cli/test_show.py`.
    """
    home, project = homes
    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        SQLiteEpisodicStore(connection).bump("guidance_served")

    reported = asyncio.run(_called(home, project))

    assert reported is not None
    counters = reported["counters"]
    assert set(counters) >= set(COUNTERS)
    assert counters["guidance_served"] == 1
    assert counters["guidance_silent"] == 0


def _written(home: Path, project: Path, snapshot: Snapshot) -> None:
    """Land *snapshot* as the graph served for *project*, counted into *home*'s store."""
    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        path = project / STORE_DIR / SNAPSHOT_NAME
        SnapshotFile(path, SQLiteEpisodicStore(connection)).write(snapshot)


def _spliced(home: Path, project: Path, edge: dict[str, Any]) -> None:
    """Land the served graph for *project* with *edge* as its only move.

    `SnapshotFile.write` refuses a body carrying payloads (FR-014), so a served
    file that carries them arrives the only way one now can: by editing a file
    that was written cleanly. What `inspect` has to narrow is what it finds on
    disk, which is what this puts there.
    """
    _written(home, project, _SNAPSHOT)
    path = project / STORE_DIR / SNAPSHOT_NAME
    document = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**document, "edges": [edge]}), encoding="utf-8")


#: One procedure and one move between it and another, in the served form of
#: `contracts/storage.md`: the shape aggregation writes, so what `inspect`
#: answers with is asserted against the file it will really read.
_CONDITION = {
    "process_type": "BugFix",
    "same_file_as_previous": True,
    "previous_outcome": "success",
    "intended_activity": None,
}
_SNAPSHOT = Snapshot(
    level="class/program",
    episode_high_water=12841,
    nodes={
        "ChangeImplementation/Edit": {
            "level": "class/program",
            "is_a": ["ChangeImplementation"],
            "templates": [["Edit file_path new_string old_string", 475]],
            "support": 475,
            "outcome_counts": {"success": 441, "failure": 12},
            "last_seen": "2026-09-12T09:58:02Z",
        }
    },
    edges=[
        {
            "source": "ChangeImplementation/Edit",
            "target": "ArtifactEvaluation/pytest",
            "condition": _CONDITION,
            "guidance": [],
            "pitfalls": [],
            "support": 38,
            "weight": 121.0,
            "last_seen": "2026-09-12T09:58:40Z",
            "outcome_counts": {"success": 31, "failure": 7},
            "supporting_steps": [12793, 12801],
            "supporting_step_count": 38,
        }
    ],
    generated_at=datetime(2026, 9, 12, 10, 4, 11, tzinfo=UTC),
)


def test_the_served_nodes_and_edges_are_reported_with_their_conditions(
    homes: tuple[Path, Path],
) -> None:
    """FR-065: the graph read back is procedures, the moves between them, and when they apply."""
    home, project = homes
    _written(home, project, _SNAPSHOT)

    reported = asyncio.run(_called(home, project))

    assert reported is not None
    assert (reported["level"], reported["episode_high_water"]) == ("class/program", 12841)
    assert reported["nodes"] == [
        {
            "node": "ChangeImplementation/Edit",
            "support": 475,
            "outcome_counts": {"success": 441, "failure": 12},
            "last_seen": "2026-09-12T09:58:02Z",
        }
    ]
    edge = reported["edges"][0]
    assert edge["edge"] == "ChangeImplementation/Edit -> ArtifactEvaluation/pytest"
    assert (edge["support"], edge["condition"]) == (38, _CONDITION)


def test_an_annotation_is_reported_on_the_move_it_was_written_about(
    homes: tuple[Path, Path],
) -> None:
    """FR-038: a note is the one thing in the graph a run meant rather than counted."""
    home, project = homes
    annotated = replace(
        _SNAPSHOT,
        edges=[
            {
                **_SNAPSHOT.edges[0],
                "annotations": [
                    {
                        "text": "run the module, not the whole suite",
                        "author": "agent",
                        "written_at": "2026-09-13T08:12:00Z",
                    }
                ],
            }
        ],
    )
    _written(home, project, annotated)

    reported = asyncio.run(_called(home, project))

    assert reported is not None
    assert reported["edges"][0]["annotations"] == [
        {
            "text": "run the module, not the whole suite",
            "author": "agent",
            "written_at": "2026-09-13T08:12:00Z",
        }
    ]


def test_a_move_nobody_annotated_reports_an_empty_list_rather_than_nothing(
    homes: tuple[Path, Path],
) -> None:
    """FR-038: no note is an answer, so the key is there to read either way."""
    home, project = homes
    _written(home, project, _SNAPSHOT)

    reported = asyncio.run(_called(home, project))

    assert reported is not None
    assert reported["edges"][0]["annotations"] == []


def test_a_project_with_no_served_graph_is_told_why_rather_than_answered_emptily(
    homes: tuple[Path, Path],
) -> None:
    """No tool returns an empty success: nothing to show is a reason, and a counted one (R11)."""
    home, project = homes

    reported = asyncio.run(_called(home, project))

    assert reported is not None
    assert reported["reason"] == (
        "no graph has been built for this project yet; run `processrecall rebuild`"
    )
    assert reported["counters"]["snapshot_unreadable"] == 1
    assert str(project.parent) not in json.dumps(reported), "a reason must not carry a path"


def test_no_payload_of_the_private_store_reaches_the_answer(homes: tuple[Path, Path]) -> None:
    """FR-054: `inspect` narrows what it reads, so a body with payloads still answers in counts."""
    home, project = homes
    _spliced(
        home,
        project,
        {
            **_SNAPSHOT.edges[0],
            "record_ref": "conversation/prompt-a#12793",
            "result_snippet": "FAILED tests/server/test_inspect.py::test_leak",
            "prompt": "fix the inspect tool",
            "path": str(project / "processrecall" / "server" / "mcp" / "tools" / "inspect.py"),
        },
    )

    reported = asyncio.run(_called(home, project))

    answered = json.dumps(reported)
    assert reported is not None
    assert "record_ref" not in answered
    assert "result_snippet" not in answered
    assert "fix the inspect tool" not in answered
    assert str(project.parent) not in answered
