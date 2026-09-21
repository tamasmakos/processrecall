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

## The ingest path

Telemetry is the primary source for the episodic layer, and it reaches the
memory as a file the developer's own collector writes, named by the
`telemetry_path` setting. Empty is the shipped state: until the file is named
there is no telemetry source, which is reported rather than raised on.

1. **Bind.** `SessionStart` records which project a session belongs to. No
   telemetry record carries a working directory, so `session.id` is the only
   way back to a project: ingest is strictly downstream of the hook rather than
   a fallback for it, and a record whose session no hook opened is dropped at
   the door and counted.
2. **Drain.** `cli.derive` is the one place that file is read, inside the
   detached job `SessionEnd` spawns — the same pass the snapshots are folded
   in. Never on a hook path, which has a timeout the file has no bound to
   respect, and never from a process that stays up to watch it, which would be
   the listening port this memory does not open. The pass resumes from a
   persisted offset, so its cost is what the last unit of work appended rather
   than everything the session ever emitted.
3. **Read.** `trajectory.telemetry` is the door every record comes through. One
   exported line is a batch and all of it is read; attributes are bound from an
   allow-list, so a prompt or a response body arriving under a new name is
   unreadable by default; records are put back into the order the harness wrote
   them in, by event time with the sequence number breaking a tie.
4. **Reconcile.** Two captures of one tool call, telemetry's and the hook's,
   become one step under one dedup key in either arrival order. Telemetry wins
   every clash and the hook fills only the fields the record did not carry;
   every clashing field is counted, so telemetry winning never hides that the
   hook read something else.

Joining the drained lines to that step ingest is the part of this path not yet
built: the pass advances the offset and reports the lines it passed over, and
`processrecall doctor` reads the file to report on the collector and on which
harness gates the records it finds imply.

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

## The three layers

`graph/schema.py` declares the graph once, as three layers bottom to top, and
every column the store holds is checked against that declaration. The
**semantic layer** is what the code is: entities keyed by path and, where known,
by qualified symbol, with the containment, call, unresolved-call and
implementation relations between them. It is derived at the end of a unit of
work rather than on the hook path, and incrementally — a file whose content
fingerprint is already derived is not read again. The **episodic layer** is what
happened: sequences, steps, inferences and agents, with the ordering,
membership, performance, spawn, consumption and touched relations. A step
carries two independent outcome axes, a result of ok or failure and a decision
of accepted or rejected, because a refusal is a policy signal and a failure a
capability one, and one enumeration for both would corrupt either statistic.
The **procedural layer** is what to do next: procedures at three levels of
generality, the subsumption between them, and their transitions with support,
condition, guidance and pitfalls. It is the one layer with no rows of its own —
a procedure is a fold over episodic rows, so the rows are the record and the
snapshot is a cache of the aggregation.

These three are the graph's layers; the two below are the Tensor Brain's, and
all three of these sit in its index layer.

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
