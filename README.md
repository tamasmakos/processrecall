# processrecall

Procedural graph memory for a coding agent. It records what the agent *did* —
every completed action as a node in a temporal procedural graph — and serves
back what usually comes next from that position, at the four moments worth
interrupting for.

It ships as a Claude Code plugin and runs inside the session: no background
service, no listening port, and no network call on the capture or guidance
path (the bootstrap sync below is the one exception, once per plugin
version). What it keeps is shape — action templates, counts, conditions and
annotations — never prompt text, file contents or credentials.

## Install

The repository is its own marketplace. Two commands in a Claude Code session:

```
/plugin marketplace add tamasmakos/processrecall
/plugin install processrecall@processrecall
```

Two things must already be there, and neither announces itself when missing:
[uv](https://docs.astral.sh/uv/) on your `PATH`, and on Windows a POSIX `sh` —
that is Git for Windows, which Claude Code itself already requires. Every hook
runs through `sh`.

The first session does the rest. `SessionStart` runs
[bin/bootstrap.sh](https://github.com/tamasmakos/processrecall/blob/main/bin/bootstrap.sh),
which syncs the plugin's own virtual environment under `$CLAUDE_PLUGIN_DATA/venv`
with uv — once, and a no-op on every session after that. Every hook invokes that
interpreter by absolute path, and the tool server reaches the same environment
through `uv run`, so nothing here depends on what `python` means on your `PATH`.

The tool server prepares the same environment itself when it starts, so it
connects on that first session too, rather than only after a restart.

The `processrecall-mcp` console script in that environment is the stdio tool
server, launchable by any MCP client.

Without Claude Code, the package installs on its own: `uv tool install
processrecall` (or `pip install processrecall`) takes the same release from
PyPI, with no plugin and no checkout involved.

```bash
uv tool install processrecall
processrecall       # stdio tool server, the name a registry client runs
processrecall-mcp   # the same server, kept for existing invocations
```

To work on the plugin instead, point Claude Code at a checkout:

```bash
git clone https://github.com/tamasmakos/processrecall.git
claude --plugin-dir processrecall
```

Set `PROCESSRECALL_PLUGIN_SOURCE=checkout` to have bootstrap install that
checkout as an editable install instead of the pinned release. The switch is
only read the first time bootstrap runs for a given plugin version, so
flipping it on a machine that already prepared that version requires deleting
`$CLAUDE_PLUGIN_DATA/venv/.ready` first.

## What it records

Each completed tool call is abstracted to a template — the program, the activity
class it belongs to and the files it touched, with no arguments carried over —
and appended to the episodic index at `~/.processrecall/episodes.db`. End of work
folds those rows into two adjacency snapshots by counting: `graph.json` under the
project's own `.processrecall/` directory, and a cross-project one under
`~/.processrecall/` for procedures this project has not seen yet.

## The four triggers

Silence is the default. Guidance is offered on four occasions and no fifth, and
never from an edge supported by fewer than `min_support` episodes — a single
observation is silence, not low-confidence advice.

| Trigger | Fires | Serves |
| --- | --- | --- |
| `prompt_start` | a prompt begins, before any action | the usual first moves for this kind of work |
| `after_write` | a write whose usual successor verifies it | the verification that usually follows |
| `repetition` | the same procedure `k` times running | the loop, once per procedure and sequence |
| `pitfall` | the likeliest next move fails often | the warning, one step before the move |

Every statement shows the number of episodes behind it. Rendering is
deterministic and no language model takes part in it.

## The five commands

The package lives in the plugin's environment and on no `PATH`, so call its
interpreter by path — `~/.claude/plugins/data/processrecall-processrecall/venv/bin/python`,
or `...\venv\Scripts\python.exe` on Windows:

```bash
~/.claude/plugins/data/processrecall-processrecall/venv/bin/python -m processrecall.cli show counters
```

| Command | Does |
| --- | --- |
| `bootstrap` | prepare the plugin's environment as `SessionStart` does |
| `backfill` | replay the harness's own past sessions into the episodic index |
| `rebuild` | re-derive both snapshots; `--check` compares instead of writing |
| `prune` | delete episodic history before `--before`, then re-derive |
| `show` | print the memory's shape, never a payload |

`show` takes one subject: `graph` (nodes, edges, conditions, annotations),
`counters`, `sequences` (recent prompts and how they ended) or `config` (every
value with where it came from).

## The four tools

The plugin's stdio server exposes four tools, in the order the agent meets them:

| Tool | Answers |
| --- | --- |
| `recall` | guidance for a named procedure, or for where the work already is |
| `remember` | attach one note to one move, so the next run reads the reason |
| `mark_outcome` | declare how a piece of work turned out, beside the derived outcome |
| `inspect` | the graph in counts: nodes, edges, conditions, annotations, counters |

The shipped [remember skill](https://github.com/tamasmakos/processrecall/blob/main/skills/remember/SKILL.md)
says when a note is worth writing.

## The counters

Nothing here fails loudly against a developer's own turn, so every suppression
is counted instead: `guidance_silent`, `guidance_below_support`,
`guidance_deadline_exceeded`, `capture_excluded`, `capture_store_busy`,
`steps_recorded` and the rest. `show counters` prints the full table — every
counter the package can increment against what the store kept — and the
`inspect` tool serves the same table to the agent.

## Excluding a project

Recording is on by default for every project. Two ways out, both checked before
anything is written, so an excluded project leaves no step, no snippet and no
prompt text behind:

- **A project marker** — create `.processrecall/optout` in the project
  directory. Its presence is the whole signal; the contents are ignored.
- **A home deny list** — `~/.processrecall/deny.txt`, one glob pattern per line
  matched against the project's absolute path. Blank lines and `#` comments are
  ignored, and an absent list denies nothing. This is the one to use for a
  repository you would rather not add a marker file to.

## Documentation

- [docs/architecture.md](https://github.com/tamasmakos/processrecall/blob/main/docs/architecture.md)
  — capture, derivation, serving and what each module owns
- [docs/configuration.md](https://github.com/tamasmakos/processrecall/blob/main/docs/configuration.md)
  — every setting, its default and where it can be set
- [docs/design.md](https://github.com/tamasmakos/processrecall/blob/main/docs/design.md)
  — historical design record: the GraphKnows fork this plugin was cut from,
  kept for provenance, not a description of what ships here

## License

[Apache-2.0](https://github.com/tamasmakos/processrecall/blob/main/LICENSE).
