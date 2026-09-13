"""Shared fixtures for the Claude Code integration tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index

ROOT = Path(__file__).resolve().parents[3]
PAYLOADS = ROOT / "tests" / "fixtures" / "payloads"


@pytest.fixture
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """An episodic index of this test's own, under a home directory of its own."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return open_index(tmp_path / "episodes.db")
