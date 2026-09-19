"""The v2 data-model contract: three layers, declared once (FR-015).

The symbolic index's shape, written down in one place. Semantic, episodic and
procedural are declared here as tables and fields, and the SQLite DDL, the
snapshot bodies and the generated contract documents are all rendered from this
declaration — so no field is written by hand in two places and none of the
three can drift from the others.

Every field carries the `reference` flag the routing rule of FR-016 turns on: a
value that refers to another identity in the schema is an edge, a value that
describes the thing itself is a node attribute. In SQLite a reference lives in
a foreign-keyed column, because there the foreign key *is* the edge; the rule
bites at snapshot write, where the served form is the form that gets traversed.

Declaration only — no SQLite, no reader. This module is imported by the hot path,
so it stays a description of the shape and never touches the store; its only I/O
is the `--render` seam, reached solely through `python -m processrecall.graph.schema`.

Example:
    from processrecall.graph.schema import LAYERS

    episodic = next(layer for layer in LAYERS if layer.name == "episodic")
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

#: Stamped into the episodic index's `meta` table and checked on every open. A
#: store at `1` migrates forward once; any other value stays a refusal.
STORE_SCHEMA_VERSION = "2"

#: The version stamp every snapshot body carries. Moves with the store's.
SNAPSHOT_FORMAT = 2

#: Entities carried per procedure in the `precedes_work_on` projection, and
#: pre-computed callers carried per projected entity. The snapshot is read and
#: parsed whole on every guidance call, so these two bounds are the hot path's
#: entire knowledge of the call graph (R17): the renderer never walks a call
#: graph at serve time. If the latency budget fails, these numbers move and the
#: traversal does not move onto the store.
PRECEDES_ENTITIES = 8
CALLERS_PER_ENTITY = 8


class FieldType(StrEnum):
    """What a field holds, in the layer-neutral spelling of the contract.

    `JSON` is a structured body in the served snapshot, which is why it is
    distinct from `TEXT`: relationally there is no such column, and nothing in
    the procedural layer is relational.
    """

    TEXT = "text"
    INTEGER = "integer"
    REAL = "real"
    JSON = "json"


class StepResult(StrEnum):
    """Axis one of a step's outcome: whether the call could do the thing (FR-022)."""

    OK = "ok"
    FAILURE = "failure"


class StepDecision(StrEnum):
    """Axis two: whether the call was allowed to try (FR-022).

    Kept apart from `StepResult` because a refusal is a policy signal and a failure is
    a capability signal; one enumeration over both is what made every step read
    `neutral`.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DecisionSource(StrEnum):
    """Who decided a step's `decision` (FR-008).

    `USER_ABORT` and `USER_REJECT` are the only two that mean a refusal, and they
    arrive on `claude_code.tool_decision` records alone — a reject axis fed from tool
    results would be silently empty.
    """

    CONFIG = "config"
    HOOK = "hook"
    USER_PERMANENT = "user_permanent"
    USER_TEMPORARY = "user_temporary"
    USER_ABORT = "user_abort"
    USER_REJECT = "user_reject"


@dataclass(frozen=True, slots=True)
class Field:
    """One declared field, and where the routing rule sends it.

    Attributes:
        name: The column name in SQLite, and the body key in the snapshot.
        type: One of `FieldType`.
        reference: The table this field refers to an identity in, empty when it
            describes the thing itself. Truthiness is the rule's flag: a
            reference is served as an edge, a plain value as a node attribute.
            Where the referred identity is a composite key, every component of
            it carries the flag.
        primary_key: Whether this field is (part of) the table's identity, as
            data-model.md marks it (`text, PK`). SQLite-neutral: it says what
            the row's key is, not how a column spells that in DDL.
        vocabulary: The values this field may take, empty when it is open. A
            closed vocabulary is part of the contract: the generated document
            lists it, so a reader learns the values from the declaration rather
            than from the writer that happens to fill the column.
    """

    name: str
    type: FieldType
    reference: str = ""
    primary_key: bool = False
    vocabulary: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Table:
    """One named set of fields: a SQLite table, or one shape of snapshot body."""

    name: str
    fields: tuple[Field, ...]


@dataclass(frozen=True, slots=True)
class Layer:
    """One of the three layers, and the shapes it holds.

    Attributes:
        name: `semantic`, `episodic` or `procedural`.
        persisted: Whether the layer has rows of its own. The procedural layer
            does not: a procedure is an aggregation of episodic rows, the rows
            are the record, and the snapshot is a cache of the aggregation.
        tables: The shapes the layer declares, each with its fields.
    """

    name: str
    persisted: bool
    tables: tuple[Table, ...]


def _text(name: str, reference: str = "", primary_key: bool = False) -> Field:
    """A `FieldType.TEXT` field — the common case, spelled once."""
    return Field(name=name, type=FieldType.TEXT, reference=reference, primary_key=primary_key)


def _closed(name: str, vocabulary: type[StrEnum]) -> Field:
    """A `FieldType.TEXT` field that takes one of an enumerated set of values."""
    return Field(name=name, type=FieldType.TEXT, vocabulary=tuple(member.value for member in vocabulary))


def _integer(name: str, reference: str = "") -> Field:
    """A `FieldType.INTEGER` field."""
    return Field(name=name, type=FieldType.INTEGER, reference=reference)


def _json(name: str) -> Field:
    """A `FieldType.JSON` body. Never a reference: a body is not an identity."""
    return Field(name=name, type=FieldType.JSON)


def _real(name: str) -> Field:
    """A `FieldType.REAL` field."""
    return Field(name=name, type=FieldType.REAL)


#: Layer 1. Code entities and the relations between them, derived off the hot
#: path for the files the episodic layer says work touched, and incremental on
#: the content fingerprint (R15).
_SEMANTIC_TABLES = (
    Table(
        name="code_entities",
        fields=(
            _text("entity_key", primary_key=True),
            _text("kind"),
            _text("extension"),
            _text("language"),
            _text("fingerprint"),
            _text("first_seen"),
            _text("last_seen"),
            _integer("support"),
            _integer("start_line"),
            _integer("end_line"),
        ),
    ),
    Table(
        name="code_relations",
        fields=(
            _text("source_key", reference="code_entities"),
            _text("relation"),
            _text("target_key", reference="code_entities"),
            _text("target_name"),
            _integer("sites"),
        ),
    ),
)


#: Layer 2. What happened. Every field this feature adds is nullable and
#: nothing is backfilled (R14): null here means the hook captured the step
#: before telemetry existed and genuinely did not know.
_EPISODIC_TABLES = (
    Table(
        name="sequences",
        fields=(
            _text("conversation_id"),
            _integer("session_epoch"),
            _text("prompt_id"),
            _text("agent_id", reference="agents"),
            _text("project_dir_key"),
            _text("process_type"),
            _text("process_type_source"),
            _text("status"),
            _text("derived_outcome"),
            _text("declared_outcome"),
            _text("started_at"),
            _text("ended_at"),
            _integer("prompt_length"),
            _text("command_name"),
            _text("command_source"),
            _text("workflow_run_id"),
            _text("workflow_name"),
            _text("app_version"),
            _text("head_revision"),
            _text("head_branch"),
            _text("source"),
        ),
    ),
    Table(
        name="steps",
        fields=(
            _integer("step_id"),
            _text("dedup_key"),
            _text("conversation_id", reference="sequences"),
            _integer("session_epoch", reference="sequences"),
            _text("prompt_id", reference="sequences"),
            _text("agent_id", reference="agents"),
            _integer("position"),
            _text("node_key"),
            _text("activity_class"),
            _text("program"),
            _text("template"),
            _text("files"),
            _text("result_snippet"),
            # The v1 collapsed column: kept readable for stores still on it, migrated
            # forward onto `result`/`decision` by FR-029, never written by v2 code.
            _text("outcome"),
            _text("record_ref"),
            _text("occurred_at"),
            _text("rationale_label"),
            _text("symbol_ref"),
            _text("valid_from"),
            _text("invalidated_at"),
            _closed("result", StepResult),
            _text("kind"),
            _closed("decision", StepDecision),
            _closed("decision_source", DecisionSource),
            _integer("duration_ms"),
            _text("error_type"),
            _integer("input_size_bytes"),
            _integer("result_size_bytes"),
            _text("tool_source"),
            _text("source"),
            _text("recorded_at"),
        ),
    ),
    Table(
        name="inferences",
        fields=(
            _text("inference_id", primary_key=True),
            _text("sequence_key", reference="sequences"),
            _text("model"),
            _integer("input_tokens"),
            _integer("output_tokens"),
            _integer("cache_read_tokens"),
            _integer("cache_creation_tokens"),
            _integer("cost_micros"),
            _integer("duration_ms"),
            _text("speed"),
            _text("effort"),
            _text("query_source"),
            _text("outcome"),
            _integer("status_code"),
            _integer("attempt"),
            _text("occurred_at"),
            _integer("first_content_ms"),
            _text("stop_reason"),
            _text("error_class"),
        ),
    ),
    Table(
        name="agents",
        fields=(
            _text("agent_id", primary_key=True),
            _text("kind"),
            _text("agent_type"),
            _text("agent_source"),
            _integer("is_built_in"),
            _integer("is_async"),
            _text("workflow_run_id"),
            _text("workflow_name"),
            _text("parent_agent_id", reference="agents"),
            _text("first_seen"),
            _text("last_seen"),
        ),
    ),
    Table(
        name="step_touches",
        fields=(
            _integer("step_id", reference="steps"),
            _text("entity_key", reference="code_entities"),
            _text("mode"),
            _text("resolution"),
        ),
    ),
    Table(
        name="step_consumes",
        fields=(
            _integer("step_id", reference="steps"),
            _text("inference_id", reference="inferences"),
            _text("link"),
        ),
    ),
)

#: Layer 3. What to do next: the fold over the episodic layer, served from the
#: snapshot and stored nowhere else.
_PROCEDURAL_TABLES = (
    Table(
        name="procedures",
        fields=(
            _text("key"),
            _text("level"),
            _text("activity_class"),
            _text("program"),
            _text("file_ext"),
            _text("node_type"),
            _json("templates"),
            _integer("support"),
            _json("outcome_counts"),
            _integer("median_cost_micros"),
            _integer("median_duration_ms"),
            _text("last_seen"),
            _real("activation"),
            # The routing rule would make this an edge to step identities; data-model.md
            # calls the JSON-attribute form the one violation of that rule the model
            # still contains (data-model.md:249-250), matching the transition's spelling.
            _json("supporting_steps"),
        ),
    ),
    Table(
        name="subsumes",
        fields=(
            _text("parent", reference="procedures"),
            _text("child", reference="procedures"),
        ),
    ),
    Table(
        name="transitions",
        fields=(
            _text("edge_key"),
            _text("source", reference="procedures"),
            _text("target", reference="procedures"),
            _json("condition"),
            _integer("support"),
            _real("weight"),
            _real("dependency_measure"),
            _real("lift"),
            _real("reported_rate_lower"),
            # The routing rule would make this an edge to step identities; data-model.md
            # calls the JSON-attribute form the one violation of that rule the model
            # still contains (data-model.md:249-250).
            _json("supporting_steps"),
            _json("outcome_counts"),
            _text("last_seen"),
            _json("guidance"),
            _json("pitfalls"),
            _json("annotations"),
            _text("origin"),
            _text("schema_version"),
            _text("valid_from"),
            _text("invalidated_at"),
        ),
    ),
    Table(
        name="precedes_work_on",
        fields=(
            _text("source", reference="procedures"),
            _text("entity_key", reference="code_entities"),
            _integer("support"),
            _json("callers"),
        ),
    ),
)

#: The three layers, bottom to top: what the code is, what happened, what to do
#: next.
LAYERS = (
    Layer(name="semantic", persisted=True, tables=_SEMANTIC_TABLES),
    Layer(name="episodic", persisted=True, tables=_EPISODIC_TABLES),
    Layer(name="procedural", persisted=False, tables=_PROCEDURAL_TABLES),
)


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """One OTel log record the episodic layer is sourced from (FR-001, FR-002).

    Attributes:
        name: The `event.name` the collector writes.
        becomes: The episodic shape the record turns into.
        note: What a reader of the contract has to know about the record.
    """

    name: str
    becomes: str
    note: str = ""


#: The records consumed, in the order the generated contract lists them. Nothing
#: else is read: an unlisted record has no row to become.
CONSUMED_RECORDS = (
    TelemetryRecord("claude_code.user_prompt", "a sequence", "opens the turn, carries `prompt.id`"),
    TelemetryRecord("claude_code.api_request", "an inference"),
    TelemetryRecord(
        "claude_code.api_error",
        "an inference that failed",
        "the terminal signal; retries are not separate events",
    ),
    TelemetryRecord(
        "claude_code.api_refusal",
        "an inference that was refused",
        "refusals arrive on a successful stream and never fire `api_error`",
    ),
    TelemetryRecord(
        "claude_code.tool_result", "a step that ran", "not emitted for a rejected call"
    ),
    TelemetryRecord(
        "claude_code.tool_decision",
        "a step's permission outcome",
        "the **only** record a rejected call produces",
    ),
    TelemetryRecord(
        "claude_code.subagent_completed",
        "an agent",
        "the only events-mode record naming an agent type",
    ),
)


def _table_row(cells: tuple[str, ...]) -> str:
    """One Markdown table row."""
    return f"| {' | '.join(cells)} |"


def _markdown_table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    """A Markdown table: header, separator, one row per tuple."""
    separator = _table_row(tuple("---" for _ in headers))
    return "\n".join((_table_row(headers), separator, *(_table_row(row) for row in rows)))


def _records_consumed() -> str:
    """The body of `contracts/telemetry-records.md` (FR-001, FR-002)."""
    rows = tuple((f"`{record.name}`", record.becomes, record.note) for record in CONSUMED_RECORDS)
    return "\n".join(
        (
            "## Records consumed",
            "",
            f"{len(CONSUMED_RECORDS)} records, and no others. Metrics are not consumed.",
            "",
            _markdown_table(("Record", "What it becomes", "Note"), rows),
        )
    )


def _edge_targets(table: Table) -> str:
    """The tables a table's reference fields point at, in declaration order."""
    targets = dict.fromkeys(field.reference for field in table.fields if field.reference)
    return ", ".join(f"`{target}`" for target in targets) or "none"


def _field_names(table: Table) -> str:
    """The table's fields, in declaration order, so a rename or swap shows in the render."""
    return ", ".join(f"`{field.name}`" for field in table.fields)


def _tables_and_edges() -> str:
    """The body of `contracts/graph-schema-v2.md`: where the routing rule sends each table."""
    rows = tuple(
        (layer.name, f"`{table.name}`", _field_names(table), _edge_targets(table))
        for layer in LAYERS
        for table in layer.tables
    )
    return "\n".join(
        (
            "## Declared tables and their edges",
            "",
            "A field declaring a target is an edge to that table; every other field is a node",
            "attribute of the table it is declared on.",
            "",
            _markdown_table(("Layer", "Table", "Fields", "Edges to"), rows),
        )
    )


def _closed_vocabularies() -> str:
    """The closed vocabularies of `contracts/graph-schema-v2.md`.

    What values a constrained field may take, so the contract carries the vocabulary
    and not only the column (FR-022, FR-008).
    """
    rows = tuple(
        (
            f"`{table.name}`",
            f"`{field.name}`",
            ", ".join(f"`{value}`" for value in field.vocabulary),
        )
        for layer in LAYERS
        for table in layer.tables
        for field in table.fields
        if field.vocabulary
    )
    return "\n".join(
        (
            "## Closed vocabularies",
            "",
            "A field listed here takes one of these values and no other; every field not",
            "listed is open.",
            "",
            _markdown_table(("Table", "Field", "Values"), rows),
        )
    )


@dataclass(frozen=True, slots=True)
class GeneratedBody:
    """One contract document's generated region, markers included.

    Attributes:
        document: The file name under `contracts/` the body belongs to.
        body: The region, opening and closing marker included, so that containment in
            the document is the agreement check FR-030 asks for.
    """

    document: str
    body: str


def _generated(document: str, body: str) -> GeneratedBody:
    """Wrap a rendered body in the markers that delimit it in its document."""
    marker = f"<!-- generated: {document} by python -m processrecall.graph.schema --render -->"
    region = f"{marker}\n\n{body}\n\n<!-- /generated -->"
    return GeneratedBody(document=document, body=region)


def render_bodies() -> tuple[GeneratedBody, ...]:
    """Every generated contract region, rendered from the declaration (FR-030)."""
    return (
        _generated(
            "graph-schema-v2.md",
            f"{_tables_and_edges()}\n\n{_closed_vocabularies()}",
        ),
        _generated("telemetry-records.md", _records_consumed()),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m processrecall.graph.schema --render`: the declaration as contract."""
    from argparse import ArgumentParser

    parser = ArgumentParser(prog="python -m processrecall.graph.schema", description=__doc__)
    parser.add_argument(
        "--render",
        action="store_true",
        help="write the generated contract bodies to stdout",
    )
    if not parser.parse_args(argv).render:
        parser.error("nothing to render: pass --render")
    for generated in render_bodies():
        print(generated.body)
    return 0


if __name__ == "__main__":  # pragma: no cover - the module's command-line seam
    raise SystemExit(main())
