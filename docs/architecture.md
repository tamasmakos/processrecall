# Architecture

Two layers and one traversal between them. Capture writes downward: raw harness
activity becomes symbols — a step, its activity class, its outcome — and lands
in the episodic index. Serving reads the same traversal upward: a position in
the graph re-activates what is attached to it, and a renderer turns that into
one short statement. Nothing in either direction leaves the machine.

## The path of one action

1. **Arrive.** The `PostToolUse` hook receives the completed tool call. The
   project's exclusion is checked first, on `cwd` alone: an excluded project is
   never parsed, so no step, snippet or prompt text exists to suppress.
2. **Abstract.** `trajectory` turns the payload into one canonical event, and
   `procedures` labels it: the shell grammar reads a command down to its
   program and files, the taxonomy places it in an activity class, and the
   outcome rules say how it ended. `artifacts` attributes an edit to the
   enclosing symbol, so a step can be about a function rather than a file.
3. **Record.** `graph.store` appends one row to the episodic index — a single
   SQLite file under `~/.processrecall/`, safe for concurrent sessions. Writes
   are deduplicated by a key derived from the record, so replaying a session
   records nothing twice.
4. **Derive.** At the end of work, `graph.derive` folds the rows into the
   abstract graph by counting: nodes are procedures at three levels of
   generality, edges are observed transitions carrying their support count,
   condition, guidance and pitfalls. Both snapshots are written atomically —
   the project's own and the cross-project one.
5. **Serve.** On the next turn, `guidance` locates the agent on the previous
   step, extracts the `h`-hop neighbourhood around that position, asks the four
   triggers whether this is an occasion to speak, and renders what survives the
   support floor and the token budget. The project's graph is consulted first
   and the cross-project one only for procedures unseen here.

## What each package owns

| Package | Owns |
| --- | --- |
| `trajectory` | the harness seam: sources, sinks, the canonical event, the transcript reader |
| `procedures` | what an event *means*: the shell grammar, activity classes, taxonomy levels, outcome rules |
| `symbolic` | the concept index — a term indexed by the embedding of its definition — and the optional classifiers behind one strategy seam |
| `artifacts` | edits in source terms: the parser, and the symbol an edit falls inside |
| `graph` | the episodic index, the abstract graph, annotations, the JSON snapshot and the edit API |
| `ranking` | reciprocal-rank fusion, the attention side: averaging over candidates |
| `guidance` | locate, neighbourhood, triggers, fusion and the deterministic renderer |
| `integrations/claude_code` | the hooks the harness invokes, standard library only |
| `server/mcp` | the stdio server and its four tools |
| `cli` | the operator surface: what the agent does through hooks, a human reads back here |

`integrations` imports the standard library only, and `guidance` never imports
`symbolic` or `artifacts`: the hook's latency budget is what those rules protect,
and import-linter holds them.

## The two layers

The vocabulary is the Tensor Brain's. The **representation layer** is the working
state of one turn: the located position, the extracted neighbourhood, the fused
candidate set. The **index layer** is symbolic: concepts (activity classes,
process types), predicates (the transitions between procedures) and episodic
instances (one recorded step at one time). Attention averages over all
candidates — that is fusion — while sampling commits to one, which is the single
statement finally rendered. Semantic and episodic recall share this traversal;
there is no second retrieval path for facts.

## What never happens

No language model participates in serving: rendering is deterministic, and every
statement carries the number of episodes behind it. Guidance that would exceed
its token budget or its deadline is silence rather than truncation, and the
silence is counted. Served text contains action templates, counts, conditions
and annotations only — never file contents, prompt text or credentials.
