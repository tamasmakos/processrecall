"""``python -m processrecall.cli`` — the command line of `contracts/cli.md`.

Argument parsing and the exit code are the whole of this module: unlike the
hook, which must never surface against a developer's own action (FR-014), a
terminal is exactly where a human expects to see one.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from processrecall.cli.show import Inspection, show
from processrecall.graph.episodic import open_index
from processrecall.graph.store import SQLiteEpisodicStore

#: The subjects `show` answers for, in the order `contracts/cli.md` lists them.
SUBJECTS = ("graph", "counters", "sequences", "config")


def _parser() -> argparse.ArgumentParser:
    """The command line of `contracts/cli.md`, as far as it is implemented."""
    parser = argparse.ArgumentParser(prog="processrecall")
    commands = parser.add_subparsers(dest="command", required=True)
    show_command = commands.add_parser("show", help="inspect the memory, printing no payloads")
    show_command.add_argument("subject", choices=SUBJECTS)
    show_command.add_argument(
        "--project", type=Path, default=Path.cwd(), help="the project to report on"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command named in *argv*, printing what it found; 0 when it answered."""
    arguments = _parser().parse_args(argv)
    with closing(open_index()) as connection:
        view = Inspection(store=SQLiteEpisodicStore(connection), project_dir=arguments.project)
        print(show(arguments.subject, view))
    return 0


if __name__ == "__main__":
    sys.exit(main())
