"""Preparing the plugin's runtime environment, once (FR-068, FR-069, R14).

``bin/bootstrap.sh`` is the whole of the policy: the fast path that every
session after the first takes, the one pinned install that the first one pays
for, and the single `systemMessage` that a missing `uv` is reported with instead
of a failed session. It is asserted the way the harness runs it — a real ``sh``
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

#: Where a plugin root declares the release it runs, relative to that root.
MANIFEST = Path(".claude-plugin") / "plugin.json"


def ready_key(root: Path = ROOT) -> str:
    """The marker the script writes for *root*: the version its manifest pins.

    Read out of the same manifest the script reads, rather than restated here,
    so the assertion cannot come to agree with a pin the script never sees.
    """
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    return str(manifest["version"])


def prepare(
    data: Path, path: str, root: Path = ROOT, *, source: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the bootstrap script over *data*, seeing only the tooling on *path*.

    *source* is the development switch, left out of the environment entirely
    when it is ``None``: an end user's session is the one that never sets it.
    """
    switch = {"PROCESSRECALL_PLUGIN_SOURCE": source} if source is not None else {}
    return subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "PATH": path,
            "CLAUDE_PLUGIN_ROOT": str(root),
            "CLAUDE_PLUGIN_DATA": str(data),
            **switch,
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


def rooted_at(destination: Path, pin: str) -> Path:
    """A plugin root at *destination* whose manifest pins *pin*, written in place."""
    manifest = destination / MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"name": "processrecall", "version": pin}), encoding="utf-8")
    return destination


def path_without_uv(tmp_path: Path) -> str:
    """A ``PATH`` carrying the script's own tools but guaranteed no ``uv`` on it.

    ``/usr/bin:/bin`` merely happens to lack ``uv`` on most machines; this
    builds the minimal ``PATH`` the assertion actually needs, so a host that
    installed ``uv`` there cannot flip it.
    """
    bin_dir = tmp_path / "min-bin"
    bin_dir.mkdir()
    for tool in ("sh", "sed", "uname", "cat", "mkdir"):
        if found := shutil.which(tool):
            # A one-line `sh` shim rather than a symlink: Windows reserves
            # symlink creation to a privileged account, and the script only ever
            # reaches these through `PATH`, where a shim is indistinguishable.
            shim = bin_dir / tool
            shim.write_text(f'#!/bin/sh\nexec "{Path(found).as_posix()}" "$@"\n', encoding="utf-8")
            shim.chmod(0o755)
    return str(bin_dir)


def stub_uv(bin_dir: Path, body: str) -> str:
    """A ``PATH`` whose only ``uv`` is *body* — the install itself never runs here."""
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


@pytest.fixture
def uv_stub(tmp_path: Path) -> tuple[str, Path]:
    """A ``PATH`` carrying a ``uv`` that records its invocation instead of installing.

    Each line is the environment the venv is named in, then the arguments, so a
    case can assert which environment a subcommand was pointed at.
    """
    calls = tmp_path / "uv-calls.txt"
    path = stub_uv(tmp_path / "stub-bin", f'echo "[${{VIRTUAL_ENV:-}}] $*" >> "{calls}"')
    return path, calls


def test_a_ready_marker_naming_this_pin_costs_nothing(
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


def test_a_changed_pin_is_what_makes_the_marker_stale(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """The pin is the whole of the upgrade path: a new release is a new environment."""
    path, calls = uv_stub
    root = rooted_at(tmp_path / "install", "0.1.0")
    data = tmp_path / "plugin-data"
    mark_ready(data, ready_key(root))
    rooted_at(root, "0.2.0")

    result = prepare(data, path, root)

    assert result.returncode == 0, result.stderr
    assert calls.exists(), "a changed pin left the venv holding the previous release"
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == ready_key(root)


def test_a_changed_root_is_not_what_makes_the_marker_stale(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """The install is no longer editable, so the venv is not a pointer at a root.

    The retired key carried the root because `uv sync` installed the project
    editable; an install of a released distribution cannot go stale that way,
    and asserting the absence is what stops the hazard creeping back.
    """
    path, calls = uv_stub
    data = tmp_path / "plugin-data"
    previous = rooted_at(tmp_path / "install" / "unpacked", "0.1.0")
    current = rooted_at(tmp_path / "install" / "moved", "0.1.0")
    mark_ready(data, ready_key(previous))

    result = prepare(data, path, current)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not calls.exists(), f"a moved root re-installed: {calls.read_text(encoding='utf-8')}"
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == ready_key(current)


def test_an_unreadable_manifest_is_one_message_and_an_unharmed_session(tmp_path: Path) -> None:
    """FR-069: the key cannot be read, so nothing is claimed and nothing is raised."""
    root = tmp_path / "install"
    root.mkdir()
    data = tmp_path / "plugin-data"

    result = prepare(data, path_without_uv(tmp_path), root)

    assert result.returncode == 0, result.stderr
    assert "plugin.json" in json.loads(result.stdout)["systemMessage"]
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


def test_the_first_session_installs_the_pinned_release_once(
    tmp_path: Path, uv_stub: tuple[str, Path]
) -> None:
    """FR-068: one venv and one pinned install into the harness's data directory."""
    path, calls = uv_stub
    data = tmp_path / "plugin-data"

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    # The venv path is the script's own join of `$data` and `venv`, not this
    # platform's: `sh` writes a forward slash even where `Path` would not.
    venv = f"{data}/venv"
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"[] venv {venv}",
        f"[{venv}] pip install processrecall=={ready_key()}",
    ]
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == ready_key()


@pytest.mark.parametrize(
    ("source", "requirement"),
    [(None, None), ("checkout", f"--editable {ROOT}")],
)
def test_the_checkout_switch_is_what_names_the_root_as_the_source(
    tmp_path: Path, uv_stub: tuple[str, Path], source: str | None, requirement: str | None
) -> None:
    """FR-024: set to `checkout`, the root editable; unset, no `--editable` at all.

    The pinned-install contract for the unset case is
    ``test_the_first_session_installs_the_pinned_release_once``'s job; this test
    is only about what the switch itself changes. Either way the marker holds
    the manifest pin, so the switch decides what is installed and never when
    preparation runs again.
    """
    path, calls = uv_stub
    data = tmp_path / "plugin-data"

    result = prepare(data, path, source=source)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    installed = calls.read_text(encoding="utf-8").splitlines()[-1]
    if requirement is None:
        assert "--editable" not in installed
    else:
        assert installed == f"[{data}/venv] pip install {requirement}"
    assert (data / "venv" / ".ready").read_text(encoding="utf-8") == ready_key()


def test_an_install_that_fails_is_reported_rather_than_marked_ready(tmp_path: Path) -> None:
    """FR-069: an environment that was not prepared says so, and says what to run."""
    data = tmp_path / "plugin-data"
    path = stub_uv(tmp_path / "stub-bin", 'echo "no such version" >&2; exit 1')

    result = prepare(data, path)

    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert f"uv pip install processrecall=={ready_key()}" in message, message
    assert not (data / "venv" / ".ready").exists()


def test_the_session_start_verb_is_unreachable_by_design() -> None:
    """R14: `hooks.json` runs `bin/bootstrap.sh` itself; this verb has nothing to do."""
    assert hooks.bootstrap({"hook_event_name": "SessionStart", "source": "startup"}) is None
