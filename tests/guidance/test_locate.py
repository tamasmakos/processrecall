"""Localisation: where the agent stands after the action it just took (FR-043).

Read through `locate`, because that is the whole seam: guidance served after
action t-1 is guidance for action t, so the position is the node the *previous*
step landed on — never the one being asked about.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.guidance.locate import locate
from processrecall.symbolic.packs import ActivityClass

READ = "Inspection/Read/py"
EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"


def walk(*node_keys: str) -> tuple[EpisodicStep, ...]:
    """One prompt that performed *node_keys* in the order given."""
    key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id="p1")
    return tuple(
        _step(node_key, position=position, key=key) for position, node_key in enumerate(node_keys)
    )


def _step(node_key: str, *, position: int, key: SequenceKey) -> EpisodicStep:
    """One recorded row, named by the node key the recorder derived for it."""
    activity_class, program, _ = node_key.split("/")
    return EpisodicStep(
        dedup_key=f"{key.prompt_id}-{position}",
        sequence_key=key,
        position=position,
        node_key=node_key,
        activity_class=ActivityClass(activity_class),
        program=program,
        template=f"{program} <File>",
        occurred_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
    )


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
