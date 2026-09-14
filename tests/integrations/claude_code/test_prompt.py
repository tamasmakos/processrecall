"""The ``prompt`` verb: the turn is opened, and what usually opens it is served.

``contracts/agent-hooks.md`` gives the verb three steps in order — the
exclusion check on ``cwd`` first (R13), the sequence's ``Start`` node stored
with its process type as the condition, then the successors of ``Start`` for
that process type (FR-047), this project's snapshot consulted before the
cross-project one (FR-048). The assertions here follow that order.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from processrecall.config import STORE_DIR, ProcessType, home_dir
from processrecall.graph.abstract import (
    START_KEY,
    AbstractGraph,
    Condition,
    TransitionEdge,
    edge_key,
    served,
)
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import SNAPSHOT_NAME, SnapshotFile
from processrecall.graph.store import SequenceKey, SQLiteEpisodicStore
from processrecall.integrations.claude_code.hooks import OPTOUT_MARKER, open_prompt
from processrecall.procedures.outcome import Outcome
from processrecall.trajectory.paths import project_key

from .conftest import hook

pytestmark = pytest.mark.unit

#: The generality the snapshots below are derived at, and when they were.
LEVEL = "class/program"
DERIVED_AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


class FakeCounters:
    """The counter table in memory, for the writers a test drives directly."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


#: The sequence the payloads below belong to: the main agent, at the epoch
#: nothing has rotated.
DEMO = SequenceKey("sess-demo", 0, "prompt-1", "")


def submit(project: Path) -> dict[str, Any]:
    """One ``UserPromptSubmit`` payload for *project*, as the harness delivers it."""
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-demo",
        "prompt_id": "prompt-1",
        "cwd": str(project),
        "transcript_path": str(project / ".transcripts" / "sess-demo.jsonl"),
        "prompt": "Add a --quiet flag to the CLI",
    }


def test_the_prompt_of_an_excluded_project_is_answered_with_silence(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """R13: the check comes first, so no sequence and no guidance come of the turn."""
    project = tmp_path / "private-client"
    (project / OPTOUT_MARKER).parent.mkdir(parents=True)
    (project / OPTOUT_MARKER).touch()
    store = SQLiteEpisodicStore(index)

    assert open_prompt(submit(project), index) == ""

    assert store.counters()["capture_excluded"] == 1
    assert store.sequence(DEMO) is None


def test_the_turn_is_opened_with_its_process_type_as_the_starts_condition(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-020: the sequence carries the process type its ``Start`` is conditioned on.

    ``Unknown`` is that type on the shipped path, not a gap: the prompt-to-
    process-type classifier is optional enrichment and its absence may neither
    raise nor guess (FR-059, FR-060).
    """
    project = tmp_path / "demo"
    project.mkdir()

    assert open_prompt(submit(project), index) == ""

    sequence = SQLiteEpisodicStore(index).sequence(DEMO)
    assert sequence is not None
    assert sequence.status == "open"
    assert sequence.project_dir_key == project_key(str(project))
    assert sequence.process_type is ProcessType.UNKNOWN


def transition(
    target: str,
    support: int,
    *,
    source: str = START_KEY,
    process_type: ProcessType = ProcessType.UNKNOWN,
) -> TransitionEdge:
    """One move into *target*, out of ``Start`` unless *source* says otherwise."""
    return TransitionEdge(
        edge_key=edge_key(source, target),
        source=source,
        target=target,
        condition=Condition(
            process_type=process_type, same_file_as_previous=None, previous_outcome=None
        ),
        support=support,
        weight=float(support),
        supporting_steps=tuple(range(1, support + 1)),
        outcome_counts={Outcome.SUCCESS: support},
        last_seen=DERIVED_AT,
        pitfalls=(),
    )


def write_snapshot(path: Path, *edges: TransitionEdge) -> None:
    """Land the snapshot *edges* make up at *path*, through the writer `rebuild` uses."""
    graph = AbstractGraph(level=LEVEL, nodes={}, edges=edges, episode_high_water=len(edges))
    SnapshotFile(path, FakeCounters()).write(served(graph, DERIVED_AT))


def test_the_successors_of_start_for_the_prompts_process_type_are_what_is_served(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-047: Start's successors, conditioned on the process type, and nothing else."""
    project = tmp_path / "demo"
    write_snapshot(
        project / STORE_DIR / SNAPSHOT_NAME,
        transition("Inspection/Read", 5),
        transition("Verification/pytest", 3),
        transition("ChangeImplementation/Edit", 9, process_type=ProcessType.BUG_FIX),
        transition("Verification/ruff", 7, source="Inspection/Read"),
    )

    assert open_prompt(submit(project), index) == (
        "- a prompt like this usually starts with Inspection/Read (5 episodes)\n"
        "- a prompt like this usually starts with Verification/pytest (3 episodes)"
    )
    assert SQLiteEpisodicStore(index).counters()["guidance_served"] == 1


def test_a_prompt_this_project_has_no_graph_for_is_opened_from_the_cross_project_one(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-048: the fallback is what makes a project with no graph yet not silent."""
    project = tmp_path / "demo"
    write_snapshot(home_dir() / SNAPSHOT_NAME, transition("Inspection/Read", 4))

    assert open_prompt(submit(project), index) == (
        "- a prompt like this usually starts with Inspection/Read (4 episodes)"
    )
    assert SQLiteEpisodicStore(index).counters()["guidance_fallback_global"] == 1


def test_this_projects_own_opening_is_served_ahead_of_every_other_projects(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-048: precedence, not weight — what this project did outranks what others did."""
    project = tmp_path / "demo"
    write_snapshot(project / STORE_DIR / SNAPSHOT_NAME, transition("Inspection/Read", 2))
    write_snapshot(home_dir() / SNAPSHOT_NAME, transition("Verification/pytest", 40))

    assert open_prompt(submit(project), index) == (
        "- a prompt like this usually starts with Inspection/Read (2 episodes)\n"
        "- a prompt like this usually starts with Verification/pytest (40 episodes)"
    )


def test_the_verb_answers_the_harness_with_the_opening_it_served(tmp_path: Path) -> None:
    """``contracts/agent-hooks.md``: one JSON object out, and it is additional context."""
    project = tmp_path / "demo"
    write_snapshot(project / STORE_DIR / SNAPSHOT_NAME, transition("Inspection/Read", 6))

    result = hook("prompt", submit(project), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "additionalContext": (
            "- a prompt like this usually starts with Inspection/Read (6 episodes)"
        )
    }


def test_a_turn_nothing_is_known_about_is_opened_in_silence(tmp_path: Path) -> None:
    """Empty output is the normal case; the turn is opened all the same."""
    project = tmp_path / "demo"

    result = hook("prompt", submit(project), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    with closing(open_index(tmp_path / STORE_DIR / "episodes.db")) as index:
        assert SQLiteEpisodicStore(index).sequence(DEMO) is not None


def test_a_store_that_cannot_even_be_opened_leaves_the_prompt_untouched(tmp_path: Path) -> None:
    """FR-014: an unopenable index is the same ordinary failure as a busy one."""
    project = tmp_path / "demo"
    (tmp_path / ".processrecall" / "episodes.db").mkdir(parents=True)

    result = hook("prompt", submit(project), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    fallback = (tmp_path / ".processrecall" / "log" / "hooks.jsonl").read_text(encoding="utf-8")
    assert "capture_store_busy" in fallback
