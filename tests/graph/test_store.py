"""The store seam: what capture writes and what the graph reads back (FR-056).

Everything here goes through `EpisodicStore` rather than SQL of its own —
`contracts/python-api.md` puts persistence behind this seam precisely so a
different backend can answer the same questions, and a test that reached past
it into `steps` would pin the store to SQLite.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.store import (
    EpisodicStep,
    EpisodicStore,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)

from .conftest import make_step

KEY = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteEpisodicStore]:
    """A store over a freshly created episodic index, closed when the test ends."""
    connection = open_index(tmp_path / "episodes.db")
    try:
        yield SQLiteEpisodicStore(connection)
    finally:
        connection.close()


def _open_sequence(store: SQLiteEpisodicStore) -> None:
    """Open the sequence every step in this module hangs off."""
    store.open_sequence(
        Sequence(
            key=KEY,
            project_dir_key="proj",
            started_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        )
    )


def _step(*, dedup_key: str = "t1", position: int = 0) -> EpisodicStep:
    """One recordable step, with only the fields a test varies exposed."""
    return make_step(dedup_key=dedup_key, sequence_key=KEY, position=position)


def test_a_recorded_step_comes_back_whole_from_the_sequence_it_was_recorded_on(
    store: SQLiteEpisodicStore,
) -> None:
    _open_sequence(store)

    assert store.record(_step()) is True

    recorded = store.steps(KEY)
    assert len(recorded) == 1
    assert recorded[0] == replace(_step(), step_id=recorded[0].step_id)


def test_recording_a_step_twice_reports_the_duplicate_rather_than_raising(
    store: SQLiteEpisodicStore,
) -> None:
    _open_sequence(store)
    store.record(_step())

    assert store.record(_step(position=1)) is False

    assert len(store.steps(KEY)) == 1


def test_an_opened_sequence_reads_back_open_and_counting_its_steps(
    store: SQLiteEpisodicStore,
) -> None:
    _open_sequence(store)
    store.record(_step())

    assert store.sequence(KEY) == Sequence(
        key=KEY,
        project_dir_key="proj",
        started_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        step_count=1,
    )


def test_a_sequence_that_was_never_opened_is_absent_rather_than_empty(
    store: SQLiteEpisodicStore,
) -> None:
    assert store.sequence(KEY) is None


def test_closing_a_sequence_records_when_the_turn_ended(store: SQLiteEpisodicStore) -> None:
    _open_sequence(store)
    ended_at = datetime(2026, 9, 13, 10, 5, tzinfo=UTC)

    store.close_sequence(KEY, ended_at)

    closed = store.sequence(KEY)
    assert closed is not None
    assert (closed.status, closed.ended_at) == ("closed", ended_at)


def test_iterating_from_a_high_water_mark_yields_only_what_came_after_it(
    store: SQLiteEpisodicStore,
) -> None:
    _open_sequence(store)
    store.record(_step(dedup_key="t1", position=0))
    store.record(_step(dedup_key="t2", position=1))
    high_water = store.steps(KEY)[0].step_id

    assert [step.dedup_key for step in store.iter_steps(since=high_water)] == ["t2"]


def test_a_bumped_counter_is_readable_and_counts_every_bump(store: SQLiteEpisodicStore) -> None:
    store.bump("steps_recorded")
    store.bump("steps_recorded")
    store.bump("steps_duplicate")

    assert store.counters() == {"steps_recorded": 2, "steps_duplicate": 1}


def test_a_counter_the_store_could_not_write_lands_in_the_hook_log_instead(
    tmp_path: Path,
) -> None:
    broken = open_index(tmp_path / "episodes.db")
    broken.close()
    log_path = tmp_path / "log" / "hooks.jsonl"

    SQLiteEpisodicStore(broken, log_path=log_path).bump("capture_store_busy")

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["counter"] == "capture_store_busy"


def test_the_shipped_store_is_substitutable_for_the_seam_fr_056(
    store: SQLiteEpisodicStore,
) -> None:
    assert isinstance(store, EpisodicStore)
