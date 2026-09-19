"""The v2 declaration: three layers, the stamps, and the projection bounds."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.schema import (
    CALLERS_PER_ENTITY,
    CONSUMED_RECORDS,
    LAYERS,
    PRECEDES_ENTITIES,
    SNAPSHOT_FORMAT,
    STORE_SCHEMA_VERSION,
    FieldType,
    Table,
    main,
    render_bodies,
)

CONTRACTS = Path(__file__).resolve().parents[2] / ".claude/specs/007-otel-graph-schema-v2/contracts"


def test_declares_the_three_layers_in_stack_order() -> None:
    assert tuple(layer.name for layer in LAYERS) == ("semantic", "episodic", "procedural")


def test_only_the_procedural_layer_is_unpersisted() -> None:
    """A procedure is an aggregation of rows; the rows are the record (FR-015)."""
    assert {layer.name: layer.persisted for layer in LAYERS} == {
        "semantic": True,
        "episodic": True,
        "procedural": False,
    }


def test_both_stamps_moved_to_v2() -> None:
    assert STORE_SCHEMA_VERSION == "2"
    assert SNAPSHOT_FORMAT == 2


def test_projection_bounds_keep_the_snapshot_servable() -> None:
    assert (PRECEDES_ENTITIES, CALLERS_PER_ENTITY) == (8, 8)


def _declared_tables() -> dict[str, Table]:
    return {table.name: table for layer in LAYERS for table in layer.tables}


def test_declares_the_tables_of_each_layer() -> None:
    assert {layer.name: tuple(table.name for table in layer.tables) for layer in LAYERS} == {
        "semantic": ("code_entities", "code_relations"),
        "episodic": (
            "sequences",
            "steps",
            "inferences",
            "agents",
            "step_touches",
            "step_consumes",
        ),
        "procedural": ("procedures", "subsumes", "transitions", "precedes_work_on"),
    }


def test_every_reference_names_a_declared_table() -> None:
    """The routing rule needs the target resolvable, or an edge points nowhere (FR-016)."""
    declared = _declared_tables()
    for table in declared.values():
        for field in table.fields:
            if field.reference:
                assert field.reference in declared, f"{table.name}.{field.name}"


def test_honesty_fields_are_declared_as_plain_values() -> None:
    """A degradation must be recordable, and recorded on the thing it degrades."""
    declared = _declared_tables()
    honesty = {
        "step_touches": "resolution",
        "step_consumes": "link",
        "steps": "source",
        "sequences": "app_version",
    }
    for table_name, field_name in honesty.items():
        field = next(f for f in declared[table_name].fields if f.name == field_name)
        assert not field.reference


def test_step_declares_result_and_decision_as_two_axes() -> None:
    """Result says whether the call could act, decision whether it was allowed to, and
    each takes its own closed vocabulary (FR-022, FR-008).
    """
    declared = {field.name: field for field in _declared_tables()["steps"].fields}
    assert declared["result"].vocabulary == ("ok", "failure")
    assert declared["decision"].vocabulary == ("accepted", "rejected")
    assert declared["decision_source"].vocabulary == (
        "config",
        "hook",
        "user_permanent",
        "user_temporary",
        "user_abort",
        "user_reject",
    )
    shared_values = set(declared["result"].vocabulary) & set(declared["decision"].vocabulary)
    assert not shared_values, "a value on both axes is the collapse FR-022 forbids"


def test_procedure_declares_the_activation_that_orders_candidates() -> None:
    """Candidate ordering reads a recency-weighted activation off the procedure, so it is
    folded onto the node beside the raw support it outranks (FR-024, FR-039).
    """
    declared = {field.name: field for field in _declared_tables()["procedures"].fields}
    assert declared["activation"].type == FieldType.REAL
    assert declared["support"].type == FieldType.INTEGER


def test_transition_declares_dependency_measure_lift_and_lower_bound() -> None:
    """The three ranking statistics are real-valued attributes of the transition itself:
    a direction, a lift over the target's base rate, and the only rate a statement may
    say out loud (FR-025, FR-040, FR-041).
    """
    declared = {field.name: field for field in _declared_tables()["transitions"].fields}
    assert {
        "dependency_measure": declared["dependency_measure"].type,
        "lift": declared["lift"].type,
        "reported_rate_lower": declared["reported_rate_lower"].type,
    } == {
        "dependency_measure": FieldType.REAL,
        "lift": FieldType.REAL,
        "reported_rate_lower": FieldType.REAL,
    }


def test_render_emits_the_consumed_records_body(capsys: pytest.CaptureFixture[str]) -> None:
    """`--render` writes the telemetry contract's generated body (FR-001)."""
    assert main(["--render"]) == 0
    rendered = capsys.readouterr().out
    assert "## Records consumed" in rendered
    for record in CONSUMED_RECORDS:
        assert f"`{record.name}`" in rendered


def test_main_without_render_refuses_with_a_nonzero_exit() -> None:
    """No flag, nothing to render: the CLI refuses rather than exiting quietly."""
    with pytest.raises(SystemExit):
        main([])


def test_generated_contracts_match_declaration() -> None:
    """Re-rendering rewrites each contract byte for byte, so a hand edit inside the
    markers is a failing test, not a doc change (FR-001, FR-030, SC-001).
    """
    bodies = render_bodies()
    assert {generated.document for generated in bodies} == {
        "graph-schema-v2.md",
        "telemetry-records.md",
    }
    for generated in bodies:
        document = (CONTRACTS / generated.document).read_text(encoding="utf-8")
        opening, *_, closing = generated.body.splitlines()
        start = document.index(opening)
        end = document.index(closing, start) + len(closing)
        assert document[start:end] == generated.body, generated.document


#: T009 also asks that every attribute a reader binds has a declared origin row
#: (SC-001). `processrecall/trajectory/telemetry.py` is that reader, and T013 is what
#: creates it — nothing in the codebase binds an OTel attribute yet, so there is
#: nothing to check against. That half of the coverage check belongs with T013,
#: where the reader and the assertion can land together.


#: What the store itself must hold: every table of a layer that has rows of its own.
PERSISTED_TABLES = tuple(table for layer in LAYERS if layer.persisted for table in layer.tables)

#: The expected SQLite type per `FieldType`, spelled independently of
#: `ddl._SQLITE_TYPES`: comparing the store against the renderer's own mapping
#: would let a wrong entry there pass (FR-030 wants store and declaration
#: compared, not the renderer compared with itself).
_EXPECTED_SQLITE_TYPES: Mapping[FieldType, str] = {
    FieldType.TEXT: "TEXT",
    FieldType.INTEGER: "INTEGER",
    FieldType.REAL: "REAL",
    FieldType.JSON: "TEXT",
}


def _stored_columns(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    """Each column the store holds for *table*, and the type it was declared with."""
    return {str(row[1]): str(row[2]) for row in connection.execute(f"PRAGMA table_info({table})")}


def test_store_columns_match_declaration(tmp_path: Path) -> None:
    """A store opened fresh is the declaration's shape, field by field (FR-030, SC-001)."""
    with closing(open_index(tmp_path / "episodes.db")) as connection:
        for table in PERSISTED_TABLES:
            stored = _stored_columns(connection, table.name)
            for field in table.fields:
                expected = _EXPECTED_SQLITE_TYPES[field.type]
                assert stored.get(field.name) == expected, (table.name, field.name)
            undeclared = set(stored) - {field.name for field in table.fields}
            assert not undeclared, (table.name, sorted(undeclared))
