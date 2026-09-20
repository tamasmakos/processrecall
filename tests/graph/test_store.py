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
from processrecall.graph.schema import DecisionSource, StepDecision, StepResult
from processrecall.graph.store import (
    Agent,
    CaptureSource,
    EpisodicStep,
    EpisodicStore,
    Inference,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
    StepConsumes,
    StepTouch,
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


def _inference(inference_id: str, occurred_at: datetime) -> Inference:
    """One model call of this module's turn, placed at *occurred_at*."""
    return Inference(
        inference_id=inference_id,
        sequence_key=KEY,
        outcome="ok",
        occurred_at=occurred_at,
    )


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


def test_step_records_telemetry_fields_and_source(store: SQLiteEpisodicStore) -> None:
    _open_sequence(store)
    refused = replace(
        _step(),
        result=StepResult.FAILURE,
        decision=StepDecision.REJECTED,
        decision_source=DecisionSource.USER_REJECT,
        duration_ms=1204,
        error_type="PermissionDenied",
        input_size_bytes=96,
        result_size_bytes=0,
        tool_source="mcp",
        source=CaptureSource.TELEMETRY,
    )

    store.record(refused)

    recorded = store.steps(KEY)[0]
    assert recorded == replace(refused, step_id=recorded.step_id)
    assert store.counters()["steps_from_telemetry"] == 1


def test_a_step_only_the_hook_saw_counts_against_the_fallback_path(
    store: SQLiteEpisodicStore,
) -> None:
    _open_sequence(store)

    store.record(replace(_step(), source=CaptureSource.HOOK))

    assert store.counters()["steps_from_hook"] == 1


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


def test_a_sequence_records_the_commit_and_branch_the_turn_ran_on(
    store: SQLiteEpisodicStore,
) -> None:
    opened = Sequence(
        key=KEY,
        project_dir_key="proj",
        started_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        head_revision="4655668",
        head_branch="007-otel-graph-schema-v2",
        source=CaptureSource.TELEMETRY,
    )

    store.open_sequence(opened)

    assert store.sequence(KEY) == opened


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


def test_touch_records_mode_and_resolution(store: SQLiteEpisodicStore) -> None:
    _open_sequence(store)
    store.record(_step())
    step_id = store.steps(KEY)[0].step_id
    read_file = StepTouch(step_id=step_id, entity_key="processrecall/graph/store.py", mode="read")
    modified_symbol = StepTouch(
        step_id=step_id,
        entity_key="processrecall/graph/store.py#SQLiteEpisodicStore.record_touch",
        mode="modified",
        resolution="symbol",
    )

    store.record_touch(read_file)
    store.record_touch(modified_symbol)

    touched = store.touches_for(step_id)
    assert touched == (read_file, modified_symbol)
    assert [touch.resolution for touch in touched] == ["file", "symbol"]
    assert store.counters()["touched_file_only"] == 1
    assert "touched_symbol_resolved" not in store.counters()


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


def test_spawn_edge_forms_when_subagent_arrives_first(store: SQLiteEpisodicStore) -> None:
    """The parent is named on the child's own row, so neither arrival order loses it.

    `parent_agent_id` is observed in span enrichment and nowhere else (R7), and
    enrichment sees the sub-agent before the turn that spawned it has an agent
    row; the `subagent_completed` event that names the type and the workflow run
    arrives afterwards and names no parent at all.
    """
    spawned_at = datetime(2026, 9, 13, 10, 1, tzinfo=UTC)
    completed_at = datetime(2026, 9, 13, 10, 2, tzinfo=UTC)
    enriched = Agent(
        agent_id="sub-1",
        kind="subagent",
        first_seen=spawned_at,
        last_seen=spawned_at,
        parent_agent_id="main-1",
    )
    completed = Agent(
        agent_id="sub-1",
        kind="subagent",
        first_seen=completed_at,
        last_seen=completed_at,
        agent_type="code-reviewer",
        workflow_run_id="run-7",
        workflow_name="tdd",
    )
    parent = replace(enriched, agent_id="main-1", kind="main", parent_agent_id=None)

    store.record_agent(enriched)
    store.record_agent(completed)
    store.record_agent(parent)

    assert store.agent("sub-1") == replace(
        completed, first_seen=spawned_at, parent_agent_id="main-1"
    )
    assert store.agent("main-1") == parent


def test_consumed_edge_records_adjacency_link(store: SQLiteEpisodicStore) -> None:
    """No attribute joins a tool result to the model call that asked for it (R8).

    So the edge is derived from the inference most closely preceding the step
    inside the same prompt, and the row says it was derived by adjacency rather
    than observed — the honesty FR-003 asks of a field no telemetry source fills.
    """
    _open_sequence(store)
    store.record_inference(_inference("req-1", datetime(2026, 9, 13, 9, 59, 58, tzinfo=UTC)))
    store.record_inference(_inference("req-2", datetime(2026, 9, 13, 10, 0, tzinfo=UTC)))
    store.record_inference(_inference("req-3", datetime(2026, 9, 13, 10, 0, 2, tzinfo=UTC)))
    store.record(_step())
    recorded = store.steps(KEY)[0]

    consumed = store.derive_consumes(recorded)

    assert consumed == StepConsumes(step_id=recorded.step_id, inference_id="req-2", link="adjacent")
    assert store.consumes_for(recorded.step_id) == (consumed,)
    assert store.counters()["inference_adjacent"] == 1
    assert "inference_unlinked" not in store.counters()


def test_a_step_with_no_earlier_inference_is_counted_unlinked(
    store: SQLiteEpisodicStore,
) -> None:
    """A step whose turn holds no earlier model call is attributed to none (R8).

    The only inference on the turn came after the step, so nothing can have
    produced it: no edge is written, and the gap is counted rather than left as
    an unexplained missing edge (FR-003).
    """
    _open_sequence(store)
    store.record_inference(_inference("req-1", datetime(2026, 9, 13, 10, 0, 2, tzinfo=UTC)))
    store.record(_step())
    recorded = store.steps(KEY)[0]

    assert store.derive_consumes(recorded) is None

    assert store.consumes_for(recorded.step_id) == ()
    assert store.counters()["inference_unlinked"] == 1
    assert "inference_adjacent" not in store.counters()
