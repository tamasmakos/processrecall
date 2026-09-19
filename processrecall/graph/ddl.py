"""SQLite DDL rendered from the declaration in `graph/schema.py`.

Kept apart from `schema.py`, whose docstring keeps it free of SQLite: the
field-to-column mapping lives here once, so a store built fresh and a store
carried forward by `migrate.py` render the same declaration instead of each
spelling it by hand.

Example:
    from processrecall.graph.ddl import create_table_statement

    statement = create_table_statement(table)
"""

from __future__ import annotations

from collections.abc import Mapping

from processrecall.graph.schema import Field, FieldType, Table

#: How a declared field is spelled as a column. `FieldType.JSON` is a body, and
#: SQLite has no column for one: it is stored as the text it is serialised to.
_SQLITE_TYPES: Mapping[FieldType, str] = {
    FieldType.TEXT: "TEXT",
    FieldType.INTEGER: "INTEGER",
    FieldType.REAL: "REAL",
    FieldType.JSON: "TEXT",
}


def column_definition(field: Field) -> str:
    """One column definition. Nullable and undefaulted: nothing is backfilled."""
    return f"{field.name} {_SQLITE_TYPES[field.type]}"


def create_table_statement(table: Table) -> str:
    """`CREATE TABLE` for *table*, with the primary key the declaration names."""
    columns = [column_definition(field) for field in table.fields]
    if primary_key := tuple(field.name for field in table.fields if field.primary_key):
        columns.append(f"PRIMARY KEY ({', '.join(primary_key)})")
    return f"CREATE TABLE {table.name} ({', '.join(columns)})"
