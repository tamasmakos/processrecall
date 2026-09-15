"""What an agent-authored note must clear before anything stores it (FR-038a).

The one write path by which meaning, rather than frequency, enters the graph is
also the one an agent can point at the wrong move or fill with a secret, so the
note is checked before a row exists: an unknown edge, text outside the bound,
or a named credential shape is refused with a reason the agent can act on, and
nothing is scrubbed — a half-working scrubber writes a secret-shaped hole into
a committable file while reporting success (R12).

Off the hot path, but stdlib only: the screen is a `re` table, not a
dependency.

Example:
    from processrecall.graph.annotations import AnnotationValidator

    validator = AnnotationValidator(edge_keys, counters)
    text = validator.accept("Inspection/Read -> ChangeImplementation/Edit", note)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from difflib import get_close_matches
from typing import NoReturn

from processrecall.config import Counters
from processrecall.exceptions import AnnotationRejected

#: How many existing moves a `no_such_edge` refusal names, so the agent can
#: retry against a real one rather than guess again (R12).
_SUGGESTIONS = 2

#: Each refusal of FR-038a against the counter it increments (R16). One table
#: rather than a counter name at each call site: the two halves of a refusal
#: are decided together, and a reason with no counter is how a write path goes
#: quietly unmeasured.
_COUNTERS: Mapping[str, str] = {
    "no_such_edge": "annotation_rejected_no_edge",
    "too_long": "annotation_rejected_too_long",
    "credential": "annotation_rejected_credential",
}

#: The screen of R12: each credential shape against the name a refusal reports.
#: Named patterns rather than an entropy heuristic, because a name is something
#: the agent can act on and a score is not — and because a dependency here
#: would have to work offline, inside a plugin.
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("openai_api_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]+")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{16,}")),
    (
        "secret_assignment",
        re.compile(r"(?i)\b(?:api_?key|token|secret|password)\b\s*[=:]\s*\S{8,}"),
    ),
    ("base64_run", re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")),
)

#: The upper half of FR-038a's bound, in characters of stripped text. An
#: annotation is a note about one move, not the record of the work it is
#: about: `record_ref` already points at that. The lower half is 1 — an empty
#: note is refused under the same reason rather than stored as an empty row.
TEXT_CEILING = 500


@dataclass(frozen=True, slots=True)
class Annotation:
    """One agent-authored note about a move — authored, never derived (FR-038).

    It is stored against `TransitionEdge.edge_key` in a table of its own rather
    than on the edge, because the edge is re-derived from the episodic rows at
    every rebuild and anything kept on it would be re-derived away.

    Attributes:
        edge_key: The move the note is about, as `edge_key` spells it.
        text: The note, as `AnnotationValidator` accepted it (FR-038a).
        author: Who wrote it.
        written_at: When they wrote it.
    """

    edge_key: str
    text: str
    author: str
    written_at: datetime


class AnnotationValidator:
    """The three checks of FR-038a, in order, against one graph's moves.

    The edges are state rather than an argument repeated at every call: a
    validator is made for the snapshot being annotated, the way `SnapshotFile`
    is made for a path.
    """

    def __init__(self, edge_keys: Iterable[str], counters: Counters) -> None:
        self._edge_keys = tuple(edge_keys)
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}({len(self._edge_keys)} edges)"

    def accept(self, edge_key: str, text: str) -> str:
        """*text* as it may be stored on *edge_key*: stripped, and nothing else.

        Raises:
            AnnotationRejected: on the first check that refuses, having counted
                it. Nothing is written and nothing is altered.
        """
        if edge_key not in self._edge_keys:
            self._refuse(
                "no_such_edge", f"there is no move {edge_key!r}; {self._nearest(edge_key)}"
            )
        stripped = text.strip()
        if not 1 <= len(stripped) <= TEXT_CEILING:
            self._refuse(
                "too_long",
                f"the note is {len(stripped)} characters after stripping,"
                f" and the bound is 1-{TEXT_CEILING}",
            )
        if (name := _credential_in(stripped)) is not None:
            self._refuse(
                "credential",
                f"the note matches the credential pattern {name!r};"
                " rewrite it without the value, nothing was stored or scrubbed",
            )
        return stripped

    def _nearest(self, edge_key: str) -> str:
        """The existing moves *edge_key* was likeliest meant to be, named."""
        closest = get_close_matches(edge_key, self._edge_keys, n=_SUGGESTIONS, cutoff=0.0)
        if not closest:
            return "the graph holds no moves at all"
        named = " or ".join(repr(candidate) for candidate in closest)
        return f"did you mean {named}?"

    def _refuse(self, reason: str, detail: str) -> NoReturn:
        """Count *reason* and raise it, before anything has been written."""
        self._counters.bump(_COUNTERS[reason])
        raise AnnotationRejected(reason, detail)


def _credential_in(text: str) -> str | None:
    """The name of the first `CREDENTIAL_PATTERNS` shape *text* carries, if any."""
    return next((name for name, pattern in CREDENTIAL_PATTERNS if pattern.search(text)), None)
