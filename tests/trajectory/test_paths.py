"""The path seam: whatever the harness spelled -> a path safe to write down.

FR-013 and R4: every path recorded on a step is project-relative, and a path the
project does not contain still normalises to something stable that carries no
absolute user path into a shareable snapshot. The tests read only the returned
string, the way the recorder and the renderer will.
"""

from __future__ import annotations

import pytest

from processrecall.trajectory.paths import EXTERNAL_ROOT, HOME_ROOT, normalise_path

PROJECT_DIR = "/home/dev/processrecall"


@pytest.fixture(autouse=True)
def _home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anchor the home case on a known directory, whatever machine runs this."""
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, "/home/dev")


def test_a_path_inside_the_project_becomes_project_relative() -> None:
    """The anchor is ``project_dir``, so the same file matches after a move (FR-013)."""
    absolute = f"{PROJECT_DIR}/processrecall/graph/episodic.py"
    assert normalise_path(absolute, PROJECT_DIR) == "processrecall/graph/episodic.py"


def test_a_windows_spelling_normalises_to_the_same_project_relative_path() -> None:
    """A drive letter and backslashes are how a harness spelled it, not what it is."""
    windows = R"C:\Users\dev\processrecall\processrecall\graph\episodic.py"
    assert (
        normalise_path(windows, R"C:\Users\dev\processrecall") == "processrecall/graph/episodic.py"
    )


def test_a_path_the_action_spelled_relatively_is_anchored_on_the_project() -> None:
    """``rg foo tests/conftest.py`` names a project path already; it stays one."""
    assert normalise_path("tests/conftest.py", PROJECT_DIR) == "tests/conftest.py"


def test_a_traversal_collapses_so_one_file_has_one_recorded_path() -> None:
    """``..`` is a way of spelling a path, and two spellings must not be two nodes."""
    assert normalise_path("./processrecall/../tests/conftest.py", PROJECT_DIR) == (
        "tests/conftest.py"
    )


def test_a_path_under_home_keeps_its_shape_without_naming_the_user() -> None:
    """``~/.claude/settings.json`` is worth seeing; the username above it is not (R4)."""
    assert normalise_path("/home/dev/.claude/settings.json", PROJECT_DIR) == (
        f"{HOME_ROOT}/.claude/settings.json"
    )


def test_a_windows_spelled_home_path_keeps_its_shape_without_naming_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drive letter and backslashes are how a harness spelled home, not what it is."""
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, R"C:\Users\dev")
    assert normalise_path(R"C:\Users\dev\.claude\settings.json", PROJECT_DIR) == (
        f"{HOME_ROOT}/.claude/settings.json"
    )


def test_a_path_outside_the_project_and_home_keeps_only_its_name() -> None:
    """Enough to type the action, not enough to leak a filesystem layout (R4)."""
    assert normalise_path("/usr/local/bin/pytest", PROJECT_DIR) == f"{EXTERNAL_ROOT}/pytest"


def test_a_windows_path_outside_the_project_keeps_only_its_name() -> None:
    """No drive letter may leak into the external form either."""
    assert normalise_path(R"D:\work\other\x.py", R"C:\Users\dev\proj") == f"{EXTERNAL_ROOT}/x.py"
