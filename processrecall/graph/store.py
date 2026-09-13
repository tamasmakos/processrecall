"""The store seam: episodic rows in, episodic rows out (FR-056).

`contracts/python-api.md` puts persistence behind a protocol so an alternative
backend can be substituted without graph or guidance knowing. This module is
that protocol and the one implementation that ships: SQLite, over the index
`episodic.py` opens.

The dataclasses here are the episodic plane of `data-model.md` — a sequence, its
identity, and the steps hanging off it — and they are what crosses the seam in
both directions, so no caller ever spells a column name.

On the hot path, so the standard library only.

Example:
    from processrecall.graph.episodic import open_index
    from processrecall.graph.store import SQLiteEpisodicStore

    store = SQLiteEpisodicStore(open_index())
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from processrecall.config import home_dir
from processrecall.symbolic.packs import ActivityClass, ProcessType

if TYPE_CHECKING:
    from processrecall.trajectory.event import TrajectoryEvent

_logger = logging.getLogger("processrecall")

#: Bytes at which the R16 fallback log rotates aside rather than growing
#: unbounded.
_LOG_ROTATE_BYTES = 5 * 1024 * 1024

#: The 2 KB ceiling of FR-010, in characters. It lives at the write seam rather
#: than in one adapter because every source writes through the store: a snippet
#: is for recognising what happened, and ``record_ref`` points at the whole
#: record for anyone who needs the rest.
#:
#: FR-010's other half — 600 characters of a prompt — has no counterpart here:
#: no field of `EpisodicStep` or the `steps`/`sequences` schema stores prompt
#: text at all (R13), so there is nothing to bound.
RESULT_CEILING = 2048


def _canonical_json(arguments: Mapping[str, object]) -> str:
    """*arguments* as one string that two passes over the same record agree on.

    Sorted keys and no whitespace: a mapping's iteration order is an accident of
    how it was parsed, and a derived key that changed with it would record the
    same backfilled action twice (R3).
    """
    return json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class SequenceKey:
    """What identifies one prompt's chain of actions (R2).

    Four fields rather than one id because no harness issues one: a conversation
    is resumed, compacted and forked, and sub-agents run inside their parent's
    turn. ``agent_id`` is ``""`` for the main agent.
    """

    conversation_id: str
    session_epoch: int
    prompt_id: str
    agent_id: str = ""

    def dedup_key(self, event: TrajectoryEvent, ordinal: int) -> str:
        """What identifies *event* as an action, at position *ordinal* of this sequence.

        The harness's own tool-call id when it issued one (FR-008); otherwise the
        R3 derivation, a pure function of the record so that backfilling the same
        source twice derives one key rather than two (SC-003). The ``syn-``
        prefix keeps a derived key out of the space of harness-issued ones.
        """
        if event.tool_call_id:
            return event.tool_call_id
        material = "|".join(
            (
                self.conversation_id,
                str(self.session_epoch),
                self.prompt_id,
                self.agent_id,
                str(ordinal),
                event.tool_name,
                _canonical_json(event.tool_call_arguments),
            )
        )
        return f"syn-{sha256(material.encode()).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class Sequence:
    """One prompt's chain: the steps of a single user turn, and its outcome.

    ``step_count`` is read from the store rather than kept in step with the
    rows — a stored count that drifts from the rows is the kind of quietly wrong
    number Principle V exists to prevent.
    """

    key: SequenceKey
    project_dir_key: str
    started_at: datetime
    process_type: ProcessType = ProcessType.UNKNOWN
    status: str = "open"
    ended_at: datetime | None = None
    step_count: int = 0
    derived_outcome: str = "neutral"
    declared_outcome: str | None = None


@dataclass(frozen=True, slots=True)
class EpisodicStep:
    """One concrete recorded action — the Tensor Brain's episodic index, on disk.

    ``step_id`` is the store's own rowid and the snapshot's high-water mark: it
    is ``0`` on a step that has not been recorded yet, and the store assigns the
    real one.
    """

    dedup_key: str
    sequence_key: SequenceKey
    position: int
    node_key: str
    activity_class: ActivityClass
    template: str
    occurred_at: datetime
    program: str = ""
    files: tuple[str, ...] = ()
    result_snippet: str = ""
    outcome: str = "neutral"
    record_ref: str = ""
    rationale_label: str | None = None
    symbol_ref: str | None = None
    step_id: int = 0


@runtime_checkable
class EpisodicStore(Protocol):
    """Everything the pipeline asks of persistence, and nothing about a backend.

    A structural protocol, like the two harness seams: a replacement backend
    conforms by answering these questions, not by inheriting (FR-056).
    """

    def close_sequence(self, key: SequenceKey, at: datetime) -> None:
        """Mark the sequence *key* names as having ended at *at*."""
        ...

    def record(self, step: EpisodicStep) -> bool:
        """Write *step*, reporting whether it landed.

        ``False`` when the dedup key already existed; never raises on one.
        """
        ...

    def sequence(self, key: SequenceKey) -> Sequence | None:
        """The sequence *key* names, or ``None`` when nothing opened it."""
        ...

    def steps(self, key: SequenceKey) -> tuple[EpisodicStep, ...]:
        """Every step of *key*, in the order it was carried out."""
        ...

    def iter_steps(self, since: int = 0) -> Iterator[EpisodicStep]:
        """Every step recorded after ``step_id`` *since*, oldest first."""
        ...

    def bump(self, counter: str) -> None:
        """Add one to *counter* (R16).

        Never raises: a store that cannot keep the count says so elsewhere.
        """
        ...

    def counters(self) -> Mapping[str, int]:
        """Every counter this store has kept, against its value."""
        ...


#: The step columns, in the order `_step_from_row` reads them back.
_STEP_COLUMNS = (
    "dedup_key, conversation_id, session_epoch, prompt_id, agent_id, position, node_key,"
    " activity_class, template, occurred_at, program, files, result_snippet, outcome,"
    " record_ref, rationale_label, symbol_ref, step_id"
)

#: The predicate every `SequenceKey` lookup shares — a fifth key field would
#: otherwise mean editing this in four places.
_SEQUENCE_WHERE = (
    " WHERE conversation_id = ? AND session_epoch = ? AND prompt_id = ? AND agent_id = ?"
)


def _key_params(key: SequenceKey) -> tuple[str, int, str, str]:
    """The bind parameters for `_SEQUENCE_WHERE`, in its column order."""
    return (key.conversation_id, key.session_epoch, key.prompt_id, key.agent_id)


def _step_from_row(row: tuple[Any, ...]) -> EpisodicStep:
    """Rebuild a step from one `_STEP_COLUMNS` row."""
    return EpisodicStep(
        dedup_key=str(row[0]),
        sequence_key=SequenceKey(str(row[1]), int(row[2]), str(row[3]), str(row[4])),
        position=int(row[5]),
        node_key=str(row[6]),
        activity_class=ActivityClass(row[7]),
        template=str(row[8]),
        occurred_at=datetime.fromisoformat(str(row[9])),
        program=str(row[10]),
        files=tuple(json.loads(str(row[11]))),
        result_snippet=str(row[12]),
        outcome=str(row[13]),
        record_ref=str(row[14]),
        rationale_label=None if row[15] is None else str(row[15]),
        symbol_ref=None if row[16] is None else str(row[16]),
        step_id=int(row[17]),
    )


class SQLiteEpisodicStore:
    """The shipped store: one short transaction per write, over `open_index`.

    Takes an open connection rather than a path so the caller decides the store's
    lifetime — a hook opens one per invocation (R10), a test opens one per
    temporary directory.
    """

    def __init__(self, connection: sqlite3.Connection, log_path: Path | None = None) -> None:
        self._connection = connection
        self._log_path = log_path if log_path is not None else home_dir() / "log" / "hooks.jsonl"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(connection={self._connection!r})"

    def open_sequence(self, sequence: Sequence) -> None:
        """Record *sequence* as open, or leave the one already there alone.

        Idempotent because the first step of a turn is what opens it and a turn
        has many steps: re-opening must not reset a sequence's start.
        """
        with self._connection:
            self._connection.execute(
                "INSERT INTO sequences (conversation_id, session_epoch, prompt_id, agent_id,"
                " project_dir_key, process_type, status, derived_outcome, declared_outcome,"
                " started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (
                    sequence.key.conversation_id,
                    sequence.key.session_epoch,
                    sequence.key.prompt_id,
                    sequence.key.agent_id,
                    sequence.project_dir_key,
                    sequence.process_type,
                    sequence.status,
                    sequence.derived_outcome,
                    sequence.declared_outcome,
                    sequence.started_at.isoformat(),
                ),
            )

    def close_sequence(self, key: SequenceKey, at: datetime) -> None:
        """Mark the sequence *key* names as having ended at *at*.

        Only a turn the harness saw end is closed; marking one abandoned
        mid-flight ``incomplete`` instead belongs to whatever opens the next
        sequence (T026), since that is where "abandoned" is detected.
        """
        with self._connection:
            self._connection.execute(
                f"UPDATE sequences SET status = 'closed', ended_at = ?{_SEQUENCE_WHERE}",
                (at.isoformat(), *_key_params(key)),
            )

    def record(self, step: EpisodicStep) -> bool:
        """Write *step*, reporting whether it landed.

        ``False`` means the dedup key was already there (FR-008). A replayed
        harness payload is an expected, countable event rather than an error, so
        the conflict is resolved by the database and reported as a value — and
        counted as ``steps_duplicate`` (R3), because a duplicate nobody counted
        reads exactly like an action that was never sent.

        The result snippet is cut to :data:`RESULT_CEILING` on the way in
        (FR-010): the ceiling is a property of what is stored, not of the
        adapter that happened to produce the step.
        """
        with self._connection:
            cursor = self._connection.execute(
                "INSERT INTO steps (dedup_key, conversation_id, session_epoch, prompt_id,"
                " agent_id, position, node_key, activity_class, template, occurred_at,"
                " program, files, result_snippet, outcome, record_ref, rationale_label,"
                " symbol_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (dedup_key) DO NOTHING",
                (
                    step.dedup_key,
                    step.sequence_key.conversation_id,
                    step.sequence_key.session_epoch,
                    step.sequence_key.prompt_id,
                    step.sequence_key.agent_id,
                    step.position,
                    step.node_key,
                    step.activity_class,
                    step.template,
                    step.occurred_at.isoformat(),
                    step.program,
                    json.dumps(list(step.files)),
                    step.result_snippet[:RESULT_CEILING],
                    step.outcome,
                    step.record_ref,
                    step.rationale_label,
                    step.symbol_ref,
                ),
            )
        if cursor.rowcount == 1:
            return True
        self.bump("steps_duplicate")
        return False

    def sequence(self, key: SequenceKey) -> Sequence | None:
        """The sequence *key* names, or ``None`` when no turn opened it.

        ``None`` rather than an empty sequence: a turn nothing ever opened and a
        turn opened with no steps on it are different facts, and a caller that
        cannot tell them apart is the silent failure Principle V forbids.
        """
        row = self._connection.execute(
            "SELECT project_dir_key, process_type, status, started_at, ended_at,"
            " derived_outcome, declared_outcome,"
            " (SELECT count(*) FROM steps WHERE steps.conversation_id = sequences.conversation_id"
            "  AND steps.session_epoch = sequences.session_epoch"
            "  AND steps.prompt_id = sequences.prompt_id"
            "  AND steps.agent_id = sequences.agent_id) FROM sequences"
            f"{_SEQUENCE_WHERE}",
            _key_params(key),
        ).fetchone()
        if row is None:
            return None
        return Sequence(
            key=key,
            project_dir_key=str(row[0]),
            started_at=datetime.fromisoformat(str(row[3])),
            process_type=ProcessType(row[1]),
            status=str(row[2]),
            ended_at=None if row[4] is None else datetime.fromisoformat(str(row[4])),
            derived_outcome=str(row[5]),
            declared_outcome=None if row[6] is None else str(row[6]),
            step_count=int(row[7]),
        )

    def steps(self, key: SequenceKey) -> tuple[EpisodicStep, ...]:
        """Every step of *key*, in the order it was carried out."""
        rows = self._connection.execute(
            f"SELECT {_STEP_COLUMNS} FROM steps{_SEQUENCE_WHERE} ORDER BY position",
            _key_params(key),
        )
        return tuple(_step_from_row(row) for row in rows)

    def bump(self, counter: str) -> None:
        """Add one to *counter*, creating it at one if it is new (R16).

        A hook process has no return value to carry a result, so a counter is
        the only way a failed lookup can be told apart from an empty one — which
        is what Principle V asks of every path here. When the store itself is
        what failed, the count goes to the hook log instead (R16): the one thing
        that may not happen is the increment vanishing.
        """
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO counters (name, value) VALUES (?, 1)"
                    " ON CONFLICT (name) DO UPDATE SET value = value + 1",
                    (counter,),
                )
        except sqlite3.Error as exc:
            self._log_fallback(counter, exc)

    def counters(self) -> Mapping[str, int]:
        """Every counter this store has ever incremented, against its value.

        The read side is the cold path — `show counters` and `inspect` — so a
        store it cannot read raises rather than answering with an empty mapping
        that reads as "nothing ever happened".
        """
        return {
            str(name): int(value)
            for name, value in self._connection.execute("SELECT name, value FROM counters")
        }

    def iter_steps(self, since: int = 0) -> Iterator[EpisodicStep]:
        """Every step recorded after ``step_id`` *since*, oldest first.

        An iterator, and a high-water mark rather than a date: this is how the
        snapshot is built incrementally over a store that does not fit in
        memory.
        """
        rows = self._connection.execute(
            f"SELECT {_STEP_COLUMNS} FROM steps WHERE step_id > ? ORDER BY step_id",
            (since,),
        )
        return (_step_from_row(row) for row in rows)

    def _log_fallback(self, counter: str, exc: sqlite3.Error) -> None:
        """Append one JSON line recording the counter the store could not keep.

        The last resort of a hook that must exit 0 regardless (FR-014): if even
        this fails there is nowhere left to say so, and losing the line is
        preferable to raising into the agent's own action. Rotated aside at
        5 MB (R16) so an unreachable store cannot grow this file forever.
        """
        log_fallback(self._log_path, counter, exc)


def log_fallback(log_path: Path, counter: str, exc: sqlite3.Error) -> None:
    """Append one JSON line at *log_path* recording *counter*, which could not be kept.

    The last resort of a hook that must exit 0 regardless (FR-014) — also the
    path taken when the store itself could not be opened, before any
    :class:`SQLiteEpisodicStore` exists to ask. If even this fails there is
    nowhere left to say so, and losing the line is preferable to raising into
    the agent's own action. Rotated aside at 5 MB (R16) so an unreachable store
    cannot grow this file forever.
    """
    line = json.dumps(
        {
            "at": datetime.now(UTC).isoformat(),
            "counter": counter,
            "error": str(exc),
        }
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size >= _LOG_ROTATE_BYTES:
            log_path.replace(log_path.with_suffix(".jsonl.1"))
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"{line}\n")
    except OSError:
        _logger.warning("could not record counter %s: %s", counter, exc)
