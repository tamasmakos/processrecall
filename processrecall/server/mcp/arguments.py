"""What each of the four tools takes, and the only pydantic in the package.

The models are the tools' published contract: their JSON schema *is* what
`tools/list` advertises and what the transport validates a call against, so the
one thing the dependency does here is describe arguments. Everything a tool then
calls into is this package's own stdlib-only code.

Every description is written for the agent reading the tool list, because that
list is the whole documentation it gets.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from processrecall.procedures.outcome import Outcome


class RecallArguments(BaseModel):
    """Arguments of `recall`."""

    procedure: str | None = Field(
        default=None,
        description="The procedure to recall. Omitted: guidance for where the work is now.",
    )


class RememberArguments(BaseModel):
    """Arguments of `remember`."""

    edge: str = Field(description="The move the note is about, as `inspect` names it.")
    note: str = Field(description="What a later run should know about that move.")


class MarkOutcomeArguments(BaseModel):
    """Arguments of `mark_outcome`."""

    outcome: Outcome = Field(description="How the work went, overriding the derived verdict.")
    prompt_id: str | None = Field(
        default=None,
        description="The prompt being judged. Omitted: the current one.",
    )


class InspectArguments(BaseModel):
    """Arguments of `inspect`: it takes none — the whole graph is the answer."""
