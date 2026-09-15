"""`processrecall bootstrap`: the hook's preparation, by hand, with an exit code.

The policy itself lives in ``bin/bootstrap.sh`` and is asserted against that
script in ``tests/integrations/claude_code/test_bootstrap.py``. What is asserted
here is only what the command adds to it: a human at a terminal is told whether
the environment was prepared, in the exit code as well as in words (FR-069 binds
the hook, not this).

No case below lets a real ``uv`` run: the ``uv`` on the ``PATH`` is a stub that
records its arguments or fails on purpose, so the command never reaches the
network.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from processrecall.cli.bootstrap import Installation

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]

#: What the marker has to hold for the script to consider itself done: the
#: `cksum` of the lock it synced from, joined to the root it synced editable to.
READY_KEY = "{} {}".format(
    subprocess.run(
        ["cksum"], input=(ROOT / "uv.lock").read_bytes(), capture_output=True, check=True
    )
    .stdout.decode()
    .strip(),
    ROOT,
)


def stub_uv(bin_dir: Path, body: str) -> str:
    """A ``PATH`` whose only ``uv`` is *body* — the sync itself never runs here."""
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def run_cli(environment: dict[str, str], *flags: str) -> subprocess.CompletedProcess[str]:
    """What `python -m processrecall.cli bootstrap` did, seeing *environment*."""
    return subprocess.run(
        [sys.executable, "-m", "processrecall.cli", "bootstrap", *flags],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def run_bootstrap(data: Path, path: str, *flags: str) -> subprocess.CompletedProcess[str]:
    """What the command did over an install whose data directory is *data*."""
    return run_cli(
        {
            **os.environ,
            "PATH": path,
            "CLAUDE_PLUGIN_ROOT": str(ROOT),
            "CLAUDE_PLUGIN_DATA": str(data),
        },
        *flags,
    )


def test_a_sync_that_failed_is_an_exit_code_as_well_as_a_message(tmp_path: Path) -> None:
    """`contracts/cli.md`: unlike the hook, a failure here exits non-zero."""
    data = tmp_path / "plugin-data"
    path = stub_uv(tmp_path / "stub-bin", 'echo "lock is out of date" >&2; exit 1')

    finished = run_bootstrap(data, path)

    assert finished.returncode != 0
    assert "uv sync --frozen" in finished.stdout, finished.stdout
    assert not Installation(root=ROOT, data=data).ready.exists()


def test_force_re_syncs_an_environment_the_marker_calls_ready(tmp_path: Path) -> None:
    """`contracts/cli.md`: `--force` is what a stale-looking venv is rebuilt with."""
    data = tmp_path / "plugin-data"
    ready = Installation(root=ROOT, data=data).ready
    ready.parent.mkdir(parents=True)
    ready.write_text(READY_KEY, encoding="utf-8")
    calls = tmp_path / "uv-calls.txt"
    path = stub_uv(tmp_path / "stub-bin", f'echo "$UV_PROJECT_ENVIRONMENT $*" >> "{calls}"')

    assert run_bootstrap(data, path).returncode == 0
    assert not calls.exists(), "the fast path re-synced without being asked to"

    finished = run_bootstrap(data, path, "--force")

    assert finished.returncode == 0, finished.stdout
    assert calls.exists(), "--force did not re-invoke uv on an environment already marked ready"


def test_an_install_the_harness_never_named_is_reported_rather_than_raised() -> None:
    """Run outside a session, the command says which variables to set."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"}
    }

    finished = run_cli(environment)

    assert finished.returncode != 0
    assert finished.stderr == "", finished.stderr
    assert "CLAUDE_PLUGIN_ROOT" in finished.stdout, finished.stdout
    assert "CLAUDE_PLUGIN_DATA" in finished.stdout, finished.stdout
