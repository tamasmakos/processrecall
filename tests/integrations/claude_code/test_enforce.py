"""The ``enforce`` verb: a pre-action deny, derived from a pitfall and opt-in.

``contracts/agent-hooks.md`` gives the verb one job and two guards. It is off
by default and opted into (FR-049), and ``PreToolUse`` takes a decision but
never ``additionalContext`` — which is why the warning path stays in ``record``
and this one refuses outright. What it refuses with is read off a pitfall the
graph already holds for the move about to be made, never composed here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from processrecall.config import STORE_DIR, load_config
from processrecall.graph.abstract import (
    AbstractGraph,
    Condition,
    Pitfall,
    PitfallKind,
    TransitionEdge,
    edge_key,
    served,
)
from processrecall.graph.snapshot import SNAPSHOT_NAME, SnapshotFile
from processrecall.integrations.claude_code.hooks import capture, deny_reason
from processrecall.procedures.outcome import Outcome
from processrecall.symbolic.packs import ProcessType

from .conftest import hook

pytestmark = pytest.mark.unit

#: The generality the snapshots below are derived at, and when they were.
LEVEL = "class/program"
DERIVED_AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


class FakeCounters:
    """The counter table in memory, for the writers a test drives directly."""

    def __init__(self) -> None:
        self.counted: dict[str, int] = {}

    def bump(self, counter: str) -> None:
        self.counted[counter] = self.counted.get(counter, 0) + 1


def opt_in(home: Path) -> None:
    """Turn the verb on the way an operator does: one key in the home config."""
    config = home / STORE_DIR / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"enforce": True}), encoding="utf-8")


def read_action(project: Path) -> dict[str, Any]:
    """The completed ``Read`` that leaves the agent standing at ``Inspection/Read``."""
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-demo",
        "prompt_id": "prompt-1",
        "cwd": str(project),
        "tool_name": "Read",
        "tool_use_id": "toolu_read",
        "tool_input": {"file_path": "src/app.py"},
        "tool_result": "import os",
    }


def run_of_the_tests(project: Path) -> dict[str, Any]:
    """The ``pytest`` run the agent is about to start: ``ArtifactEvaluation/pytest``."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-demo",
        "prompt_id": "prompt-1",
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q tests/test_app.py"},
    }


def transition(source: str, target: str, *pitfalls: Pitfall, support: int = 6) -> TransitionEdge:
    """One recorded move from *source* to *target*, with what it goes wrong as."""
    return TransitionEdge(
        edge_key=edge_key(source, target),
        source=source,
        target=target,
        condition=Condition(
            process_type=ProcessType.UNKNOWN, same_file_as_previous=None, previous_outcome=None
        ),
        support=support,
        weight=float(support),
        supporting_steps=tuple(range(1, support + 1)),
        outcome_counts={Outcome.SUCCESS: support},
        last_seen=DERIVED_AT,
        pitfalls=pitfalls,
    )


def write_snapshot(path: Path, *edges: TransitionEdge) -> None:
    """Land the snapshot *edges* make up at *path*, through the writer `rebuild` uses."""
    graph = AbstractGraph(level=LEVEL, nodes={}, edges=edges, episode_high_water=len(edges))
    SnapshotFile(path, FakeCounters()).write(served(graph, DERIVED_AT))


def fails_often() -> Pitfall:
    """What the graph knows the run of the tests goes wrong as, after a read."""
    return Pitfall(
        kind=PitfallKind.FAILURE_PRONE, evidence="pytest -q <File>", support=4, failure_rate=0.8
    )


def test_the_move_a_pitfall_matches_is_denied_with_the_reason_it_derives(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-049: the reason is the pitfall's own evidence and count, never prose."""
    project = tmp_path / "demo"
    write_snapshot(
        project / STORE_DIR / SNAPSHOT_NAME,
        transition("Inspection/Read", "ArtifactEvaluation/pytest", fails_often()),
    )
    capture(read_action(project), index)

    assert deny_reason(run_of_the_tests(project), index, load_config()) == (
        "- pytest -q <File> is known to fail after Inspection/Read (4 episodes)"
    )


def test_the_verb_answers_the_harness_with_a_deny_decision_and_no_added_context(
    tmp_path: Path,
) -> None:
    """``contracts/agent-hooks.md``: ``PreToolUse`` takes a decision, never context."""
    project = tmp_path / "demo"
    opt_in(tmp_path)
    write_snapshot(
        project / STORE_DIR / SNAPSHOT_NAME,
        transition("Inspection/Read", "ArtifactEvaluation/pytest", fails_often()),
    )
    assert hook("record", read_action(project), home=tmp_path).returncode == 0

    result = hook("enforce", run_of_the_tests(project), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "additionalContext" not in result.stdout
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "- pytest -q <File> is known to fail after Inspection/Read (4 episodes)"
            ),
        }
    }


def test_a_move_a_pitfall_matches_is_allowed_until_the_verb_is_opted_into(
    tmp_path: Path,
) -> None:
    """FR-049: off is the shipped state, however sure the graph is of the pitfall."""
    project = tmp_path / "demo"
    write_snapshot(
        project / STORE_DIR / SNAPSHOT_NAME,
        transition("Inspection/Read", "ArtifactEvaluation/pytest", fails_often()),
    )
    assert hook("record", read_action(project), home=tmp_path).returncode == 0

    result = hook("enforce", run_of_the_tests(project), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def completed_run(project: Path) -> dict[str, Any]:
    """The ``pytest`` run just finished: the agent stands on it, about to repeat it."""
    return {
        **run_of_the_tests(project),
        "hook_event_name": "PostToolUse",
        "tool_use_id": "toolu_pytest",
        "tool_result": "1 failed, 2 passed",
    }


def test_a_move_the_graph_only_knows_as_a_loop_is_not_refused(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-031: a loop makes no claim about outcome, so it is no ground to refuse."""
    project = tmp_path / "demo"
    looping = Pitfall(
        kind=PitfallKind.REPETITION_LOOP, evidence="ArtifactEvaluation/pytest", support=5
    )
    write_snapshot(
        project / STORE_DIR / SNAPSHOT_NAME,
        transition("ArtifactEvaluation/pytest", "ArtifactEvaluation/pytest", looping),
    )
    capture(completed_run(project), index)

    assert deny_reason(run_of_the_tests(project), index, load_config()) == ""


def test_a_locked_index_allows_the_action_rather_than_refusing_blind(
    tmp_path: Path,
) -> None:
    """FR-014: a lookup the store cannot answer is no ground to stop the developer."""
    project = tmp_path / "demo"
    write_snapshot(
        project / STORE_DIR / SNAPSHOT_NAME,
        transition("Inspection/Read", "ArtifactEvaluation/pytest", fails_often()),
    )
    unreadable = tmp_path / "episodes.db"
    unreadable.write_bytes(b"not a database")
    locked = sqlite3.connect(unreadable)

    assert deny_reason(run_of_the_tests(project), locked, load_config()) == ""
