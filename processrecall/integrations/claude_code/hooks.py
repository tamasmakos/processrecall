"""The Claude Code hooks: a hook payload in, the one canonical event out (FR-001).

This is the whole of what welds the system to Claude Code: the six verbs of
``contracts/agent-hooks.md``, and the framing that reads one JSON object from
the harness and writes at most one back. Everything downstream
reads a :class:`~processrecall.trajectory.event.TrajectoryEvent` and cannot tell
which harness produced it, so the harness's spellings — ``session_id``,
``tool_use_id``, ``tool_input`` — stop here, at the mapping table of
``contracts/trajectory-event.md``.

Two rules of that contract live in this module rather than downstream. The
result is pre-cut to :data:`RESULT_CEILING` characters *here* (FR-010) so the
untruncated text is never held in memory longer than it has to be; the store
itself enforces the same ceiling at the write seam, for every other source
that writes through it. And a payload missing a field that places the action
in a sequence is counted and dropped, never raised — a hook that raises
surfaces against the developer's own action (R16).

On the hot path, so the standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, TextIO

from processrecall.config import STORE_DIR, Config, Counters, ProcessType, home_dir, load_config
from processrecall.exceptions import PackError
from processrecall.graph.abstract import (
    START_KEY,
    Pitfall,
    PitfallKind,
    TransitionEdge,
    edges_from,
)
from processrecall.graph.episodic import SequenceIdentity, open_index
from processrecall.graph.record import record_event
from processrecall.graph.snapshot import SNAPSHOT_NAME, SnapshotFile
from processrecall.graph.store import (
    RESULT_CEILING,
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
    log_fallback,
)
from processrecall.guidance.fusion import Fusion
from processrecall.guidance.locate import locate
from processrecall.guidance.neighborhood import Neighborhood
from processrecall.guidance.render import BulletRenderer, Deadline, GuidanceStatement
from processrecall.guidance.triggers import Triggers
from processrecall.procedures.step import steps_from
from processrecall.procedures.taxonomy import identify_procedure
from processrecall.trajectory.event import SourceKind, TrajectoryEvent
from processrecall.trajectory.paths import lexical_path, project_key
from processrecall.trajectory.vocabulary import load_vocabulary

_logger = logging.getLogger("processrecall")

#: The project-level opt-out: its presence under the project directory is the
#: whole signal, contents ignored (R13).
OPTOUT_MARKER = Path(STORE_DIR) / "optout"

#: The home-level deny list, under :func:`~processrecall.config.home_dir`: one
#: ``fnmatch`` pattern per line. It lives in the home directory because the
#: repository one may not want to add a marker file to is precisely the
#: sensitive one (R13).
DENY_LIST = "deny.txt"

#: What a prompt is taken to be for. The process-type classifier of FR-059 is
#: optional enrichment whose absence may neither raise nor guess (FR-060), so
#: the condition the sequence's ``Start`` is stored under — and the one its
#: successors are read for — is the deterministic default until it ships.
PROMPT_PROCESS_TYPE = ProcessType.UNKNOWN


def _project_dir(payload: Mapping[str, Any]) -> str:
    """The ``cwd`` *payload* names, or ``""`` where it names none."""
    return str(payload.get("cwd") or "")


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
    """The completed action *payload* describes; see :func:`_adapt` for ``None``."""
    return _adapt(payload, counters, hook_event="PostToolUse")


def adapt_pre_tool_use(payload: Mapping[str, Any], counters: Counters) -> TrajectoryEvent | None:
    """The action *payload* is about to take, adapted by the one mapping table.

    The same event the completed action would produce, minus the result it does
    not have yet: what :func:`enforce` needs of a pre-action payload is the
    procedure it would land on, and that is derived from the tool and its
    arguments alone. Nothing is recorded from it.
    """
    return _adapt(payload, counters, hook_event="PreToolUse")


def _adapt(
    payload: Mapping[str, Any], counters: Counters, *, hook_event: str
) -> TrajectoryEvent | None:
    """The event *payload* describes, or ``None`` when it describes no routable one.

    ``None`` covers three cases, neither raised into the developer's action:
    ``cwd`` is excluded (FR-058, R13), checked before anything else in the
    payload is read; a payload for a hook event other than *hook_event* is not
    this adapter's to read and is ignored outright, uncounted; a payload with
    no ``session_id``, ``prompt_id`` or ``tool_name`` cannot be placed on a
    sequence, so it increments ``capture_payload_malformed`` and the caller
    moves on.
    """
    if is_excluded(_project_dir(payload), counters):
        return None
    if payload.get("hook_event_name") != hook_event:
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
        # Cut here too, not just in the store: so the untruncated result is
        # never held in this process longer than it takes to slice it (FR-010).
        tool_call_result=str(payload.get("tool_result") or "")[:RESULT_CEILING],
        prompt_id=str(payload.get("prompt_id") or ""),
        project_dir=_project_dir(payload),
        record_ref=_record_ref(payload),
        occurred_at=datetime.now(UTC),
        source_kind=SourceKind.LIVE,
    )
    if not event.is_routable:
        counters.bump("capture_payload_malformed")
        return None
    return event


def capture(payload: Mapping[str, Any], connection: sqlite3.Connection) -> tuple[EpisodicStep, ...]:
    """Record the action *payload* describes; the steps that landed, in order.

    Nothing landing is an ordinary answer, not an error: a payload that names no
    routable action — excluded, not a ``PostToolUse``, malformed — is already
    counted by :func:`adapt_post_tool_use`, and everything past the adapter is
    :func:`~processrecall.graph.record.record_event`'s to answer for.
    """
    store = SQLiteEpisodicStore(connection)
    event = adapt_post_tool_use(payload, store)
    return () if event is None else record_event(event, connection)


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
    """``SessionStart``: unreachable by design (FR-068, R14).

    R14 has ``hooks.json`` run ``bin/bootstrap.sh`` directly — a POSIX shell
    is the one thing a freshly installed plugin is sure to have, and this
    interpreter does not exist yet on the session the environment still needs
    preparing. This entry stays in :data:`VERBS` for the contract's six
    names, not because anything still calls it.
    """
    return None


@dataclass(frozen=True, slots=True)
class _Opening:
    """What serving the start of one prompt takes: where it is, and on what budget.

    Attributes:
        project_dir: The project the prompt was submitted in, whose snapshot is
            consulted before the cross-project one (FR-048).
        counters: Where every suppression, fallback and render is counted (R16).
        config: The tuning the support floor is read from.
        deadline: The soft budget, started when the hook was entered (R9).
    """

    project_dir: str
    counters: Counters
    config: Config
    deadline: Deadline


def open_prompt(
    payload: Mapping[str, Any], connection: sqlite3.Connection, deadline: Deadline | None = None
) -> str:
    """Open the turn *payload* submits and serve what opens it; ``""`` for silence.

    The three steps of ``contracts/agent-hooks.md``, in its order. The
    exclusion check comes first and on ``cwd`` alone, so an excluded project's
    prompt is answered without the payload being read at all (R13); the turn is
    then recorded with its process type, which is the condition its successors
    are read for (FR-047).

    *deadline* is the caller's, when it has one: :func:`prompt` starts it at
    hook entry, ahead of the index open and the config load, so the budget
    charges for both (R9). A caller with no budget of its own, such as a unit
    test driving this directly, gets one struck here instead.
    """
    store = SQLiteEpisodicStore(connection)
    project_dir = _project_dir(payload)
    if is_excluded(project_dir, store):
        return ""
    opening = _Opening(
        project_dir=project_dir,
        counters=store,
        config=load_config(),
        deadline=deadline or Deadline(),
    )
    key = SequenceIdentity(connection, str(payload.get("session_id") or "")).key(
        str(payload.get("prompt_id") or ""), str(payload.get("agent_id") or "")
    )
    _open_turn(store, opening.project_dir, key)
    served = _opening_guidance(opening)
    store.bump("guidance_served" if served else "guidance_silent")
    return served


def _open_turn(store: SQLiteEpisodicStore, project_dir: str, key: SequenceKey) -> None:
    """Record *key* as an open sequence in *project_dir*, its process type on it.

    A store that will not take the write is counted rather than raised, as
    every other write on this path is: a prompt must not fail because the
    memory watching it could not note the turn (FR-014).
    """
    try:
        store.open_sequence(
            Sequence(
                key=key,
                project_dir_key=project_key(project_dir),
                started_at=datetime.now(UTC),
                process_type=PROMPT_PROCESS_TYPE,
            )
        )
    except sqlite3.Error:
        store.bump("capture_store_busy")


def _opening_guidance(opening: _Opening) -> str:
    """What usually opens a prompt in *opening*'s project, as the text to serve.

    The prompt has carried out no step yet, so there is nothing to locate on
    and the position is the start node itself: its successors are the whole of
    what FR-047 serves here, and the trigger seam is what decides whether they
    carry evidence enough to be said at all (FR-045a).
    """
    firing = Triggers(opening.config, opening.counters).fire(
        (), Neighborhood(center=START_KEY, edges=_fused_openings(opening))
    )
    if firing is None:
        return ""
    return BulletRenderer(opening.counters, opening.deadline).render(
        [statement for edge in firing.edges for statement in _statements(edge)]
    )


def _fused_openings(opening: _Opening) -> tuple[TransitionEdge, ...]:
    """The moves that open a prompt, this project's ahead of every other's (FR-048)."""
    fused = Fusion(opening.counters).fuse(
        _moves_from(_project_snapshot(opening.project_dir), START_KEY, opening.counters),
        _moves_from(home_dir() / SNAPSHOT_NAME, START_KEY, opening.counters),
    )
    return tuple(candidate.edge for candidate in fused.edges)


def _project_snapshot(project_dir: str) -> Path:
    """Where *project_dir* keeps its own abstract graph (FR-053)."""
    return Path(project_dir) / STORE_DIR / SNAPSHOT_NAME


def _moves_from(
    path: Path,
    source: str,
    counters: Counters,
    *,
    process_type: ProcessType | None = PROMPT_PROCESS_TYPE,
) -> tuple[TransitionEdge, ...]:
    """The successors of *source* in the snapshot at *path*, for *process_type*.

    ``None`` means any process type — the pre-action lookup :func:`_matching_pitfall`
    makes has no prompt-start condition to restrict to, unlike the opening moves
    :func:`_fused_openings` serves, which is what the default keeps serving.

    A snapshot that is missing or unreadable yields no move rather than a
    fault: the reader has already counted it (R11), and a project whose graph
    has never been built is the ordinary first case rather than an error. The
    format stamp only guards the document's outer shape, so `edges_from` gets
    the same guard `SnapshotFile.read` gives the rest of the document — an
    edge body it cannot parse is the same kind of unreadable snapshot, not a
    fault inside a hook.
    """
    snapshot = SnapshotFile(path, counters).read()
    if snapshot is None:
        return ()
    try:
        edges = edges_from(snapshot)
    except (KeyError, TypeError, ValueError):
        counters.bump("snapshot_unreadable")
        return ()
    return tuple(
        edge
        for edge in edges
        if edge.source == source
        and (process_type is None or edge.condition.process_type is process_type)
    )


def _statement(edge: TransitionEdge) -> GuidanceStatement:
    """One opening move as the claim it is served as, and the episodes behind it."""
    return GuidanceStatement(
        text=f"a prompt like this usually starts with {edge.target}",
        support=edge.support,
    )


def _statements(edge: TransitionEdge) -> tuple[GuidanceStatement, ...]:
    """*edge*'s statistical claim, and any note an agent attached to it (FR-039).

    The note's support is the move's, not its own: `edge.annotations` carries
    no count of its own to render.
    """
    return (
        _statement(edge),
        *(
            GuidanceStatement.from_annotation(annotation, edge.support)
            for annotation in edge.annotations
        ),
    )


def prompt(payload: Mapping[str, Any]) -> Response:
    """``UserPromptSubmit``: open the sequence and serve what starts it (FR-047).

    The deadline is struck first, before the index is even opened, so the
    soft budget of R9 charges for the open and the config load along with the
    render (see :class:`_Opening`). One index opened per invocation and
    closed here whatever happened (R10), as in :func:`record`. A store that
    cannot be opened at all leaves the turn unopened and the prompt
    unanswered: it is counted through the R16 fallback log, there being no
    store to count it any other way, and the developer's prompt goes on
    untouched (FR-014).
    """
    deadline = Deadline()
    try:
        connection = open_index()
    except sqlite3.Error as exc:
        log_fallback(home_dir() / "log" / "hooks.jsonl", "capture_store_busy", exc)
        return None
    with closing(connection):
        served = open_prompt(payload, connection, deadline)
    return {"additionalContext": served} if served else None


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


@dataclass(frozen=True, slots=True)
class _Move:
    """The move a pre-action payload is about to make, and where to look it up.

    Attributes:
        source: The node the agent stands on — its previous step's identity.
        target: The node the action about to be taken would land on.
        project_dir: The project it is about to be taken in, whose graph is
            consulted before the cross-project one (FR-048).
    """

    source: str
    target: str
    project_dir: str


def deny_reason(payload: Mapping[str, Any], connection: sqlite3.Connection, config: Config) -> str:
    """Why the action *payload* is about to take is refused; ``""`` to allow it.

    The move about to be made is looked up in the graph, and only what it is
    known to *fail* as is a reason to refuse it: the claim is the pitfall's
    own evidence and count (FR-044), never prose a model wrote.

    A locked index or a pack that fails to load is the ordinary failure
    :func:`capture` answers for its own writes: the lookup is dropped and
    counted as ``capture_store_busy`` rather than raised, because a check
    that cannot be made must not itself refuse the action (FR-014).
    """
    store = SQLiteEpisodicStore(connection)
    event = adapt_pre_tool_use(payload, store)
    if event is None:
        return ""
    try:
        key = SequenceIdentity(connection, event.conversation_id).key(
            event.prompt_id, event.agent_id
        )
        move = _Move(
            source=locate(store.steps(key), config.level).key,
            target=_intended_key(event, store, level=config.level),
            project_dir=event.project_dir,
        )
        pitfall = _matching_pitfall(move, store)
    except (sqlite3.Error, PackError):
        store.bump("capture_store_busy")
        return ""
    if pitfall is None:
        return ""
    return BulletRenderer(store, Deadline()).render([_refusal(move, pitfall)])


def _intended_key(event: TrajectoryEvent, counters: Counters, *, level: str) -> str:
    """The node the action *event* names would land on, spelled at *level*.

    An action decomposing into several sub-activities (FR-017) is judged on the
    first of them: that is the one about to be taken, and the only one that
    still happens if this action is refused.
    """
    activities = steps_from(event, load_vocabulary().activity_for(event.tool_name, counters.bump))
    return identify_procedure(activities[0]).key_at(level)


def _matching_pitfall(move: _Move, counters: Counters) -> Pitfall | None:
    """What the graph knows *move* fails as, this project's graph first.

    The cross-project snapshot is read only for a move this project has no
    pitfall for, which is FR-048's fallback spelled for a lookup rather than
    for a ranking.
    """
    for path in (_project_snapshot(move.project_dir), home_dir() / SNAPSHOT_NAME):
        for edge in _moves_from(path, move.source, counters, process_type=None):
            if edge.target == move.target and (pitfall := _failure_of(edge)) is not None:
                return pitfall
    return None


def _failure_of(edge: TransitionEdge) -> Pitfall | None:
    """What *edge* is known to fail as, or ``None`` where it is known no such thing.

    A repetition loop is not one: it counts repetitions rather than failures
    and makes no claim about how they went (FR-031), so it is something to warn
    about one step early, not something to refuse an action over.
    """
    failures = (pitfall for pitfall in edge.pitfalls if pitfall.kind is PitfallKind.FAILURE_PRONE)
    return next(failures, None)


def _refusal(move: _Move, pitfall: Pitfall) -> GuidanceStatement:
    """The refused move as the claim it is refused with, and its evidence."""
    return GuidanceStatement(
        text=f"{pitfall.evidence} is known to fail after {move.source}",
        support=pitfall.support,
    )


def enforce(payload: Mapping[str, Any]) -> Response:
    """``PreToolUse``: deny an action a pitfall matches, when opted in (T050).

    Off unless opted into, and the check comes before anything else: with
    ``Config.enforce`` at its shipped default this verb reads no index, no
    snapshot and no payload, which is what "disabled by default" has to mean on
    a hook the harness runs before every action (FR-049).

    One index opened per invocation and closed here whatever happened (R10), as
    in :func:`record`. A store that cannot be opened at all allows the action:
    a memory that cannot be consulted is not a reason to stop the developer
    (FR-014).
    """
    config = load_config()
    if not config.enforce:
        return None
    try:
        connection = open_index()
    except sqlite3.Error as exc:
        log_fallback(home_dir() / "log" / "hooks.jsonl", "capture_store_busy", exc)
        return None
    with closing(connection):
        reason = deny_reason(payload, connection, config)
    return _denial(reason) if reason else None


def _denial(reason: str) -> Mapping[str, Any]:
    """*reason* as the one decision ``PreToolUse`` accepts.

    A deny and its reason, and nothing else: the event carries no
    ``additionalContext``, which is why a pitfall is *warned* about one step
    earlier, by ``record`` (FR-046).
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


#: The one sentence that reaches for ``skills/remember/SKILL.md``. It is served
#: from :func:`close` rather than from :func:`record` because a nudge the agent
#: meets after every action buys notes written out of habit; one at the end of
#: the work is asked of an agent that has something to say (FR-040).
REMEMBER_NUDGE = (
    "This piece of work has ended. If a move went a way the counts alone will not "
    "explain to the next run, call `remember` once with that move's edge key — the "
    "`remember` skill says what is worth a note and what is not."
)


def close(payload: Mapping[str, Any]) -> Response:
    """``Stop`` and ``SubagentStop``: nudge for a note on the work just ended (T060).

    The harness runs this verb once per sequence, which is what "at end of work
    rather than on every step" means for a nudge — so it is emitted here, on
    every sequence but one an excluded project ran (FR-058, checked first as in
    :func:`open_prompt`): an agent whose project opted out is told nothing,
    matching the "suppress capture entirely" of R13. Short of that, the nudge
    is unconditional — an agent that has nothing to say declines, and the store
    holds no signal for whether it does. Closing the sequence and scoring it,
    the rest of the verb's contract, lands with T049.
    """
    try:
        connection = open_index()
    except sqlite3.Error as exc:
        log_fallback(home_dir() / "log" / "hooks.jsonl", "capture_store_busy", exc)
        return None
    with closing(connection):
        if is_excluded(_project_dir(payload), SQLiteEpisodicStore(connection)):
            return None
    return {"additionalContext": REMEMBER_NUDGE}


#: Where the detached session-end job takes its lock, under the home store
#: directory: one job per machine rather than one per project, because the job
#: rewrites the cross-project snapshot as well as the project's own and two of
#: them would race on that one file.
SESSION_END_LOCK = "session-end.lock"

#: How long an unreleased lock means "a job is still running". The job itself
#: removes the lock when it finishes (`--release-lock`), so this is a
#: crash-recovery backstop rather than the normal throttle: only a job that
#: died mid-run leaves a lock for this to judge, and it is longer than
#: re-deriving the whole index takes, so a job that really is running is
#: never doubled.
SESSION_END_MAX_RUNTIME = timedelta(hours=1)


def _take_session_end_lock(path: Path) -> bool:
    """Whether this session may run the job, taking the lock at *path* when it may.

    A lock a job is still running behind is left alone, and so is this session's
    job (``contracts/agent-hooks.md``). Exclusive creation is the claim itself,
    so two sessions ending at once cannot both spawn one; losing that race, like
    an unwritable home directory, is an ``OSError`` and means the same thing
    here — this session does not run the job.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if _job_is_running(path):
            return False
        path.unlink(missing_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_EXCL, 0o600))
    except OSError as exc:
        _logger.debug("the session-end lock %s was not taken: %s", path, exc)
        return False
    return True


def _job_is_running(path: Path) -> bool:
    """Whether the lock at *path* is one a job of a previous session still holds."""
    try:
        taken = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except FileNotFoundError:
        return False
    return datetime.now(UTC) - taken < SESSION_END_MAX_RUNTIME


def _spawn_session_end(project_dir: str, lock: Path) -> None:
    """Start the enrichment job for *project_dir*, detached from this process.

    ``processrecall rebuild`` is the job: it re-derives both graphs from the
    whole episodic index, which is where classification (FR-059) and symbol
    attribution (FR-063) get to run at all, since neither may run on the hot
    path (FR-064). Detached, with none of this process's streams held open, so
    the child outlives the hook and the harness's timeout never reaches it.

    *lock* is passed as ``--release-lock`` so the job frees it on the way out:
    the next session's job is then blocked on nothing but the run itself,
    rather than on `SESSION_END_MAX_RUNTIME`.

    A child that cannot be started is logged rather than raised, as every other
    failure on a hook path is: enrichment is the optional layer, and its absence
    is what the rules-only graph already stands without (FR-060).
    """
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "processrecall.cli",
                "rebuild",
                "--project",
                project_dir,
                "--release-lock",
                str(lock),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        _logger.warning("the session-end job could not be started: %s", exc)


def end(payload: Mapping[str, Any]) -> Response:
    """``SessionEnd``: spawn the detached session-end job and return (T074).

    The whole of the verb: whatever the job costs, the developer has already
    left this session and the next one waits on nothing (FR-050). What it costs
    is why it is a job at all — classification and code parsing are exactly the
    work FR-064 keeps off the hot path.

    Three things stop it. A session naming no project has nothing per-project
    to fold, and the payload of this event is not required to name one. An
    excluded project (FR-058, checked first as in :func:`close`) gets no job,
    since one would write under its `.processrecall/` regardless of what it
    folds. And a job a previous session left running holds the lock, in which
    case this session leaves it alone rather than starting a second: the job
    releases the lock itself on the way out (`--release-lock`), so the next
    session to end is blocked on the run, not on `SESSION_END_MAX_RUNTIME`.
    """
    project_dir = _project_dir(payload)
    if not project_dir or _is_excluded_project(project_dir):
        return None
    if _take_session_end_lock(home_dir() / SESSION_END_LOCK):
        _spawn_session_end(project_dir, home_dir() / SESSION_END_LOCK)
    return None


def _is_excluded_project(project_dir: str) -> bool:
    """Whether *project_dir* opted out, counting it as in :func:`close`.

    A store that cannot be opened is not treated as excluded: an unwritable
    home directory already stops the job at the lock, and a project should
    not be read as opted out because of that.
    """
    try:
        connection = open_index()
    except sqlite3.Error as exc:
        log_fallback(home_dir() / "log" / "hooks.jsonl", "capture_store_busy", exc)
        return False
    with closing(connection):
        return is_excluded(project_dir, SQLiteEpisodicStore(connection))


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
