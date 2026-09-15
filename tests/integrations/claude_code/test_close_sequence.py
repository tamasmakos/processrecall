"""The ``close`` verb: the turn is ended, scored and folded into the graph (FR-050).

``contracts/agent-hooks.md`` gives the verb the work of ending a piece of work:
the sequence is closed, its derived verdict recomputed from the rows it
recorded (FR-034, FR-036), and the project's own snapshot rewritten from pure
counting — no classification, no symbol attribution, none of the enrichment the
detached session-end job owns. The nudge the same verb emits is `test_close.py`
(T060); nothing here asserts it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from collections.abc import Sequence as SequenceOf
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from processrecall.config import STORE_DIR, ActivityClass, home_dir
from processrecall.graph.abstract import END_KEY, START_KEY, edge_key
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import SNAPSHOT_NAME, Snapshot, SnapshotFile
from processrecall.graph.store import EpisodicStep, Sequence, SequenceKey, SQLiteEpisodicStore
from processrecall.integrations.claude_code.hooks import OPTOUT_MARKER, close_turn
from processrecall.trajectory.paths import project_key

from .conftest import hook

pytestmark = pytest.mark.unit

#: The sequence the payloads below belong to, and when its steps happened.
DEMO = SequenceKey("sess-demo", 0, "prompt-1", "")
AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def stop(project: Path) -> dict[str, Any]:
    """One ``Stop`` payload for *project*, as the harness delivers it."""
    return {
        "hook_event_name": "Stop",
        "session_id": "sess-demo",
        "prompt_id": "prompt-1",
        "agent_id": "",
        "cwd": str(project),
    }


def step(node_key: str, position: int, outcome: str = "neutral") -> EpisodicStep:
    """One recorded row of the turn, named by the node key the recorder derived."""
    activity_class, program = node_key.split("/")
    return EpisodicStep(
        dedup_key=f"prompt-1-{position}",
        sequence_key=DEMO,
        position=position,
        node_key=node_key,
        activity_class=ActivityClass(activity_class),
        program=program,
        template=f"{program} <File>",
        occurred_at=AT,
        outcome=outcome,
    )


def worked_in(store: SQLiteEpisodicStore, project: Path, *steps: EpisodicStep) -> None:
    """Record *steps* against an open turn of *project*, as the `record` verb would."""
    store.open_sequence(
        Sequence(key=DEMO, project_dir_key=project_key(str(project)), started_at=AT)
    )
    for recorded in steps:
        store.record(recorded)


def test_the_turn_is_closed_with_the_verdict_its_rows_derive(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-034, FR-036: the sequence's verdict is recomputed from its own steps."""
    project = tmp_path / "demo"
    store = SQLiteEpisodicStore(index)
    worked_in(
        store, project, step("Inspection/Read", 0), step("ArtifactEvaluation/pytest", 1, "failure")
    )

    close_turn(stop(project), index)

    sequence = store.sequence(DEMO)
    assert sequence is not None
    assert sequence.status == "closed"
    assert sequence.ended_at is not None
    assert sequence.derived_outcome == "failure"


def moves(snapshot: Snapshot) -> dict[str, int]:
    """Every move the snapshot serves, against how many episodes support it."""
    bodies = cast("SequenceOf[Mapping[str, Any]]", snapshot.edges)
    return {
        edge_key(body["source"], body["target"]): body["supporting_step_count"] for body in bodies
    }


def test_the_projects_own_snapshot_is_rewritten_from_the_turns_rows(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-050, FR-053: pure counting, landed in the project's own file and no other."""
    project = tmp_path / "demo"
    store = SQLiteEpisodicStore(index)
    worked_in(store, project, step("Inspection/Read", 0), step("ChangeImplementation/Edit", 1))

    close_turn(stop(project), index)

    snapshot = SnapshotFile(project / STORE_DIR / SNAPSHOT_NAME, store).read()
    assert snapshot is not None
    assert moves(snapshot) == {
        edge_key(START_KEY, "Inspection/Read"): 1,
        edge_key("Inspection/Read", "ChangeImplementation/Edit"): 1,
        edge_key("ChangeImplementation/Edit", END_KEY): 1,
    }
    assert not (home_dir() / SNAPSHOT_NAME).exists(), "the cross-project graph is the job's"


def test_the_verb_ends_the_work_the_harness_ran_it_for(tmp_path: Path) -> None:
    """``contracts/agent-hooks.md``: ``Stop`` is what closes a piece of work.

    Through the real process, as `test_close.py` is: the verb opens the index
    of its own home, which a monkeypatched one cannot move once the module has
    resolved its default path.
    """
    project = tmp_path / "demo"
    database = tmp_path / STORE_DIR / "episodes.db"
    with closing(open_index(database)) as index:
        worked_in(SQLiteEpisodicStore(index), project, step("Inspection/Read", 0))

    result = hook("close", stop(project), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (project / STORE_DIR / SNAPSHOT_NAME).exists()
    with closing(open_index(database)) as index:
        sequence = SQLiteEpisodicStore(index).sequence(DEMO)
    assert sequence is not None
    assert sequence.status == "closed"


def test_an_excluded_projects_work_ends_without_a_trace(tmp_path: Path) -> None:
    """R13, FR-058: the exclusion check comes first, so nothing is written at all."""
    project = tmp_path / "private-client"
    (project / OPTOUT_MARKER).parent.mkdir(parents=True)
    (project / OPTOUT_MARKER).touch()
    database = tmp_path / STORE_DIR / "episodes.db"
    with closing(open_index(database)) as index:
        worked_in(SQLiteEpisodicStore(index), project, step("Inspection/Read", 0))

    result = hook("close", stop(project), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (project / STORE_DIR / SNAPSHOT_NAME).exists()
    with closing(open_index(database)) as index:
        sequence = SQLiteEpisodicStore(index).sequence(DEMO)
    assert sequence is not None
    assert sequence.status == "open"


def test_a_project_that_will_not_take_the_snapshot_is_counted_not_raised(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-014: the work ends even where the file cannot be written; the row says so."""
    project = tmp_path / "demo"
    project.write_text("not a directory", encoding="utf-8")
    store = SQLiteEpisodicStore(index)
    worked_in(store, project, step("Inspection/Read", 0))

    close_turn(stop(project), index)

    assert store.counters()["snapshot_write_failed"] == 1
    sequence = store.sequence(DEMO)
    assert sequence is not None
    assert sequence.status == "closed"
