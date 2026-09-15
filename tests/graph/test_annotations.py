"""Annotation validation: the three checks of FR-038a, and what each refuses.

Read through `AnnotationValidator` rather than through a store: the point of
the check is that a rejected annotation never reaches one, so every test here
asserts on what came back and on what was counted, never on rows.
"""

from __future__ import annotations

from collections import Counter

import pytest

from processrecall.exceptions import AnnotationRejected
from processrecall.graph.annotations import AnnotationValidator

#: Two moves a fixture graph could plausibly hold, spelled as `inspect` prints
#: them so a test reads as the agent's own argument.
EDGES = (
    "Inspection/Read -> ChangeImplementation/Edit",
    "ChangeImplementation/Edit -> ArtifactEvaluation/Pytest",
)


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


@pytest.fixture
def counters() -> FakeCounters:
    return FakeCounters()


def test_a_note_on_a_known_edge_is_accepted_stripped(counters: FakeCounters) -> None:
    """FR-038a: the bound is on the stripped text, so that is what is stored."""
    validator = AnnotationValidator(EDGES, counters)

    assert validator.accept(EDGES[0], "  run the tests first  ") == "run the tests first"
    assert counters.counted == Counter()


def test_an_unknown_edge_is_refused_with_the_closest_moves(counters: FakeCounters) -> None:
    """FR-038a: the move is not created implicitly, and the reason says what exists."""
    validator = AnnotationValidator(EDGES, counters)

    with pytest.raises(AnnotationRejected) as refusal:
        validator.accept("Inspection/Read -> ArtifactEvaluation/Pytest", "never mind")

    assert refusal.value.reason == "no_such_edge"
    assert EDGES[0] in refusal.value.detail
    assert EDGES[1] in refusal.value.detail
    assert counters.counted == Counter({"annotation_rejected_no_edge": 1})


def test_a_note_over_the_bound_is_refused_with_its_length(counters: FakeCounters) -> None:
    """FR-038a: 500 characters after stripping, and the reason says how long it was."""
    validator = AnnotationValidator(EDGES, counters)

    with pytest.raises(AnnotationRejected) as refusal:
        validator.accept(EDGES[0], " " + "x" * 501 + " ")

    assert refusal.value.reason == "too_long"
    assert "501" in refusal.value.detail
    assert counters.counted == Counter({"annotation_rejected_too_long": 1})


def test_a_note_that_is_only_whitespace_is_refused(counters: FakeCounters) -> None:
    """FR-038a: the bound is 1-500, so an empty note is a refusal, not an empty row."""
    validator = AnnotationValidator(EDGES, counters)

    with pytest.raises(AnnotationRejected) as refusal:
        validator.accept(EDGES[0], "   \n  ")

    assert refusal.value.reason == "too_long"
    assert "is 0 characters" in refusal.value.detail
    assert counters.counted == Counter({"annotation_rejected_too_long": 1})


@pytest.mark.parametrize(
    ("name", "note"),
    [
        ("aws_access_key_id", "deploy needs AKIAIOSFODNN7EXAMPLE in the env"),
        ("github_token", f"push with ghp_{'a1B2' * 9} once"),
        ("openai_api_key", f"the fixture key is sk-{'x' * 24}"),
        ("slack_token", "the bot posts with xoxb-4242-4242-abcdef"),
        ("private_key", "paste -----BEGIN RSA PRIVATE KEY----- into the agent"),
        ("bearer_token", "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abc'"),
        ("secret_assignment", "set password = hunter2hunter2 before running"),
        ("base64_run", f"the header decodes from {'QUJDZGVmZ2hpams' * 3}"),
    ],
)
def test_a_note_carrying_a_credential_is_refused_by_name(
    name: str, note: str, counters: FakeCounters
) -> None:
    """FR-038a: the shape is named so the agent can rewrite the note, not scrubbed."""
    validator = AnnotationValidator(EDGES, counters)

    with pytest.raises(AnnotationRejected) as refusal:
        validator.accept(EDGES[0], note)

    assert refusal.value.reason == "credential"
    assert name in refusal.value.detail
    assert counters.counted == Counter({"annotation_rejected_credential": 1})


def test_an_edge_is_refused_against_an_empty_graph(counters: FakeCounters) -> None:
    """FR-038a: a first-run snapshot with no moves yet still names a reason."""
    validator = AnnotationValidator((), counters)

    with pytest.raises(AnnotationRejected) as refusal:
        validator.accept("Start -> End", "never mind")

    assert refusal.value.reason == "no_such_edge"
    assert "the graph holds no moves at all" in refusal.value.detail
    assert counters.counted == Counter({"annotation_rejected_no_edge": 1})


def test_a_note_failing_every_check_is_counted_once_under_the_first(
    counters: FakeCounters,
) -> None:
    """FR-038a: the checks run in order, so one call is one refusal and one count."""
    validator = AnnotationValidator(EDGES, counters)

    with pytest.raises(AnnotationRejected) as refusal:
        validator.accept("Start -> End", "AKIAIOSFODNN7EXAMPLE " + "x" * 501)

    assert refusal.value.reason == "no_such_edge"
    assert counters.counted == Counter({"annotation_rejected_no_edge": 1})
