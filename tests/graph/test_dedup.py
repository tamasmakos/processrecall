"""Deduplication: one step per tool call, and a key for a source that issues none.

FR-008 makes the harness's tool-call id the identity of an action, so recording
the same id again must leave exactly one step — and must say so, since a
duplicate that resolved silently is indistinguishable from one that was never
sent (R3, R16).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.store import (
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)
from processrecall.trajectory.event import SourceKind, TrajectoryEvent

from .conftest import make_step

KEY = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteEpisodicStore]:
    """A store over a freshly created episodic index, closed when the test ends."""
    connection = open_index(tmp_path / "episodes.db")
    opened = SQLiteEpisodicStore(connection)
    opened.open_sequence(
        Sequence(
            key=KEY,
            project_dir_key="proj",
            started_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        )
    )
    try:
        yield opened
    finally:
        connection.close()


def _step(*, dedup_key: str = "toolu_1", position: int = 0) -> EpisodicStep:
    """One recordable step, with only the fields a test varies exposed."""
    return make_step(dedup_key=dedup_key, sequence_key=KEY, position=position)


def _event(*, tool_call_id: str = "") -> TrajectoryEvent:
    """One completed action as a backfill source hands it over."""
    return TrajectoryEvent(
        operation_name="execute_tool",
        conversation_id=KEY.conversation_id,
        agent_id=KEY.agent_id,
        agent_name="",
        tool_name="Bash",
        tool_call_id=tool_call_id,
        tool_call_arguments={"command": "pytest -q", "description": "run the suite"},
        tool_call_result="2 passed",
        prompt_id=KEY.prompt_id,
        project_dir="/app",
        record_ref="/transcripts/c1.jsonl#7",
        occurred_at=datetime(2026, 9, 13, 10, 0, 1, tzinfo=UTC),
        source_kind=SourceKind.BACKFILL,
    )


def test_a_replayed_tool_call_id_is_counted_rather_than_resolved_silently(
    store: SQLiteEpisodicStore,
) -> None:
    store.record(_step())

    assert store.record(_step(position=1)) is False

    assert len(store.steps(KEY)) == 1
    assert store.counters()["steps_duplicate"] == 1


def test_a_step_that_landed_leaves_the_duplicate_count_alone(
    store: SQLiteEpisodicStore,
) -> None:
    store.record(_step())

    assert "steps_duplicate" not in store.counters()


def test_a_step_that_landed_is_counted(store: SQLiteEpisodicStore) -> None:
    """R3: a counter the package declares and never increments reads as "nothing
    was captured", which is indistinguishable from the failure it exists to report."""
    store.record(_step())

    assert store.counters()["steps_recorded"] == 1


def test_a_source_that_issues_no_tool_call_id_gets_the_same_key_on_every_pass() -> None:
    """SC-003: backfilling one record twice must derive one key, not two."""
    derived = KEY.dedup_key(_event(), ordinal=3)

    assert derived == KEY.dedup_key(_event(), ordinal=3)
    assert derived.startswith("syn-")
    assert len(derived) == len("syn-") + 24


def test_the_harness_s_own_tool_call_id_is_the_key_when_it_issued_one() -> None:
    """FR-008, with the ordinal that discriminates the action's sub-activities.

    Spelled out in full rather than as a prefix: `startswith("toolu_1")` is
    also true of `toolu_10#0`, so it would hold for a key built from the wrong
    tool call.
    """
    assert KEY.dedup_key(_event(tool_call_id="toolu_1"), ordinal=0) == "toolu_1#0"


def test_each_sub_activity_of_one_tool_call_keeps_its_own_key() -> None:
    """FR-008: one action is not one step, and a lost step is a lost edge.

    `cd build && cmake .. && make` is one tool call the harness issues one id
    for, and three sub-activities the grammar names separately. Keyed on that
    id alone they collide, and the store discards the second and third as a
    replay: the chain keeps the `cd` and forgets what it was for.
    """
    event = _event(tool_call_id="toolu_1")
    keys = {KEY.dedup_key(event, ordinal=ordinal) for ordinal in range(3)}

    assert len(keys) == 3, keys


def test_a_replayed_sub_activity_derives_the_key_it_derived_before() -> None:
    """FR-008: the discriminator is within the action, so a replay still collides.

    The ordinal counts sub-activities of this action, never steps already in
    the sequence — a key carrying the sequence position would shift on replay
    and land the same work twice, which is the duplicate this refuses.

    Two independently built events, not one asked twice: `f(x) == f(x)` on a
    single object holds for any pure function of anything at all, including a
    key that reads the sequence's current length.
    """
    first = KEY.dedup_key(_event(tool_call_id="toolu_1"), ordinal=2)
    replayed = KEY.dedup_key(_event(tool_call_id="toolu_1"), ordinal=2)

    assert first == replayed


def test_the_same_action_run_twice_in_a_sequence_keeps_two_keys() -> None:
    """R3: running `pytest` twice in a row is real signal, not a duplicate."""
    assert KEY.dedup_key(_event(), ordinal=3) != KEY.dedup_key(_event(), ordinal=4)


def test_argument_key_order_does_not_change_the_derived_key() -> None:
    """SC-003: a mapping's iteration order is an accident of parsing, not signal."""
    reordered = replace(
        _event(),
        tool_call_arguments={"description": "run the suite", "command": "pytest -q"},
    )

    assert KEY.dedup_key(_event(), ordinal=3) == KEY.dedup_key(reordered, ordinal=3)


def test_different_arguments_or_tool_name_derive_different_keys() -> None:
    different_arguments = replace(_event(), tool_call_arguments={"command": "pytest -q -x"})
    different_tool = replace(_event(), tool_name="Read")

    derived = KEY.dedup_key(_event(), ordinal=3)
    assert derived != KEY.dedup_key(different_arguments, ordinal=3)
    assert derived != KEY.dedup_key(different_tool, ordinal=3)
