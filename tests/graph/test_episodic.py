"""The episodic index seam: the one private file store, opened per invocation.

R10 fixes four pragmas and `contracts/storage.md` fixes the shape behind them.
Both are tested here through the only thing the rest of the package touches —
`open_index` — because a pragma that silently did not take, or a column that
quietly went missing, is exactly the defect this repo keeps re-learning: code
that runs, passes and records nothing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index


@pytest.fixture
def index(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A freshly created episodic index, closed again when the test ends."""
    connection = open_index(tmp_path / "episodes.db")
    try:
        yield connection
    finally:
        connection.close()


def test_open_index_applies_the_four_pragmas_r10_fixes(index: sqlite3.Connection) -> None:
    assert index.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert index.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert index.execute("PRAGMA busy_timeout").fetchone()[0] == 2000
    assert index.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _names_of(index: sqlite3.Connection, kind: str) -> set[str]:
    """The names of every ``table`` or ``index`` the store defines itself."""
    rows = index.execute(
        "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'", (kind,)
    )
    return {name for (name,) in rows}


def test_open_index_creates_the_tables_and_indexes_the_contract_specifies(
    index: sqlite3.Connection,
) -> None:
    assert _names_of(index, "table") == {
        "sequences",
        "steps",
        "code_entities",
        "code_relations",
        "inferences",
        "agents",
        "step_touches",
        "step_consumes",
        "annotations",
        "epochs",
        "counters",
        "meta",
    }
    assert _names_of(index, "index") >= {
        "steps_by_sequence",
        "steps_by_node",
        "annotations_by_edge",
    }


def _insert_sequence(index: sqlite3.Connection) -> None:
    """Write the one sequence every step in this module hangs off."""
    index.execute(
        "INSERT INTO sequences (conversation_id, session_epoch, prompt_id, agent_id,"
        " project_dir_key, started_at) VALUES ('c1', 0, 'p1', '', 'proj', '2026-09-13T10:00:00Z')"
    )


def _insert_step(index: sqlite3.Connection, *, prompt_id: str = "p1") -> None:
    """Write one step the way phase 1 does — naming no reserved column."""
    index.execute(
        "INSERT INTO steps (dedup_key, conversation_id, session_epoch, prompt_id, agent_id,"
        " position, node_key, activity_class, template, occurred_at)"
        " VALUES ('t1', 'c1', 0, ?, '', 0, 'Inspection/Read', 'Inspection',"
        " 'Read file_path', '2026-09-13T10:00:01Z')",
        (prompt_id,),
    )


def test_the_validity_interval_columns_are_reserved_and_left_unwritten_fr_033(
    index: sqlite3.Connection,
) -> None:
    _insert_sequence(index)
    _insert_step(index)

    stored = index.execute("SELECT valid_from, invalidated_at FROM steps").fetchone()
    assert stored == (None, None)


def test_a_step_whose_sequence_does_not_exist_is_refused(index: sqlite3.Connection) -> None:
    _insert_sequence(index)

    with pytest.raises(sqlite3.IntegrityError):
        _insert_step(index, prompt_id="no-such-prompt")


def test_store_is_stamped_schema_version_2(tmp_path: Path) -> None:
    """The stamp a created store carries, pinned at the literal `2`.

    Spelled out rather than read back from the module's own constant: an
    assertion against the constant moves with it, so it holds whatever the
    build declares and can never report a store stamped at the wrong shape.
    `2` is the version `contracts/graph-schema-v2.md` names and the one the
    migration carries a v1 store forward to.
    """
    path = tmp_path / "episodes.db"
    open_index(path).close()

    reopened = open_index(path)
    try:
        stored = reopened.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    finally:
        reopened.close()
    assert stored == ("2",)


def test_a_store_of_an_unknown_schema_version_is_refused_not_written_to(tmp_path: Path) -> None:
    path = tmp_path / "episodes.db"
    created = open_index(path)
    with created:
        created.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    created.close()

    with pytest.raises(sqlite3.DatabaseError, match="99"):
        open_index(path)


def test_two_connections_writing_at_once_under_wal_both_land(tmp_path: Path) -> None:
    path = tmp_path / "episodes.db"
    first = open_index(path)
    second = open_index(path)
    try:
        with first:
            first.execute(
                "INSERT INTO sequences (conversation_id, session_epoch, prompt_id, agent_id,"
                " project_dir_key, started_at) VALUES"
                " ('c1', 0, 'p1', '', 'proj', '2026-09-13T10:00:00Z')"
            )
            first.execute(
                "INSERT INTO steps (dedup_key, conversation_id, session_epoch, prompt_id,"
                " agent_id, position, node_key, activity_class, template, occurred_at)"
                " VALUES ('t1', 'c1', 0, 'p1', '', 0, 'Inspection/Read', 'Inspection',"
                " 'Read file_path', '2026-09-13T10:00:01Z')"
            )
        with second:
            second.execute(
                "INSERT INTO sequences (conversation_id, session_epoch, prompt_id, agent_id,"
                " project_dir_key, started_at) VALUES"
                " ('c2', 0, 'p2', '', 'proj', '2026-09-13T10:00:00Z')"
            )
            second.execute(
                "INSERT INTO steps (dedup_key, conversation_id, session_epoch, prompt_id,"
                " agent_id, position, node_key, activity_class, template, occurred_at)"
                " VALUES ('t2', 'c2', 0, 'p2', '', 0, 'Inspection/Read', 'Inspection',"
                " 'Read file_path', '2026-09-13T10:00:01Z')"
            )
    finally:
        first.close()
        second.close()

    reopened = open_index(path)
    try:
        dedup_keys = {row[0] for row in reopened.execute("SELECT dedup_key FROM steps")}
    finally:
        reopened.close()
    assert dedup_keys == {"t1", "t2"}


def test_first_run_creates_the_processrecall_directory_the_store_lives_in(tmp_path: Path) -> None:
    absent = tmp_path / ".processrecall"

    open_index(absent / "episodes.db").close()

    assert absent.is_dir()
