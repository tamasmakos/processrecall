"""The forward migration: a populated v1 store becomes a v2 store (FR-029)."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.migrate import MIGRATION_COUNTER, migrate_forward
from processrecall.graph.schema import LAYERS, STORE_SCHEMA_VERSION
from processrecall.graph.store import SQLiteEpisodicStore

V1_STORE = Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "v1.db"

#: What the migration has to leave behind: every table of a persisted layer, with
#: every field the declaration gives it.
DECLARED_TABLES = tuple(table for layer in LAYERS if layer.persisted for table in layer.tables)

@pytest.fixture
def v1_copy(tmp_path: Path) -> Path:
    """A scratch copy of the frozen v1 store, since the migration writes."""
    copy = tmp_path / "episodes.db"
    shutil.copyfile(V1_STORE, copy)
    return copy


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _primary_key(connection: sqlite3.Connection, table: str) -> set[str]:
    """The columns `PRAGMA table_info` marks as (part of) *table*'s key."""
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})") if row[5]}


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_v1_store_migrates_forward_in_one_transaction(v1_copy: Path) -> None:
    with closing(sqlite3.connect(v1_copy)) as connection:
        sequence_count = _count(connection, "sequences")
        step_count = _count(connection, "steps")

        assert migrate_forward(connection) is True
        assert not connection.in_transaction

        stamp = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert stamp == (STORE_SCHEMA_VERSION,)

        for table in DECLARED_TABLES:
            present = _columns(connection, table.name)
            assert present, table.name
            missing = {field.name for field in table.fields} - present
            assert not missing, (table.name, sorted(missing))

            declared_key = {field.name for field in table.fields if field.primary_key}
            if declared_key:
                assert _primary_key(connection, table.name) == declared_key, table.name

        bumped = connection.execute(
            "SELECT value FROM counters WHERE name = ?", (MIGRATION_COUNTER,)
        ).fetchone()
        assert bumped == (1,)

        assert _count(connection, "sequences") == sequence_count
        assert _count(connection, "steps") == step_count


#: A table the migration creates, denied so that the column additions and the
#: earlier creations are already applied when it is interrupted, giving the
#: rollback something to undo. Named literally and checked below, rather than
#: taken from declaration order, so re-ordering `LAYERS` cannot silently change
#: what the test exercises.
INTERRUPTED_TABLE = "step_consumes"


def _deny_creating(
    table: str,
) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    """An authorizer that lets the migration run until it creates *table*."""

    def authorize(
        action: int,
        argument: str | None,
        _second: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_CREATE_TABLE and argument == table:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorize


def test_an_interrupted_migration_leaves_a_readable_v1_store(v1_copy: Path) -> None:
    assert INTERRUPTED_TABLE in {table.name for table in DECLARED_TABLES}

    with closing(sqlite3.connect(v1_copy)) as connection:
        v1_tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert INTERRUPTED_TABLE not in v1_tables

        sequence_count = _count(connection, "sequences")
        step_count = _count(connection, "steps")

        connection.set_authorizer(_deny_creating(INTERRUPTED_TABLE))
        with pytest.raises(sqlite3.DatabaseError):
            migrate_forward(connection)
        connection.set_authorizer(None)

        stamp = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert stamp == ("1",)
        assert not _columns(connection, INTERRUPTED_TABLE)
        assert "source" not in _columns(connection, "sequences")
        assert (
            connection.execute(
                "SELECT value FROM counters WHERE name = ?", (MIGRATION_COUNTER,)
            ).fetchone()
            is None
        )
        assert _count(connection, "sequences") == sequence_count
        assert _count(connection, "steps") == step_count


def test_a_migrated_store_is_not_migrated_again(v1_copy: Path) -> None:
    """Forward only, once per store: every later open finds it stamped `2`."""
    with closing(sqlite3.connect(v1_copy)) as connection:
        assert migrate_forward(connection) is True
        assert migrate_forward(connection) is False
        assert connection.execute(
            "SELECT value FROM counters WHERE name = ?", (MIGRATION_COUNTER,)
        ).fetchone() == (1,)


#: The columns this feature adds to `steps`, checked against the fixture below
#: rather than derived from the declaration, so a column that stops being an
#: addition stops being asserted about here.
ADDED_STEP_FIELDS = (
    "result",
    "kind",
    "decision",
    "decision_source",
    "duration_ms",
    "error_type",
    "input_size_bytes",
    "result_size_bytes",
    "tool_source",
    "source",
)


def test_migrated_store_keeps_every_step_readable(v1_copy: Path) -> None:
    """Every v1 step reads back through the v2 store, the additions null (SC-013)."""
    with closing(sqlite3.connect(v1_copy)) as connection:
        recorded = _count(connection, "steps")
        assert recorded, V1_STORE
        assert not set(ADDED_STEP_FIELDS) & _columns(connection, "steps")

    with closing(open_index(v1_copy)) as connection:
        steps = tuple(SQLiteEpisodicStore(connection).iter_steps())

    assert len(steps) == recorded
    for step in steps:
        added = {field: getattr(step, field) for field in ADDED_STEP_FIELDS}
        assert all(value is None for value in added.values()), (step.dedup_key, added)
