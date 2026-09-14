"""`end`: the detached session-end job, spawned behind a lock (FR-050, T074).

Driven in process rather than through the real hook, unlike the other verb
tests here, because what is asserted is the child this verb leaves behind: the
recorder replacing `subprocess.Popen` has to live in the process running the
verb. The home directory is the test's own, so the lock is too.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from processrecall.config import STORE_DIR
from processrecall.integrations.claude_code import hooks

pytestmark = pytest.mark.unit

PAYLOAD = {
    "hook_event_name": "SessionEnd",
    "session_id": "sess-demo",
    "cwd": "/work/demo",
    "reason": "clear",
}


class _Spawns(list[tuple[Sequence[str], dict[str, Any]]]):
    """Every child a verb asked for, in place of starting one."""

    def __call__(self, command: Sequence[str], **options: Any) -> None:
        self.append((command, options))


@pytest.fixture
def spawns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Spawns:
    """The children `end` starts, over a home directory of this test's own."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    recorder = _Spawns()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    return recorder


def test_the_job_runs_detached_and_the_verb_answers_nothing(spawns: _Spawns) -> None:
    """FR-050: the enrichment the session earned is a child, not a wait."""
    assert hooks.end(PAYLOAD) is None

    assert len(spawns) == 1, spawns
    command, options = spawns[0]
    assert list(command)[:4] == [sys.executable, "-m", "processrecall.cli", "rebuild"]
    assert PAYLOAD["cwd"] in command
    assert options["start_new_session"] is True
    assert options["stdout"] is subprocess.DEVNULL


def test_a_job_from_a_previous_session_is_left_alone(spawns: _Spawns, tmp_path: Path) -> None:
    """FR-050: the lock a running job holds is the whole of what stops a second."""
    lock = tmp_path / STORE_DIR / hooks.SESSION_END_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()

    assert hooks.end(PAYLOAD) is None

    assert spawns == [], "a job was spawned on top of one still running"


def test_a_lock_no_job_is_behind_is_taken_over(spawns: _Spawns, tmp_path: Path) -> None:
    """A job that died leaves its lock behind; nothing else ever removes one.

    Without this the first session to crash mid-job would silence enrichment
    for good, which is a worse failure than running it twice.
    """
    lock = tmp_path / STORE_DIR / hooks.SESSION_END_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    abandoned = (datetime.now(UTC) - hooks.SESSION_END_MAX_RUNTIME).timestamp() - 1
    os.utime(lock, (abandoned, abandoned))

    assert hooks.end(PAYLOAD) is None

    assert len(spawns) == 1, spawns


def test_a_session_naming_no_project_spawns_nothing(spawns: _Spawns, tmp_path: Path) -> None:
    """The job is per project (FR-053): with no `cwd`, there is no project to fold.

    Only `session_id` is required of this event (`contracts/agent-hooks.md`),
    and a job left to default the project would fold whichever directory the
    hook happened to be run from.
    """
    assert hooks.end({"hook_event_name": "SessionEnd", "session_id": "sess-demo"}) is None

    assert spawns == []
    assert not (tmp_path / STORE_DIR / hooks.SESSION_END_LOCK).exists(), "a lock nothing holds"
