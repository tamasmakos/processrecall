"""The one write path by which meaning, not frequency, enters the graph (FR-038).

Asserted over the transport the plugin runs, like `test_inspect.py`: the handler
screens a note against the snapshot of the directory it was launched in and
writes it into the store of the home it was launched into, so only a spawned
server over a home and a project of its own sees both halves — what the call
answers, and what the store then holds.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from processrecall.config import STORE_DIR
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import SNAPSHOT_NAME, Snapshot, SnapshotFile
from processrecall.graph.store import SQLiteEpisodicStore

pytestmark = pytest.mark.unit

#: The move these tests annotate, spelled as `inspect` prints it.
EDIT_AFTER_READ = "Inspection/Read -> ChangeImplementation/Edit"

#: Two moves, in the served form of `contracts/storage.md`, so a refusal has
#: real names to offer back (FR-038a).
_SNAPSHOT = Snapshot(
    level="class/program",
    episode_high_water=12841,
    nodes={},
    edges=[
        {"source": "Inspection/Read", "target": "ChangeImplementation/Edit"},
        {"source": "ChangeImplementation/Edit", "target": "ArtifactEvaluation/pytest"},
        {"source": "Documentation/Write", "target": "ReleaseManagement/Bash"},
    ],
    generated_at=datetime(2026, 9, 12, 10, 4, 11, tzinfo=UTC),
)


@pytest.fixture
def homes(tmp_path: Path) -> tuple[Path, Path]:
    """A fresh `(home, project)` pair, *project* already serving :data:`_SNAPSHOT`."""
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        path = project / STORE_DIR / SNAPSHOT_NAME
        SnapshotFile(path, SQLiteEpisodicStore(connection)).write(_SNAPSHOT)
    return home, project


async def _called(arguments: dict[str, str], homes: tuple[Path, Path]) -> dict[str, Any] | None:
    """One `remember` call, answered by a server serving the home and project of *homes*."""
    home, project = homes
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
        return (await session.call_tool("remember", dict(arguments))).structuredContent


def test_a_note_is_stored_against_the_move_it_names_attributed_and_timestamped(
    homes: tuple[Path, Path],
) -> None:
    """FR-038: an annotation is authored, so it carries who wrote it and when."""
    home, _ = homes

    reported = asyncio.run(
        _called({"edge": EDIT_AFTER_READ, "note": "read the whole function first"}, homes)
    )

    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        stored = SQLiteEpisodicStore(connection).annotations_for(None)
    assert len(stored) == 1
    assert (stored[0].edge_key, stored[0].text) == (
        EDIT_AFTER_READ,
        "read the whole function first",
    )
    assert reported is not None
    assert (reported["stored"], reported["edge"]) == (True, EDIT_AFTER_READ)
    assert (reported["author"], reported["written_at"]) == (
        stored[0].author,
        stored[0].written_at.isoformat(),
    )


def test_a_move_the_graph_does_not_hold_is_refused_with_the_nearest_ones_it_does(
    homes: tuple[Path, Path],
) -> None:
    """FR-038a: a misspelled move is answered with real names, never created implicitly."""
    home, _ = homes

    reported = asyncio.run(
        _called(
            {"edge": "Inspection/Read -> ChangeImplementation/Edti", "note": "close, but no"},
            homes,
        )
    )

    with closing(open_index(home / STORE_DIR / "episodes.db")) as connection:
        store = SQLiteEpisodicStore(connection)
        assert store.annotations_for(None) == ()
        assert store.counters()["annotation_rejected_no_edge"] == 1
    assert reported is not None
    assert reported["stored"] is False
    assert reported["reason"] == "no_such_edge"
    assert EDIT_AFTER_READ in reported["detail"]
    assert "Documentation/Write -> ReleaseManagement/Bash" not in reported["detail"]
