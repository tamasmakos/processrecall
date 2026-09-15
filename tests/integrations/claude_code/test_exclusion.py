"""Exclusion: checked on ``cwd`` first, and it suppresses capture entirely.

FR-058 and R13. The force of the requirement is in the word *entirely* — no
step, no snippet, no prompt text — which is why the check takes the project
directory alone and is asked before the payload is touched at all.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from processrecall.integrations.claude_code.hooks import adapt_post_tool_use, is_excluded

pytestmark = pytest.mark.unit

#: What the hook writes when it suppresses capture (R13).
EXCLUDED = "capture_excluded"


class FakeCounters:
    """The counter table in memory: what the hook counted, and how often."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory of this test's own, holding no deny list yet."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_a_project_carrying_the_optout_marker_is_excluded(tmp_path: Path, home: Path) -> None:
    """The marker's presence is the signal; R13 reads no contents."""
    project = tmp_path / "demo"
    (project / ".processrecall").mkdir(parents=True)
    (project / ".processrecall" / "optout").touch()
    counters = FakeCounters()

    assert is_excluded(str(project), counters) is True
    assert counters.counted == Counter({EXCLUDED: 1})


def test_an_ordinary_project_is_recorded_and_counts_nothing(tmp_path: Path, home: Path) -> None:
    """FR-058: recording is on by default, with no marker and no deny list."""
    project = tmp_path / "demo"
    project.mkdir()
    counters = FakeCounters()

    assert is_excluded(str(project), counters) is False
    assert not counters.counted


def deny(home: Path, *lines: str) -> None:
    """Write the home-level deny list, one ``fnmatch`` pattern per line."""
    (home / ".processrecall").mkdir(parents=True, exist_ok=True)
    (home / ".processrecall" / "deny.txt").write_text("\n".join(lines), encoding="utf-8")


def test_a_project_matching_a_deny_pattern_is_excluded(tmp_path: Path, home: Path) -> None:
    """The deny list carries the exclusion for a repository one may not mark."""
    deny(home, f"{tmp_path.as_posix()}/client-*")
    counters = FakeCounters()

    assert is_excluded(str(tmp_path / "client-acme"), counters) is True
    assert counters.counted == Counter({EXCLUDED: 1})


def test_a_project_the_deny_list_does_not_match_is_recorded(tmp_path: Path, home: Path) -> None:
    """A deny list that exists is not itself an exclusion."""
    deny(home, f"{tmp_path.as_posix()}/client-*")
    counters = FakeCounters()

    assert is_excluded(str(tmp_path / "demo"), counters) is False
    assert not counters.counted


def test_blank_and_commented_deny_lines_are_ignored(tmp_path: Path, home: Path) -> None:
    """A commented-out pattern must not keep denying what it names (R13)."""
    deny(home, "", f"# {tmp_path.as_posix()}/demo", "   ")
    counters = FakeCounters()

    assert is_excluded(str(tmp_path / "demo"), counters) is False
    assert not counters.counted


def test_a_deny_pattern_matches_however_the_harness_spelled_the_cwd(
    tmp_path: Path, home: Path
) -> None:
    """R13 matches on the normalised directory, so one denial holds for every spelling."""
    deny(home, f"{tmp_path.as_posix()}/client-*")
    counters = FakeCounters()

    assert is_excluded(f"{tmp_path}/./client-acme/", counters) is True
    assert counters.counted == Counter({EXCLUDED: 1})


def test_a_drive_letter_deny_pattern_matches_on_windows(tmp_path: Path, home: Path) -> None:
    """A pattern written the way a user would spell it still matches, drive and all."""
    deny(home, f"{tmp_path}\\client-*")
    counters = FakeCounters()

    assert is_excluded(str(tmp_path / "client-acme"), counters) is True
    assert counters.counted == Counter({EXCLUDED: 1})


def test_an_excluded_project_produces_no_event_at_all(tmp_path: Path, home: Path) -> None:
    """FR-058's force: capture is suppressed entirely, not merely marked."""
    project = tmp_path / "demo"
    (project / ".processrecall").mkdir(parents=True)
    (project / ".processrecall" / "optout").touch()
    counters = FakeCounters()
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-demo",
        "prompt_id": "prompt-1",
        "cwd": str(project),
        "tool_use_id": "toolu_1",
        "tool_name": "Read",
        "tool_input": {"file_path": "secret.py"},
        "tool_result": "top secret contents",
    }

    event = adapt_post_tool_use(payload, counters)

    assert event is None
    assert counters.counted == Counter({EXCLUDED: 1})
