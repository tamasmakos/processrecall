"""`processrecall backfill` — past sessions replayed through the live seam.

A memory that only learns from work done after it was installed starts empty
and stays thin. This command reads the harness's own session transcripts for a
project and writes them through the same
:func:`~processrecall.graph.record.record_event` the hook writes through
(FR-015), so a seeded step is indistinguishable from a live one and the dedup
key keeps a session that was already captured live from landing twice.

A record whose format the reader does not understand costs its own record and
nothing more (FR-016): :class:`~processrecall.trajectory.transcript.\
TranscriptSource` skips and counts it, and the summary here reports how many
were skipped and why, grouped by reason.

Example:
    from processrecall.cli.backfill import Replay, backfill

    print(backfill(Replay(connection=connection, project_dir=Path.cwd())))
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from processrecall.graph.record import record_event
from processrecall.graph.store import SQLiteEpisodicStore
from processrecall.trajectory.event import TrajectoryEvent
from processrecall.trajectory.transcript import TranscriptSource, transcript_directory


@dataclass(frozen=True, slots=True)
class Replay:
    """What one backfill replays, and how much of it.

    Attributes:
        connection: The episodic index, opened by the caller and closed by it.
        project_dir: The project whose transcripts are read. Discovery only:
            every action is filed under the directory its own record names, so
            a session that changed directory still lands where it ran.
        since: The earliest action to record, or ``None`` for all of them.
        dry_run: Whether to report what would be written without writing it.
    """

    connection: sqlite3.Connection
    project_dir: Path
    since: datetime | None = None
    dry_run: bool = False

    def transcripts(self) -> tuple[Path, ...]:
        """This project's session transcripts, in a fixed order.

        Sorted, so two runs over one directory replay the sessions the same
        way round; a directory the harness never made replays nothing rather
        than raising, since a project with no recorded sessions is an empty
        backfill and not a fault.
        """
        return tuple(sorted(transcript_directory(self.project_dir).glob("*.jsonl")))

    def includes(self, event: TrajectoryEvent) -> bool:
        """Whether *event* is recent enough for this run to record."""
        return self.since is None or event.occurred_at >= self.since


@dataclass(frozen=True, slots=True)
class Replayed:
    """What one backfill found, wrote, and could not read.

    Attributes:
        sessions: Transcripts read.
        steps: Episodic rows that landed — or, on a dry run, that would have.
        skipped: Each reason a record was passed over, against how many
            records it accounts for (FR-016).
        store_failures: How many writes the store itself dropped rather than
            landed or skipped — the same ``capture_store_busy`` the hook
            counts and never surfaces (FR-014), which is why this is what a
            backfill's exit code reflects instead.
    """

    sessions: int
    steps: int
    skipped: Counter[str]
    store_failures: int = 0

    def __str__(self) -> str:
        """The summary an operator reads, skipped records grouped by reason."""
        headline = f"sessions={self.sessions}  steps={self.steps}  skipped={self.skipped.total()}"
        grouped = (f"  {count}  {reason}" for reason, count in sorted(self.skipped.items()))
        lines = [headline, *grouped]
        if self.store_failures:
            lines.append(f"store dropped {self.store_failures} write(s): capture_store_busy")
        return "\n".join(lines)


def backfill(replay: Replay) -> Replayed:
    """Record every action *replay* names, reporting what landed and what did not."""
    transcripts = replay.transcripts()
    steps = 0
    skipped: Counter[str] = Counter()
    with _write_target(replay) as target:
        store = SQLiteEpisodicStore(target)
        busy_before = store.counters().get("capture_store_busy", 0)
        for transcript in transcripts:
            source = TranscriptSource(transcript, SQLiteEpisodicStore(target))
            for event in source.events():
                if replay.includes(event):
                    steps += len(record_event(event, target))
            skipped.update(skip.category for skip in source.skipped)
        store_failures = store.counters().get("capture_store_busy", 0) - busy_before
    return Replayed(
        sessions=len(transcripts), steps=steps, skipped=skipped, store_failures=store_failures
    )


@contextmanager
def _write_target(replay: Replay) -> Iterator[sqlite3.Connection]:
    """Where *replay* writes: the index itself, or a throwaway copy of it.

    A dry run replays into an in-memory copy rather than deriving rows and
    declining to write them, so what it reports is what a real run would
    land — duplicates against history already there included — and the index
    on disk is untouched either way.
    """
    if not replay.dry_run:
        yield replay.connection
        return
    with closing(sqlite3.connect(":memory:")) as scratch:
        replay.connection.backup(scratch)
        yield scratch


def parse_since(raw: str) -> datetime:
    """*raw* as the instant ``--since`` names, in UTC when it names no zone.

    A bare date is the ordinary spelling, and an offset-naive instant cannot be
    compared with the offset-aware time an action carries — so the zone is
    assumed here, once, rather than guessed at every comparison.
    """
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
