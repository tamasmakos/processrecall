"""The tool-server declaration (FR-068).

`.mcp.json` is the third half of the plugin claim the harness reads: the stdio
server that carries the four memory tools, launched — like every hook — by the
interpreter bootstrap builds under `$CLAUDE_PLUGIN_DATA/venv`, by absolute path
and never a bare `python` off `PATH`, running a module that actually ships.
"""

from __future__ import annotations

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

INTERPRETER = "${CLAUDE_PLUGIN_DATA}/venv/bin/python"


@pytest.fixture(scope="module")
def server() -> dict[str, Any]:
    """The one declared server, reached the way the harness reaches it."""
    servers = manifest_declared("mcpServers")["mcpServers"]
    assert list(servers) == ["processrecall"], sorted(servers)
    entry: dict[str, Any] = servers["processrecall"]
    return entry


def test_the_server_speaks_stdio(server: dict[str, Any]) -> None:
    """FR-070: no service, no port — JSON-RPC on the child's stdin and stdout."""
    assert server["type"] == "stdio", server.get("type")


def test_the_server_is_launched_by_the_bootstrapped_interpreter(server: dict[str, Any]) -> None:
    """FR-068: a bare `python` would resolve against whatever is on `PATH`."""
    assert server["command"] == INTERPRETER, server.get("command")


def test_the_server_runs_a_module_that_ships(server: dict[str, Any]) -> None:
    """A declaration naming a missing module installs a server that never starts."""
    flag, module = server["args"][:2]
    assert flag == "-m", server["args"]
    spec = find_spec(module)
    assert spec is not None, f"{module} is declared but not importable"
    assert hasattr(import_module(module), "main"), (
        f"{module} has no main() to run under `python -m`"
    )


def test_the_declared_module_is_the_package_entry_point(server: dict[str, Any]) -> None:
    """One server, one source of truth: the `processrecall-mcp` console script."""
    scripts = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["scripts"]
    module, _, _ = scripts["processrecall-mcp"].partition(":")
    assert server["args"][1] == module, server["args"]
