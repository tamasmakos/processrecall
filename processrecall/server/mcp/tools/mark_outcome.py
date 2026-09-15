"""`mark_outcome`: the agent's verdict on a turn, beside the derived one (FR-035).

A declaration never replaces what the rules derived (FR-036) — it lands in its
own column, so a rule change can re-derive the other one and a reader can see
which of the two is which. The answer therefore reports both, and says whether
they disagree rather than leaving that to be inferred.

A prompt nothing opened is rejected with its reason: this tool judges a turn the
store already holds, and inventing one to hang a verdict on would put a sequence
with no steps into the episodic index.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

from processrecall.graph.episodic import open_index
from processrecall.graph.store import EpisodicStore, SQLiteEpisodicStore
from processrecall.server.mcp.arguments import MarkOutcomeArguments


def mark_outcome(arguments: MarkOutcomeArguments) -> dict[str, Any]:
    """Declare how the turn *arguments* names went, over the store of this home."""
    with closing(open_index()) as connection:
        return _declared(SQLiteEpisodicStore(connection), arguments)


def _declared(store: EpisodicStore, arguments: MarkOutcomeArguments) -> dict[str, Any]:
    """Write the declaration *arguments* carries into *store*, as the caller sees it."""
    sequence = (
        store.sequence_for_prompt(arguments.prompt_id)
        if arguments.prompt_id is not None
        else store.latest_sequence()
    )
    if sequence is None:
        reason = (
            f"no sequence was opened for prompt {arguments.prompt_id}; omit prompt_id"
            " to judge the current turn"
            if arguments.prompt_id is not None
            else "no turn is currently open; pass prompt_id to name one explicitly"
        )
        return {
            "stored": False,
            "prompt_id": arguments.prompt_id,
            "reason": reason,
        }
    store.declare_outcome(sequence.key, arguments.outcome)
    return {
        "stored": True,
        "prompt_id": sequence.key.prompt_id,
        "declared": str(arguments.outcome),
        "derived": sequence.derived_outcome,
        "state": "overridden" if arguments.outcome != sequence.derived_outcome else "confirmed",
    }
