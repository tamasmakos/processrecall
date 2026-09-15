"""Shared builders for `cli/` tests: recording turns and reading what a run wrote."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from processrecall.config import ActivityClass, ProcessType
from processrecall.graph.episodic import open_index
from processrecall.graph.store import EpisodicStep, Sequence, SequenceKey, SQLiteEpisodicStore
from processrecall.trajectory.paths import project_key

#: When a fixture turn was carried out by default, fixed so a rebuild of it is comparable.
STARTED_AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def record_turn(
    home: Path,
    project: Path,
    *node_keys: str,
    prompt_id: str = "p1",
    started_at: datetime = STARTED_AT,
) -> None:
    """Record one closed turn under *home*, whose steps are *node_keys* in order."""
    connection = open_index(home / ".processrecall" / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id=prompt_id)
    store.open_sequence(
        Sequence(
            key=key,
            project_dir_key=project_key(str(project)),
            started_at=started_at,
            process_type=ProcessType.BUG_FIX,
            status="closed",
        )
    )
    for position, node_key in enumerate(node_keys):
        activity_class, program = node_key.split("/")
        store.record(
            EpisodicStep(
                dedup_key=f"{prompt_id}-{position}",
                sequence_key=key,
                position=position,
                node_key=node_key,
                activity_class=ActivityClass(activity_class),
                program=program,
                template=f"{program} <File>",
                occurred_at=started_at + timedelta(seconds=position),
                files=("src/app.py",),
                outcome="success",
            )
        )
    connection.close()


def run_cli(home: Path, *arguments: str, answer: str = "") -> subprocess.CompletedProcess[str]:
    """What `python -m processrecall.cli` did for the store under *home*."""
    return subprocess.run(
        [sys.executable, "-m", "processrecall.cli", *arguments],
        capture_output=True,
        text=True,
        input=answer,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
    )


def snapshot_document(path: Path) -> dict[str, Any]:
    """The snapshot written at *path*."""
    return dict(json.loads(path.read_text(encoding="utf-8")))
