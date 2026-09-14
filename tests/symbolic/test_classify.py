"""Optional enrichment: three classifiers behind one seam, absent without a model.

Read through the `Classifier` protocol, which is all any caller sees (FR-059).
The two halves worth testing are the ones FR-060 and FR-061 fix: without the
classify extra's model nothing raises and `NullClassifier` counts the absence,
and with one, a label that is not in the classifier's closed vocabulary is no
opinion rather than a new symbol.
"""

from __future__ import annotations

import builtins
import sys
import types
from collections import Counter
from collections.abc import Mapping, Sequence

import pytest

from processrecall.config import ActivityClass
from processrecall.symbolic.classify import (
    Classifier,
    NullClassifier,
    PromptProcessType,
    RationaleActivity,
    UnknownCommandActivity,
    load_classifiers,
)


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


class FakeModel:
    """A stand-in for the extra's model that answers whatever it was handed."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.offered: dict[str, Sequence[str]] = {}

    def classify_text(self, text: str, tasks: Mapping[str, Sequence[str]]) -> Mapping[str, object]:
        self.offered = dict(tasks)
        return dict.fromkeys(tasks, self.answer)


def test_null_classifier_has_no_opinion_and_counts_the_absence() -> None:
    """FR-060: the extra's absence is a countable state, never an error."""
    counters = FakeCounters()
    classifier: Classifier = NullClassifier(counters)

    assert classifier.label("ran the tests to see which assertion broke") is None
    assert counters.counted["enrichment_unavailable"] == 1


def test_rationale_is_labelled_with_an_activity_class_of_the_pack() -> None:
    """FR-059: a step's rationale becomes the activity it intended."""
    model = FakeModel(["Search"])
    classifier: Classifier = RationaleActivity(model)

    assert classifier.label("looking for where the ceiling is enforced") == "Search"
    assert set(model.offered["intended_activity"]) <= {str(m) for m in ActivityClass}


def test_prompt_is_labelled_with_a_process_type_of_the_pack() -> None:
    """FR-059: a prompt becomes what the turn was for."""
    classifier: Classifier = PromptProcessType(FakeModel("BugFix"))

    assert classifier.label("the hook writes two steps for one tool call") == "BugFix"


def test_unrecognised_command_is_labelled_with_an_activity_class() -> None:
    """FR-059: a command the program tables do not know still gets an activity."""
    classifier: Classifier = UnknownCommandActivity(FakeModel(["ScriptExecution"]))

    assert classifier.label("just --check-everything") == "ScriptExecution"


def test_a_label_outside_the_vocabulary_is_no_opinion() -> None:
    """FR-061: enrichment may not mint a symbol the package cannot spell."""
    classifier: Classifier = RationaleActivity(FakeModel(["Refactoring"]))

    assert classifier.label("pulled the helper out of the loop") is None


def test_without_the_extra_installed_all_three_load_null_and_count_the_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-060: activation gates on the ``classify`` extra's import, nothing else."""
    real_import = builtins.__import__

    def _no_gliner2(name: str, *args: object, **kwargs: object) -> object:
        if name == "gliner2":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_gliner2)
    counters = FakeCounters()

    classifiers = load_classifiers(counters)
    labelled = [
        classifiers.rationale_activity.label("re-ran the failing case"),
        classifiers.prompt_process_type.label("add the end verb"),
        classifiers.unknown_command_activity.label("just --list"),
    ]

    assert labelled == [None, None, None]
    assert counters.counted["enrichment_unavailable"] == 3


def test_weights_that_will_not_load_are_absence_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-060: the extra being present but the weights failing is still no error."""

    class _BrokenGLiNER2:
        @staticmethod
        def from_pretrained(model_id: str) -> object:
            raise OSError(f"no such model: {model_id}")

    fake_module = types.ModuleType("gliner2")
    fake_module.GLiNER2 = _BrokenGLiNER2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner2", fake_module)
    counters = FakeCounters()

    assert load_classifiers(counters).rationale_activity.label("anything") is None
    assert counters.counted["enrichment_unavailable"] == 1
