"""One canonical action -> the episodic rows it decomposes into.

Bottom-up decode, once, for every source there is. An action reaches this
module as a :class:`~processrecall.trajectory.event.TrajectoryEvent` — from the
live hook or from the backfill reader, and the difference stops being visible
at the adapter — and leaves it as the ordered steps of FR-017, written under
the sequence it belongs to. Both callers write through here rather than each
deriving its own rows, which is what makes a backfilled step indistinguishable
from a live one and de-duplicable against it (FR-015).

On the hot path, so the standard library only.

Example:
    from processrecall.graph.record import record_event

    landed = record_event(event, connection)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from processrecall.exceptions import PackError
from processrecall.graph.episodic import SequenceIdentity
from processrecall.graph.store import (
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)
from processrecall.graph.templates import template_of
from processrecall.procedures.outcome import Outcome, classify_outcome
from processrecall.procedures.step import SubActivity, steps_from
from processrecall.procedures.taxonomy import identify_procedure
from processrecall.trajectory.event import TrajectoryEvent
from processrecall.trajectory.paths import normalise_path, project_key
from processrecall.trajectory.vocabulary import load_vocabulary


@dataclass(frozen=True, slots=True)
class _Action:
    """One adapted action and where it lands: what every step of it shares.

    An action decomposes into several sub-activities (FR-017), and all of them
    sit on one sequence, at consecutive positions, under one verdict — resolved
    once here rather than threaded through every call below.

    Attributes:
        event: The action, canonically.
        key: The sequence it belongs to, at the conversation's current epoch.
        first_position: Where its first sub-activity lands in that sequence.
        outcome: How it went. The canonical event carries no error flag —
            ``contracts/trajectory-event.md`` maps none from this harness — so
            the verdict is derived from the result text alone (FR-034).
        activities: What it actually did, in the order it did it.
    """

    event: TrajectoryEvent
    key: SequenceKey
    first_position: int
    outcome: Outcome
    activities: tuple[SubActivity, ...]


def _step_of(activity: SubActivity, action: _Action) -> EpisodicStep:
    """One sub-activity of *action*, as the row the episodic index stores."""
    event = action.event
    identity = identify_procedure(activity)
    position = action.first_position + activity.ordinal
    return EpisodicStep(
        dedup_key=action.key.dedup_key(event, activity.ordinal),
        sequence_key=action.key,
        position=position,
        node_key=identity.key,
        activity_class=identity.activity_class,
        template=template_of(activity),
        occurred_at=event.occurred_at,
        program=identity.program,
        files=tuple(normalise_path(path, event.project_dir) for path in activity.files),
        result_snippet=event.tool_call_result,
        outcome=action.outcome,
        record_ref=event.record_ref,
    )


def record_event(
    event: TrajectoryEvent, connection: sqlite3.Connection
) -> tuple[EpisodicStep, ...]:
    """Record the action *event* describes; the steps that landed, in order.

    Nothing landing is an ordinary answer, not an error: a step whose dedup key
    was already there is counted by the store (FR-008), which is also how a
    backfilled action that live capture already saw writes nothing.

    A store that will not take the write is one of those ordinary answers too:
    the step is dropped and counted as ``capture_store_busy`` rather than
    raised, because a capture that fails must not surface against the action it
    was only watching (FR-014). The store writes the count to its own fallback
    log when it is the store itself that is unreachable (R16). A pack that
    fails to load is the same kind of failure, from ``load_vocabulary``
    instead.

    A fault partway through a multi-activity action still reports the steps
    that landed before it struck, never all-or-nothing: they are already rows
    in the store, so a caller told otherwise would go looking for guidance on
    steps it believes never happened.
    """
    store = SQLiteEpisodicStore(connection)
    key = SequenceIdentity(connection, event.conversation_id).key(event.prompt_id, event.agent_id)
    landed: list[EpisodicStep] = []
    try:
        store.open_sequence(
            Sequence(
                key=key,
                project_dir_key=project_key(event.project_dir),
                started_at=event.occurred_at,
            )
        )
        action = _Action(
            event=event,
            key=key,
            first_position=len(store.steps(key)),
            outcome=classify_outcome(event.tool_call_result, is_error=False),
            activities=steps_from(
                event, load_vocabulary().activity_for(event.tool_name, store.bump)
            ),
        )
        for activity in action.activities:
            step = _step_of(activity, action)
            if store.record(step):
                landed.append(step)
    except (sqlite3.Error, PackError):
        store.bump("capture_store_busy")
    return tuple(landed)
