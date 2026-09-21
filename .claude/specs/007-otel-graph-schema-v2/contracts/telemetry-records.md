# Contract: Telemetry Records Consumed

**Feature**: 007-otel-graph-schema-v2 | **Satisfies**: FR-001, FR-002, FR-003, FR-011, SC-001,
SC-002, SC-006

This is the per-field origin table SC-001 is measured against: every field the memory stores
from telemetry, the record it comes from, the attribute, the gate that must be on, the harness
version floor, and the hook fallback. The records-consumed list below **is generated from
`processrecall/graph/schema.py`** (R2, R13) — a hand edit inside its markers is a failing test.
The field-origin tables past it are hand-maintained.

Source of record: `code.claude.com/docs/en/monitoring-usage`, events section. Events are
stable. Spans are beta, off by default, and appear here only as the enrichment column.

<!-- generated: telemetry-records.md by python -m processrecall.graph.schema --render -->

## Records consumed

7 records, and no others. Metrics are not consumed.

| Record | What it becomes | Note |
| --- | --- | --- |
| `claude_code.user_prompt` | a sequence | opens the turn, carries `prompt.id` |
| `claude_code.api_request` | an inference |  |
| `claude_code.api_error` | an inference that failed | the terminal signal; retries are not separate events |
| `claude_code.api_refusal` | an inference that was refused | refusals arrive on a successful stream and never fire `api_error` |
| `claude_code.tool_result` | a step that ran | not emitted for a rejected call |
| `claude_code.tool_decision` | a step's permission outcome | the **only** record a rejected call produces |
| `claude_code.subagent_completed` | an agent | the only events-mode record naming an agent type |

<!-- /generated -->

Explicitly not consumed: `assistant_response` (its payload is the response text FR-011
forbids), the quota, rate-limit, plugin, MCP, mention, survey and cleanup events (harness
health, not the developer's work), and every metric (pre-aggregated, no per-step identity).

## Standard attributes, on every record

| Attribute | Memory's treatment |
|---|---|
| `session.id` | **kept** — the only attribution key there is (R4) |
| `app.version` | **kept** — the version-floor key (R10) |
| `app.entrypoint`, `terminal.type` | dropped, nothing reads them |
| `organization.id`, `user.id`, `user.email` | **dropped at the door** (R11) |
| `user.account_uuid`, `user.account_id` | **dropped at the door** (R11) |
| `user.groups`, `identity.source` | **dropped at the door** — gateway deployments only |
| custom `OTEL_RESOURCE_ATTRIBUTES` | **dropped at the door** — team and department names |
| `vcs.repository.*` | dropped; the memory already knows its project |
| `prompt.id` | **kept** — the correlation key for the whole turn |
| `event.timestamp` | **kept** — the ordering key |
| `event.sequence` | **kept** — tiebreaker only, never primary; per-process, and can go backwards across a resume |

Two of these defaults matter enough to be operator-facing:

- `OTEL_METRICS_INCLUDE_VERSION` defaults to **false**. Without `app.version` no floor can be
  checked and `gap_version_floor` is bumped once per session. The shipped example
  configuration sets it; `doctor` reports its absence first.
- `OTEL_METRICS_INCLUDE_SESSION_ID` defaults to true but can be turned off. With it off every
  record is unattributable and ingest yields nothing but `telemetry_session_unbound`. `doctor`
  names that case rather than reporting an empty read.

## Field origins

`gate` is the environment variable that must be set for the attribute to be emitted at all;
blank means ungated. `floor` is the minimum harness version.

### Sequence fields, from `user_prompt`

| Stored field | Attribute | Gate | Floor | Hook fallback |
|---|---|---|---|---|
| `prompt_id` | `prompt.id` | | | yes, `prompt_id` |
| `conversation_id` | `session.id` | | | yes, `session_id` |
| `prompt_length` | `prompt_length` | | | no |
| `command_name` | `command_name` | partial: custom/plugin/MCP names collapse to `custom`/`mcp` without `OTEL_LOG_TOOL_DETAILS` | | no |
| `command_source` | `command_source` | | | no |
| `occurred_at` | `event.timestamp` | | | yes |
| `session_epoch` | — | — | — | **hook only**, by definition (R5) |
| `project_dir` | — | — | — | **hook only**, by definition (R4) |

### Step fields, from `tool_result` and `tool_decision`

| Stored field | Attribute | Record | Gate | Floor | Hook fallback |
|---|---|---|---|---|---|
| `step_id` material | `tool_use_id` | both | | | yes, same value |
| `tool_name` | `tool_name` | both | | | yes |
| `success` | `success` | result | | | yes |
| `duration_ms` | `duration_ms` | result | | | no |
| `error_type` | `error_type` | result | | | no |
| `decision` | `decision_type` / `decision` | result / decision | | | no |
| `decision_source` | `decision_source` / `source` | result / decision | | semantics changed at v2.1.216 | no |
| `input_size_bytes` | `tool_input_size_bytes` | result | | | no |
| `result_size_bytes` | `tool_result_size_bytes` | result | | | no |
| `tool_source` | `tool_source` | decision | | v2.1.214 | no |
| `mcp_server_scope` | `mcp_server_scope` | result | | | no |
| touched entities | `tool_parameters` / `tool_input` | both | `OTEL_LOG_TOOL_DETAILS` | | yes, hook tool input |
| `head_revision`, `head_branch` | `vcs.ref.head.revision`, `.name` | result | `OTEL_LOG_TOOL_DETAILS` | v2.1.269 | no |

`tool_use_id` is documented as matching the value passed to hooks, which is what makes
deduplication between the two sources exact rather than heuristic (FR-008).

### Inference fields, from `api_request`, `api_error`, `api_refusal`

| Stored field | Attribute | Record | Floor |
|---|---|---|---|
| `model` | `model` | all three | |
| `input_tokens`, `output_tokens` | same | request | |
| `cache_read_tokens`, `cache_creation_tokens` | same | request | |
| `cost_micros` | `cost_usd_micros` | request | |
| `duration_ms` | `duration_ms` | request, error | |
| `speed`, `effort` | same | all three | |
| `query_source` | `query_source` | all three | |
| `inference_id` | `request_id`, else `client_request_id`, else derived | all three | v2.1.214 for the second |
| `status_code` | `status_code` | error | |
| `attempt` | `attempt` | error, refusal | |
| `stop_reason` | `refusal` when `api_refusal`; otherwise span only | | |

`cost_usd` is **not** stored: `cost_usd_micros` is the same quantity as an integer, and a
float here would make aggregate cost per procedure non-reproducible.

### Agent fields, from `subagent_completed`

`agent_type`, `agent.source`, `is_built_in`, `is_async`, `total_tool_uses`, `duration_ms`,
`model`, and — floor v2.1.212 — `final_model`, `model_swapped`. `agent_type` collapses
user-authored names to `custom` without the tool-details gate, so it is a category, not an
identity.

## Never read, under any gate

The reader binds attributes from an allow-list, so these are not filtered after parsing — they
are never given a name (R11):

| Attribute | Record |
|---|---|
| `prompt` | `user_prompt` |
| `response` | `assistant_response` |
| `body` | `api_request_body`, `api_response_body` |
| `body_ref` | the same, and it is an absolute host path |
| `user_prompt` (span attribute) | interaction span |

Encountering any of them increments `telemetry_content_stripped`; encountering an identity
attribute increments `telemetry_identity_stripped`. Both continue processing the record
(FR-012).

## Span enrichment

Consumed only if a trace file is configured and present, and never required (FR-002).
Fills `stop_reason`, `first_content_ms`, `error_class`, permission wait duration, and the only
source of `agent_id` / `parent_agent_id`, which is what makes the **spawned** edge possible.
Absence of each is a named gap counter, not a null.

`workspace.host_paths` is a span resource attribute carrying absolute developer paths and is
dropped at the door like the identity attributes.

## Ordering and correlation rules

1. Order by `event.timestamp`; break ties with `event.sequence`. Never order by
   `event.sequence` alone — it restarts per process and can decrease within one session after
   a resume.
2. Correlate everything in a turn by `prompt.id`.
3. Correlate a step across sources by `tool_use_id`.
4. Correlate a step to its inference by adjacency within one `prompt.id`, and record that the
   link was adjacency (R8).
5. Attribute a record to a project only through its `session.id` (R4). No attribution, no
   storage.
