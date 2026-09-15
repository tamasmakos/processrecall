# processrecall documentation

Procedural graph memory for a coding agent: it records what the agent *did* and
serves back what usually comes next. It ships as a Claude Code plugin and runs
inside the session — no service, no port, no network on the hot path.

Start at the [README](../README.md) for install, the four triggers, the five
commands, the four tools, the counters and how to exclude a project.

| Answers | Page |
| --- | --- |
| What each module owns, and how capture, derivation and serving stack. | [architecture.md](architecture.md) |
| Every setting, its default, where it can be set and how to read back which value won. | [configuration.md](configuration.md) |
| Why the system is shaped this way — the decisions, settled and dated. | [design.md](design.md) |

`docs/agents/` is how this repository is worked on — tickets, labels, workflows —
and not part of the installed package.
