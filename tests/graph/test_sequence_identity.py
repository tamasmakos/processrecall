"""Sequence identity: the 4-tuple, and the epoch that makes clear start a new one.

R2 puts the whole of FR-012 in one integer held beside the conversation, so
these tests are about that integer's arithmetic seen through the key it
produces — a resumed session that quietly landed on a fresh chain, or a cleared
one that kept the old chain, are both stores that look full and answer wrong.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from processrecall.graph.episodic import SequenceIdentity, open_index
from processrecall.graph.store import SequenceKey


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
