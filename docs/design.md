# processrecall — design record

Settled in the design interview on 2026-09-12. Every decision below was put to the owner
and agreed. Facts were verified against the working tree, the sibling `graphknows`
checkout, the local transcript corpus, and the Claude Code documentation.

## 1. What processrecall is

A neurosymbolic, procedural-graph-based agentic memory for coding agents, forked from
GraphKnows. It records what an agent *did* as a temporal procedural graph and serves
situational "what to do next" guidance back into the agent's context. It ships as a Claude
Code plugin whose repository root is also the Python package. Zero services.

Vocabulary follows the Procedural Graph formulation: nodes are procedures, edges are
permissible transitions, each edge carries `condition`, `guidance`, `pitfalls`. Module
names follow Tensor Brain where they map: the episodic store is the *episodic index*, the
ontology pack is the *concept index*, the abstract graph's relations are the *predicate
index*.

## 2. Facts that shaped the design

- The working tree is an uncommitted rename of `graphknows/` to `processrecall/` on top of
  one initial commit (445 files, 81k deleted lines). Env prefix and class names still say
  GraphKnows. The remote is `github.com/tamasmakos/processrecall`.
- `prototype_procedural_graph_v5.py` is stdlib-only but non-importable: it imports the v3
  base module, which exists only in the sibling checkout `dev/graphknows` (1132 lines,
  rdflib + SEON from `~/.cache/graphknows-prototype`). v3 holds the pieces v4 and v5 reuse:
  the Bash program tables, `subcommands` (shlex), `unwrap`, `classify_program`,
  `artifacts_in`, `Step`, `steps_from`, `classify_outcome`, `OUTCOME_RULES`.
- GLiNER2 in the prototypes never writes edge text. v3 uses it to classify Claude's
  rationale into ten SEON-style activities and to classify Bash commands the program table
  does not know. v4 adds typing the user prompt as a process (BugFix, FeatureAddition,
  Enhancement, Investigation, Documentation, Release Management). Guidance in v4 is
  "action templates per node": command shape with paths and numbers abstracted, no prose.
  v5 runs rules only; GLiNER2's contribution was resolving a 4% Unknown residue.
- The only store is ArcadeDB over REST (JVM, 16 GB heap default). The ontology stack is
  4.6k lines of RDF/OWL/SKOS/WordNet; the bundled CCO assets were dropped in the rename.
  Two GLiNER models run (relex via the old `gliner` package, GLiNER2 base via the schema
  API); per-chunk labels are mined by spaCy NER. The code parser is 64 lines of stdlib
  `ast`, Python only. A stdlib-only Claude Code hooks module exists with six verbs, a
  transcript checkpoint, and a settings.json block. No OpenTelemetry anywhere.
- Transcript corpus: 618 MB, 2334 session files, 57 over 1 MB; ~6k tool calls for the
  graphknows project alone. In-memory adjacency plus SQLite is sufficient.
- Claude Code hooks (documented): payloads carry `session_id`, `prompt_id`,
  `tool_use_id`, `tool_name`, `tool_input`, `cwd`, `transcript_path`, `agent_id`,
  `agent_type`; PostToolUse adds `tool_result`. `additionalContext` is accepted on
  PostToolUse, UserPromptSubmit, Stop, SubagentStop — **not** PreToolUse (allow/deny/
  updatedInput/systemMessage only). Default hook timeout 600 s, per-hook configurable.
  SessionStart reports startup/resume/clear/compact/fork. The transcript JSONL format is
  documented as internal and version-unstable. Plugins declare hooks, MCP servers, skills,
  `bin/`, `settings.json` together; `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`,
  `CLAUDE_PROJECT_DIR` are exported to hook and MCP commands. No Python install step
  exists; the documented pattern is a SessionStart bootstrap into `CLAUDE_PLUGIN_DATA`.
  Hooks run under a POSIX shell on this Windows machine.

## 3. Decisions

### 3.1 Harness abstraction
- Canonical trajectory event schema, field names aligned to OTel GenAI semantic
  conventions. Every channel is an adapter.
- Two protocols: `TrajectorySource` (events in) and `GuidanceSink` (text out), plus a
  per-harness tool vocabulary pack (JSON) mapping harness tool names to abstract
  procedure classes. Claude Code is the only implementation in this phase.

### 3.2 Event sources
- **Primary: OTel.** The telemetry event stream is the primary source for the episodic layer
  (`OTEL_LOG_TOOL_DETAILS=1` carries content), read from a file the developer's own
  collector writes; every consumed field traces to a named record and attribute.
- **Reconciled: hooks.** PostToolUse still writes one episodic step to SQLite (stdlib
  `sqlite3`), deduplicated by `tool_use_id`, but supplies only the fields telemetry does
  not carry; where both carry one, the telemetry value is stored and the disagreement is
  counted.
- **Backfill: transcript JSONL** behind the same protocol, used for old sessions and for
  the rationale text (assistant text before a tool call) that GLiNER2 classifies. A format
  break degrades classification, not the graph.
- The plugin receiving telemetry directly is a declared, unbuilt alternative transport;
  adopting it reopens the standing no-listening-port decision.
- The transcript checkpoint keyed by record uuid goes; `tool_use_id` is the dedup key.

### 3.3 Procedure model
- A step decomposes through the lifted v3 shell grammar into ordered sub-activities
  (class, program, tokens, artifacts). Node identity at three taxonomy levels stored as
  one node with is-a ancestors: `class`, `class/program`, `class/program/ext`. All three
  materialized; the serving level is an evaluation result (numbers not yet recorded).
- Class vocabulary = SEON-style activities (Inspection, Search, Change Implementation,
  Artifact Evaluation, Script Execution, Checkin, Checkout, Environment Configuration,
  Network Retrieval, Delegation). The harness tool→class table is the vocabulary pack.
- Synthetic `Start` node per prompt sequence (condition = process type) and `End` node at
  Stop. Prompt-start guidance is the successors of `Start`; edit-API validation checks
  reachability to `End`.
- Subagent steps form their own sequence keyed by (session, prompt, agent_id), feeding the
  same taxonomy; the parent's Agent call is one Delegation node.
- Compact and resume continue a session id; clear and fork start new sequences.
- Path normalization is relative to the hook's project directory (replaces v4's hardcoded
  repo-name fold).

### 3.4 Temporal shape
- Two layers sharing one traversal: the **episodic store** (each session a time-ordered
  chain of concrete steps) and the **abstract procedural graph** aggregated from it.
  Abstract edges point back to supporting episodic steps and carry support counts,
  recency, success/failure stats. Designed so `valid_from` / `invalidated_at` can be
  added later.
- Edge weighting per v5 E2: transitions from clean prompts count four times.
- Sequence model per v5 E3: variable-order back-off n-gram (3 → 2 → 1), plus v5 E1
  artifact conditioning (same file as previous step).

### 3.5 Edge attributes
- `condition`: GLiNER2 labels plus deterministic context — prompt process type, rationale
  intent, same-artifact-as-previous, previous step outcome.
- `guidance`: target node's top action templates plus transition statistics.
- `pitfalls`: transitions and templates over-represented in failed prompts, detected loops.
- Plus **Claude-authored annotations** via the `remember` MCP tool and a skill that says
  when to use it. This is the agent doing memory writes, not an ingestion LLM call.

### 3.6 Outcomes
- Per step and per prompt from v3's `OUTCOME_RULES` (`tool_result.is_error`, pytest/ruff
  regexes, commit hashes) plus the explicit `mark_outcome` tool. Stored on episodic
  steps and sessions, never baked into abstract edges, so it can be recomputed.
- No LLM judge.

### 3.7 GLiNER2
- Kept: GLiNER2 base through the schema API (`{label: description}`), as three
  classifiers behind a strategy protocol: rationale → intended activity, prompt → process
  type, unknown Bash command → activity. Rules-only is the default; classifiers activate
  when the `classify` extra is installed.
- Dropped: relex model, old `gliner` package, spaCy NER label mining, vendored relation
  verifier, hygiene gate.
- Entity layer (schema-driven spans over tool results: errors, libraries, config keys,
  symptoms) is **phase 2**, after the procedural core runs under the plugin.

### 3.8 Ontology
- SEON's relevant classes and predicates (SPO, CMPO, QAPO, ACE extension) frozen into a
  hand-curated JSON pack: label, definition, parent, domain, range. Same shape as the
  existing `packs/data/*.json`. The RDF→pack digest tool may live as a script outside the
  package. Whole RDF/OWL/SKOS/WordNet stack deleted.

### 3.9 Code parser
- Rebuilt on `tree-sitter` + `tree-sitter-language-pack`. First languages: Python,
  TypeScript/JavaScript, Go, Rust, Bash.
- Extracts symbols (qualified name, line range) and imports between files. Call edges
  later.
- Edit→symbol mapping: locate `new_string` in the file at ingest time, take the enclosing
  symbol, fall back to the file node.
- The v3 shlex shell grammar stays for command parsing in phase 1; tree-sitter-bash may
  replace it later.

### 3.10 Storage
- Episodic store: SQLite in the user's home processrecall directory, WAL mode. Private
  (holds snippets and prompts).
- Abstract graph snapshots: JSON adjacency. Per-project at `<project>/.processrecall/
  graph.json`, gitignored by default but committable (node names, abstracted templates,
  counts, annotations — no payloads). Global snapshot in the home directory, served as
  fallback for unseen nodes. Written to temp file then renamed.
- `CLAUDE_PLUGIN_DATA` holds only the venv.
- Retention: structured events + bounded snippets (tool result 2 KB, prompt 600 chars)
  + pointer to the transcript. No raw payload duplication.
- A store protocol exists so a server backend can return later; the old `GraphStore`
  interface is not reused.

### 3.11 Serving
- Hot path is stdlib-only: PostToolUse and UserPromptSubmit hooks read the JSON snapshot
  and return `additionalContext`. Budget ~300 tokens, 5 s hook timeout.
- Localization on the previous step (PostToolUse after a(t-1) guides a(t)); h-hop
  neighborhood extraction; deterministic renderer as the only Ψ implementation behind a
  pluggable interface. No guidance model.
- **Triggers only**, silence is the default: prompt start (process-type guidance), after
  Edit/Write when verification is expected next, same node repeated k times, matching
  pitfall for the chosen action (delivered one step early).
- PreToolUse deny with reason exists as an off-by-default enforce verb.
- Stop: incremental abstract-graph update in process (pure counting) and the remember
  nudge. SessionEnd: spawns a detached cold job for GLiNER2 classification, tree-sitter
  symbol mapping, snapshot rewrite.
- Graph scope: global episodic store; abstract graphs per project plus one global; served
  project-first.

### 3.12 MCP tools
- `recall` — guidance for a named node or the current position.
- `remember` — write an annotation onto an edge.
- `mark_outcome` — explicit success/failure for the current or a named prompt.
- `inspect` — node and edge listing with counts.
- The edit API (add / delete / revise with structural validation and rejection memory)
  stays a Python interface until a refiner exists.

### 3.13 Deferred
- Evaluation harness (replay, next-step top-k, loop rate, entity recall). The prototypes
  seed it, kept unchanged outside the published tree.
- Self-evolution refiner (later a Claude skill, not a separate LLM client).
- Entity layer, bitemporal validity, tree-sitter-bash, second harness, marketplace listing.

## 4. Package layout

```
processrecall/
  trajectory/            canonical event schema, TrajectorySource/GuidanceSink protocols,
                         Claude Code transcript reader, harness vocabulary pack loader
  procedures/            shell grammar (lifted v3), step → sub-activity, taxonomy levels,
                         outcome rules
  symbolic/              ontology pack loader (concept index), GLiNER2 classifiers behind
                         a strategy protocol, rules-only default
  graph/                 SQLite episodic index, abstract graph builder, n-gram model,
                         JSON snapshot, edit API with validation
  guidance/              locate, neighborhood extraction, trigger rules, deterministic
                         renderer
  artifacts/             tree-sitter code parser, edit → symbol mapping
  integrations/
    claude_code/         stdlib-only hooks (framing lifted from the old module)
  server/mcp/            four tools
  cli/                   bootstrap, backfill, rebuild, show
```
Import-linter: `integrations` imports stdlib only; `guidance` never imports `symbolic` or
`artifacts`.

Plugin files at repo root: `.claude-plugin/plugin.json`, `.claude-plugin/mcp.json`,
`hooks/hooks.json`, `skills/`, alongside `pyproject.toml`. SessionStart bootstrap runs
`uv sync` into a venv under `CLAUDE_PLUGIN_DATA` once; all hooks and the MCP server
invoke that venv's python by absolute path. Clear error if uv is missing. Extras opted
into via a plugin setting. Development via `--plugin-dir`; marketplace later.

## 5. Dependencies

- Core: `pydantic`, `pydantic-settings`, `tree-sitter`, `tree-sitter-language-pack`, `mcp`.
- Extra `classify`: `gliner2`, `torch`, `transformers`.
- Removed: spacy, nltk, dateparser, gliner, sentence-transformers, rank-bm25, numpy,
  httpx, dspy, litellm, rdflib, networkx, langgraph, langfuse.
- Python 3.11+, ruff, mypy, import-linter, deptry, bandit retained; spec 006 swapped the
  build backend for `uv_build`, which ships the module root whole instead of declaring packs.

## 6. Removal ledger

Out: ArcadeDB backend and compose stack, deploy/ and release image workflow, RDF/OWL/
SKOS/WordNet ontology stack, relex model and vendored verifier, spaCy and nltk code,
dspy/litellm LLM decoder, embedder, retriever and channels (except the 23-line RRF
function), `Memory` facade, LangGraph integration, document packs, `evaluation/`, all
GraphKnows docs and ADRs, CHANGELOG, CONTRIBUTING, tests of deleted code, integration
CI jobs (release CI jobs returned with spec 006, below), multi-tenancy.

In: SEON as a JSON pack, v3 shell grammar and step model, v4 taxonomy levels and action
templates, v5 back-off n-gram and artifact conditioning, hooks stdin/stdout framing, RRF,
code parser (rebuilt), MCP server scaffolding (rewritten tools).

Back in, amended by spec 006: the release CI jobs, in a shape the fork had no use for at
the time. A version tag runs `.github/workflows/release.yml`, which builds one distribution,
publishes it to PyPI and lists the stdio server in the MCP registry. `deploy/` and the
release image workflow stay out.

## 7. Process

- Orphan branch; a single skeleton commit is the new initial commit: new layout, lifted
  and tested modules, plugin manifest, bootstrap hook, new README and architecture doc,
  CLAUDE.md with the Python discipline and Tensor Brain sections kept verbatim and the
  domain section rewritten for procedural graphs. Old history survives only in the
  sibling `graphknows` checkout.
- Force push to origin only on explicit go at that moment.
- Env prefix and class names become processrecall in the new settings module.
- Tests use synthetic hook payloads and a small anonymized transcript fixture; real
  transcripts never enter the repo. The wheel-size assertion stays as a measurable
  outcome of the dependency cut.
