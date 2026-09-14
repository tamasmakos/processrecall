"""The hooks declaration (FR-068, `contracts/agent-hooks.md`).

`hooks/hooks.json` is the other half of the plugin claim the harness reads: the
seven Claude Code events wired to the six verbs of the contract, each entry
bounded by `timeout: 5` — the harness default is 600 s, which would let a wedged
hook hang a session — and each invoking the bootstrapped interpreter by absolute
path, never a bare `python` off `PATH`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from processrecall.integrations.claude_code.hooks import VERBS

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"

#: Event → verb, from the table in `contracts/agent-hooks.md`.
CONTRACT: dict[str, str] = {
    "SessionStart": "bootstrap",
    "UserPromptSubmit": "prompt",
    "PostToolUse": "record",
    "PreToolUse": "enforce",
    "Stop": "close",
    "SubagentStop": "close",
    "SessionEnd": "end",
}

INTERPRETER = '"$CLAUDE_PLUGIN_DATA/venv/bin/python"'


@pytest.fixture(scope="module")
def declaration() -> dict[str, Any]:
    """The shipped hooks declaration, reached the way the harness reaches it."""
    declared = json.loads(MANIFEST.read_text(encoding="utf-8"))["hooks"]
    path = REPO_ROOT / declared
    assert path.is_file(), f"{declared} is declared by the manifest but not shipped"
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def commands(declaration: dict[str, Any], event: str) -> list[dict[str, Any]]:
    """Every command entry declared for *event*, across its matchers."""
    return [command for matcher in declaration["hooks"][event] for command in matcher["hooks"]]


def test_every_contract_event_is_declared(declaration: dict[str, Any]) -> None:
    """Seven events: a missing one is a channel that never fires."""
    assert set(declaration["hooks"]) == set(CONTRACT), sorted(declaration["hooks"])


#: `SessionStart` runs `bin/bootstrap.sh` directly (R14): the interpreter the
#: other six verbs are named through is exactly what that script must
#: prepare, so it cannot be the one invoking it.
SCRIPTED_EVENTS = {"SessionStart"}


def test_each_event_invokes_the_verb_the_contract_gives_it(
    declaration: dict[str, Any],
) -> None:
    """`Stop` and `SubagentStop` share `close`; every name is one that exists."""
    for event, expected in CONTRACT.items():
        if event in SCRIPTED_EVENTS:
            continue
        for command in commands(declaration, event):
            named = re.findall(r"claude_code (\w+)", command["command"])
            assert named == [expected], f"{event} invokes {named}, not {expected!r}"
            assert expected in VERBS, f"{expected!r} is not a verb of the package"


def test_every_entry_is_a_command_bounded_by_a_timeout(
    declaration: dict[str, Any],
) -> None:
    """The harness default of 600 s would let a wedged hook hang a session."""
    for event in CONTRACT:
        expected = 5 if event not in SCRIPTED_EVENTS else 120
        for command in commands(declaration, event):
            assert command["type"] == "command", command
            assert command["timeout"] == expected, f"{event}: {command.get('timeout')}"


def test_every_command_names_the_bootstrapped_interpreter_by_absolute_path(
    declaration: dict[str, Any],
) -> None:
    """FR-068: a bare `python` would resolve against whatever is on `PATH`."""
    for event in CONTRACT:
        if event in SCRIPTED_EVENTS:
            continue
        for command in commands(declaration, event):
            shell = command["command"]
            assert shell.startswith("sh -c "), f"{event}: {shell}"
            assert INTERPRETER in shell, f"{event}: {shell}"
            assert shell.count("python") == shell.count(INTERPRETER), (
                f"{event} names a python other than the bootstrapped one: {shell}"
            )


def test_session_start_runs_bootstrap_sh_directly(declaration: dict[str, Any]) -> None:
    """R14: `bin/bootstrap.sh` prepares the interpreter, so it cannot be run by it."""
    for command in commands(declaration, "SessionStart"):
        shell = command["command"]
        assert shell == 'sh "$CLAUDE_PLUGIN_ROOT/bin/bootstrap.sh"', shell


def test_no_command_carries_its_own_exit_code(declaration: dict[str, Any]) -> None:
    """Each verb's own contract is "always exits 0" (`contracts/agent-hooks.md`);
    a shell trailer here would mask a missing interpreter or a non-zero the verb
    itself returned instead of surfacing it."""
    for event in CONTRACT:
        for command in commands(declaration, event):
            shell = command["command"]
            assert "exit 0" not in shell, f"{event}: {shell}"
