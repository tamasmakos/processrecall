"""Forward-only v1 → v2: one store, one transaction, no way back (FR-029).

A store written at version `1` holds history that is not reconstructible — the
steps of turns already taken — so the move to `2` widens the shape and touches
no row: every column the declaration adds is nullable, and null means the
harness recorded that step before telemetry existed (R14).

The whole move is one transaction. Either the store comes out stamped `2` with
the columns and the tables the declaration names, or it comes out exactly the v1
store it went in as, which v1 code still reads. There is no downgrade and no
dual-read path: a store this migration does not recognise is left for
`open_index` to refuse.

The statements are rendered from `graph/schema.py` via `graph/ddl.py`, so a field
added to the declaration migrates an existing store without being spelled a
second time here.

`episodic.open_index` calls `migrate_forward` when it finds a store stamped `1`,
and `align_to_declaration` on every other open: a store already at `2` gains
whatever the declaration has added since it was created, without being counted
as a migration.

Example:
    from processrecall.graph.migrate import migrate_forward

    migrated = migrate_forward(connection)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from processrecall.graph.ddl import column_definition, create_table_statement
from processrecall.graph.schema import LAYERS, STORE_SCHEMA_VERSION, Table

#: Bumped once per store carried forward (contracts/counters.md). In the same
#: transaction as the move, so a rolled-back migration is not counted as one.
MIGRATION_COUNTER = "migration_v1_v2"

#: The one stamp this migration reads. Forward only, so a store at any other
#: version — including one already at `2` — is left untouched.
MIGRATABLE_VERSION = "1"


def _present_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """The columns *table* already has, empty when the store has no such table."""
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _statements_for(connection: sqlite3.Connection, table: Table) -> Iterator[str]:
    """What *table* needs to match the declaration: a creation, or the missing columns."""
    if not (present := _present_columns(connection, table.name)):
        yield create_table_statement(table)
        return
    for field in table.fields:
        if field.name not in present:
            yield f"ALTER TABLE {table.name} ADD COLUMN {column_definition(field)}"


def _forward_statements(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Every statement that lifts *connection*'s store to the declared shape."""
    return tuple(
        statement
        for layer in LAYERS
        if layer.persisted
        for table in layer.tables
        for statement in _statements_for(connection, table)
    )


def align_to_declaration(connection: sqlite3.Connection) -> None:
    """Bring *connection*'s store to the declared shape, in the caller's transaction.

    Idempotent: a table already present and a column already added yield no
    statement, so every open after the first writes nothing.
    """
    for statement in _forward_statements(connection):
        connection.execute(statement)


def _stamped_version(connection: sqlite3.Connection) -> str | None:
    """The version stamped in `meta`, or `None` when nothing is stamped."""
    row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return None if row is None else str(row[0])


def migrate_forward(connection: sqlite3.Connection) -> bool:
    """Migrate an already-opened store from v1 to v2, and say whether it moved.

    Reads the stamp inside the transaction it writes under, so two sessions
    opening the same store at once cannot both find it at `1`.

    Args:
        connection: An open connection to the episodic index.

    Returns:
        Whether this call migrated the store. `False` is the ordinary answer:
        every open after the first finds it stamped `2` already.

    Raises:
        BaseException: Whatever interrupted the move, re-raised after the
            transaction is rolled back. The store is then still the v1 store it
            was, stamp included.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        if _stamped_version(connection) != MIGRATABLE_VERSION:
            connection.rollback()
            return False
        align_to_declaration(connection)
        connection.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (STORE_SCHEMA_VERSION,),
        )
        connection.execute(
            "INSERT INTO counters (name, value) VALUES (?, 1)"
            " ON CONFLICT (name) DO UPDATE SET value = value + 1",
            (MIGRATION_COUNTER,),
        )
    except BaseException:
        connection.rollback()
        raise
    connection.commit()
    return True
