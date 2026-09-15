"""The tool-server declaration (FR-068).

`.claude-plugin/mcp.json` is the third half of the plugin claim the harness
reads: the stdio server that carries the four memory tools.

The harness spawns this one *directly* — no shell is interposed, unlike a hook
command — so the `command` key is a program name resolved against the operating
system's `PATH`, and nothing else. That is the whole of why these tests look at
spawnability rather than at the text of a command line: a declaration naming a
POSIX shell reads fine and is unspawnable on Windows, where `sh` is not on the
system `PATH` even when Git Bash is installed.

`uv` is the program named because the plugin already requires it — `bin/bootstrap.sh`
refuses to prepare the environment without it — and it is the one prerequisite
that installs onto the system `PATH` on every platform the plugin supports.
Naming it also retires the first-session race that the old `sh -c` wrapper
existed to paper over: the harness starts MCP servers in parallel with the
`SessionStart` hook that builds the venv, and `uv run` builds the very same venv
itself when it is not there yet.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import tomllib
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import manifest_declared

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The venv `bin/bootstrap.sh` prepares and every hook runs out of. The server
#: must share it: a second environment would double a 300 MB install and drift
#: from the one the hooks write the graph with.
PLUGIN_VENV = "${CLAUDE_PLUGIN_DATA}/venv"

#: Shells that a declaration must not name. Each is absent from the Windows
#: system `PATH`, so naming one makes the server unspawnable there.
SHELLS = ("sh", "bash", "dash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh")


@pytest.fixture(scope="module")
def server() -> dict[str, Any]:
    """The one declared server, reached the way the harness reaches it."""
    servers = manifest_declared("mcpServers")["mcpServers"]
    assert list(servers) == ["processrecall"], sorted(servers)
    entry: dict[str, Any] = servers["processrecall"]
    return entry


@pytest.fixture(scope="module")
def declared_script(server: dict[str, Any]) -> str:
    """The console script the launch hands to `uv run`.

    It is the last argument: everything before it is `uv`'s own, and `uv run`
    takes the command to run as its trailing positional.
    """
    return str(server["args"][-1])


def test_the_server_speaks_stdio(server: dict[str, Any]) -> None:
    """FR-070: no service, no port — JSON-RPC on the child's stdin and stdout."""
    assert server["type"] == "stdio", server.get("type")


def test_the_server_names_a_program_on_the_system_path(server: dict[str, Any]) -> None:
    """The harness spawns the command itself, so `PATH` must already resolve it.

    Worth only as much as the platform it runs on, which is why the `plugin`
    job in `.github/workflows/ci.yml` runs this file on `windows-latest` as
    well: on Linux `shutil.which("sh")` resolves, so this assertion alone would
    have passed for the very declaration that could not be spawned. The
    denylist below is what holds on either runner.
    """
    command = server["command"]
    assert shutil.which(command) is not None, (
        f"{command!r} does not resolve on PATH; the harness spawns it with no shell"
    )


def test_the_server_never_asks_for_a_shell(server: dict[str, Any]) -> None:
    """A shell in `command` is a Windows outage: none of them is on `PATH` there."""
    assert server["command"] not in SHELLS, server["command"]


def test_the_launch_leaves_placeholders_for_the_harness_to_expand(
    server: dict[str, Any],
) -> None:
    """`${VAR}`, never `$VAR`: nothing expands a bare `$` when no shell runs.

    Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` and
    `${CLAUDE_PROJECT_DIR}` into `command`, `args` and `env` before spawning. A
    bare `$CLAUDE_PLUGIN_DATA` survives into the child as those 19 characters.
    """
    text = json.dumps(server)
    assert "$CLAUDE" not in text.replace("${CLAUDE", ""), text


def test_the_server_runs_out_of_the_bootstrapped_environment(server: dict[str, Any]) -> None:
    """FR-068: the interpreter is the prepared venv's, never one off `PATH`."""
    assert server["env"]["UV_PROJECT_ENVIRONMENT"] == PLUGIN_VENV, server["env"]


def test_the_server_runs_the_plugin_that_declared_it(server: dict[str, Any]) -> None:
    """`--project`: `uv run` otherwise resolves against the session's cwd."""
    assert "--project" in server["args"], server["args"]
    assert server["args"][server["args"].index("--project") + 1] == "${CLAUDE_PLUGIN_ROOT}"


def test_the_server_installs_only_what_it_runs(server: dict[str, Any]) -> None:
    """`--frozen` honours `uv.lock`; `--no-dev` leaves out 151 MB of tooling."""
    assert {"--frozen", "--no-dev"} <= set(server["args"]), server["args"]


def test_the_server_runs_a_script_that_ships(declared_script: str) -> None:
    """A declaration naming a missing entry point installs a server that never starts."""
    scripts = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["scripts"]
    assert declared_script in scripts, f"{declared_script} is declared but not packaged"
    module, _, attribute = scripts[declared_script].partition(":")
    assert find_spec(module) is not None, f"{module} is declared but not importable"
    assert hasattr(import_module(module), attribute), f"{module} has no {attribute}()"


@pytest.mark.slow
def test_the_declared_launch_completes_an_initialize_handshake(
    server: dict[str, Any], tmp_path: Path
) -> None:
    """The one check the text assertions cannot make: spawn it, and talk to it.

    Run exactly as the harness runs it — the declared argv, the declared `env`,
    the placeholders expanded the way Claude Code expands them, and no shell —
    against a throwaway environment, so a working local venv cannot mask a
    declaration that only works because of one.
    """
    expansions = {"${CLAUDE_PLUGIN_ROOT}": str(REPO_ROOT), "${CLAUDE_PLUGIN_DATA}": str(tmp_path)}

    def expand(text: str) -> str:
        for placeholder, value in expansions.items():
            text = text.replace(placeholder, value.replace("\\", "/"))
        return text

    argv = [server["command"], *(expand(argument) for argument in server["args"])]
    env = {key: expand(value) for key, value in server["env"].items()}
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }

    completed = subprocess.run(  # nosec B603
        argv,
        input=f"{json.dumps(request)}\n",
        capture_output=True,
        text=True,
        timeout=600,
        env={**_os_environ(), **env},
        check=False,
    )

    assert completed.stdout, f"no reply on stdout\nstderr:\n{completed.stderr}"
    reply = json.loads(completed.stdout.splitlines()[0])
    assert reply["result"]["serverInfo"]["name"] == "processrecall-memory", reply


def _os_environ() -> dict[str, str]:
    """The ambient environment, minus this test run's own virtualenv.

    `uv run` would otherwise adopt the dev venv `pytest` is running under
    instead of the one `UV_PROJECT_ENVIRONMENT` names, and the test would pass
    without ever proving the declared environment gets built.
    """
    shadowing = {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    return {key: value for key, value in os.environ.items() if key not in shadowing}
