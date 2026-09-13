"""The three errors processrecall raises.

One per thing that can fail on its own terms: the runtime environment was never
prepared, a curated vocabulary pack is malformed, or an annotation failed
validation before storage. They share no base class — nothing catches
"any processrecall error", and each of the three is handled where it is raised.
"""

from __future__ import annotations


class BootstrapError(Exception):
    """The runtime environment could not be prepared because tooling is missing.

    Raised once, at the point bootstrap discovers the gap, with the one
    actionable message FR-069 asks for: what is missing and how to get it. The
    session that triggered bootstrap carries on — this error reports a broken
    environment, it does not ask the caller to repair one.
    """

    def __init__(self, tool: str, remedy: str) -> None:
        super().__init__(f"processrecall needs '{tool}', which is not available. {remedy}")
        self.tool = tool
        self.remedy = remedy


class PackError(Exception):
    """A curated vocabulary pack is malformed and was not loaded.

    Loading is all-or-nothing: a pack that fails to parse, or that breaks the
    label/definition/parent/domain/range shape, leaves nothing behind. The
    message names the pack and the defect so the person who hand-edited it knows
    which file to open.
    """

    def __init__(self, pack: str, problem: str) -> None:
        super().__init__(f"Pack '{pack}' is malformed and was not loaded: {problem}")
        self.pack = pack
        self.problem = problem


class AnnotationRejected(Exception):  # noqa: N818 — the spec names it; it is not an "Error"
    """An agent-authored annotation failed validation, so nothing was stored.

    Raised by validation, before any write: a rejected annotation writes zero
    rows and nothing is scrubbed. ``reason`` is one of :data:`REASONS` — the
    codes the ``remember`` tool reports (``no_such_edge``, ``too_long``,
    ``credential``); ``detail`` is what makes that code actionable — the
    nearest existing edges, the actual length, the credential pattern that
    matched.
    """

    #: The codes ``contracts/mcp-tools.md`` fixes. Kept here so the test suite
    #: can assert that every call site names a real one.
    REASONS = frozenset({"no_such_edge", "too_long", "credential"})

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"Annotation rejected ({reason}): {detail}. Nothing was stored.")
        self.reason = reason
        self.detail = detail
