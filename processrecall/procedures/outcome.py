"""How an action went: one result -> ``success``, ``failure`` or ``neutral``.

FR-034 and FR-037: the verdict is derived by rules from the harness's error flag
and from output a tool spells the same way every time — a pytest summary line, a
ruff summary line, the hash git prints when a commit lands. No model judges an
outcome, so a rule change re-derives rather than re-infers (R5).

Lifted from prototype v3 (R18). On the hot path, so the standard library only.
"""

from __future__ import annotations

import re
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
