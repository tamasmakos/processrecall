"""The synthetic corpus checks itself: every fixture is loadable and says what it names.

FR-073 puts the whole test suite on synthetic payloads, so the corpus is the one
input nothing else validates. A sequence filed under ``replayed_duplicate`` that
carries no duplicate would make every test that reads it green for the wrong
reason — these tests read each fixture the way its consumers will, through the
public loaders where one exists, and assert the trait its name promises.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from processrecall.config import LEVELS
from processrecall.symbolic.packs import ActivityClass
from processrecall.trajectory.vocabulary import load_vocabulary

FIXTURES = Path(__file__).parent
PAYLOADS = FIXTURES / "payloads"

#: The synthetic nodes a sequence opens and closes at (FR-020); they carry no
#: activity class because no action performed them.
SEQUENCE_BOUNDARY = frozenset({"Start", "End"})


def _strings(value: Any) -> Iterator[str]:
    """Every string anywhere in *value* — what a leak detector reads."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


#: Every payload sequence the corpus owes, against the trait it is named for.
CASES = (
    "replayed_duplicate",
    "subagent_delegation",
    "compaction",
    "clear",
    "excluded_project",
    "malformed",
)

#: The synthetic project the corpus works in, and the one it is opted out of.
PROJECT_DIR = "/work/demo"
EXCLUDED_DIR = "/work/private-client"

#: The payload fields that place an action in a sequence (`contracts/trajectory-event.md`).
ROUTING_FIELDS = ("session_id", "prompt_id", "tool_name")

#: An absolute path by any of FR-013/R4's spellings: POSIX, a drive letter, or backslash-rooted.
_ABSOLUTE_PATH = re.compile(r"^(/|\\|[A-Za-z]:)")


def _actions(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The completed tool actions of a sequence — the ``PostToolUse`` payloads."""
    return [payload for payload in payloads if payload["hook_event_name"] == "PostToolUse"]


def _read_sequence(case: str) -> list[dict[str, Any]]:
    """The hook payloads of one named sequence, in the order the harness fires them."""
    envelope = json.loads((PAYLOADS / f"{case}.json").read_text(encoding="utf-8"))
    return list(envelope["payloads"])


@pytest.fixture(scope="module")
def snapshot() -> dict[str, Any]:
    """The hand-built snapshot: a reader can be tested against it before
    `graph/snapshot.py` exists to write one."""
    return json.loads((FIXTURES / "graph.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES)
def test_every_named_sequence_parses_into_hook_payloads(case: str) -> None:
    """Each case is one JSON envelope of payloads, each naming the hook event it answers.

    The whole corpus works in two synthetic projects and no other: FR-073's
    "no real session transcripts" is only true if no fixture names a real path.
    """
    envelope = json.loads((PAYLOADS / f"{case}.json").read_text(encoding="utf-8"))
    assert envelope["description"], f"{case} documents what it is for"
    payloads = list(envelope["payloads"])
    assert payloads, f"{case} carries at least one payload"
    assert all(payload["hook_event_name"] for payload in payloads)
    assert all(payload["cwd"] in {PROJECT_DIR, EXCLUDED_DIR} for payload in payloads)


def test_the_replayed_sequence_delivers_one_action_twice() -> None:
    """FR-008's input: the same ``tool_use_id``, twice, byte for byte."""
    payloads = _actions(_read_sequence("replayed_duplicate"))
    replayed = [payload for payload in payloads if payload["tool_use_id"] == "toolu_dup"]
    assert len(replayed) == 2
    assert replayed[0] == replayed[1]


def test_the_subagent_sequence_carries_a_delegation_and_the_work_it_delegated() -> None:
    """FR-011's input: one ``Task`` action on the main agent, then actions of a sub-agent."""
    payloads = _read_sequence("subagent_delegation")
    delegations = [action for action in _actions(payloads) if action["tool_name"] == "Task"]
    delegated = [action for action in _actions(payloads) if action["agent_id"]]
    assert [action["agent_id"] for action in delegations] == [""]
    assert delegated and {action["agent_id"] for action in delegated} == {"agent-7"}
    assert any(payload["hook_event_name"] == "SubagentStop" for payload in payloads)


def test_the_compaction_sequence_resumes_the_prompt_it_interrupted() -> None:
    """FR-012's continuing half: a compact restart between two actions of one prompt."""
    payloads = _read_sequence("compaction")
    restarts = [payload for payload in payloads if payload["hook_event_name"] == "SessionStart"]
    assert [restart["source"] for restart in restarts] == ["compact"]
    assert {action["prompt_id"] for action in _actions(payloads)} == {"prompt-1"}


def test_the_clear_sequence_reuses_the_session_for_a_second_prompt() -> None:
    """FR-012's restarting half: one session id across a clear, so only the epoch separates them."""
    payloads = _read_sequence("clear")
    restarts = [payload for payload in payloads if payload["hook_event_name"] == "SessionStart"]
    assert [restart["source"] for restart in restarts] == ["clear"]
    assert len({payload["session_id"] for payload in payloads}) == 1
    assert {action["prompt_id"] for action in _actions(payloads)} == {"prompt-1", "prompt-2"}


def test_the_excluded_sequence_works_only_in_the_opted_out_project() -> None:
    """FR-058's input: a whole sequence whose ``cwd`` the exclusion check must refuse."""
    payloads = _read_sequence("excluded_project")
    assert {payload["cwd"] for payload in payloads} == {EXCLUDED_DIR}
    assert _actions(payloads), "there is capture to suppress"


def test_the_malformed_sequence_omits_every_routing_field_in_turn() -> None:
    """FR-001's refusal path: one action per field the adapter needs to place it."""
    payloads = _actions(_read_sequence("malformed"))
    missing_per_payload = [[field for field in ROUTING_FIELDS if field not in a] for a in payloads]
    assert all(len(missing) == 1 for missing in missing_per_payload)
    assert {missing[0] for missing in missing_per_payload} == set(ROUTING_FIELDS)


def test_the_transcript_fixture_is_synthetic_and_only_the_corrupt_line_is_unreadable() -> None:
    """FR-073: ``session.jsonl`` walked through the same leak check as the snapshot.

    Every line but the one deliberately corrupted must parse, and no string in
    a line that parses may be an absolute path other than the synthetic
    project directory: a real transcript would leak the machine it was taken
    from through both.
    """
    lines = (FIXTURES / "session.jsonl").read_text(encoding="utf-8").splitlines()
    unreadable = []
    for ordinal, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            unreadable.append(ordinal)
            continue
        for text in _strings(record):
            assert text.startswith(PROJECT_DIR) or not _ABSOLUTE_PATH.match(text), text
    assert unreadable == [8]


def test_the_snapshot_is_the_shape_the_storage_contract_declares(snapshot: dict[str, Any]) -> None:
    """A hand-built snapshot, so a reader can be tested before a writer exists."""
    assert snapshot["format"] == 1
    assert snapshot["level"] in LEVELS
    assert isinstance(snapshot["episode_high_water"], int)
    assert snapshot["nodes"] and snapshot["edges"]


def test_every_edge_runs_between_nodes_the_snapshot_declares(snapshot: dict[str, Any]) -> None:
    """A traversal reads the edges and looks the endpoints up; a dangling one would KeyError."""
    nodes = snapshot["nodes"]
    endpoints = {end for edge in snapshot["edges"] for end in (edge["source"], edge["target"])}
    assert endpoints <= set(nodes)


def test_every_node_is_named_in_the_activity_vocabulary_it_is_served_at(
    snapshot: dict[str, Any],
) -> None:
    """Node keys are ``identify_procedure`` keys, so guidance can resolve them to a class."""
    classes = {member.value for member in ActivityClass}
    for key, node in snapshot["nodes"].items():
        if key in SEQUENCE_BOUNDARY:
            continue
        assert node["level"] == snapshot["level"]
        assert key.split("/")[0] in classes
        assert node["is_a"] == [key.split("/")[0]]


def test_the_snapshot_carries_no_payload_and_no_absolute_path(snapshot: dict[str, Any]) -> None:
    """FR-054: the snapshot is the shareable half, so the fixture may not teach otherwise."""
    for text in _strings(snapshot):
        assert not _ABSOLUTE_PATH.match(text), text
    for edge in snapshot["edges"]:
        assert all(isinstance(step_id, int) for step_id in edge["supporting_steps"])


def test_the_fake_pack_loads_through_the_loader_the_shipped_pack_uses() -> None:
    """FR-004: a second harness supplies a pack, and no code here changes for it."""
    fake = load_vocabulary(FIXTURES / "vocab_fake.json")
    assert fake.harness != load_vocabulary().harness
    assert fake.tools


def test_the_fake_pack_spells_every_tool_differently_and_still_reaches_the_same_classes() -> None:
    """SC-011 needs a vocabulary that shares nothing but the classes it maps onto."""
    fake, shipped = load_vocabulary(FIXTURES / "vocab_fake.json"), load_vocabulary()
    assert not set(fake.tools) & set(shipped.tools)
    assert {entry.activity_class for entry in fake.tools.values()} == {
        entry.activity_class for entry in shipped.tools.values()
    }
