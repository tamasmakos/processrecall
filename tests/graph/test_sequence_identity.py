"""Sequence identity: the 4-tuple, and the epoch that makes clear start a new one.

R2 puts the whole of FR-012 in one integer held beside the conversation, so
these tests are about that integer's arithmetic seen through the key it
produces — a resumed session that quietly landed on a fresh chain, or a cleared
one that kept the old chain, are both stores that look full and answer wrong.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from processrecall.graph.episodic import SequenceIdentity, open_index
from processrecall.graph.store import SequenceKey
from processrecall.trajectory.telemetry import in_record_order


class FakeCounters:
    """A counter sink that keeps what it was bumped with, so a test reads it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


@pytest.fixture
def index(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A freshly created episodic index, closed again when the test ends."""
    connection = open_index(tmp_path / "episodes.db")
    try:
        yield connection
    finally:
        connection.close()


def test_a_conversation_never_seen_before_keys_its_turns_at_epoch_zero(
    index: sqlite3.Connection,
) -> None:
    identity = SequenceIdentity(index, "c1")

    assert identity.epoch == 0
    assert identity.key("p1") == SequenceKey("c1", 0, "p1", "")


@pytest.mark.parametrize("source", ["clear", "fork"])
def test_clear_and_fork_begin_a_new_sequence_even_on_the_same_session_id(
    index: sqlite3.Connection, source: str
) -> None:
    identity = SequenceIdentity(index, "c1")
    before = identity.key("p1")

    assert identity.begin(source) == 1
    assert identity.key("p1") == SequenceKey("c1", 1, "p1", "")
    assert identity.key("p1") != before


def test_a_second_clear_increments_the_epoch_again(index: sqlite3.Connection) -> None:
    identity = SequenceIdentity(index, "c1")
    identity.begin("clear")

    assert identity.begin("clear") == 2
    assert identity.key("p1") == SequenceKey("c1", 2, "p1", "")


def test_a_sub_agent_keys_its_own_chain_alongside_the_main_agent(
    index: sqlite3.Connection,
) -> None:
    identity = SequenceIdentity(index, "c1")

    assert identity.key("p1", "sub") != identity.key("p1")


def test_conversations_keep_independent_epochs(index: sqlite3.Connection) -> None:
    SequenceIdentity(index, "c1").begin("clear")

    assert SequenceIdentity(index, "c2").epoch == 0


@pytest.mark.parametrize("source", ["startup", "resume", "compact", "a-source-nobody-ships-yet"])
def test_resume_and_compaction_continue_the_sequence_they_interrupted(
    index: sqlite3.Connection, source: str
) -> None:
    identity = SequenceIdentity(index, "c1")
    identity.begin("clear")
    before = identity.key("p1")

    assert identity.begin(source) == 1
    assert identity.key("p1") == before


def _record(written_at: str, sequence: int) -> dict[str, str | int | bool]:
    """A telemetry record carrying only the two attributes ordering reads."""
    return {"event.timestamp": written_at, "event.sequence": sequence}


def test_ordering_breaks_ties_on_harness_sequence_counter() -> None:
    """`event.sequence` separates records sharing a timestamp, and nothing more.

    The counter restarts per process and can decrease inside one session after a
    resume, so it orders only what `event.timestamp` cannot: `earlier` carries
    the highest counter of the three and still comes first (FR-006). Its offset
    differs from the others' because a timestamp is an instant, not the string
    it was written as — compared as text it would sort last.
    """
    earlier = _record("2026-09-20T14:00:00+03:00", 99)
    tied_first = _record("2026-09-20T12:00:01+00:00", 1)
    tied_second = _record("2026-09-20T12:00:01+00:00", 2)

    ordered = in_record_order([tied_second, tied_first, earlier], FakeCounters())

    assert ordered == (earlier, tied_first, tied_second)


def test_ordering_compares_a_naive_timestamp_against_aware_ones() -> None:
    """A record with no UTC offset is treated as UTC rather than raising (FR-006).

    Two harnesses need not agree on whether to emit an offset; a naive
    `event.timestamp` compared against an aware one is where a bare
    `datetime.fromisoformat` ordering would raise `TypeError` instead of sorting.
    """
    naive = _record("2026-09-20T12:00:00", 1)
    aware = _record("2026-09-20T13:00:00+00:00", 1)

    ordered = in_record_order([aware, naive], FakeCounters())

    assert ordered == (naive, aware)


def test_ordering_drops_a_record_with_no_parseable_timestamp_and_counts_it() -> None:
    """A record that cannot say when it was written is refused, not raised into (FR-009, FR-010)."""
    counters = FakeCounters()
    intact = _record("2026-09-20T12:00:00+00:00", 1)
    unparseable = {"event.timestamp": "not-a-timestamp", "event.sequence": 1}

    ordered = in_record_order([unparseable, intact], counters)

    assert ordered == (intact,)
    assert counters.counted["telemetry_record_partial"] == 1


def test_a_conversation_with_no_rotation_resolves_every_instant_to_epoch_zero(
    index: sqlite3.Connection,
) -> None:
    identity = SequenceIdentity(index, "c1")

    assert identity.epoch_at(datetime(2020, 1, 1, tzinfo=UTC)) == 0


def test_epoch_at_resolves_to_the_rotation_in_force_at_a_past_instant(
    index: sqlite3.Connection,
) -> None:
    """A telemetry record lands on the epoch in force at its own timestamp, not the current one (R5)."""
    identity = SequenceIdentity(index, "c1")
    identity.begin("clear")
    first_rotation, = index.execute(
        "SELECT started_at FROM epochs WHERE conversation_id = 'c1' AND session_epoch = 1"
    ).fetchone()
    identity.begin("clear")

    assert identity.epoch == 2
    assert identity.epoch_at(datetime.fromisoformat(first_rotation)) == 1
