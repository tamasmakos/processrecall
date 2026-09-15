"""The outcome seam: one action's result -> success, failure or neutral.

FR-034 and FR-037: the verdict comes from the harness's error flag and from
recognisable tool output, by rules, with no model anywhere on the path. The
tests read a result the way the recorder will and never reach into the rule
table underneath.
"""

from __future__ import annotations

from processrecall.procedures.outcome import Outcome, classify_outcome


def test_an_errored_action_failed_whatever_its_output_says() -> None:
    """The error flag wins: a green summary under a raised error is still a failure."""
    assert classify_outcome("3 passed in 0.12s", is_error=True) is Outcome.FAILURE


def test_a_test_run_is_read_from_its_summary_line() -> None:
    """A mixed pytest summary failed: one red test is not half a success."""
    assert classify_outcome("3 passed in 0.12s", is_error=False) is Outcome.SUCCESS
    assert classify_outcome("1 failed, 3 passed in 0.4s", is_error=False) is Outcome.FAILURE
    assert classify_outcome("2 errors in 0.1s", is_error=False) is Outcome.FAILURE
    assert classify_outcome("0 failed, 3 passed in 0.4s", is_error=False) is Outcome.SUCCESS


def test_a_lint_run_is_read_from_its_summary_line() -> None:
    """Ruff says it in words rather than counts, so it needs rules of its own."""
    assert classify_outcome("All checks passed!", is_error=False) is Outcome.SUCCESS
    assert classify_outcome("Found 7 errors (2 fixed).", is_error=False) is Outcome.FAILURE


def test_a_commit_that_printed_a_hash_landed() -> None:
    """Git only prints ``[branch hash]`` for a commit it actually wrote."""
    landed = "[005-procedural-graph-memory 8dd2467] T016: implement store\n 2 files changed"
    assert classify_outcome(landed, is_error=False) is Outcome.SUCCESS
    assert classify_outcome("[main (root-commit) a1b2c3d] init", is_error=False) is (
        Outcome.SUCCESS
    )
    assert classify_outcome("nothing to commit, working tree clean", is_error=False) is (
        Outcome.NEUTRAL
    )


def test_output_no_rule_recognises_is_neutral_rather_than_unsuccessful() -> None:
    """Most actions report nothing a machine can read, and that is not a failure (R5)."""
    assert classify_outcome("1: from __future__ import annotations", is_error=False) is (
        Outcome.NEUTRAL
    )
    assert classify_outcome("", is_error=False) is Outcome.NEUTRAL
