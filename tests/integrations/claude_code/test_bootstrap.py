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
import shutil
import subprocess
from pathlib import Path

import pytest

from processrecall.integrations.claude_code import hooks

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "bin" / "bootstrap.sh"


def ready_key(root: Path = ROOT) -> str:
    """The marker the script writes for *root*: `cksum` of its lock, then the root.

    Built the way the script builds it — a real ``cksum`` over the same bytes —
    rather than reimplemented here, so the assertion cannot come to agree with a
    checksum the script never computes.
    """
    checksum = subprocess.run(
        ["cksum"],
        input=(root / "uv.lock").read_bytes(),
        capture_output=True,
        check=True,
    ).stdout.decode()
    return f"{checksum.strip()} {root}"


def prepare(data: Path, path: str, root: Path = ROOT) -> subprocess.CompletedProcess[str]:
    """Run the bootstrap script over *data*, seeing only the tooling on *path*."""
    return subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "PATH": path,
            "CLAUDE_PLUGIN_ROOT": str(root),
            "CLAUDE_PLUGIN_DATA": str(data),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def mark_ready(data: Path, key: str) -> Path:
    """Write *key* as the prepared marker under *data*, and give back its path."""
    (data / "venv").mkdir(parents=True, exist_ok=True)
    marker = data / "venv" / ".ready"
    marker.write_text(key, encoding="utf-8")
    return marker


def copied_root(destination: Path) -> Path:
    """A second plugin root: the same lock, at the path a version bump gives it."""
    destination.mkdir(parents=True)
    shutil.copyfile(ROOT / "uv.lock", destination / "uv.lock")
    return destination


def path_without_uv(tmp_path: Path) -> str:
    """A ``PATH`` carrying the script's own tools but guaranteed no ``uv`` on it.

    ``/usr/bin:/bin`` merely happens to lack ``uv`` on most machines; this
    builds the minimal ``PATH`` the assertion actually needs, so a host that
    installed ``uv`` there cannot flip it.
    """
    bin_dir = tmp_path / "min-bin"
    bin_dir.mkdir()
    for tool in ("sh", "sed", "cksum", "uname", "cat", "mkdir"):
        if found := shutil.which(tool):
            # A one-line `sh` shim rather than a symlink: Windows reserves
            # symlink creation to a privileged account, and the script only ever
            # reaches these through `PATH`, where a shim is indistinguishable.
            shim = bin_dir / tool
            shim.write_text(f'#!/bin/sh\nexec "{Path(found).as_posix()}" "$@"\n', encoding="utf-8")
            shim.chmod(0o755)
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


def test_a_ready_marker_naming_this_lock_and_root_costs_nothing(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """FR-068: prepared exactly once — every later session exits before `uv`."""
    path, calls = uv_stub
    data = tmp_path / "plugin-data"
    mark_ready(data, ready_key())

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not calls.exists(), f"uv was invoked: {calls.read_text(encoding='utf-8')}"


def test_a_changed_lock_is_what_makes_the_marker_stale(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """The marketplace tracks `main`, where a merge changes the lock and not the version."""
    path, calls = uv_stub
    root = copied_root(tmp_path / "install")
    data = tmp_path / "plugin-data"
    mark_ready(data, ready_key(root))
    lock = root / "uv.lock"
    lock.write_bytes(lock.read_bytes() + b"\n# a newly merged dependency\n")

    result = prepare(data, path, root)

    assert result.returncode == 0, result.stderr
    assert calls.exists(), "a changed lock left the venv unsynced"
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == ready_key(root)


def test_a_changed_root_is_what_makes_the_marker_stale(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """The install is editable: a venv still pointing at the previous root is broken."""
    path, calls = uv_stub
    data = tmp_path / "plugin-data"
    previous = copied_root(tmp_path / "install" / "0.1.0")
    current = copied_root(tmp_path / "install" / "0.2.0")
    mark_ready(data, ready_key(previous))

    result = prepare(data, path, current)

    assert result.returncode == 0, result.stderr
    assert calls.exists(), "a moved root left the venv pointing at the old one"
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == ready_key(current)


def test_an_unreadable_lock_is_one_message_and_an_unharmed_session(tmp_path: Path) -> None:
    """FR-069: the key cannot be computed, so nothing is claimed and nothing is raised."""
    root = tmp_path / "install"
    root.mkdir()
    data = tmp_path / "plugin-data"

    result = prepare(data, path_without_uv(tmp_path), root)

    assert result.returncode == 0, result.stderr
    assert "uv.lock" in json.loads(result.stdout)["systemMessage"]
    assert not (data / "venv" / ".ready").exists()


def test_a_missing_uv_is_one_message_and_an_unharmed_session(tmp_path: Path) -> None:
    """FR-069: what is missing and how to install it, said once, exit 0."""
    data = tmp_path / "plugin-data"

    result = prepare(data, path_without_uv(tmp_path))

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert "uv" in message
    assert "astral.sh/uv" in message, message
    assert not (data / "venv" / ".ready").exists()


@pytest.mark.parametrize(
    ("system", "suggested", "withheld"),
    [
        ("MINGW64_NT-10.0", "install.ps1", "install.sh"),
        ("Darwin", "install.sh", "install.ps1"),
    ],
)
def test_the_install_hint_names_this_platform_s_installer(
    tmp_path: Path, system: str, suggested: str, withheld: str
) -> None:
    """FR-069: exactly one command to paste, and on Windows the curl line is not it."""
    data = tmp_path / "plugin-data"
    path = path_without_uv(tmp_path)
    uname = Path(path) / "uname"
    uname.unlink()
    uname.write_text(f"#!/bin/sh\necho {system}\n", encoding="utf-8")
    uname.chmod(0o755)

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert suggested in message, message
    assert withheld not in message, message


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
    # The venv path is the script's own join of `$data` and `venv`, not this
    # platform's: `sh` writes a forward slash even where `Path` would not.
    assert invocations == [f"{data}/venv sync --frozen --no-dev --project {ROOT}"]
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == ready_key()


def test_a_sync_that_fails_is_reported_rather_than_marked_ready(tmp_path: Path) -> None:
    """FR-069: an environment that was not prepared says so, and says what to run."""
    data = tmp_path / "plugin-data"
    path = stub_uv(tmp_path / "stub-bin", 'echo "lock is out of date" >&2; exit 1')

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert "uv sync --frozen --no-dev" in message, message
    assert not (data / "venv" / ".ready").exists()


def test_the_session_start_verb_is_unreachable_by_design() -> None:
    """R14: `hooks.json` runs `bin/bootstrap.sh` itself; this verb has nothing to do."""
    assert hooks.bootstrap({"hook_event_name": "SessionStart", "source": "startup"}) is None
