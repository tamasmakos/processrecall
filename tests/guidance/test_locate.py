"""Localisation: where the agent stands after the action it just took (FR-043).

Read through `locate`, because that is the whole seam: guidance served after
action t-1 is guidance for action t, so the position is the node the *previous*
step landed on — never the one being asked about.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from processrecall.guidance.locate import locate

from .conftest import walk

READ = "Inspection/Read/py"
EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"
FILE = "src/render.py"


def test_the_position_is_the_node_the_previous_step_landed_on() -> None:
    """FR-043: after action t-1, guidance is for action t — from where t-1 left off."""
    steps = walk(READ, EDIT, TEST)
    position = locate(steps, level="class/program")

    assert position.key == "ArtifactEvaluation/pytest"
    assert position.previous is steps[-1]


def test_a_prompt_with_no_step_yet_stands_at_start() -> None:
    """FR-047: before action 0 there is no previous step, and `Start` is the position."""
    position = locate((), level="class/program")

    assert position.key == "Start"
    assert position.previous is None


def test_the_key_is_spelled_at_the_class_level() -> None:
    """The coarsest level names the previous step's activity class alone."""
    position = locate(walk(READ, EDIT, TEST), level="class")

    assert position.key == "ArtifactEvaluation"


def test_the_key_is_spelled_at_the_full_level() -> None:
    """The finest level names the previous step's full node key."""
    position = locate(walk(READ, EDIT, TEST), level="class/program/ext")

    assert position.key == TEST


def test_an_unknown_level_is_rejected() -> None:
    """`locate` cannot spell a key at a level `LEVELS` does not materialise."""
    steps = walk(READ)

    with pytest.raises(ValueError):
        locate(steps, level="bogus")


def test_position_carries_last_k_steps_and_active_symbol() -> None:
    """FR-038: the working state is the last k steps and the symbol in hand, not one row."""
    walked = walk(*(READ, EDIT) * 4)
    worked_on = replace(walked[-1], symbol_ref=f"{FILE}::Renderer.render")
    steps = (*walked[:-1], worked_on)

    position = locate(steps, level="class/program")

    assert position.recent == steps[3:]
    assert position.previous is worked_on
    assert position.symbol == f"{FILE}#Renderer.render"


def test_position_carries_the_file_most_recently_touched() -> None:
    """FR-038: the working state names the file being worked on, not only the symbol."""
    read, edited = walk(READ, EDIT)
    steps = (replace(read, files=("src/other.py",)), replace(edited, files=(FILE,)))

    position = locate(steps, level="class/program")

    assert position.file == FILE
