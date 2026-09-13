"""The two seams a harness may touch: events in, guidance out (FR-002).

Both are structural, so an adapter satisfies one by having the method, not by
inheriting anything — that is what lets the hook adapter, the backfill reader
and a test fake be the same kind of thing to everything downstream (FR-004).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from processrecall.trajectory.event import SourceKind, TrajectoryEvent
from processrecall.trajectory.protocol import (
    ListSink,
    NullSink,
    TrajectorySource,
    deliver,
)


def make_event() -> TrajectoryEvent:
    """A well-formed event — the seams carry it, they never inspect it."""
    return TrajectoryEvent(
        operation_name="execute_tool",
        conversation_id="conv-1",
        agent_id="",
        agent_name="",
        tool_name="Bash",
        tool_call_id="toolu_01",
        tool_call_arguments={"command": "pytest -q"},
        tool_call_result="1 passed",
        prompt_id="prompt-1",
        project_dir="/app",
        record_ref="/transcripts/conv-1.jsonl#12",
        occurred_at=datetime(2026, 1, 1, 9, 30, tzinfo=UTC),
        source_kind=SourceKind.LIVE,
    )


class OneEventSource:
    """The smallest conforming source: it yields, and it inherits nothing."""

    def events(self) -> Iterator[TrajectoryEvent]:
        yield make_event()


def test_a_source_is_anything_that_yields_events() -> None:
    """Structural, so a harness adapter never imports a base class to be one."""
    assert isinstance(OneEventSource(), TrajectorySource)
    assert [event.tool_name for event in OneEventSource().events()] == ["Bash"]


class HasNoEvents:
    """A near-miss: a differently named method, not the one the contract asks for."""

    def iter_events(self) -> Iterator[TrajectoryEvent]:
        yield make_event()


def test_something_without_events_is_not_a_source() -> None:
    """The method is the whole contract, so its absence has to disqualify."""
    assert not isinstance(HasNoEvents(), TrajectorySource)


def test_a_list_sink_keeps_what_it_was_given_in_order() -> None:
    """The sink a test reads back: guidance is text out, and order is meaning."""
    sink = ListSink()

    sink.emit("first")
    sink.emit("second")

    assert sink.texts == ["first", "second"]


def test_a_null_sink_accepts_guidance_and_keeps_none_of_it() -> None:
    """The sink for a run with nowhere to put text: still a sink, still silent.

    Backfill and the offline tools render without a harness listening; they need
    a conforming sink rather than a branch around every emit.
    """
    sink = NullSink()

    sink.emit("guidance nobody is listening for")


class BrokenSink:
    """A sink whose delivery fails — the harness channel that went away."""

    def emit(self, text: str) -> None:
        raise OSError("the hook's stdout is closed")


def test_a_sink_failure_is_reported_rather_than_raised() -> None:
    """A failed channel must never surface as an exception in the agent's path.

    ``False`` is the verdict the caller counts, the same shape the rest of the
    pipeline uses for an expected failure (FR-002).
    """
    assert deliver(BrokenSink(), "guidance") is False


def test_delivered_guidance_reaches_the_sink_and_reports_success() -> None:
    """The ordinary case: the text arrives and the caller counts nothing."""
    sink = ListSink()

    assert deliver(sink, "run the tests first") is True
    assert sink.texts == ["run the tests first"]
