"""Hook capture as telemetry's fallback: the label that makes it yield (FR-007).

Telemetry is the primary source for a step's readings, and the hook keeps the
two things no telemetry record carries — the project a session belongs to and
the epoch it is on (R4, R5). What makes that division operative is the capture
source the hook writes: it is the only thing that tells the store a row is the
fallback's, so a telemetry record arriving later folds into it and wins every
field both sources read. An unlabelled row is indistinguishable and never
yields.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from processrecall.graph.schema import CaptureSource
from processrecall.graph.store import EpisodicStep, Sequence, SequenceKey, SQLiteEpisodicStore
from processrecall.integrations.claude_code.hooks import capture, open_prompt
from processrecall.trajectory.paths import project_key

from .conftest import PAYLOADS

pytestmark = pytest.mark.unit

#: The sequence the synthetic corpus's prompt opens: the main agent, at the
#: epoch nothing has rotated.
DEMO = SequenceKey("sess-demo", 0, "prompt-1", "")

#: Where that prompt was submitted, and so what the session is bound to.
DEMO_PROJECT = "/work/demo"

#: The step fields ``contracts/telemetry-records.md`` gives no hook fallback
#: for: telemetry is their only source, so a hook capture leaves them unset
#: rather than guessing at them (R14).
TELEMETRY_ONLY_STEP_FIELDS = (
    "decision",
    "decision_source",
    "duration_ms",
    "error_type",
    "input_size_bytes",
    "result_size_bytes",
    "tool_source",
)

#: The same, for the sequence a ``user_prompt`` record opens.
TELEMETRY_ONLY_SEQUENCE_FIELDS = (
    "prompt_length",
    "command_name",
    "command_source",
    "app_version",
    "head_revision",
    "head_branch",
)


def _session() -> tuple[dict[str, Any], dict[str, Any]]:
    """The synthetic corpus's prompt and the first action taken under it."""
    envelope = json.loads((PAYLOADS / "replayed_duplicate.json").read_text(encoding="utf-8"))
    prompt, action, *_ = envelope["payloads"]
    return prompt, action


def _unsupplied(row: EpisodicStep | Sequence, names: tuple[str, ...]) -> list[str]:
    """Which of *names* *row* left unset, as the failure message's own list."""
    return [name for name in names if getattr(row, name) is None]


def test_hook_supplies_only_fields_telemetry_lacks(index: sqlite3.Connection) -> None:
    """FR-007: the hook binds the session to its project, and labels the rest fallback."""
    prompt, action = _session()
    open_prompt(prompt, index)
    steps = capture(action, index)

    sequence = SQLiteEpisodicStore(index).sequence(DEMO)
    assert sequence is not None
    assert sequence.project_dir_key == project_key(DEMO_PROJECT)
    assert sequence.source is CaptureSource.HOOK
    assert _unsupplied(sequence, TELEMETRY_ONLY_SEQUENCE_FIELDS) == list(
        TELEMETRY_ONLY_SEQUENCE_FIELDS
    )
    assert [step.source for step in steps] == [CaptureSource.HOOK]
    assert _unsupplied(steps[0], TELEMETRY_ONLY_STEP_FIELDS) == list(TELEMETRY_ONLY_STEP_FIELDS)
