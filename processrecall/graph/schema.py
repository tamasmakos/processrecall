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

from processrecall.trajectory.records import CONSUMED_EVENT_NAMES

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

#: Hops the `ppr_neighbourhood` walk takes out of its seeds over the snapshot's
#: own adjacency (FR-031). Declared here beside the projection bounds because it
#: bounds the same read from the other side: those two say how much adjacency a
#: snapshot carries, this one says how far a traversal follows it. If the latency
#: budget fails, this number moves and the walk does not move onto the store.
PPR_ITERATIONS = 3


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


class CaptureSource(StrEnum):
    """Which capture path wrote a step or a sequence (FR-007).

    An honesty field rather than a declared closed set: it says where the row
    came from, so a hook-only row is not read as a telemetry observation.
    ``BOTH`` is what deduplication leaves behind when telemetry and the hook
    both saw the row, and ``None`` is a row from before telemetry existed,
    which genuinely does not know (R14).
    """

    TELEMETRY = "telemetry"
    HOOK = "hook"
    BOTH = "both"


class ProvenanceTerm(StrEnum):
    """The closed set of W3C PROV-O terms the declaration may cite (FR-045).

    Every member is one PROV-O itself defines; a term that cannot be verified against
    the vocabulary's live documentation is dropped rather than guessed.
    """

    ACTIVITY = "prov:Activity"
    AGENT = "prov:Agent"
    ENTITY = "prov:Entity"
    PLAN = "prov:Plan"
    SPECIALIZATION_OF = "prov:specializationOf"
    USED = "prov:used"
    WAS_GENERATED_BY = "prov:wasGeneratedBy"
    WAS_INFLUENCED_BY = "prov:wasInfluencedBy"
    WAS_INFORMED_BY = "prov:wasInformedBy"


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
        projected: Whether the served snapshot may carry this field. A stored-only
            field is held in the store and read from it alone: projected into the
            snapshot it would make a from-scratch rebuild differ from an
            incremental derivation (FR-044, FR-028).
        genai_attribute: The OpenTelemetry generative-AI attribute this field
            corresponds to, empty when that convention defines none for it
            (FR-045). Documentary, like `Table.provenance`: an attribute name that
            cannot be verified against the convention's live documentation is
            dropped rather than guessed.
    """

    name: str
    type: FieldType
    reference: str = ""
    primary_key: bool = False
    vocabulary: tuple[str, ...] = ()
    projected: bool = True
    genai_attribute: str = ""


@dataclass(frozen=True, slots=True)
class Table:
    """One named set of fields: a SQLite table, or one shape of snapshot body.

    Attributes:
        name: The table name in SQLite, and the shape's name in the contract.
        fields: The fields the shape declares, in declaration order.
        provenance: The PROV-O term this node or edge type corresponds to (FR-045).
            Documentary alignment only: the store stays a property graph and nothing
            here obliges a triple store. Every term is one PROV-O itself defines —
            a term that cannot be verified against the vocabulary's live
            documentation is dropped rather than guessed.
    """

    name: str
    fields: tuple[Field, ...]
    provenance: ProvenanceTerm


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


def _text(
    name: str, reference: str = "", primary_key: bool = False, genai_attribute: str = ""
) -> Field:
    """A `FieldType.TEXT` field — the common case, spelled once."""
    return Field(
        name=name,
        type=FieldType.TEXT,
        reference=reference,
        primary_key=primary_key,
        genai_attribute=genai_attribute,
    )


def _closed(name: str, vocabulary: type[StrEnum]) -> Field:
    """A `FieldType.TEXT` field that takes one of an enumerated set of values."""
    return Field(
        name=name, type=FieldType.TEXT, vocabulary=tuple(member.value for member in vocabulary)
    )


def _stored_only(name: str) -> Field:
    """A `FieldType.TEXT` field the store holds and the snapshot never carries."""
    return Field(name=name, type=FieldType.TEXT, projected=False)


def _integer(name: str, reference: str = "", genai_attribute: str = "") -> Field:
    """A `FieldType.INTEGER` field."""
    return Field(
        name=name, type=FieldType.INTEGER, reference=reference, genai_attribute=genai_attribute
    )


def _json(name: str, reference: str = "") -> Field:
    """A `FieldType.JSON` body: a structured value, or the identities it names.

    A reference is only meaningful on the unpersisted procedural layer: SQLite
    has no column for a JSON body (`graph/ddl.py` renders it as plain `TEXT`),
    so a persisted table declaring one would silently lose the edge.
    """
    return Field(name=name, type=FieldType.JSON, reference=reference)


def _real(name: str) -> Field:
    """A `FieldType.REAL` field."""
    return Field(name=name, type=FieldType.REAL)


#: Layer 1. Code entities and the relations between them, derived off the hot
#: path for the files the episodic layer says work touched, and incremental on
#: the content fingerprint (R15).
_SEMANTIC_TABLES = (
    Table(
        name="code_entities",
        provenance=ProvenanceTerm.ENTITY,
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
        provenance=ProvenanceTerm.WAS_INFLUENCED_BY,
        fields=(
            _text("source_key", reference="code_entities"),
            _text("relation"),
            _text("target_key", reference="code_entities"),
            _text("target_name"),
            _integer("sites"),
        ),
    ),
)


def line_range(start_line: int | None, end_line: int | None) -> tuple[int, int] | None:
    """The bounds `code_entities` carries for a symbol, or `None` for a file (FR-017).

    Refuses a half-set pair: one bound alone encloses nothing, so a touched edge
    resolved against it would answer `file` for an edit inside the symbol (FR-046).
    Checked here, on the declaration, and called by `graph/semantic.py`'s code
    entity, which wires a parsed symbol's bounds through it on the way into the row.

    Raises:
        ValueError: *start_line* and *end_line* disagree on whether the symbol
            has bounds at all — one is set and the other is `None`.
    """
    if start_line is None and end_line is None:
        return None
    if start_line is None or end_line is None:
        raise ValueError(
            f"line range ({start_line}, {end_line}): a symbol declares both bounds or "
            "neither, because the pair is what a touched position is resolved against"
        )
    return start_line, end_line


#: Layer 2. What happened. Every field this feature adds is nullable and
#: nothing is backfilled (R14): null here means the hook captured the step
#: before telemetry existed and genuinely did not know.
_EPISODIC_TABLES = (
    Table(
        name="sequences",
        provenance=ProvenanceTerm.ACTIVITY,
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
        provenance=ProvenanceTerm.ACTIVITY,
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
            # The time the memory wrote the row, beside the time the thing happened:
            # the pair is what lets a rebuild reconstruct what the graph believed at a
            # past time (FR-044).
            _stored_only("recorded_at"),
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
        ),
    ),
    Table(
        name="inferences",
        provenance=ProvenanceTerm.ACTIVITY,
        fields=(
            _text("inference_id", primary_key=True),
            _text("sequence_key", reference="sequences"),
            # The response-side reading: this column is filled once the call has
            # returned, from the model that actually served it, not the one requested.
            _text("model", genai_attribute="gen_ai.response.model"),
            _integer("input_tokens", genai_attribute="gen_ai.usage.input_tokens"),
            _integer("output_tokens", genai_attribute="gen_ai.usage.output_tokens"),
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
            # The time the memory wrote the row, beside the time the thing happened,
            # for the same reason as `steps` (FR-044).
            _stored_only("recorded_at"),
            _integer("first_content_ms"),
            _text("stop_reason", genai_attribute="gen_ai.response.finish_reasons"),
            _text("error_class"),
        ),
    ),
    Table(
        name="agents",
        provenance=ProvenanceTerm.AGENT,
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
    # A row whose `mode` is `modified` reads as `prov:wasGeneratedBy` instead: the
    # entity is the step's output there, not its input (see `_provenance_alignment`).
    Table(
        name="step_touches",
        provenance=ProvenanceTerm.USED,
        fields=(
            _integer("step_id", reference="steps"),
            _text("entity_key", reference="code_entities"),
            _text("mode"),
            _text("resolution"),
        ),
    ),
    Table(
        name="step_consumes",
        provenance=ProvenanceTerm.WAS_INFORMED_BY,
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
        provenance=ProvenanceTerm.PLAN,
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
        ),
    ),
    # Declared parent-to-child; `prov:specializationOf` itself reads child-to-parent
    # (see `_provenance_alignment`).
    Table(
        name="subsumes",
        provenance=ProvenanceTerm.SPECIALIZATION_OF,
        fields=(
            _text("parent", reference="procedures"),
            _text("child", reference="procedures"),
        ),
    ),
    Table(
        name="transitions",
        provenance=ProvenanceTerm.WAS_INFLUENCED_BY,
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
        # The steps a transition was derived from: references to step identities,
        # so the routing rule serves them as an edge rather than as a list
        # attribute on the transition they evidence (FR-025, FR-016). This is the
        # contract record of that edge's shape, not a body of its own: the served
        # form keeps `supporting_steps` on the transition edge body itself
        # (`graph/abstract.py`'s `_edge_body`), where the routing rule reads it.
        name="supported_by",
        provenance=ProvenanceTerm.WAS_GENERATED_BY,
        fields=(
            _text("transition", reference="transitions"),
            _json("supporting_steps", reference="steps"),
        ),
    ),
    Table(
        name="precedes_work_on",
        provenance=ProvenanceTerm.WAS_INFLUENCED_BY,
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
#: else is read: an unlisted record has no row to become. The names themselves
#: are `trajectory.records.CONSUMED_EVENT_NAMES`'s to declare (R17 puts
#: `trajectory` below `graph`); this pairs each with the episodic shape it
#: becomes.
(
    _USER_PROMPT,
    _API_REQUEST,
    _API_ERROR,
    _API_REFUSAL,
    _TOOL_RESULT,
    _TOOL_DECISION,
    _SUBAGENT_COMPLETED,
) = CONSUMED_EVENT_NAMES

CONSUMED_RECORDS = (
    TelemetryRecord(_USER_PROMPT, "a sequence", "opens the turn, carries `prompt.id`"),
    TelemetryRecord(_API_REQUEST, "an inference"),
    TelemetryRecord(
        _API_ERROR,
        "an inference that failed",
        "the terminal signal; retries are not separate events",
    ),
    TelemetryRecord(
        _API_REFUSAL,
        "an inference that was refused",
        "refusals arrive on a successful stream and never fire `api_error`",
    ),
    TelemetryRecord(_TOOL_RESULT, "a step that ran", "not emitted for a rejected call"),
    TelemetryRecord(
        _TOOL_DECISION,
        "a step's permission outcome",
        "the **only** record a rejected call produces",
    ),
    TelemetryRecord(
        _SUBAGENT_COMPLETED,
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


def _provenance_alignment() -> str:
    """The provenance alignment of `contracts/graph-schema-v2.md` (FR-045).

    Which standard term each declared node or edge type corresponds to. Documentary:
    the store stays a property graph and nothing here obliges a triple store.
    """
    rows = tuple(
        (layer.name, f"`{table.name}`", f"`{table.provenance}`")
        for layer in LAYERS
        for table in layer.tables
    )
    return "\n".join(
        (
            "## Provenance alignment",
            "",
            "Each declared shape and the PROV-O term it corresponds to. Every term is one the",
            "W3C PROV-O recommendation itself defines; no software-engineering ontology term is",
            "cited, because none could be verified against live documentation. Two rows read",
            "with a caveat: a `step_touches` row whose mode is `modified` corresponds to",
            "`prov:wasGeneratedBy`, the entity being the step's output rather than its input,",
            "and `subsumes` is declared parent-to-child where `prov:specializationOf` reads",
            "child-to-parent.",
            "",
            _markdown_table(("Layer", "Node or edge type", "PROV-O term"), rows),
        )
    )


def _genai_attributes() -> str:
    """The generative-AI attribute alignment of `contracts/graph-schema-v2.md` (FR-045)."""
    rows = tuple(
        (f"`{table.name}`", f"`{field.name}`", f"`{field.genai_attribute}`")
        for layer in LAYERS
        for table in layer.tables
        for field in table.fields
        if field.genai_attribute
    )
    return "\n".join(
        (
            "## Generative-AI convention attributes",
            "",
            "The OpenTelemetry generative-AI attribute each model-call field corresponds to. A",
            "model-call field absent from this table has no attribute in that convention: the",
            "cache token counts, the cost and the latencies are Claude Code's own. `error_class`",
            "maps to the general OpenTelemetry `error.type` attribute rather than a `gen_ai.*`",
            "one, so it is absent from this table too. `model` is declared against",
            "`gen_ai.response.model`, not the request-side attribute, because the column holds",
            "the model that served the call, not merely the one asked for.",
            "",
            _markdown_table(("Table", "Field", "Attribute"), rows),
        )
    )


def _stored_only_fields() -> str:
    """The stored-and-never-projected fields of `contracts/graph-schema-v2.md`.

    The exclusion is part of the contract: a from-scratch rebuild equals an
    incremental derivation only while no served body carries one (FR-044, FR-028).
    """
    rows = tuple(
        (f"`{table.name}`", f"`{field.name}`")
        for layer in LAYERS
        for table in layer.tables
        for field in table.fields
        if not field.projected
    )
    return "\n".join(
        (
            "## Stored and never projected",
            "",
            "A field listed here is held in the store and never appears in a served",
            "snapshot body: projecting it would make a from-scratch rebuild differ from an",
            "incremental derivation.",
            "",
            _markdown_table(("Table", "Field"), rows),
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
            "\n\n".join(
                (
                    _tables_and_edges(),
                    _closed_vocabularies(),
                    _stored_only_fields(),
                    _provenance_alignment(),
                    _genai_attributes(),
                )
            ),
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
