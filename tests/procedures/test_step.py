"""The decomposition seam: one recorded action -> its ordered sub-activities.

FR-017: a step is not an activity, it decomposes into them. The tests read an
event the way the recorder will — class, program, tokens and files per
sub-activity, in the order the action performed them — and never reach into the
command grammar underneath.
"""

from __future__ import annotations

from processrecall.config import ActivityClass
from processrecall.procedures.step import SubActivity, steps_from
from tests.trajectory.factories import make_event


def test_a_shell_action_decomposes_into_ordered_sub_activities() -> None:
    """``pytest | tail`` is an evaluation *then* an inspection, in that order."""
    event = make_event(tool_call_arguments={"command": "pytest -q tests/procedures | tail -n 5"})
    assert steps_from(event, None) == (
        SubActivity(
            0,
            ActivityClass.ARTIFACT_EVALUATION,
            "pytest",
            ("pytest", "-q", "tests/procedures"),
            ("tests/procedures",),
        ),
        SubActivity(1, ActivityClass.INSPECTION, "tail", ("tail", "-n", "5"), ()),
    )


def test_a_tabled_tool_yields_the_one_sub_activity_its_vocabulary_names() -> None:
    """Only the argument *names* become tokens: a template can never carry a payload."""
    event = make_event(
        tool_name="Edit",
        tool_call_arguments={
            "file_path": "processrecall/procedures/step.py",
            "old_string": "before",
            "new_string": "after",
        },
    )
    assert steps_from(event, ActivityClass.CHANGE_IMPLEMENTATION) == (
        SubActivity(
            0,
            ActivityClass.CHANGE_IMPLEMENTATION,
            "Edit",
            ("Edit", "file_path", "new_string", "old_string"),
            ("processrecall/procedures/step.py",),
        ),
    )


def test_an_action_that_names_no_activity_is_still_one_unknown_sub_activity() -> None:
    """FR-022: plumbing-only commands stay on the chain, counted, never dropped."""
    event = make_event(tool_call_arguments={"command": "cd /app && echo done"})
    assert steps_from(event, None) == (
        SubActivity(0, ActivityClass.UNKNOWN, "Bash", ("cd", "/app", "&&", "echo", "done"), ()),
    )
