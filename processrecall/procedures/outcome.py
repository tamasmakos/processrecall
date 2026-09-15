"""How an action went: one result -> ``success``, ``failure`` or ``neutral``.

FR-034 and FR-037: the verdict is derived by rules from the harness's error flag
and from output a tool spells the same way every time — a pytest summary line, a
ruff summary line, the hash git prints when a commit lands. No model judges an
outcome, so a rule change re-derives rather than re-infers (R5).

Lifted from prototype v3 (R18). On the hot path, so the standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum


class Outcome(StrEnum):
    """How one recorded action went.

    ``NEUTRAL`` is the honest verdict for output no rule recognises, not a
    failure: most actions report nothing a machine can read, and calling them
    unsuccessful would make almost every sequence look broken (R5).
    """

    SUCCESS = "success"
    FAILURE = "failure"
    NEUTRAL = "neutral"


#: Result patterns and their verdicts, applied in order, first match winning.
#: A red summary is matched before a green one so that ``1 failed, 3 passed``
#: reads as the failure it is. One counted-red rule serves both summaries a run
#: produces: pytest's ``2 errors`` and ruff's ``Found 7 errors`` are the same
#: sentence to it.
OUTCOME_RULES: tuple[tuple[re.Pattern[str], Outcome], ...] = (
    (re.compile(r"\b[1-9]\d* (?:failed|error)"), Outcome.FAILURE),
    (re.compile(r"\b\d+ passed"), Outcome.SUCCESS),
    (re.compile(r"\bAll checks passed"), Outcome.SUCCESS),
    (re.compile(r"^\[\S+ (?:\(root-commit\) )?[0-9a-f]{7,40}\]", re.MULTILINE), Outcome.SUCCESS),
)


def classify_outcome(result: str, *, is_error: bool) -> Outcome:
    """The rule-derived verdict for one action's result.

    *is_error* is the harness's own error flag for the action. It is checked
    first and on its own: a tool that raised failed, whatever its output says.
    """
    if is_error:
        return Outcome.FAILURE
    for pattern, outcome in OUTCOME_RULES:
        if pattern.search(result):
            return outcome
    return Outcome.NEUTRAL


def sequence_outcome(outcomes: Iterable[str]) -> Outcome:
    """The verdict a whole turn derives from the verdicts of *outcomes*.

    One failed action is what makes a turn a failure, however much of it went
    well: a piece of work that had to be fixed is not the same evidence as one
    that never broke (FR-027 reads it that way too). A turn nothing recognised
    an outcome on is ``NEUTRAL`` rather than a success, for the reason
    `Outcome.NEUTRAL` exists at all.
    """
    verdicts = {Outcome(outcome) for outcome in outcomes}
    if Outcome.FAILURE in verdicts:
        return Outcome.FAILURE
    return Outcome.SUCCESS if Outcome.SUCCESS in verdicts else Outcome.NEUTRAL
