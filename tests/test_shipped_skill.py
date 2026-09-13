"""The shipped `remember` skill (FR-040).

An annotation is written by the agent, so the guidance that says *when* one is
worth writing is itself a shipped artefact: `skills/remember/SKILL.md` (its
discovery declaration lands with T064). The verb-level behaviour of the nudge
that points at it — that it is `close`'s alone, once per sequence — lives in
`tests/integrations/claude_code/test_close.py`, alongside the rest of that
verb's tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "skills" / "remember" / "SKILL.md"


def test_the_repository_ships_the_remember_skill() -> None:
    """FR-040: the guidance is a skill on disk, named after the tool it drives."""
    assert SKILL.is_file(), f"{SKILL} is not shipped"
    text = SKILL.read_text(encoding="utf-8")

    assert text.startswith("---\n"), "SKILL.md carries no frontmatter"
    frontmatter = text.split("---\n")[1]
    assert "name: remember" in frontmatter, frontmatter
    assert "description:" in frontmatter, frontmatter


def test_the_skill_says_when_an_annotation_is_worth_writing() -> None:
    """Guidance that names no moment and no tool tells the agent nothing."""
    body = SKILL.read_text(encoding="utf-8").split("---\n", 2)[2]

    assert "remember" in body, "the skill never names the tool that writes the note"
    for moment in ("worth", "not worth"):
        assert moment in body.lower(), f"the skill never says what is {moment} writing"
