"""The ``record`` verb, end to end: one completed action, one episodic step.

FR-007 asks for a step per completed action; FR-014 and SC-002 ask that a
capture which cannot be written surfaces nowhere near the developer's own
action. So the assertions here come in two kinds: what a healthy store holds
afterwards, and what a faulted one leaves behind — a counter, an untouched
payload and exit 0.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.store import SequenceKey, SQLiteEpisodicStore
from processrecall.integrations.claude_code.hooks import capture

from .conftest import PAYLOADS, hook

pytestmark = pytest.mark.unit

#: The sequence the synthetic corpus's actions belong to: the main agent, at the
#: epoch nothing has rotated.
DEMO = SequenceKey("sess-demo", 0, "prompt-1", "")


def action() -> dict[str, Any]:
    """The synthetic corpus's ``Read`` of ``src/app.py``: one completed action."""
    envelope = json.loads((PAYLOADS / "replayed_duplicate.json").read_text(encoding="utf-8"))
    return next(
        payload for payload in envelope["payloads"] if payload.get("tool_use_id") == "toolu_dup"
    )


def test_one_completed_action_lands_as_one_step_on_its_sequence(
    index: sqlite3.Connection,
) -> None:
    """FR-007: the harness's post-action event is the capture path, whole."""
    recorded = capture(action(), index)

    steps = SQLiteEpisodicStore(index).steps(DEMO)
    assert len(recorded) == 1
    assert [step.dedup_key for step in steps] == ["toolu_dup"]
    assert steps[0].node_key == "Inspection/Read/py"
    assert steps[0].files == ("src/app.py",)


def test_a_store_that_cannot_be_written_drops_the_step_and_leaves_the_action_alone(
    index: sqlite3.Connection, tmp_path: Path
) -> None:
    """FR-014, SC-002: the drop is counted where a maintainer finds it, and nowhere else."""
    payload = action()
    as_delivered = json.dumps(payload, sort_keys=True)
    read_only = sqlite3.connect(f"file:{tmp_path / 'episodes.db'}?mode=ro", uri=True)

    assert capture(payload, read_only) == ()
    assert json.dumps(payload, sort_keys=True) == as_delivered
    assert SQLiteEpisodicStore(index).steps(DEMO) == ()
    fallback = (tmp_path / ".processrecall" / "log" / "hooks.jsonl").read_text(encoding="utf-8")
    assert "capture_store_busy" in fallback


@pytest.mark.parametrize("store_is_a_directory", [False, True])
def test_the_hook_process_records_in_silence_and_exits_zero(
    tmp_path: Path, store_is_a_directory: bool
) -> None:
    """SC-002: the harness sees exit 0 and no output, store healthy or not."""
    if store_is_a_directory:
        (tmp_path / ".processrecall" / "episodes.db").mkdir(parents=True)

    result = hook("record", action(), home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    if store_is_a_directory:
        fallback = (tmp_path / ".processrecall" / "log" / "hooks.jsonl").read_text(encoding="utf-8")
        assert "capture_store_busy" in fallback
    else:
        with closing(open_index(tmp_path / ".processrecall" / "episodes.db")) as index:
            assert [step.dedup_key for step in SQLiteEpisodicStore(index).steps(DEMO)] == [
                "toolu_dup"
            ]
