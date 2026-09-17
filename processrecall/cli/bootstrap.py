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
import subprocess  # nosec B404
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
        root: The plugin root, holding the script and the manifest it reads the pin from.
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
    not have: the marker matching the pinned version is the whole of the fast
    path, so removing it is what "install the pin again" means (R14).

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
    # No shell, and no caller string in the argument vector: the only interpolation
    # is `installation.root`, which `Installation.from_environment` read from the
    # harness's own variables, joined to the fixed `SCRIPT` path (B603). `sh` is
    # deliberately resolved through PATH rather than hard-coded to /bin/sh, which
    # is not where every platform this installs on keeps it (B607).
    finished = subprocess.run(  # nosec B603 B607
        ["sh", str(installation.root / SCRIPT)], capture_output=True, text=True, check=False
    )
    if finished.returncode != 0:
        return f"{SCRIPT} exited {finished.returncode}: {finished.stderr}"
    if not finished.stdout:
        return None
    return str(json.loads(finished.stdout)["systemMessage"])
