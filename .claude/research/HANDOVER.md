# Handover — graph schema v2 prototypes (session of 2026-09-17)

Branch `006-release-distribution`. Prototype sources and run artifacts under `.claude/research/`
(force-added; `.claude/` is gitignored).

---

## 0. Read this first: what was wrong with my starting assumptions

Two corrections the next session should not have to rediscover.

**`.claude/graph-schema-epic.md` is 0 bytes.** Untracked, no git history, unrecoverable from disk.
I built the v6 prototype from `.claude/specs/007-otel-graph-schema-v2/spec.md` without saying I had
never read the epic. The user supplied the epic text by hand later, and it reframed the task — v6
had skewed episodic/procedural because spec 007 line 9 says *"this rewrites the epic with OTel as
the primary source"*. If the epic is still empty, the authoritative text is in this session's
transcript, and FR-015..FR-037 of spec 007 restate most of it.

**The semantic layer was declared, not built.** v6 listed `CALLS`/`IMPLEMENTS` as SEON-aligned
relations while no code could emit them. That is the exact defect I had flagged elsewhere. v7 fixes
it by actually deriving the layer.

---

## 1. How the system works *today* (as shipped, not as specified)

### The serving path, end to end

```
hook / recall tool
  → locate(steps, level)            guidance/locate.py     last step → a node key
  → successors_from(snapshot, key)  guidance/successors.py edges leaving that key
     (or neighborhood.extract, h hops, BFS)
  → Fusion.fuse(project, fallback)  guidance/fusion.py     project graph vs global graph
  → render                          guidance/render.py
```

- `locate` reads the **last step only**. No steps yet → `START_KEY`.
- `successors_from` reads a **snapshot file on disk**, not the DB. Missing/unreadable snapshot
  yields `()` and bumps a counter — never a fault.
- `neighborhood.extract` is breadth-first, follows arrows outward only, expands each node once
  (so self-loops terminate). `h` is a budget, passed in, never read from `Config`.
- Standard library only on the hot path. No LLM in serving. Hook budget p95 ≤ 250 ms.

### How a path is actually queried — the part worth keeping

This is the piece the user found clearest, so here it is precisely.

**The "graph query" is not a graph query.** There is no traversal engine, no Cypher, no SPARQL.
A path is a Python function that filters a flat edge list:

```python
tuple(edge for edge in edges_from(snapshot)
      if edge.source == source
      and (process_type is None or edge.condition.process_type is process_type))
```

That is the whole of P1. The "graph" is a list of `TransitionEdge` records; "traversal" is a
comprehension over it. `neighborhood.extract` adds hops by grouping edges by `source` into a dict
once, then walking frontiers. Everything is in memory, from one JSON snapshot.

**Fusion is rank-based, and today it fuses the wrong axis.** `rrf_score(rank) = 1 / (60 + rank + 1)`
(`ranking/rrf.py`, `RRF_K = 60`). `Fusion.fuse` takes **two** lists: this project's edges and the
cross-project fallback's. Each is ranked independently by `(-support, edge_key)`, scores are summed
per `edge_key`, and ordering is `(not project_scope, -score, key)` — so **every project move
outranks every global-only move**, and RRF only decides order within a scope.

The important consequence: **FR-031's seven traversals do not exist yet.** RRF today fuses
*project vs global*, not *P1..P7*. Exactly one traversal is implemented — usual-next-moves. When the
report says "P1 runnable, P3 degraded, P5/P6/P7 blocked", that is readiness of the *inputs* each
proposed path would need, not code that runs.

The seven, per FR-031, in order:

| | traversal | status |
|---|---|---|
| P1 | usual next moves from the located procedure | **implemented** |
| P2 | fallback via the procedure's more general form when support < floor | inputs ready |
| P3 | what follows a change to the last modified entity | degrades to file resolution |
| P4 | concrete successors observed on a given entity | degrades to file resolution |
| P5 | filtered start-of-prompt moves for a kind of work | **blocked** — all `process_type` Unknown |
| P6 | what follows work on the callers of a modified symbol | **blocked** — TOUCHED never reaches symbol |
| P7 | moves usually refused at this point | **blocked** — no `tool_decision` ever ingested |

FR-033 restricts the hook path to P1, P3 (writes only), P7. FR-036 says a traversal that does not
beat the baseline on a held-out split **must not** enter the fused result — that measurement (AC-11)
is still unrun.

### What the live store actually contains

`inspect`: 136 nodes, 976 edges, **0 edges with guidance, 0 with pitfalls**, 2 annotations,
976 with a condition. Counters: `steps_recorded` 6965, `guidance_served` 151, `class_unknown` 471,
`guidance_over_budget` 30, `guidance_silent` 13, `guidance_below_support` 12.

| field | populated |
|---|---|
| `outcome` | 7019 / 7019 = `neutral`. No other value exists. |
| `symbol_ref` | 0 / 7019 |
| `result_snippet` | 2 / 7019 (both the same test fixture) |
| `rationale_label` | 0 / 7019 |
| `files` | 3228 / 7019 (46%) |
| `template` | 7019 / 7019 |
| `process_type` | 429 / 429 sequences = `Unknown` |
| `derived_outcome` | all neutral |

Top real edges: `Search/grep → Inspection/head` 507, `Unknown/StructuredOutput → End` 315,
`Inspection/head → Search/grep` 228, `ArtifactEvaluation/pytest → Inspection/tail` 144,
`ChangeImplementation/Edit → ChangeImplementation/Edit` 129.

The procedural graph is deadlock-free: 136/136 nodes reach `End`.

---

## 2. Experience actually using it

I called `recall` with no arguments. It correctly located `ScriptExecution/python` and returned
**18 statements**, every one of the form *"after X the work usually goes to Y"*. No pitfalls, no
guidance text.

That is a histogram, not guidance. With `outcome` constant at `neutral`, "usually" is the only
predicate the graph can express, so procedural memory degenerates to a Markov chain over tool names.
And the graph's highest-confidence knowledge (`grep → head → grep`, 507 + 228) is that I thrash
between reading and searching — serving that back reinforces churn.

Earlier v6 finding, still standing: **no traversal beat `usual_next` alone** (35.3% top-1) under
plain RRF.

---

## 3. The prototypes

All throwaway, all under `.claude/research/`.

### v6 — `prototype_graph_schema_v6.py` + `_report.py`
Three-layer schema analysis sourced from spec 007. Findings that survive: no traversal beats
usual-next; graph is deadlock-free; **`docs/design.md` names the OTel gate `OTEL_LOG_TOOL_CONTENT`
while the epic and spec 007 both say `OTEL_LOG_TOOL_DETAILS` — the doc is wrong.** Superseded on
the semantic layer.

### v7 — `prototype_codegraph_v7.py` + `_report.py` → `out/codegraph_v7.html`
The semantic layer built for real, from **tree-sitter tags.scm captures** (`@definition.class`,
`@definition.function`, `@reference.call`) — the epic's mandated derivation source.

```
1443 CodeEntity   (901 function, 230 method, 153 file, 141 class, 12 module)
1286 CONTAINS
1753 CALLS            (support 2031)
2229 CALLS_UNRESOLVED (support 2853)
   3 IMPLEMENTS
21,801 LOC in 4.6 s → 10.6 s projected at 50k LOC. AC-9 passes, 5.7× headroom.
```

Resolution rule: same-file match preferred; exactly one target → `CALLS`; ambiguous or none →
`CALLS_UNRESOLVED`, **never dropped** (FR-018). This matters: unresolved outnumbers resolved, top
entries `str` 207, `get` 149, `len` 90, `read_text` 87 — **~58% of call sites would vanish** under
resolve-or-discard.

SEON mapping is a `seon_iri` node attribute: code entities → `swo:SoftwareItem`, Sequence →
`spo:SpecificPerformedProcess`, Step → `spo:PerformedActivity`, Agent → `spo:Agent`.

Report is 5 tabs (Schema, Subgraphs, P1-P7, SEON & OTel, Census), self-contained HTML, with an
interactive symbol-subgraph explorer (`CAP = 12` neighbours/hop).

### v8 — `prototype_outcome_backfill_v8.py` → `out/outcome_backfill_v8.json`
Answers: *if the outcome were ingested, would the graph say anything the support-ranked graph
cannot?* Reads 27 transcripts, rebuilds the transition graph carrying `is_error`.

```
store:       7019 steps, 100% neutral
transcripts: 2035 moves — 1933 ok, 62 failure, 40 rejected  (5.0% bad)
```

Pitfalls that derive immediately and cannot be produced today:

```
[REJECTED]         ChangeImplementation/Edit      x12  worst x11: .claude/workflows/implement-tasks.js
[REJECTED]         Unknown/processrecall"          x5
[REPEATED_FAILURE] browser_batch                   x5
[REJECTED]         Inspection/git                  x4
[REJECTED]         docker                          x3
[REPEATED_FAILURE] sonarqube analyze_code_snippet  x3
```

Same recall call, with outcome ingested:

> after `ChangeImplementation/Edit` the work usually goes to `ChangeImplementation/Edit`
> (80 seen, **9% of them failed or were refused**)
> **PITFALL REJECTED: 12 times here; worst repeat ×11: `…/implement-tasks.js`**

**Caveat:** the backfill's activity-class mapping is my approximation of the store's bucketing, not
the store's own code, so node names are near-enough rather than exact. The outcome counts come
straight off `is_error` and are solid.

---

## 4. The central finding

The system records *what happened* and never *whether it worked*. Every downstream weakness follows
from that single gap.

The "what broke after a step" feedback loop needs three joins; one works:

- **step → file** — 46% populated. Works.
- **step → symbol** — `symbol_ref` 0%. FR-063 requires attributing edits to the enclosing symbol
  *at record time*; nothing does. TOUCHED is worse than it looks: of 440 distinct recorded paths,
  **56 reach a CodeEntity (12.7% by path, 29.0% weighted)**.
- **step → did it break** — nothing.

So a more granular code KG does not unblock blast-radius attribution. Granularity is the second
problem. The outcome signal is the first, and it demonstrably exists in the transcripts.

---

## 5. Recommended order of work

1. **Populate `outcome` at record time** from `is_error` + `tool_decision{decision=reject}`. Small,
   it is the epic's `PitfallKind.REJECTED` (FR-027), and it unblocks P7 outright.
2. **Wire FR-063 symbol attribution** so `symbol_ref` stops being 0%. P6 is blocked *solely* on
   TOUCHED not reaching symbol resolution — `CALLS` now exists (v7 proves it is derivable in 10.6 s
   at 50k LOC).
3. **Then** invest in the granular code KG. Doing this first buys a graph nothing can query for the
   thing you want.
4. Run **AC-11** (P3/P6 recall@1 vs P1 on v5's held-out split). FR-036 forbids fusing an unmeasured
   traversal, so this gates everything in §1's table.
5. Fix `docs/design.md`: `OTEL_LOG_TOOL_CONTENT` → `OTEL_LOG_TOOL_DETAILS`.

---

## 6. Facts worth not rediscovering

- `processrecall/artifacts/parse.py` (260 lines) is the production tree-sitter module. It extracts
  **symbols + imports only — no call sites** — and drops the node type, so class and function
  collapse. The 141 classes in v7 exist only because `@definition.class` is a separate capture.
  **`parse.py` has no production caller.**
- `tree_sitter_language_pack` bundles **0 `.scm` files**. v7's queries are written inline;
  production must vendor the real `tags.scm`.
- tags-style queries **do** capture call sites on `tree_sitter` 0.26.0 — the epic's mechanism is
  sound, `parse.py` simply does not implement it.
- The tree-sitter captures API changed at 0.25: `ts.QueryCursor(query).captures(root)` vs
  `query.captures(root)`. v7 wraps both in `_captures`.
- OTel gates — required: `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_LOGS_EXPORTER=otlp`,
  `OTEL_LOG_TOOL_DETAILS=1`. **Forbidden by invariant:** `OTEL_LOG_USER_PROMPTS`,
  `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_RAW_API_BODIES`.
- Three cross-layer edges are the entire overlap between layers: `TOUCHED` (episodic→semantic),
  `INSTANCE_OF` (episodic→procedural), `PRECEDED_ON` (procedural→semantic).
- `vars()` fails on a `slots=True` dataclass — use `dataclasses.asdict`.
- Chrome `--screenshot` needs an **absolute** Windows path or it fails with access-denied.
- `raw.githubusercontent.com` serves HTML as `text/plain` with `nosniff`, so a browser shows markup.
  Use `raw.githack.com` to share a rendered report.
- Windows console mangles `§`; write "section".
