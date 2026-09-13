"""Shared fixtures for the Claude Code integration tests."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from processrecall.graph.episodic import open_index

ROOT = Path(__file__).resolve().parents[3]
PAYLOADS = ROOT / "tests" / "fixtures" / "payloads"


def hook(verb: str, payload: Mapping[str, Any], home: Path) -> subprocess.CompletedProcess[str]:
    """Run *verb* the way the harness does, over a home of its own."""
    return subprocess.run(
        [sys.executable, "-m", "processrecall.integrations.claude_code", verb],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        check=False,
    )


@pytest.fixture
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """An episodic index of this test's own, under a home directory of its own."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return open_index(tmp_path / "episodes.db")
