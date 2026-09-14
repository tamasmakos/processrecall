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
| `backoff_order` | `3` | how many preceding procedures the variable-order back-off starts from |
| `clean_prompt_weight` | `4.0` | how much a transition seen inside a cleanly-ended prompt outweighs one that was not |
| `enforce` | `false` | whether a pre-action move a pitfall matches is refused outright. Off is the shipped state: the memory advises, and only an operator who asks for it lets it stop an action |
| `same_file_conditioning` | `true` | whether the back-off also conditions on the previous procedure having stayed on the same file |

A `level` naming none of the materialised ones is refused at load: it is not a
quieter setting but a renderer that finds no node and a hook that never speaks
again.

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
