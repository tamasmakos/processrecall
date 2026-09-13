"""Localisation: where the agent stands after the action it just took (FR-043).

Read through `locate`, because that is the whole seam: guidance served after
action t-1 is guidance for action t, so the position is the node the *previous*
step landed on — never the one being asked about.
"""

from __future__ import annotations

import pytest

from processrecall.guidance.locate import locate

from .conftest import walk

READ = "Inspection/Read/py"
EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"


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
    with pytest.raises(ValueError):
        locate(walk(READ), level="bogus")
