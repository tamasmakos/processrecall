"""The `event.name` values telemetry recognises as consumed records (FR-009).

Declared here, at :mod:`processrecall.trajectory` — below :mod:`processrecall.graph`
in R17's layering — so the reader reads the names off this module rather than
importing upward across the layers to reach
:data:`processrecall.graph.schema.CONSUMED_RECORDS`. That module pairs each of
these names with the episodic shape it becomes, so the set of names stays
declared exactly once; only the edge between the two modules points down.

On the hot path's layer, so the standard library only.
"""

from __future__ import annotations

#: The `event.name` values the memory consumes, in the order
#: `graph.schema.CONSUMED_RECORDS` lists them in the generated contract. A
#: record type is recognised once it has an episodic shape to become there,
#: and not before.
CONSUMED_EVENT_NAMES: tuple[str, ...] = (
    "claude_code.user_prompt",
    "claude_code.api_request",
    "claude_code.api_error",
    "claude_code.api_refusal",
    "claude_code.tool_result",
    "claude_code.tool_decision",
    "claude_code.subagent_completed",
)
