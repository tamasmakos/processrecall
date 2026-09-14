"""Preparing the plugin's runtime environment, once (FR-068, FR-069, R14).

``bin/bootstrap.sh`` is the whole of the policy: the fast path that every
session after the first takes, the one `uv sync` that the first one pays for,
and the single `systemMessage` that a missing `uv` is reported with instead of
a failed session. It is asserted the way the harness runs it — a real ``sh``
process over a plugin data directory of the test's own, with a ``PATH`` that
holds exactly the tooling the case is about.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from processrecall.integrations.claude_code import hooks

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "bin" / "bootstrap.sh"

#: The version the script itself reads, from the same file it reads it from —
#: not the package's, which merely happens to agree with it today.
PLUGIN_VERSION = re.search(
    r'"version"\s*:\s*"([^"]*)"', (ROOT / ".claude-plugin" / "plugin.json").read_text()
).group(1)


def prepare(data: Path, path: str) -> subprocess.CompletedProcess[str]:
    """Run the bootstrap script over *data*, seeing only the tooling on *path*."""
    return subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "PATH": path,
            "CLAUDE_PLUGIN_ROOT": str(ROOT),
            "CLAUDE_PLUGIN_DATA": str(data),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def path_without_uv(tmp_path: Path) -> str:
    """A ``PATH`` carrying the script's own tools but guaranteed no ``uv`` on it.

    ``/usr/bin:/bin`` merely happens to lack ``uv`` on most machines; this
    builds the minimal ``PATH`` the assertion actually needs, so a host that
    installed ``uv`` there cannot flip it.
    """
    bin_dir = tmp_path / "min-bin"
    bin_dir.mkdir()
    for tool in ("sh", "sed", "head", "cat", "mkdir"):
        if found := shutil.which(tool):
            (bin_dir / tool).symlink_to(found)
    return str(bin_dir)


def stub_uv(bin_dir: Path, body: str) -> str:
    """A ``PATH`` whose only ``uv`` is *body* — the sync itself never runs here."""
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


@pytest.fixture
def uv_stub(tmp_path: Path) -> tuple[str, Path]:
    """A ``PATH`` carrying a ``uv`` that records its invocation instead of syncing."""
    calls = tmp_path / "uv-calls.txt"
    path = stub_uv(tmp_path / "stub-bin", f'echo "$UV_PROJECT_ENVIRONMENT $*" >> "{calls}"')
    return path, calls


def test_a_ready_marker_naming_this_version_costs_nothing(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """FR-068: prepared exactly once — every later session exits before `uv`."""
    path, calls = uv_stub
    data = tmp_path / "plugin-data"
    (data / "venv").mkdir(parents=True)
    (data / "venv" / ".ready").write_text(PLUGIN_VERSION, encoding="utf-8")

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not calls.exists(), f"uv was invoked: {calls.read_text(encoding='utf-8')}"


def test_a_missing_uv_is_one_message_and_an_unharmed_session(tmp_path: Path) -> None:
    """FR-069: what is missing and how to install it, said once, exit 0."""
    data = tmp_path / "plugin-data"

    result = prepare(data, path_without_uv(tmp_path))

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert "uv" in message
    assert "astral.sh/uv" in message, message
    assert not (data / "venv" / ".ready").exists()


def test_the_first_session_syncs_the_locked_environment_once(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """FR-068: one locked sync into the harness's data directory, then a marker."""
    path, calls = uv_stub
    data = tmp_path / "plugin-data"

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    invocations = calls.read_text(encoding="utf-8").splitlines()
    assert invocations == [f"{data / 'venv'} sync --frozen --project {ROOT}"]
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == PLUGIN_VERSION


def test_a_sync_that_fails_is_reported_rather_than_marked_ready(tmp_path: Path) -> None:
    """FR-069: an environment that was not prepared says so, and says what to run."""
    data = tmp_path / "plugin-data"
    path = stub_uv(tmp_path / "stub-bin", 'echo "lock is out of date" >&2; exit 1')

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert "uv sync --frozen" in message, message
    assert not (data / "venv" / ".ready").exists()


def test_the_session_start_verb_is_unreachable_by_design() -> None:
    """R14: `hooks.json` runs `bin/bootstrap.sh` itself; this verb has nothing to do."""
    assert hooks.bootstrap({"hook_event_name": "SessionStart", "source": "startup"}) is None
