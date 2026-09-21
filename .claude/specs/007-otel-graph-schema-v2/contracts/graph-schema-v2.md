# Contract: Graph Schema v2

**Feature**: 007-otel-graph-schema-v2 | **Satisfies**: FR-015, FR-016, FR-017 through FR-030,
SC-004, SC-007, SC-011

The versioned declaration of all three layers. Full field lists are in
[data-model.md](../data-model.md); this document is the *contract* — the parts an automated
check holds the code to, and the parts an outside reader may rely on.

**Generated from `processrecall/graph/schema.py`.** A hand edit here is a failing test.

## Version

| Stamp | Value | Where |
|---|---|---|
| Store schema version | `2` | `meta` table in the episodic index |
| Snapshot format | `2` | `SNAPSHOT_FORMAT`, in every snapshot body |
| Contract version | `2` | the header of this generated document |

All three move together. A store at `1` migrates forward on open (R14). A store at any other
value is refused with `sqlite3.DatabaseError` — unchanged v1 behaviour, and deliberately not
softened. A snapshot at format `1` is not migrated; it is re-derived from the rows.

## The three layers

| Layer | Holds | Persisted in | Derived from |
|---|---|---|---|
| Semantic | code entities and their relations | SQLite | the working tree, for touched files only |
| Episodic | sequences, steps, inferences, agents, and the edges between them | SQLite | telemetry, with the hook as fallback |
| Procedural | procedures and transitions | the snapshot only | the fold over the episodic layer |

The procedural layer is **never** persisted relationally. A procedure is an aggregation of
rows; the rows are the record; the snapshot is a cache of the aggregation. This is what makes
SC-007 checkable at all — `processrecall rebuild --check` re-derives both snapshots and reports
the first divergence, and "no divergence" is only meaningful because nothing in the procedural
layer has an independent existence.

## The routing rule (FR-016)

> A value that refers to another identity in the schema is an **edge**. A value that describes
> the thing itself is a **node attribute**.

**Enforced at snapshot write.** Every field in the declaration carries a `reference` flag.
`snapshot.py` refuses to serialise a node body containing a key declared as a reference, and
names the offending key in the refusal. A test feeds it a violating body and asserts the
refusal.

**Not enforced in SQLite**, where a reference lives in a foreign-keyed column, because there
the foreign key *is* the edge (R12). One declaration drives both, so the two representations
cannot disagree about what counts as a reference.

<!-- generated: graph-schema-v2.md by python -m processrecall.graph.schema --render -->

## Declared tables and their edges

A field declaring a target is an edge to that table; every other field is a node
attribute of the table it is declared on.

| Layer | Table | Fields | Edges to |
| --- | --- | --- | --- |
| semantic | `code_entities` | `entity_key`, `kind`, `extension`, `language`, `fingerprint`, `first_seen`, `last_seen`, `support`, `start_line`, `end_line` | none |
| semantic | `code_relations` | `source_key`, `relation`, `target_key`, `target_name`, `sites` | `code_entities` |
| episodic | `sequences` | `conversation_id`, `session_epoch`, `prompt_id`, `agent_id`, `project_dir_key`, `process_type`, `process_type_source`, `status`, `derived_outcome`, `declared_outcome`, `started_at`, `ended_at`, `prompt_length`, `command_name`, `command_source`, `workflow_run_id`, `workflow_name`, `app_version`, `head_revision`, `head_branch`, `source` | `agents` |
| episodic | `steps` | `step_id`, `dedup_key`, `conversation_id`, `session_epoch`, `prompt_id`, `agent_id`, `position`, `node_key`, `activity_class`, `program`, `template`, `files`, `result_snippet`, `outcome`, `record_ref`, `occurred_at`, `recorded_at`, `rationale_label`, `symbol_ref`, `valid_from`, `invalidated_at`, `result`, `kind`, `decision`, `decision_source`, `duration_ms`, `error_type`, `input_size_bytes`, `result_size_bytes`, `tool_source`, `source` | `sequences`, `agents` |
| episodic | `inferences` | `inference_id`, `sequence_key`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `cost_micros`, `duration_ms`, `speed`, `effort`, `query_source`, `outcome`, `status_code`, `attempt`, `occurred_at`, `recorded_at`, `first_content_ms`, `stop_reason`, `error_class` | `sequences` |
| episodic | `agents` | `agent_id`, `kind`, `agent_type`, `agent_source`, `is_built_in`, `is_async`, `workflow_run_id`, `workflow_name`, `parent_agent_id`, `first_seen`, `last_seen` | `agents` |
| episodic | `step_touches` | `step_id`, `entity_key`, `mode`, `resolution` | `steps`, `code_entities` |
| episodic | `step_consumes` | `step_id`, `inference_id`, `link` | `steps`, `inferences` |
| procedural | `procedures` | `key`, `level`, `activity_class`, `program`, `file_ext`, `node_type`, `templates`, `support`, `outcome_counts`, `median_cost_micros`, `median_duration_ms`, `last_seen`, `activation` | none |
| procedural | `subsumes` | `parent`, `child` | `procedures` |
| procedural | `transitions` | `edge_key`, `source`, `target`, `condition`, `support`, `weight`, `dependency_measure`, `lift`, `reported_rate_lower`, `outcome_counts`, `last_seen`, `guidance`, `pitfalls`, `annotations`, `origin`, `schema_version`, `valid_from`, `invalidated_at` | `procedures` |
| procedural | `supported_by` | `transition`, `supporting_steps` | `transitions`, `steps` |
| procedural | `precedes_work_on` | `source`, `entity_key`, `support`, `callers` | `procedures`, `code_entities` |

## Closed vocabularies

A field listed here takes one of these values and no other; every field not
listed is open.

| Table | Field | Values |
| --- | --- | --- |
| `steps` | `result` | `ok`, `failure` |
| `steps` | `decision` | `accepted`, `rejected` |
| `steps` | `decision_source` | `config`, `hook`, `user_permanent`, `user_temporary`, `user_abort`, `user_reject` |

## Stored and never projected

A field listed here is held in the store and never appears in a served
snapshot body: projecting it would make a from-scratch rebuild differ from an
incremental derivation.

| Table | Field |
| --- | --- |
| `steps` | `recorded_at` |
| `inferences` | `recorded_at` |

## Provenance alignment

Each declared shape and the PROV-O term it corresponds to. Every term is one the
W3C PROV-O recommendation itself defines; no software-engineering ontology term is
cited, because none could be verified against live documentation. Two rows read
with a caveat: a `step_touches` row whose mode is `modified` corresponds to
`prov:wasGeneratedBy`, the entity being the step's output rather than its input,
and `subsumes` is declared parent-to-child where `prov:specializationOf` reads
child-to-parent.

| Layer | Node or edge type | PROV-O term |
| --- | --- | --- |
| semantic | `code_entities` | `prov:Entity` |
| semantic | `code_relations` | `prov:wasInfluencedBy` |
| episodic | `sequences` | `prov:Activity` |
| episodic | `steps` | `prov:Activity` |
| episodic | `inferences` | `prov:Activity` |
| episodic | `agents` | `prov:Agent` |
| episodic | `step_touches` | `prov:used` |
| episodic | `step_consumes` | `prov:wasInformedBy` |
| procedural | `procedures` | `prov:Plan` |
| procedural | `subsumes` | `prov:specializationOf` |
| procedural | `transitions` | `prov:wasInfluencedBy` |
| procedural | `supported_by` | `prov:wasGeneratedBy` |
| procedural | `precedes_work_on` | `prov:wasInfluencedBy` |

## Generative-AI convention attributes

The OpenTelemetry generative-AI attribute each model-call field corresponds to. A
model-call field absent from this table has no attribute in that convention: the
cache token counts, the cost and the latencies are Claude Code's own. `error_class`
maps to the general OpenTelemetry `error.type` attribute rather than a `gen_ai.*`
one, so it is absent from this table too. `model` is declared against
`gen_ai.response.model`, not the request-side attribute, because the column holds
the model that served the call, not merely the one asked for.

| Table | Field | Attribute |
| --- | --- | --- |
| `inferences` | `model` | `gen_ai.response.model` |
| `inferences` | `input_tokens` | `gen_ai.usage.input_tokens` |
| `inferences` | `output_tokens` | `gen_ai.usage.output_tokens` |
| `inferences` | `stop_reason` | `gen_ai.response.finish_reasons` |

<!-- /generated -->

## Edge types

| Layer | Edge | From | To |
|---|---|---|---|
| Semantic | `contains` | file or symbol | symbol |
| Semantic | `calls` | symbol | symbol |
| Semantic | `unresolved_call` | symbol | a name, not an entity |
| Semantic | `implements` | symbol | a base or protocol name |
| Episodic | `follows` | step | step |
| Episodic | `belongs_to` | step | sequence |
| Episodic | `performed_by` | sequence or step | agent |
| Episodic | `spawned` | agent | agent — span-derived only |
| Episodic | `consumed` | step | inference |
| Episodic | `touched` | step | code entity |
| Procedural | `subsumes` | procedure | procedure, one level down |
| Procedural | `transition` | procedure | procedure |
| Procedural | `precedes_work_on` | procedure | code entity |

`unresolved_call` is its own edge type rather than a `calls` edge with a null target, so
"we could not resolve this" is never confusable with "this failed to parse" (FR-027, R16).

## Bounds that keep the snapshot servable

The snapshot is read and parsed whole on every guidance call, so its size is the latency
budget (SC-009). Two projections are therefore bounded in the declaration:

| Bound | Value | Meaning |
|---|---|---|
| `PRECEDES_ENTITIES` | 8 | entities per procedure in `precedes_work_on` |
| `CALLERS_PER_ENTITY` | 8 | pre-computed callers carried per projected entity |

These are the hot path's entire knowledge of the call graph. The renderer never reads the
semantic store and never walks a call graph at serve time (R17). If SC-009 fails, these
numbers move; the traversal does not move onto the store.

## What a published snapshot may not contain (SC-011)

Four prohibitions, enforced by a test that greps a generated snapshot:

| Prohibited | How this schema avoids it |
|---|---|
| prompt text, response text, raw bodies | never bound by the reader, under any gate |
| file contents | the semantic layer stores keys and fingerprints, never source |
| absolute paths outside the project key | `entity_key` is repository-relative with forward slashes, and the writer rejects an absolute one; `body_ref` and `workspace.host_paths` are dropped at the door |
| **per-step monetary amounts** | cost lives only as `median_cost_micros` on a procedure node — an aggregate over many steps. No step, inference or transition body carries a cost |

The last one constrains the fold rather than the store: `inferences.cost_micros` is stored in
the private index, which is right, and must not be projected into the served form at step
granularity. A per-step amount in a committed snapshot is a spend disclosure.

## Honesty fields

Fields that exist so that a degradation cannot be mistaken for an observation:

| Field | On | Values | Why |
|---|---|---|---|
| `resolution` | `touched` | `symbol`, `file` | the memory may know the file and not the symbol (FR-020) |
| `link` | `consumed` | `observed`, `adjacent` | no attribute joins a tool call to its inference (R8) |
| `source` | step, sequence | `telemetry`, `hook`, `both` | which record this came from (FR-008) |
| `origin` | transition | `folded`, `annotated` | read by the renderer when attributing a statement (FR-034) |
| `app_version` | sequence | the observed harness version | makes a version-floor gap explicit (R10) |

Nothing in this list may be defaulted to the confident value. A `link` that defaults to
`observed` would make FR-003 unverifiable.

## Agreement check (FR-030)

`tests/graph/test_schema.py` asserts three things, and names the field when any fails:

1. **Store agrees with the declaration.** For every v2 table, `PRAGMA table_info` matches the
   declared columns, in name and type.
2. **This document agrees with the declaration.** Regenerating it produces a byte-identical
   file.
3. **The reader agrees with the declaration.** Every attribute
   [telemetry-records.md](./telemetry-records.md) says is bound appears in the declaration,
   and every declared telemetry-sourced field has an origin row.

A fourth check lives in `tests/graph/test_migration.py`: a v1 fixture store migrates, its prior
steps remain readable, and guidance derived from it before and after the migration is
identical (FR-029).

## Compatibility

- **Forward**: a v1 store opens, migrates in one transaction, and is stamped `2`. An
  interrupted migration leaves a v1 store, which v1 code still reads.
- **Backward**: none. There is no downgrade and no dual-read path.
- **Additive within v2**: a new nullable column with no reader is not additive, it is dead
  weight. New fields arrive with the traversal that reads them.
