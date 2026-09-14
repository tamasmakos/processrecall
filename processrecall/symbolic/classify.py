"""The optional enrichment: free text to a curated symbol, or no opinion.

FR-059 fixes three classifiers — a step's rationale to the activity it intended,
a prompt to its process type, an unrecognised command to an activity — and one
seam between them, so a caller asks the same question of all three. FR-060 makes
the model behind them optional: without the ``classify`` extra the deterministic
rules are the whole path, and :class:`NullClassifier` is what loads, counting the
absence rather than leaving it indistinguishable from nothing to label.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol

from processrecall.config import Counters
from processrecall.symbolic.packs import ActivityClass, ProcessType

_logger = logging.getLogger("processrecall")

#: The model the classify extra loads when it is installed — the same weights
#: ``GRAPHKNOWS_TYPING_MODEL`` defaults to elsewhere. Fixed here rather than
#: read from settings: this module answers to FR-060's stdlib-only layering
#: (R17), and the extra being installed is the whole gate (FR-060).
_MODEL_ID = "fastino/gliner2-base-v1"

#: The labels a classifier may answer with, per closed set. ``Unknown`` is left
#: out of both: it is what the deterministic rules already default to (FR-022),
#: so a classifier that cannot place the text is better read as having no
#: opinion — which leaves the rule's own answer standing — than as agreeing.
ACTIVITY_LABELS: frozenset[str] = frozenset(
    str(member) for member in ActivityClass if member is not ActivityClass.UNKNOWN
)
PROCESS_TYPE_LABELS: frozenset[str] = frozenset(
    str(member) for member in ProcessType if member is not ProcessType.UNKNOWN
)


class Classifier(Protocol):
    """The one question FR-059 lets a caller ask of any of the three."""

    def label(self, text: str) -> str | None:
        """What *text* is about, or ``None`` for no opinion.

        Never raises and never guesses: an answer outside the classifier's
        curated vocabulary is no opinion, since enrichment may not mint a symbol
        the rest of the package cannot spell (FR-061).
        """
        ...


class TextClassifier(Protocol):
    """The classify extra's model, reduced to the one call the three make."""

    def classify_text(self, text: str, tasks: Mapping[str, Sequence[str]]) -> Mapping[str, object]:
        """*text* against each named task's labels, one answer per task."""
        ...


class NullClassifier:
    """What loads when the classify extra is absent (FR-060).

    It has no opinion about anything, and says so out loud: every call counts
    ``enrichment_unavailable``, so a graph built without the extra is readably
    unenriched rather than one that simply found nothing to say.
    """

    def __init__(self, counters: Counters) -> None:
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def label(self, text: str) -> str | None:
        """No opinion, counted."""
        self._counters.bump("enrichment_unavailable")
        return None


class _SchemaClassifier:
    """One classification task asked of the extra's model, answered in the pack.

    The three of FR-059 differ only in what they are asked about and which
    closed set the answer must fall in, so the asking lives here once and each
    of them declares that pair.
    """

    task: ClassVar[str]
    vocabulary: ClassVar[frozenset[str]]

    def __init__(self, model: TextClassifier) -> None:
        self._model = model

    def __repr__(self) -> str:
        return f"{type(self).__name__}(task={self.task!r})"

    def label(self, text: str) -> str | None:
        """What the model calls *text*, if the pack can spell it (FR-061)."""
        answered = self._model.classify_text(text, {self.task: sorted(self.vocabulary)})
        chosen = _single(answered.get(self.task))
        return chosen if chosen in self.vocabulary else None


class RationaleActivity(_SchemaClassifier):
    """A step's rationale to the activity class it intended (FR-059)."""

    task = "intended_activity"
    vocabulary = ACTIVITY_LABELS


class PromptProcessType(_SchemaClassifier):
    """A prompt to what the turn it opened was for (FR-059)."""

    task = "process_type"
    vocabulary = PROCESS_TYPE_LABELS


class UnknownCommandActivity(_SchemaClassifier):
    """A command no program table recognises to an activity class (FR-059)."""

    task = "command_activity"
    vocabulary = ACTIVITY_LABELS


@dataclass(frozen=True, slots=True)
class ClassifierSet:
    """The three of FR-059, resolved together because they share one model."""

    rationale_activity: Classifier
    prompt_process_type: Classifier
    unknown_command_activity: Classifier


def load_classifiers(counters: Counters) -> ClassifierSet:
    """The three classifiers, model-backed where the extra is installed (FR-060).

    Where it is not — no ``gliner2`` importable, or its weights fail to load —
    all three are a :class:`NullClassifier`, so a caller writes the same code
    either way and the difference shows in the counters rather than in a
    traceback.
    """
    model = _load_model()
    if model is None:
        absent = NullClassifier(counters)
        return ClassifierSet(absent, absent, absent)
    return ClassifierSet(
        RationaleActivity(model), PromptProcessType(model), UnknownCommandActivity(model)
    )


def _load_model() -> TextClassifier | None:
    """The installed model, or ``None`` for any reason it is not there.

    FR-060's gate is the ``classify`` extra itself: an ``ImportError`` is the
    expected absent state and stays silent. Weights that fail to load despite
    the extra being installed are the one case worth logging, since an
    operator who opted in deserves to read why it is not being used.
    """
    try:
        from gliner2 import GLiNER2
    except ImportError:
        return None
    try:
        return GLiNER2.from_pretrained(_MODEL_ID)
    except Exception as unavailable:
        _logger.warning("%s did not load: %s", _MODEL_ID, unavailable)
        return None


def _single(answer: object) -> str | None:
    """The one label in *answer*, whichever shape the model reported it in.

    ``gliner2``'s ``classify_text`` (0.1.x, ``format_results=True``, the
    default this module calls with) answers a single-label task with the
    label string itself and a multi-label one with a plain list of label
    strings; the caller wants one symbol, so a list is read as its best entry
    and anything else as no answer at all.
    """
    if isinstance(answer, str):
        return answer
    if isinstance(answer, list) and answer and isinstance(answer[0], str):
        return answer[0]
    return None
