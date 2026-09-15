"""The two seams a harness may touch: events in, guidance out (FR-002).

Every harness integration is an adapter behind these — nothing downstream of
here knows whether an action arrived as a hook payload or a replayed transcript
line, and nothing upstream knows how the answer reaches the agent.

Both are structural protocols: an adapter conforms by having the method, so a
harness package never imports a base class to be a source (FR-004, ``contracts/
python-api.md``).

On the hot path, so the standard library only.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from processrecall.trajectory.event import TrajectoryEvent


@runtime_checkable
class TrajectorySource(Protocol):
    """Events in: the bottom-up channel, one event per completed tool action."""

    def events(self) -> Iterator[TrajectoryEvent]:
        """Yield the actions this source has, oldest first.

        An iterator rather than a sequence: the live source is unbounded and the
        backfill source reads a file it must not hold in memory.
        """
        ...


@runtime_checkable
class GuidanceSink(Protocol):
    """Text out: the top-down channel, rendered guidance back to the agent."""

    def emit(self, text: str) -> None:
        """Deliver rendered guidance. Must never raise: a sink failure is counted."""
        ...


class NullSink:
    """Accepts guidance and keeps none of it: a run with nowhere to put text.

    Backfill and the offline tools render with no harness listening; they get a
    conforming sink rather than a branch around every emit.
    """

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def emit(self, text: str) -> None:
        """Discard *text*."""


class ListSink:
    """Keeps every emitted text, in order — the sink a test reads back."""

    def __init__(self) -> None:
        self._texts: list[str] = []

    def __repr__(self) -> str:
        return f"{type(self).__name__}(texts={self._texts!r})"

    @property
    def texts(self) -> list[str]:
        """What has been emitted so far, oldest first."""
        return list(self._texts)

    def emit(self, text: str) -> None:
        """Append *text* to what this sink has been given."""
        self._texts.append(text)


def deliver(sink: GuidanceSink, text: str) -> bool:
    """Emit *text* through *sink*, reporting failure as a value (FR-002).

    A sink is a harness channel that can go away mid-session — a closed stdout,
    a socket that shut. Guidance is advisory, so losing it must never surface as
    an exception in the agent's path: ``False`` is the sink failure the caller
    counts as ``guidance_silent`` (R16), the same shape the rest of the pipeline
    uses for an expected one.
    """
    try:
        sink.emit(text)
    except OSError:
        return False
    return True
