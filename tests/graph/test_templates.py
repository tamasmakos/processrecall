"""The template seam: one sub-activity -> the abstracted shape of its invocation.

FR-051 and R18: what a step stores of its command line is a template — the shape
that makes two steps comparable — and never the payload. The tests read the
string the episodic row keeps, and assert on what it may not carry as much as on
what it says.
"""

from __future__ import annotations

from processrecall.config import ActivityClass
from processrecall.graph.templates import template_of
from processrecall.procedures.step import SubActivity


def test_a_template_keeps_the_command_shape_and_drops_its_paths() -> None:
    """The verb and its flags identify the action; the file it named does not."""
    activity = SubActivity(
        0,
        ActivityClass.INSPECTION,
        "git diff",
        ("git", "diff", "--stat", "processrecall/graph/store.py"),
        ("processrecall/graph/store.py",),
    )
    assert template_of(activity) == "git diff --stat <File>"


def test_a_number_is_a_quantity_in_a_template_never_the_quantity() -> None:
    """``tail -n 5`` and ``tail -n 500`` are one procedure, not two."""
    activity = SubActivity(
        0, ActivityClass.INSPECTION, "tail", ("tail", "-n", "500", "build/run.log"), ()
    )
    assert template_of(activity) == "tail -n <N> <File>"


def test_a_quoted_or_overlong_argument_is_the_payload_and_is_replaced() -> None:
    """R18: a commit message or a search prose is content, and must not be stored."""
    activity = SubActivity(
        0,
        ActivityClass.CHANGE_IMPLEMENTATION,
        "git commit",
        ("git", "commit", "-m", "T021: lift action templates into the graph"),
        (),
    )
    assert template_of(activity) == "git commit -m <str>"


def test_an_assignment_value_is_content_and_is_replaced() -> None:
    """R18: ``KEY=value`` still binds a payload to a name, and the value must go."""
    activity = SubActivity(
        0,
        ActivityClass.ENVIRONMENT_CONFIGURATION,
        "export",
        ("export", "API_KEY=sk-abc123"),
        (),
    )
    assert template_of(activity) == "export API_KEY=<str>"


def test_a_flag_bound_path_is_abstracted_too() -> None:
    """``--out=<path>`` is a path argument spelled with an ``=`` instead of a space."""
    activity = SubActivity(
        0,
        ActivityClass.CHANGE_IMPLEMENTATION,
        "tool",
        ("tool", "--out=/home/u/x.txt"),
        (),
    )
    assert template_of(activity) == "tool --out=<File>"


def test_a_backslash_path_is_still_classified_as_a_file() -> None:
    """A Windows-spelled path must not be misread by a POSIX-only suffix check."""
    activity = SubActivity(
        0,
        ActivityClass.INSPECTION,
        "cat",
        ("cat", "src\\a.py"),
        (),
    )
    assert template_of(activity) == "cat <File>"


def test_a_redirect_folds_to_one_token_instead_of_leaking_as_a_number_and_a_path() -> None:
    """E4: ``2>/dev/null`` is plumbing, and spelling it out invents a distinct template."""
    activity = SubActivity(
        0, ActivityClass.ARTIFACT_EVALUATION, "pytest", ("pytest", "-q", "2", ">", "/dev/null"), ()
    )
    assert template_of(activity) == "pytest -q <redirect>"


def test_redirects_written_back_to_back_stay_one_redirect() -> None:
    """How many streams were plumbed is not what distinguishes one run from another."""
    activity = SubActivity(
        0,
        ActivityClass.ARTIFACT_EVALUATION,
        "pytest",
        ("pytest", ">", "out.txt", "2", ">&", "1"),
        (),
    )
    assert template_of(activity) == "pytest <redirect>"


def test_a_template_is_bounded_however_long_the_command_line_was() -> None:
    """R18/v5 E4: the head of the invocation names the procedure; the tail is bulk."""
    activity = SubActivity(
        0,
        ActivityClass.SCRIPT_EXECUTION,
        "make",
        ("make", *(f"target{ordinal}" for ordinal in range(30))),
        (),
    )
    expected = " ".join(["make", *(f"target{ordinal}" for ordinal in range(11))])
    assert template_of(activity) == expected
