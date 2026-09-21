# Quickstart: Validating Graph Schema v2

**Feature**: 007-otel-graph-schema-v2 | **Plan**: [plan.md](./plan.md)

How to prove this feature works, end to end, as commands. Every scenario below maps to a
success criterion in [spec.md](./spec.md) and is runnable without a live Claude Code session —
the fixtures are synthetic OTLP lines built to
[contracts/telemetry-records.md](./contracts/telemetry-records.md), because a captured line
carries an email address and an account UUID and those do not enter this repository.

Scenario 12 is the only one that needs a real harness and a real collector.

## Prerequisites

```bash
uv sync                       # the five runtime dependencies, unchanged by this feature
make gate                     # must pass on the host before anything below means anything
```

**Read this before running `make gate`.** Today `scripts/gate.sh` refuses to start without
Docker and runs every check inside a container, against a tree that may not be the one you
edited. That contradicts the constitution in two places and is reported as a pre-existing
environment defect in [plan.md](./plan.md). A Verify command observed in the wrong tree has
established nothing, so fix the gate first or run the individual `pytest` invocations below
directly and know that is what you did.

No collector, no network and no services are needed for scenarios 1 through 11.

---

## 1. Every stored field has a documented origin — SC-001

```bash
pytest tests/graph/test_schema.py -q
```

Passes when the store's live `PRAGMA table_info`, the generated contract document and the
reader's bound-attribute set all agree. **Expected**: zero fields with an undocumented origin;
a mismatch names the field.

```bash
python -m processrecall.graph.schema --render > /tmp/generated.md
diff /tmp/generated.md .claude/specs/007-otel-graph-schema-v2/contracts/telemetry-records.md
```

**Expected**: no output. A diff here means the document was hand-edited, which is the failure
mode the generation exists to prevent.

## 2. Forbidden content is never stored — SC-002

```bash
pytest tests/trajectory/test_telemetry.py -q -k forbidden
```

Feeds a fixture line carrying all five forbidden fields — `prompt`, `response`, `body`,
`body_ref`, and the `user_prompt` span attribute — then searches the resulting store for each
value. **Expected**: zero occurrences, `telemetry_content_stripped` at 5, and the record
itself stored.

The identity attributes get the same treatment:

```bash
pytest tests/trajectory/test_telemetry.py -q -k identity
```

**Expected**: no email, account UUID, account id, user id or organisation id anywhere in the
store, and `telemetry_identity_stripped` non-zero.

## 3. The tool-details gate off, loudly — SC-003

```bash
pytest tests/trajectory/test_telemetry.py -q -k gate_off
processrecall show counters | grep gap_tool_details
```

**Expected**: the session is recorded, guidance is still served, and `gap_tool_details` is at
1 for the session — not per record. Zero silent degradations.

## 4. Two sources, one step — SC-004

```bash
pytest tests/graph/test_dedup.py -q
```

Replays the same tool call from telemetry and from the hook in **both** orders, plus
interleaved. **Expected**: exactly one stored step in every permutation, carrying the
telemetry value for every disputed field, with `steps_duplicate` and
`telemetry_hook_disagreement` both moved.

The compound-command case is in the same module and is the check Principle II is at risk on:

```bash
pytest tests/graph/test_dedup.py -q -k compound
```

**Expected**: `cd build && cmake .. && ctest` yields three steps sharing one `tool_use_id`
with ordinals 0, 1, 2. A change that makes this test pass by producing one step has weakened
it (R6).

## 5. Refusals surface as a warning — SC-005

```bash
pytest tests/guidance/test_paths.py -q -k refused
```

Feeds a corpus where a tool call is rejected repeatedly at the same point, folds, and asks for
guidance there. **Expected**: an avoid-this statement, attributed to `usually_refused`, absent
below the support floor and present above it and the rate it reports is the lower bound over
the observed counts, not the raw proportion. This signal does not exist in the current
system, so the same test against `main` must fail.

## 6. Events only, no spans — SC-006

```bash
pytest tests/trajectory/test_telemetry.py -q -k events_only
processrecall show counters | grep '^gap_'
```

**Expected**: every part of the model produced, with exactly three gap counters raised —
`gap_ttft`, `gap_permission_wait`, `gap_agent_nesting`. `gap_stop_reason` and
`gap_error_class` may also be raised; they report absent enrichment, not an incomplete model,
and the inference `outcome` is present regardless (R9).

## 7. Rebuild equals incremental, on a migrated store — SC-007

```bash
pytest tests/graph/test_migration.py -q
processrecall rebuild --project . --check ; echo "exit=$?"
```

**Expected**: `exit=0` and no output. `--check` re-derives both snapshots and reports the
first divergence in node or edge; zero of each is the criterion. Run it *after* the migration
fixture, because a v1 store that migrates and then diverges is the failure this is aimed at.

## 8. Semantic derivation inside 60 seconds, off the hot path — SC-008

```bash
pytest tests/graph/test_semantic.py -q -m slow
```

**Expected**: a 50,000-line synthetic tree derives in under 60 seconds, and the second run
over an unchanged tree is dominated by `semantic_files_skipped` rather than
`semantic_files_parsed`.

```bash
pytest tests/test_no_services.py -q
```

**Expected**: the recorder's ledger is empty. This is the test that proves nothing in the
capture path opened a socket or spawned a process, and it must now cover the telemetry reader.
Routing the telemetry path around it instead of through it is the failure Principle II names.

## 9. Guidance still inside 250 ms at p95 — SC-009

```bash
pytest tests/guidance/test_latency.py -q -m slow
```

Fuses all nine traversals over the fixture corpus. **Expected**: p95 under 250 ms with the
semantic store never opened on that path. If it fails, the projection bounds
`PRECEDES_ENTITIES` and `CALLERS_PER_ENTITY` in `graph/schema.py` move — the traversal does
not move onto the store (R17).

## 10. Every path measured against the baseline — SC-010

```bash
python scripts/measure_traversals.py --corpus research/corpus --out docs/measurements/007.md
cat docs/measurements/007.md
```

**Expected**: a row per traversal giving recall@1, recall@5 and MRR alone, the same three for
the baseline, the fused numbers with and without it, and the significance test for the
difference — on a temporal split of the prototype corpus, sessions before the split time
training and sessions after it testing. Commit the output whichever way it falls. **Expected**:
zero traversals in the fused result that do not beat the baseline by a margin the test
supports.

## 11. A publishable snapshot — SC-011

```bash
processrecall rebuild --project .
python - <<'PY'
import json, pathlib, re
body = pathlib.Path(".processrecall/graph.json").read_text()
assert not re.search(r'"[A-Za-z]:[\\\\/]|"/(home|Users)/', body), "absolute path in snapshot"
assert "cost_micros" not in body or "median_cost_micros" in body, "per-step cost in snapshot"
print("clean")
PY
```

**Expected**: `clean`. Zero prompt text, zero file contents, zero absolute paths outside the
project key, and zero per-step monetary amounts — cost appears only as
`median_cost_micros` on a procedure node.

## 12. Counters are readable — SC-012

```bash
processrecall show counters
pytest tests/graph/test_counters.py -q
```

**Expected**: every name this feature introduces appears in the output, *including names still
at zero*, and the test asserts that every name any module passes to `bump` is listed in
`COUNTERS`. Zero counters with no way to read them.

## 13. An old store still opens — SC-013

```bash
cp tests/fixtures/stores/v1.db /tmp/v1.db
PROCESSRECALL_DATABASE_PATH=/tmp/v1.db processrecall show sequences
PROCESSRECALL_DATABASE_PATH=/tmp/v1.db processrecall show counters | grep migration_v1_v2
```

**Expected**: 100% of the v1 store's recorded steps still readable, `migration_v1_v2` at 1,
and the new columns null rather than zero — a null means the hook did not know, and a zero
would be a claim it did (R14).

## 14. Content gates on, nothing stored — SC-014

```bash
pytest tests/trajectory/test_telemetry.py -q -k gates_on
processrecall show counters | grep telemetry_content_stripped
```

**Expected**: the session recorded, zero content fields stored, and the condition readable on
the operator surface. This is SC-002 from the other direction: there the gates were off and
nothing arrived; here they are on, content arrives, and the memory refuses it.

## 15. Modified touches reach the symbol — SC-015

```bash
uv run python -m pytest -q tests/graph/test_semantic.py -k resolution
processrecall show counters | grep -E 'touched_(symbol_resolved|file_only)'
```

**Expected**: on the fixture project, every modification inside a symbol's line range resolves
to that symbol and the rest resolve to a file with `touched_file_only` bumped — zero touches
with an unexplained resolution. Against `main` the resolved counter does not exist and the test
fails, which is the point: this join has been empty since the column was added.

---

## 16. Against a real collector (manual, needs a harness)

The only scenario that leaves the test suite.

```bash
processrecall doctor --collector-config > /tmp/otel-collector.yaml
otelcol --config /tmp/otel-collector.yaml &        # your collector, your machine
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_METRICS_INCLUDE_VERSION=true
claude                                              # do a few turns, then exit
processrecall doctor
```

`doctor` reports, in order: whether the file exists, when it was last written, how many bytes
lie between the persisted offset and the end, whether `app.version` and `session.id` were
observed, which gates the observed records imply, and every telemetry counter.

**Expected on a healthy setup**: a file that exists, a small or zero backlog,
`app.version` observed, `telemetry_records_read` moving, and `telemetry_session_unbound` at
zero.

**Expected on the interesting failures**:

| Symptom | Reading |
|---|---|
| `telemetry_absent` | the collector is not writing where the memory is looking |
| `telemetry_session_unbound` climbing with everything else at zero | the hooks are not installed, or `OTEL_METRICS_INCLUDE_SESSION_ID` is off (R4) |
| `gap_version_floor` at 1 with no other gap | `OTEL_METRICS_INCLUDE_VERSION` is unset, so no floor can be checked |
| `telemetry_offset_reset` on every pass | the collector has rotation enabled; the file transport wants append-style growth |
| `telemetry_stale` | the collector stopped, or is batching beyond the session |

`doctor` is a new verb and extends the CLI contract recorded in
`.claude/specs/005-procedural-graph-memory/contracts/cli.md`; that document is updated in the
same change.

---

## Uninstall check

```bash
pip uninstall processrecall
```

**Expected**: the recorded data survives in `~/.processrecall/`, the collector keeps running
because the memory never owned it, and prior behaviour returns. Nothing this feature adds
creates a dependency the user cannot remove.
