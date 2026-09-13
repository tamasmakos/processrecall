"""The Claude Code adapter: a hook payload becomes the one canonical event, or nothing.

The mapping table of ``contracts/trajectory-event.md`` is the contract, so it is
asserted field by field against the synthetic corpus. Two rules have teeth here:
the result is cut to 2048 characters at this boundary (FR-010), because nothing
downstream may re-read the original; and a payload missing a routing field is
counted and dropped rather than raised into the developer's action (rule 5).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from processrecall.integrations.claude_code.hooks import adapt_post_tool_use
from processrecall.trajectory.event import SourceKind

pytestmark = pytest.mark.unit

PAYLOADS = Path(__file__).resolve().parents[2] / "fixtures" / "payloads"

#: What the adapter writes when it drops an event (R16).
MALFORMED = "capture_payload_malformed"


class FakeCounters:
    """The counter table in memory: what the adapter counted, and how often."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def actions(case: str) -> list[dict[str, Any]]:
    """The completed tool actions of one named synthetic sequence."""
    envelope = json.loads((PAYLOADS / f"{case}.json").read_text(encoding="utf-8"))
    return [
        payload for payload in envelope["payloads"] if payload["hook_event_name"] == "PostToolUse"
    ]


def test_a_post_tool_use_payload_adapts_onto_the_contract_fields() -> None:
    """Every row of the mapping table, on the ordinary main-agent action."""
    counters = FakeCounters()

    event = adapt_post_tool_use(actions("replayed_duplicate")[0], counters)

    assert event is not None
    assert event.operation_name == "execute_tool"
    assert event.conversation_id == "sess-demo"
    assert event.prompt_id == "prompt-1"
    assert event.tool_call_id == "toolu_dup"
    assert event.tool_name == "Read"
    assert event.tool_call_arguments == {"file_path": "/work/demo/src/app.py"}
    assert event.tool_call_result.startswith("1  def render(items):")
    assert event.project_dir == "/work/demo"
    assert event.record_ref == "/work/demo/.transcripts/sess-demo.jsonl#toolu_dup"
    assert event.source_kind is SourceKind.LIVE
    assert event.occurred_at.utcoffset() is not None
    assert not counters.counted


def test_an_oversized_result_is_cut_to_the_ceiling_at_this_boundary() -> None:
    """FR-010: 2048 characters exactly, and the cut happens before anything else sees it."""
    oversized = actions("replayed_duplicate")[0] | {"tool_result": "x" * 3000}

    event = adapt_post_tool_use(oversized, FakeCounters())

    assert event is not None
    assert len(event.tool_call_result) == 2048


def test_a_result_within_the_ceiling_is_left_whole() -> None:
    """The ordinary action is short, and truncation must not trim or pad it."""
    event = adapt_post_tool_use(actions("replayed_duplicate")[-1], FakeCounters())

    assert event is not None
    assert event.tool_call_result == "1 passed in 0.11s"


@pytest.mark.parametrize("index", range(3))
def test_a_payload_missing_a_routing_field_is_counted_and_dropped(index: int) -> None:
    """Rule 5: no event, one counter increment, and nothing raised at the agent."""
    counters = FakeCounters()

    event = adapt_post_tool_use(actions("malformed")[index], counters)

    assert event is None
    assert counters.counted == Counter({MALFORMED: 1})


def test_the_agent_that_acted_is_carried_by_id_and_by_name() -> None:
    """``agent_id`` and ``agent_type`` name the sub-agent; the main agent is ``""``."""
    main, delegated = actions("subagent_delegation")[0], actions("subagent_delegation")[1]

    parent = adapt_post_tool_use(main, FakeCounters())
    child = adapt_post_tool_use(delegated, FakeCounters())

    assert parent is not None and (parent.agent_id, parent.agent_name) == ("", "")
    assert child is not None and (child.agent_id, child.agent_name) == ("agent-7", "code-reviewer")
