# Configuration

Defaults are the shipped tuning, and an installation that sets nothing is the
ordinary case. Every field below is a decision the design records, kept as
configuration precisely so that a measurement which revises it changes a value
rather than code.

## Where a value comes from

Three sources, in increasing precedence:

1. the shipped default;
2. `~/.processrecall/config.json` — a flat JSON object. A key naming no field is
   dropped with a log line rather than raising, so a stale key cannot stop a
   session from starting;
3. the environment — `PROCESSRECALL_` plus the field name, upper-cased, parsed
   to the type of the field's default.

Read back which value won, and where it came from, with:

```bash
python -m processrecall.cli show config
```

## The settings

| Setting | Default | Means |
| --- | --- | --- |
| `level` | `class/program` | the generality guidance is served at; one of `class`, `class/program`, `class/program/ext`. All three stay materialised on every node — this picks which one the renderer reads |
| `k` | `3` | repetitions of a procedure before the `repetition` trigger fires |
| `h` | `1` | radius, in hops, of the neighbourhood extracted around the located node |
| `min_support` | `2` | episodes an edge needs before guidance may be served from it; below it the edge exists, silently |
| `project_weight` | `1.5` | how much a candidate list from this project's own graph outweighs a cross-project one in the fused ranking; enough to decide between otherwise equal lists, not enough to beat two agreeing traversals elsewhere |
| `backoff_order` | `3` | how many preceding procedures the variable-order back-off starts from |
| `clean_prompt_weight` | `4.0` | how much a transition seen inside a cleanly-ended prompt outweighs one that was not |
| `enforce` | `false` | whether a pre-action move a pitfall matches is refused outright. Off is the shipped state: the memory advises, and only an operator who asks for it lets it stop an action |
| `same_file_conditioning` | `true` | whether the back-off also conditions on the previous procedure having stayed on the same file |
| `telemetry_path` | *empty* | the collector output file telemetry is read from. Empty is the shipped state: until you name the file your own collector writes there is no telemetry source, and `doctor` reports so |
| `unmeasured_traversals` | `false` | whether a traversal that has not beaten the baseline on the held-out temporal split may contribute candidates to the fused result. Off is the shipped state: it still runs and is still measured, but stays dark |

A `level` naming none of the materialised ones is refused at load: it is not a
quieter setting but a renderer that finds no node and a hook that never speaks
again.

## Telemetry

`telemetry_path` names a file the memory only reads. The collector that writes it
is yours: processrecall never installs, starts or supervises one. Nor does it read
the file on the hook path — the end-of-unit-of-work pass drains it, resuming from a
persisted read offset.

The plugin ships an example collector configuration as package data. Print it,
review it, and keep it where you keep your own:

```bash
python -m processrecall.cli doctor --collector-config > otel-collector.yaml
otelcol --config otel-collector.yaml
```

Then set `telemetry_path` to the path that configuration's `file` exporter writes,
and check the result:

```bash
python -m processrecall.cli doctor
```

The report says whether the named file exists, when it was last written to, and
which gates the records it read imply. A file that is not there yet reads as no
telemetry source rather than as a failure: naming it before the collector has
written anything is the ordinary cold start.

### The harness gates

Claude Code exports nothing until its own environment variables are set. These are
the harness's, not the memory's: they carry no `PROCESSRECALL_` prefix and
`show config` does not report them.

| Variable | Set to | Default | With it off |
| --- | --- | --- | --- |
| `CLAUDE_CODE_ENABLE_TELEMETRY` | `1` | unset | nothing is exported at all |
| `OTEL_LOGS_EXPORTER` | `otlp` | unset | the records ride no signal the reader consumes |
| `OTEL_METRICS_INCLUDE_VERSION` | `true` | off | no `app.version`, so no harness version floor can be checked and `gap_version_floor` is bumped |
| `OTEL_METRICS_INCLUDE_SESSION_ID` | `true` | on | every record is session-unbound and attaches to nothing |
| `OTEL_LOG_TOOL_DETAILS` | `1` | unset | no file paths and no command text, so touched entities fall back to the hook's own capture and `gap_tool_details` is bumped |

The last one is a privacy decision rather than a tuning one. With it on, tool
parameters and tool input — file paths, command lines, search patterns, the text of
an edit — land in the collector's file in the clear, and anyone who can read that
file reads them. The memory strips prompts, responses and identity before storing,
but the collector has already written the line by then.

## Where the data lives

| Path | Holds |
| --- | --- |
| `~/.processrecall/episodes.db` | the episodic index — every recorded step, and the counters. Private, and retained until an explicit `prune` |
| `~/.processrecall/graph.json` | the cross-project abstract graph |
| `<project>/.processrecall/graph.json` | the project's own abstract graph. Ignored by version control by default, and safe to commit deliberately: node names, templates, counts and annotations only |
| `~/.processrecall/config.json` | the settings above |
| `~/.processrecall/deny.txt` | the exclusion patterns |

## Excluding a project

Recording is on by default. Either marker suppresses capture entirely, and both
are checked before anything is written:

- `<project>/.processrecall/optout` — presence is the whole signal, contents
  ignored;
- `~/.processrecall/deny.txt` — one glob pattern per line, matched against the
  project's absolute path. Blank lines and `#` comments are ignored, and an
  absent list denies nothing.
