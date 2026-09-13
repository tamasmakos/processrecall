"""Backfill: the harness's own past session records, read as events (FR-015).

The same seam as live capture — a :class:`~processrecall.trajectory.protocol.
TrajectorySource` — over a Claude Code session transcript instead of a hook
payload, so the steps it produces are indistinguishable from live-captured ones
and de-duplicate against them on ``tool_call_id``.

A transcript reports an action across two records: the assistant record that
opens the tool call carries its name and arguments, the user record that answers
carries its result. An event is therefore yielded when the *result* arrives,
which is also when the action completed — the moment live capture stamps. A call
whose result never arrives did not complete, and is dropped for the same reason
``PostToolUse`` would never have fired for it.

Sub-agent (sidechain) records replay onto the main sequence: ``agent_id`` and
``agent_name`` are always ``""`` here, so ``isSidechain`` is read nowhere yet.
Giving sidechains their own identity on backfill is deferred rather than done
half-way.

On the hot path, so the standard library only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from processrecall.config import RESULT_CEILING, Counters
from processrecall.trajectory.event import SourceKind, TrajectoryEvent

#: Record types that carry no action. They are not formats the reader failed to
#: read, so they are passed over in silence; a type outside this set and outside
#: the two that do carry actions is a changed format, and is reported.
_ACTIONLESS_TYPES = frozenset({"summary", "system", "file-history-snapshot", "checkpoint"})

#: Where the harness keeps one directory of session transcripts per project,
#: under the user's home.
_PROJECTS_DIRECTORY_NAME = Path(".claude") / "projects"

#: What the harness replaces in a project directory to name that directory:
#: everything that is not alphanumeric, so ``/work/demo`` is ``-work-demo``.
_NOT_IN_A_DIRECTORY_NAME = re.compile(r"[^A-Za-z0-9]")


def transcript_directory(project_dir: Path, home: Path | None = None) -> Path:
    """Where the harness keeps *project_dir*'s session transcripts, under *home*.

    *project_dir* is resolved first: the harness only ever names the directory
    a session actually ran in, so a relative path such as ``.`` must mangle to
    the same name an absolute one would, not to a directory the harness never
    wrote (FR-015). *home* defaults to the caller's own, and is a parameter
    only so a test can point it at a fixture home instead.
    """
    name = _NOT_IN_A_DIRECTORY_NAME.sub("-", str(project_dir.resolve()))
    return (home or Path.home()) / _PROJECTS_DIRECTORY_NAME / name


@dataclass(frozen=True, slots=True)
class SkippedRecord:
    """One record the reader did not understand, and why (FR-016).

    Where it was, not what it said: the ordinal sends a maintainer to the
    line, so an anonymised report of a real session carries none of its
    content.

    Attributes:
        ordinal: The line the record was read from.
        category: The shape of the failure, free of any per-record value —
            what a summary across many records groups by, so a run with many
            orphaned results or unparseable timestamps still reports one row
            per shape rather than one per record.
        reason: The fuller message a maintainer reads instead, which for some
            shapes names the record — a call id, a raw timestamp — that
            *category* deliberately leaves out.
    """

    ordinal: int
    category: str
    reason: str


@dataclass(frozen=True, slots=True)
class _PendingCall:
    """A tool call an assistant record opened, waiting for its result.

    It carries the session fields of the record it was read from rather than
    reading them off the answering one: a resumed transcript may change cwd
    between the call and its result, and the action belongs to the call.
    """

    ordinal: int
    tool_name: str
    arguments: Mapping[str, object]
    conversation_id: str
    project_dir: str
    prompt_id: str


@dataclass(slots=True)
class _Pass:
    """One reading of the transcript, start to finish.

    Iterating a :class:`TranscriptSource` twice must not let the two passes
    corrupt each other's counting, so this — not the source — is what
    ``_read``, ``_skip`` and the rest thread through: the source itself keeps
    only what outlives a single pass.
    """

    ordinal: int = 0
    prompt_id: str = ""
    pending: dict[str, _PendingCall] = field(default_factory=dict)
    skipped: list[SkippedRecord] = field(default_factory=list)


class TranscriptSource:
    """One session transcript, replayed as the actions it recorded.

    Iterating restarts the pass, so a transcript can be read twice: the dedup
    key is a pure function of the record, and the second pass writes nothing.
    """

    def __init__(self, path: Path, counters: Counters) -> None:
        self._path = path
        self._counters = counters
        self._last_pass = _Pass()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path!s})"

    @property
    def skipped(self) -> tuple[SkippedRecord, ...]:
        """What the last pass could not read, in the order it was met."""
        return tuple(self._last_pass.skipped)

    def events(self) -> Iterator[TrajectoryEvent]:
        """Yield one event per completed tool call, oldest first."""
        pass_ = _Pass()
        self._last_pass = pass_
        with self._path.open(encoding="utf-8") as lines:
            for ordinal, line in enumerate(lines, start=1):
                pass_.ordinal = ordinal
                if (record := self._parse(line, pass_)) is not None:
                    yield from self._read(record, pass_)

    def _parse(self, line: str, pass_: _Pass) -> Mapping[str, Any] | None:
        """*line* as one transcript record, reporting a line that is not one."""
        if (record := _parsed_record(line)) is None:
            self._skip("not readable as a JSON record", pass_)
        return record

    def _read(self, record: Mapping[str, Any], pass_: _Pass) -> Iterator[TrajectoryEvent]:
        """The actions *record* completes — none, for a record that opens one."""
        kind = record.get("type")
        if kind == "assistant":
            self._note_calls(record, pass_)
        elif kind == "user":
            yield from self._completions(record, pass_)
        elif kind not in _ACTIONLESS_TYPES:
            self._skip(f"unrecognised record type {kind!r}", pass_)

    def _skip(self, category: str, pass_: _Pass, *, detail: str | None = None) -> None:
        """Pass over the record being read, counted and reported (FR-016).

        *detail* is the fuller message, when *category* alone would run every
        record of one shape together; it defaults to *category* for a shape
        that carries no per-record value to begin with.
        """
        pass_.skipped.append(
            SkippedRecord(ordinal=pass_.ordinal, category=category, reason=detail or category)
        )
        self._counters.bump("backfill_records_skipped")

    def _note_calls(self, record: Mapping[str, Any], pass_: _Pass) -> None:
        """Remember every tool call *record* opens, under its call id."""
        for block in self._blocks(record, pass_):
            if block.get("type") != "tool_use":
                continue
            arguments = block.get("input")
            pass_.pending[str(block.get("id") or "")] = _PendingCall(
                ordinal=pass_.ordinal,
                tool_name=str(block.get("name") or ""),
                arguments=arguments if isinstance(arguments, Mapping) else {},
                conversation_id=str(record.get("sessionId") or ""),
                project_dir=str(record.get("cwd") or ""),
                prompt_id=pass_.prompt_id,
            )

    def _completions(self, record: Mapping[str, Any], pass_: _Pass) -> Iterator[TrajectoryEvent]:
        """The actions *record* answers; a real user turn instead opens a prompt.

        A meta record or a sidechain prompt also carries string content, but
        neither is the turn that follows it belongs to — only a genuine user
        turn re-keys the sequence.
        """
        if isinstance(_content(record), str):
            if not record.get("isMeta") and not record.get("isSidechain"):
                pass_.prompt_id = str(record.get("uuid") or "")
            return
        for block in self._blocks(record, pass_):
            if block.get("type") != "tool_result":
                continue
            if (event := self._completed(block, record, pass_)) is not None:
                yield event

    def _completed(
        self, block: Mapping[str, Any], record: Mapping[str, Any], pass_: _Pass
    ) -> TrajectoryEvent | None:
        """The action *block* reports the result of, as one canonical event."""
        call_id = str(block.get("tool_use_id") or "")
        if (call := pass_.pending.pop(call_id, None)) is None:
            self._skip(
                "result for a tool call that no record opens",
                pass_,
                detail=f"result for tool call {call_id!r} that no record opens",
            )
            return None
        stamp = record.get("timestamp")
        if (occurred_at := _parsed_time(stamp)) is None:
            self._skip(
                "timestamp is not an offset-aware time",
                pass_,
                detail=f"timestamp {stamp!r} is not an offset-aware time",
            )
            return None
        return TrajectoryEvent(
            operation_name="execute_tool",
            conversation_id=call.conversation_id,
            agent_id="",
            agent_name="",
            tool_name=call.tool_name,
            tool_call_id=call_id,
            tool_call_arguments=call.arguments,
            tool_call_result=_result_text(block.get("content"))[:RESULT_CEILING],
            prompt_id=call.prompt_id,
            project_dir=call.project_dir,
            record_ref=f"{self._path}#{call.ordinal}",
            occurred_at=occurred_at,
            source_kind=SourceKind.BACKFILL,
        )

    def _blocks(self, record: Mapping[str, Any], pass_: _Pass) -> tuple[Mapping[str, Any], ...]:
        """The content blocks of *record*, or none when its shape has changed."""
        content = _content(record)
        if not isinstance(content, list):
            self._skip(f"message content is {type(content).__name__}, not a list of blocks", pass_)
            return ()
        return tuple(block for block in content if isinstance(block, Mapping))


def _parsed_record(line: str) -> Mapping[str, Any] | None:
    """*line* as one transcript record, or ``None`` when it is not one."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _parsed_time(stamp: object) -> datetime | None:
    """*stamp* as the offset-aware time it spells, or ``None`` if it spells none.

    A naive time is refused rather than assumed to be UTC: the event rejects it
    anyway, and two machines' sessions order against each other only when every
    timestamp carries its offset.
    """
    try:
        occurred_at = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    return occurred_at if occurred_at.utcoffset() is not None else None


def _content(record: Mapping[str, Any]) -> object:
    """What the message of *record* holds: prompt text, or a list of blocks."""
    message = record.get("message")
    return message.get("content") if isinstance(message, Mapping) else None


def _result_text(content: object) -> str:
    """A tool result as the one string the pipeline reads, uncut.

    The harness spells it either way round — a bare string, or the text blocks
    a content-bearing tool answers with. The caller cuts the result to
    :data:`RESULT_CEILING`, the same boundary the hook adapter cuts at, so a
    backfilled result is truncated exactly where a live one would have been
    (FR-010).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text") or "") for block in content if isinstance(block, Mapping)
        )
    return ""
