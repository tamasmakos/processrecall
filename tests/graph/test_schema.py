"""The v2 declaration: three layers, the stamps, and the projection bounds."""

from __future__ import annotations

from pathlib import Path

import pytest

from processrecall.graph.schema import (
    CALLERS_PER_ENTITY,
    CONSUMED_RECORDS,
    LAYERS,
    PRECEDES_ENTITIES,
    SNAPSHOT_FORMAT,
    STORE_SCHEMA_VERSION,
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


def test_every_rendered_body_is_the_generated_region_of_its_contract() -> None:
    """A hand edit inside the markers is a failing test, not a doc change (FR-030)."""
    for generated in render_bodies():
        document = (CONTRACTS / generated.document).read_text(encoding="utf-8")
        assert generated.body in document, generated.document
