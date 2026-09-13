"""The Claude Code hooks: a hook payload in, the one canonical event out (FR-001).

This is the whole of what welds the system to Claude Code: the six verbs of
``contracts/agent-hooks.md``, and the framing that reads one JSON object from
the harness and writes at most one back. Everything downstream
reads a :class:`~processrecall.trajectory.event.TrajectoryEvent` and cannot tell
which harness produced it, so the harness's spellings — ``session_id``,
``tool_use_id``, ``tool_input`` — stop here, at the mapping table of
``contracts/trajectory-event.md``.

Two rules of that contract live in this module rather than downstream. The
result is cut to :data:`RESULT_CEILING` characters *here* (FR-010), because a
bound applied later would mean the untruncated text had already been written
somewhere; and a payload missing a field that places the action in a sequence is
counted and dropped, never raised — a hook that raises surfaces against the
developer's own action (R16).

On the hot path, so the standard library only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Protocol, TextIO

from processrecall.config import STORE_DIR, home_dir
from processrecall.exceptions import PackError
from processrecall.graph.episodic import SequenceIdentity, open_index
from processrecall.graph.store import (
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
    log_fallback,
)
from processrecall.graph.templates import template_of
from processrecall.procedures.outcome import Outcome, classify_outcome
from processrecall.procedures.step import SubActivity, steps_from
from processrecall.procedures.taxonomy import identify_procedure
from processrecall.trajectory.event import SourceKind, TrajectoryEvent
from processrecall.trajectory.paths import lexical_path, normalise_path, project_key
from processrecall.trajectory.vocabulary import load_vocabulary

_logger = logging.getLogger("processrecall")

#: The 2 KB ceiling of FR-010, in characters. A snippet is for recognising what
#: happened, not for replaying it: ``record_ref`` points at the whole record.
RESULT_CEILING = 2048

#: The project-level opt-out: its presence under the project directory is the
#: whole signal, contents ignored (R13).
OPTOUT_MARKER = Path(STORE_DIR) / "optout"

#: The home-level deny list, under :func:`~processrecall.config.home_dir`: one
#: ``fnmatch`` pattern per line. It lives in the home directory because the
#: repository one may not want to add a marker file to is precisely the
#: sensitive one (R13).
DENY_LIST = "deny.txt"


class Counters(Protocol):
    """The slice of the episodic store the adapter writes to.

    Narrower than :class:`processrecall.graph.store.EpisodicStore` on purpose
    (ISP): the adapter only ever counts, so it depends on the one method it
    uses rather than on the whole store.
    """

    def bump(self, counter: str) -> None:
        """Increment the counter named *counter*."""
        ...


def is_excluded(project_dir: str, counters: Counters) -> bool:
    """Whether capture is suppressed for *project_dir*, counting it when it is.

    Asked first, on the harness's ``cwd`` alone, so that "entirely" in FR-058
    is literally true: on a match the payload is never parsed, and no step,
    snippet or prompt text exists to suppress (R13).
    """
    if (Path(project_dir) / OPTOUT_MARKER).exists() or _is_denied(project_dir):
        counters.bump("capture_excluded")
        return True
    return False


def _is_denied(project_dir: str) -> bool:
    """Whether any pattern of the home deny list matches *project_dir*.

    Matched against the POSIX-normalised absolute directory, so one project
    denied once stays denied however the harness spelled its ``cwd`` — and the
    pattern is normalised the same way, so a drive-letter pattern still matches
    on Windows. Blank lines and ``#`` comments are ignored; an absent list
    denies nothing.
    """
    try:
        lines = (home_dir() / DENY_LIST).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    except OSError:
        _logger.warning("%s: could not be read; denying nothing", home_dir() / DENY_LIST)
        return False
    project = str(lexical_path(project_dir))
    return any(
        fnmatchcase(project, str(lexical_path(pattern)))
        for line in lines
        if (pattern := line.strip()) and not pattern.startswith("#")
    )


def _record_ref(payload: Mapping[str, Any]) -> str:
    """Where the originating record can be found, as ``"<path>#<locator>"``.

    A live ``PostToolUse`` payload carries no line ordinal — the harness is
    still writing the line this action will occupy — so the tool-call id is the
    locator, and a human greps the transcript for it. The backfill reader, which
    does know the ordinal, spells the same pointer with the number (see the
    ``record_ref`` row of ``contracts/trajectory-event.md``).
    """
    transcript = str(payload.get("transcript_path") or "")
    return f"{transcript}#{payload.get('tool_use_id') or ''}" if transcript else ""


def adapt_post_tool_use(payload: Mapping[str, Any], counters: Counters) -> TrajectoryEvent | None:
    """The event *payload* describes, or ``None`` when it describes no routable one.

    ``None`` covers three cases, neither raised into the developer's action:
    ``cwd`` is excluded (FR-058, R13), checked before anything else in the
    payload is read; a payload for a hook event other than ``PostToolUse`` is
    not this adapter's to read and is ignored outright, uncounted; a
    ``PostToolUse`` payload with no ``session_id``, ``prompt_id`` or
    ``tool_name`` cannot be placed on a sequence, so it increments
    ``capture_payload_malformed`` and the caller moves on.
    """
    if is_excluded(str(payload.get("cwd") or ""), counters):
        return None
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    arguments = payload.get("tool_input")
    event = TrajectoryEvent(
        operation_name="execute_tool",
        conversation_id=str(payload.get("session_id") or ""),
        agent_id=str(payload.get("agent_id") or ""),
        agent_name=str(payload.get("agent_type") or ""),
        tool_name=str(payload.get("tool_name") or ""),
        tool_call_id=str(payload.get("tool_use_id") or ""),
        tool_call_arguments=arguments if isinstance(arguments, Mapping) else {},
        tool_call_result=str(payload.get("tool_result") or "")[:RESULT_CEILING],
        prompt_id=str(payload.get("prompt_id") or ""),
        project_dir=str(payload.get("cwd") or ""),
        record_ref=_record_ref(payload),
        occurred_at=datetime.now(UTC),
        source_kind=SourceKind.LIVE,
    )
    if not event.is_routable:
        counters.bump("capture_payload_malformed")
        return None
    return event


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
        dedup_key=action.key.dedup_key(event, position),
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


def capture(payload: Mapping[str, Any], connection: sqlite3.Connection) -> tuple[EpisodicStep, ...]:
    """Record the action *payload* describes; the steps that landed, in order.

    Nothing landing is an ordinary answer, not an error: a payload that names no
    routable action — excluded, not a ``PostToolUse``, malformed — is already
    counted by :func:`adapt_post_tool_use`, and a step whose dedup key was
    already there is counted by the store (FR-008).

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
    event = adapt_post_tool_use(payload, store)
    if event is None:
        return ()
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


#: What a verb answers the harness with, or ``None`` for nothing at all: the
#: contract's "empty output is the normal case".
Response = Mapping[str, Any] | None

#: One verb: the payload of its hook event in, its response out.
Verb = Callable[[Mapping[str, Any]], Response]


def read_payload(stream: TextIO) -> Mapping[str, Any] | None:
    """The hook event on *stream*, or ``None`` when it carries no whole one.

    The harness writes one JSON object and closes stdin. Anything else — an
    empty stream, a truncated write, a bare list — is not an event this process
    can answer, and answering nothing is the whole of the recovery: a hook that
    raises surfaces against the developer's own action (R16).
    """
    try:
        payload = json.loads(stream.read() or "null")
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        return payload
    _logger.warning("hook read no JSON object on stdin; nothing captured")
    return None


def emit(response: Mapping[str, Any], stream: TextIO) -> None:
    """Write *response* to *stream* as the one JSON object the harness reads."""
    json.dump(response, stream)
    stream.write("\n")


def bootstrap(payload: Mapping[str, Any]) -> Response:
    """``SessionStart``: prepare the plugin's interpreter, once (T067)."""
    return None


def prompt(payload: Mapping[str, Any]) -> Response:
    """``UserPromptSubmit``: open the sequence and serve what starts it (T048)."""
    return None


def record(payload: Mapping[str, Any]) -> Response:
    """``PostToolUse``: capture the completed action (T029).

    One index opened per invocation (R10), and closed here whatever happened.
    A store that cannot even be opened — read-only, unwritable directory — is
    the same ordinary failure :func:`capture` answers for an open one: nothing
    is captured, ``capture_store_busy`` is counted through the R16 fallback log
    (no store exists yet to count it any other way), and the payload is
    untouched (FR-014, SC-002). The serving half of the contract's ``record``
    — guidance for the action *after* this one — lands with the guidance
    layer; until then the verb answers nothing, which the contract already
    calls the normal case.
    """
    try:
        connection = open_index()
    except sqlite3.Error as exc:
        log_fallback(home_dir() / "log" / "hooks.jsonl", "capture_store_busy", exc)
        return None
    with closing(connection):
        capture(payload, connection)
    return None


def enforce(payload: Mapping[str, Any]) -> Response:
    """``PreToolUse``: deny an action a pitfall matches, when opted in (T050)."""
    return None


def close(payload: Mapping[str, Any]) -> Response:
    """``Stop`` and ``SubagentStop``: close the sequence and score it (T049)."""
    return None


def end(payload: Mapping[str, Any]) -> Response:
    """``SessionEnd``: spawn the detached session-end job and return (T074)."""
    return None


#: The six verbs of ``contracts/agent-hooks.md``, the only names ``hooks.json``
#: may invoke. Each is silent until its own task fills it in; the framing and
#: the exit code are what land here (T024).
VERBS: dict[str, Verb] = {
    "bootstrap": bootstrap,
    "prompt": prompt,
    "record": record,
    "enforce": enforce,
    "close": close,
    "end": end,
}
