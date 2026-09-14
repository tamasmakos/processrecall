"""Delegation: the parent's one step, the sub-agent's own chain (FR-011).

A sub-agent is not a detour inside the parent's chain. Its actions belong to a
sequence of their own, keyed by the agent the harness names, and all the parent
sequence ever learns of them is the single ``Delegation`` step of the action
that spawned them — so a procedure mined from the parent's chain reads
"delegated" rather than the sub-agent's private tool calls.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from processrecall.config import ActivityClass
from processrecall.graph.store import SequenceKey, SQLiteEpisodicStore
from processrecall.integrations.claude_code.hooks import capture

from .conftest import PAYLOADS

pytestmark = pytest.mark.unit

#: The delegating agent's chain: the main agent, whose ``agent_id`` is empty.
PARENT = SequenceKey("sess-demo", 0, "prompt-1", "")

#: The sub-agent's own chain — same conversation, same prompt, its own identity.
SUBAGENT = SequenceKey("sess-demo", 0, "prompt-1", "agent-7")


def delegation_payloads() -> list[dict[str, Any]]:
    """The synthetic delegation envelope, as the harness delivers it, whole."""
    envelope = json.loads((PAYLOADS / "subagent_delegation.json").read_text(encoding="utf-8"))
    return list(envelope["payloads"])


def test_the_delegating_action_is_one_delegation_step_on_the_parent_sequence(
    index: sqlite3.Connection,
) -> None:
    """FR-011: the parent's chain records that it delegated, and nothing more."""
    for payload in delegation_payloads():
        capture(payload, index)

    steps = SQLiteEpisodicStore(index).steps(PARENT)
    assert [step.dedup_key for step in steps] == ["toolu_10"]
    assert steps[0].activity_class is ActivityClass.DELEGATION


def test_the_subagents_actions_form_their_own_sequence(index: sqlite3.Connection) -> None:
    """FR-011: keyed by agent identity, positioned from its own start."""
    for payload in delegation_payloads():
        capture(payload, index)

    store = SQLiteEpisodicStore(index)
    steps = store.steps(SUBAGENT)
    assert [step.dedup_key for step in steps] == ["toolu_11", "toolu_12"]
    assert [step.position for step in steps] == [0, 1]
    assert store.sequence(SUBAGENT) is not None
