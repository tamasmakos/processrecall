"""The six verbs of ``contracts/agent-hooks.md``, asserted at the process boundary.

A hook runs inside the developer's own session, so its exit code and its stdout
are the whole of what the harness sees. A non-zero exit or a stray line on
stdout surfaces against the action the hook was only watching, which FR-014
forbids — which is why these assertions are made where the harness makes them:
a real ``python -m processrecall.integrations.claude_code <verb>`` process.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from processrecall.integrations.claude_code import hooks
from processrecall.integrations.claude_code.__main__ import main

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
PAYLOADS = ROOT / "tests" / "fixtures" / "payloads"

#: The verb names ``hooks.json`` is allowed to invoke, one per hook event.
VERBS = ("bootstrap", "prompt", "record", "enforce", "close", "end")


def hook(args: Sequence[str], payload: str) -> subprocess.CompletedProcess[str]:
    """Run the module the way the harness does, with *payload* written to its stdin."""
    return subprocess.run(
        [sys.executable, "-m", "processrecall.integrations.claude_code", *args],
        input=payload,
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


def assert_framed(result: subprocess.CompletedProcess[str]) -> None:
    """Assert the harness's two rules: exit 0, and at most one JSON object out."""
    assert result.returncode == 0, result.stderr
    if result.stdout.strip():
        assert isinstance(json.loads(result.stdout), dict)


def _payload(hook_event_name: str, fixture: str = "replayed_duplicate.json") -> dict[str, Any]:
    """The first payload of *hook_event_name* in *fixture*'s synthetic corpus."""
    envelope = json.loads((PAYLOADS / fixture).read_text(encoding="utf-8"))
    return next(
        payload
        for payload in envelope["payloads"]
        if payload["hook_event_name"] == hook_event_name
    )


def action() -> dict[str, Any]:
    """One ordinary completed tool action from the synthetic corpus."""
    return _payload("PostToolUse")


#: The payload each verb is driven with, per its row of ``contracts/agent-hooks.md``.
#: ``SessionStart`` comes from ``clear.json`` since ``replayed_duplicate.json`` carries
#: none; ``PreToolUse`` and ``SessionEnd`` appear in no fixture, so those two are the
#: minimal fields the contract's "Reads" column names for them.
VERB_PAYLOADS: dict[str, dict[str, Any]] = {
    "bootstrap": _payload("SessionStart", "clear.json"),
    "prompt": _payload("UserPromptSubmit"),
    "record": _payload("PostToolUse"),
    "enforce": {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-demo",
        "prompt_id": "prompt-1",
        "cwd": "/work/demo",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q tests/test_app.py"},
    },
    "close": _payload("Stop"),
    "end": {"hook_event_name": "SessionEnd", "session_id": "sess-demo"},
}


def test_verbs_dispatch_table_matches_the_contract() -> None:
    """The six verbs of ``contracts/agent-hooks.md`` are exactly what ``hooks.json`` may invoke."""
    assert set(hooks.VERBS) == set(VERBS)


@pytest.mark.parametrize("verb", VERBS)
def test_every_verb_answers_an_ordinary_payload_without_disturbing_the_session(verb: str) -> None:
    """FR-014: the memory's answer is one JSON object at most, and exit 0 always."""
    assert_framed(hook([verb], json.dumps(VERB_PAYLOADS[verb])))


@pytest.mark.parametrize("stdin", ["", "not json at all", '["not an object"]', '{"truncated":'])
def test_a_stdin_that_carries_no_event_is_answered_with_silence(stdin: str) -> None:
    """A payload the hook cannot read is not a failure of the hook: no crash, no output."""
    result = hook(["record"], stdin)

    assert_framed(result)
    assert result.stdout == ""


@pytest.mark.parametrize("args", [[], ["observe"]])
def test_an_invocation_naming_no_verb_still_exits_zero(args: list[str]) -> None:
    """A stale ``hooks.json`` is the maintainer's problem, never the session's."""
    assert_framed(hook(args, json.dumps(action())))


def test_a_payload_that_is_not_an_event_is_reported_and_reaches_no_verb(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """What the hook could not read is said once, and the verb is not asked to guess."""
    seen: list[Mapping[str, Any]] = []

    def remember(payload: Mapping[str, Any]) -> None:
        seen.append(payload)

    monkeypatch.setitem(hooks.VERBS, "record", remember)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))

    with caplog.at_level(logging.WARNING, logger="processrecall"):
        status = main(["record"])

    assert status == 0
    assert seen == []
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_a_verb_that_fails_is_recorded_and_the_action_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-014: an injected failure leaves exit 0 and silence, and a maintainer a line."""

    def boom(payload: Mapping[str, Any]) -> None:
        raise RuntimeError("the store is on fire")

    monkeypatch.setitem(hooks.VERBS, "record", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(action())))

    with caplog.at_level(logging.ERROR, logger="processrecall"):
        status = main(["record"])

    assert status == 0
    assert capsys.readouterr().out == ""
    assert "the store is on fire" in caplog.text
