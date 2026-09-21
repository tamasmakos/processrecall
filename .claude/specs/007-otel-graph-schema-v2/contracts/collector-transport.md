# Contract: Collector Transport

**Feature**: 007-otel-graph-schema-v2 | **Satisfies**: FR-004, FR-005, FR-009, FR-010, SC-005,
SC-013

Two transports are specified here. The first is built. The second is declared and deliberately
not built, and the reason is part of the contract.

---

## Transport A — a file the developer's collector writes (built)

### Shape

```text
Claude Code  --OTLP-->  OpenTelemetry Collector  --fileexporter-->  a file  <--read--  processrecall
```

The memory sits entirely on the right of that last arrow. It opens no socket, starts no
process, and writes nothing on the developer's side of it.

### Format

`fileexporter` with `format: json` writes **one JSON object per line**. Each object is an OTLP
logs payload, so the reader iterates:

```text
resourceLogs[] -> scopeLogs[] -> logRecords[]
```

and takes each `logRecords` entry as one telemetry record. It must not assume one record per
line: batching is the developer's configuration, not ours, and a line may carry one record or
a hundred. Iterating the three levels is correct at every batch size.

Attributes arrive as OTLP key/value pairs (`{"key": ..., "value": {"stringValue": ...}}`) and
are flattened by the reader. `event.name` selects the record type; an `event.name` the memory
does not recognise increments `telemetry_record_unknown` and is skipped.

### Rotation and append

`fileexporter`'s `rotation` and `append: true` are mutually exclusive. The shipped example
uses neither: the file grows, and a byte offset into it means something. A developer who
enables rotation is not broken — the fingerprint check below detects it — but will re-read.

### Resumption

Persisted beside the store, as a triple:

| Field | Meaning |
|---|---|
| `path` | the file this offset belongs to |
| `offset` | bytes consumed |
| `fingerprint` | hash of the first 4 KiB, plus the total size at last read |

On every pass, before seeking: re-read the fingerprint region and compare.

- Match, and the file is at least as long as `offset` — seek and continue.
- Mismatch, or the file is shorter than `offset` — the file was rotated, truncated or
  replaced. Reset to zero, bump `telemetry_offset_reset`, re-read everything.
- `path` changed — treat as a new file, offset zero, no counter.

Re-reading is safe because ingest is idempotent on `tool_use_id#ordinal` and on the inference
id: a record seen twice writes once and bumps the dedup counter. That idempotence is what
lets the offset be a hint rather than a contract.

**The offset is never trusted without the fingerprint.** A stale offset into a rotated file of
the same name yields well-formed JSON from the wrong place, which is the one failure mode here
that would be silent.

### Partial lines

A trailing line with no newline is mid-write. It is not consumed, and the offset stops before
it, so the next pass reads it whole. A *complete* line that will not parse increments
`telemetry_record_partial` and is skipped; the pass does not abort. This is FR-009: a
malformed record loses that record, never the pass.

### When it is read

In the detached end-of-unit-of-work job that `SessionEnd` already spawns —
`processrecall rebuild --project <dir> --release-lock <lock>` — before the semantic derivation
and before the fold. Never in a hook. Never on the guidance path. Never while an agent waits
(R20, SC-005).

### Absence

A configured path that does not exist is a **cold start**, not an error: `telemetry_absent` is
bumped, the hook's own capture stands as the record, and guidance is unaffected. A file whose
newest record is older than the session being closed increments `telemetry_stale`. Neither
condition fails a command or surfaces to the agent; both surface to
`processrecall doctor` (FR-010).

Staleness is judged on the file's modification time, a stand-in for its newest record: it is
what tells a stale source from a fresh one without opening the file, which is otherwise
"passed over without being opened" the way absence is. A collector that flushes a batch of
pre-session records after the session opens, or a file merely touched empty, would read as
fresh under this proxy; no collector this transport ships is known to do either.

### What ships

One example collector configuration as package data, printed by
`processrecall doctor --collector-config`, asserted present by the packaging test. The memory
never writes it into the developer's collector directory and never starts a collector. Its
content declares, at minimum: an OTLP receiver on loopback, a batch processor, a `fileexporter`
with `format: json` and no rotation, and the two environment settings the memory needs on the
Claude Code side (`OTEL_METRICS_INCLUDE_VERSION=true`, and the tool-details gate with its
privacy consequence stated in a comment).

### Readiness check

`processrecall doctor` reports, in this order: whether the configured path exists; when it was
last modified; how many bytes lie between the persisted offset and the end; whether
`app.version` was observed; whether `session.id` was observed; which content and detail gates
the observed records imply; and the current value of every telemetry counter. A missing file
reads as "no telemetry source", not as a failure.

---

## Transport B — an in-plugin OTLP receiver (declared, not built)

FR-005 requires the alternative to be declared. This section is that declaration. **No code
implements it, no setting enables it, and no branch anticipates it.**

Were it built, it would have to be:

| Aspect | Requirement |
|---|---|
| Endpoint | loopback only, never `0.0.0.0`, port from configuration with no default that binds |
| Protocol | OTLP/HTTP `POST /v1/logs`, JSON payload — same record shape as Transport A, so the reader is shared |
| Lifecycle | bound only while a session is open; released at `SessionEnd`; never outliving the process that opened it |
| Backpressure | bounded queue, oldest dropped, drop counted |
| Failure | a bind failure is a counted degradation, never a refusal of the hook |
| Privacy | identical allow-list; the transport does not change what is readable |

**Why it is not built.** The project holds a standing decision that there is no listening port
on the capture path. A port that exists is a port, whether or not a setting enables it: it is
a surface to audit, a failure mode to support, and a contradiction of "no services, no
network" that a default would only hide. The file transport achieves the same ingest with the
network boundary entirely outside the package, where the developer already controls it.

**What would have to change to adopt it.** The standing decision, explicitly, in
`docs/design.md` — not in a settings default. This section exists so that decision can be
taken later without re-deriving the requirements.
