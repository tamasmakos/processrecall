"""`processrecall bootstrap` — the `SessionStart` preparation, run by hand.

The policy is not restated here: this runs `bin/bootstrap.sh`, the same script
the hook runs, so that debugging an install by hand cannot be debugging a
second implementation of it (FR-068). What the command adds is the exit code —
the hook must never surface a failure against the developer's own session
(FR-069), and a human at a terminal wants exactly that.

Example:
    from processrecall.cli.bootstrap import prepare

    print(prepare(force=True) or "the plugin environment is prepared")
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The script the `SessionStart` hook runs, relative to the plugin root.
SCRIPT = Path("bin") / "bootstrap.sh"

#: The variables the harness names the install and its data directory in.
ROOT_VARIABLE = "CLAUDE_PLUGIN_ROOT"
DATA_VARIABLE = "CLAUDE_PLUGIN_DATA"


@dataclass(frozen=True, slots=True)
class Installation:
    """Where the plugin is installed, and where its environment is prepared.

    Attributes:
        root: The plugin root, holding the script and the lock it syncs from.
        data: The harness-provided data directory the venv is built under.
    """

    root: Path
    data: Path

    @classmethod
    def from_environment(cls) -> Installation | None:
        """The installation the harness named, or ``None`` outside a session."""
        root, data = os.environ.get(ROOT_VARIABLE), os.environ.get(DATA_VARIABLE)
        return cls(Path(root), Path(data)) if root and data else None

    @property
    def ready(self) -> Path:
        """The marker the script's fast path reads the prepared version from."""
        return self.data / "venv" / ".ready"


def prepare(force: bool) -> str | None:
    """Prepare the plugin's environment; the reason it is not, or ``None``.

    *force* drops the ready marker rather than passing a flag the script does
    not have: the marker matching this version is the whole of the fast path,
    so removing it is what "sync again" means (R14).

    The script reports a failure as one `systemMessage` and exits 0, since a
    hook that exited non-zero would break the session; the message is what a
    caller here turns back into a failure.
    """
    if (installation := Installation.from_environment()) is None:
        return (
            f"{ROOT_VARIABLE} and {DATA_VARIABLE} are not set. The harness sets them for the "
            "hook; by hand, point them at the plugin install and the directory to build its "
            "environment under."
        )
    if force:
        installation.ready.unlink(missing_ok=True)
    finished = subprocess.run(
        ["sh", str(installation.root / SCRIPT)], capture_output=True, text=True, check=False
    )
    if finished.returncode != 0:
        return f"{SCRIPT} exited {finished.returncode}: {finished.stderr}"
    if not finished.stdout:
        return None
    return str(json.loads(finished.stdout)["systemMessage"])
