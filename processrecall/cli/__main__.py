"""``python -m processrecall.cli`` — the command line of `contracts/cli.md`.

Argument parsing and the exit code are the whole of this module: unlike the
hook, which must never surface against a developer's own action (FR-014), a
terminal is exactly where a human expects to see one.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from processrecall.cli.backfill import Replay, backfill, parse_since
from processrecall.cli.bootstrap import prepare
from processrecall.cli.doctor import readiness
from processrecall.cli.prune import Removal, episodes_before, prune
from processrecall.cli.rebuild import check, rebuild
from processrecall.cli.show import Inspection, show
from processrecall.config import LEVELS, load_config
from processrecall.graph.derive import Derivation
from processrecall.graph.episodic import open_index
from processrecall.graph.store import EpisodicStore, SQLiteEpisodicStore

#: The subjects `show` answers for, in the order `contracts/cli.md` lists them.
SUBJECTS = ("graph", "counters", "sequences", "config")

#: The subcommands this CLI offers, in the order `_parser` registers them.
COMMANDS = ("bootstrap", "backfill", "show", "rebuild", "prune", "doctor")


def _parser() -> argparse.ArgumentParser:
    """The command line of `contracts/cli.md`, as far as it is implemented."""
    parser = argparse.ArgumentParser(prog="processrecall")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap_command = commands.add_parser(
        COMMANDS[0], help="prepare the plugin's runtime environment as the hook does"
    )
    bootstrap_command.add_argument(
        "--force", action="store_true", help="sync again even when the ready marker matches"
    )
    backfill_command = commands.add_parser(
        COMMANDS[1], help="replay the harness's own past sessions through the live seam"
    )
    backfill_command.add_argument(
        "--project", type=Path, default=Path.cwd(), help="the project whose sessions to replay"
    )
    backfill_command.add_argument(
        "--since", type=parse_since, default=None, help="the earliest action to record"
    )
    backfill_command.add_argument(
        "--dry-run", action="store_true", help="report what would be written without writing it"
    )
    show_command = commands.add_parser(COMMANDS[2], help="inspect the memory, printing no payloads")
    show_command.add_argument("subject", choices=SUBJECTS)
    show_command.add_argument(
        "--project", type=Path, default=Path.cwd(), help="the project to report on"
    )
    rebuild_command = commands.add_parser(
        COMMANDS[3], help="re-derive both snapshots from the episodic index"
    )
    rebuild_command.add_argument(
        "--project", type=Path, default=Path.cwd(), help="the project whose snapshot to rebuild"
    )
    rebuild_command.add_argument(
        "--level", choices=LEVELS, default=None, help="the generality to fold at"
    )
    rebuild_command.add_argument(
        "--check",
        action="store_true",
        help="compare instead of writing, exiting non-zero on any difference",
    )
    rebuild_command.add_argument(
        "--release-lock",
        type=Path,
        default=None,
        help="remove this file once the rebuild finishes (the session-end job's own lock)",
    )
    prune_command = commands.add_parser(
        COMMANDS[4], help="delete the episodic history recorded before a date"
    )
    prune_command.add_argument(
        "--project", type=Path, default=Path.cwd(), help="the project whose snapshot to re-derive"
    )
    prune_command.add_argument(
        "--before",
        type=parse_since,
        required=True,
        help="delete every turn that began before this instant",
    )
    prune_command.add_argument(
        "--yes", action="store_true", help="delete without asking for confirmation"
    )
    commands.add_parser(
        COMMANDS[5], help="report on the collector file the memory reads telemetry from"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command named in *argv*, printing what it found; 0 when it answered."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "bootstrap":
        return _bootstrap(arguments)
    if arguments.command == "doctor":
        print(readiness(load_config().telemetry_path))
        return 0
    with closing(open_index()) as connection:
        if arguments.command == "rebuild":
            return _rebuild(arguments, SQLiteEpisodicStore(connection))
        if arguments.command == "prune":
            return _prune(arguments, SQLiteEpisodicStore(connection))
        if arguments.command == "backfill":
            return _backfill(arguments, connection)
        view = Inspection(store=SQLiteEpisodicStore(connection), project_dir=arguments.project)
        print(show(arguments.subject, view))
    return 0


def _bootstrap(arguments: argparse.Namespace) -> int:
    """Prepare the plugin's environment, saying whether it is (`contracts/cli.md`)."""
    if (failure := prepare(arguments.force)) is None:
        print("the plugin environment is prepared")
        return 0
    print(failure)
    return 1


def _backfill(arguments: argparse.Namespace, connection: sqlite3.Connection) -> int:
    """Replay past sessions, printing what landed (FR-015).

    Non-zero when the store dropped a write rather than landing or skipping
    it: unlike the hook, a terminal is exactly where that failure must surface
    (FR-014).
    """
    replayed = backfill(
        Replay(
            connection=connection,
            project_dir=arguments.project,
            since=arguments.since,
            dry_run=arguments.dry_run,
        )
    )
    print(replayed)
    return 1 if replayed.store_failures else 0


def _rebuild(arguments: argparse.Namespace, store: EpisodicStore) -> int:
    """Rewrite both snapshots, or compare them, printing what it found (FR-032).

    A divergence is an exit code as well as a line of output: `--check` is what
    a tree is judged consistent by, and a judgement nothing downstream can read
    is no judgement (SC-004).

    `--release-lock` removes the named file once this finishes, whether it
    wrote or merely compared: it is the session-end job's own lock (FR-050),
    freed here rather than left for the next session's staleness check to time
    out (`processrecall.integrations.claude_code.hooks._take_session_end_lock`).
    """
    try:
        config = load_config()
        level = arguments.level or config.level
        source = Derivation(store=store, project_dir=arguments.project, level=level, config=config)
        if not arguments.check:
            print(rebuild(source))
            return 0
        if (divergence := check(source)) is None:
            print("both snapshots match the episodic index")
            return 0
        print(divergence)
        return 1
    finally:
        if arguments.release_lock is not None:
            arguments.release_lock.unlink(missing_ok=True)


def _prune(arguments: argparse.Namespace, store: EpisodicStore) -> int:
    """Delete the history older than `--before`, asking first unless told not to (FR-057).

    The cutoff is required by the parser, so there is no run of this command
    that deletes without one; `--yes` answers the question in advance for a
    script, and a refusal leaves the store exactly as it was.
    """
    config = load_config()
    removal = episodes_before(store, arguments.before, arguments.project)
    if not arguments.yes and not _confirmed(removal):
        print("nothing removed")
        return 0
    source = Derivation(
        store=store, project_dir=arguments.project, level=config.level, config=config
    )
    print(prune(source, removal))
    return 0


def _confirmed(removal: Removal) -> bool:
    """Whether the operator at the terminal agreed to lose *removal*.

    An unanswerable prompt — a closed stdin, a job with no terminal — is a
    refusal and not an error: the question is the last thing standing between a
    cutoff and deleted history, so silence keeps the history.
    """
    try:
        answer = input(f"remove {removal}? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


if __name__ == "__main__":
    sys.exit(main())
