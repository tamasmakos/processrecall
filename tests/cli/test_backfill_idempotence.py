"""Backfilling a session already captured live leaves it recorded once (SC-003).

The live hook and the backfill reader are two sources for one session, and the
dedup key is the harness's own tool-call id in both — so replaying a captured
session must add no step at all, and must say so: every record it passes over
is counted as ``steps_duplicate`` rather than passing silently (FR-008, R3).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from processrecall.graph.episodic import open_index
from processrecall.graph.store import SQLiteEpisodicStore
from processrecall.integrations.claude_code.hooks import capture

from .test_backfill import (
    ONE_PASS,
    PROJECT,
    SESSION,
    place_transcript,
    recorded_node_keys,
    run_backfill,
)


def _session_records() -> Iterator[Mapping[str, Any]]:
    """Every record of the fixture session a reader can make JSON of."""
    for line in SESSION.read_text(encoding="utf-8").splitlines():
        with suppress(json.JSONDecodeError):
            yield json.loads(line)


def _result_text(block: Mapping[str, Any]) -> str:
    """A tool result's text, written by the harness as a string or as blocks.

    Walked independently of :func:`processrecall.trajectory.transcript` on
    purpose: reusing it would bypass the hook seam this test exercises.
    """
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content)
    return ""


def _live_payloads() -> Iterator[dict[str, Any]]:
    """The fixture session's completed actions, as the hook received them live."""
    opened: dict[str, dict[str, Any]] = {}
    for record in _session_records():
        content = record.get("message", {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                opened[str(block.get("id"))] = {
                    "hook_event_name": "PostToolUse",
                    "session_id": str(record.get("sessionId")),
                    "prompt_id": str(record.get("parentUuid")),
                    "cwd": str(record.get("cwd")),
                    "tool_use_id": str(block.get("id")),
                    "tool_name": str(block.get("name")),
                    "tool_input": block.get("input"),
                }
            elif (payload := opened.get(str(block.get("tool_use_id")))) is not None:
                yield {**payload, "tool_result": _result_text(block)}


def _capture_live(home: Path) -> int:
    """Record the fixture session through the live hook; how many steps it was."""
    connection = open_index(home / ".processrecall" / "episodes.db")
    try:
        return sum(len(capture(payload, connection)) for payload in _live_payloads())
    finally:
        connection.close()


def _duplicates(home: Path) -> int:
    """How many writes the store has turned away as already recorded."""
    connection = open_index(home / ".processrecall" / "episodes.db")
    try:
        return SQLiteEpisodicStore(connection).counters().get("steps_duplicate", 0)
    finally:
        connection.close()


def test_backfilling_a_session_captured_live_records_it_once(tmp_path: Path) -> None:
    """SC-003: live plus backfill over one session is the step count of one pass."""
    home = tmp_path / "home"
    place_transcript(home, PROJECT)
    steps = _capture_live(home)
    before = _duplicates(home)

    finished = run_backfill(home, PROJECT)

    assert finished.returncode == 0, finished.stderr
    assert "steps=0" in finished.stdout
    assert steps == len(ONE_PASS)
    assert recorded_node_keys(home) == ONE_PASS
    assert _duplicates(home) - before == steps
