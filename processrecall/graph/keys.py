"""What `abstract` and `sequence` both derive from an episodic row.

Its key at a level, whether it shares a file with another, and the order its
prompt's rows were carried out in.

Shared here rather than one module importing the other's internals for
helpers it does not own — `abstract` folds them into nodes and edges,
`sequence` counts them into back-off tables, but the derivation is the same
either way.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from processrecall.graph.store import EpisodicStep, SequenceKey

if TYPE_CHECKING:
    from processrecall.graph.abstract import Level


def keys_of(step: EpisodicStep) -> tuple[str, str, str]:
    """*step*'s identity at each of `processrecall.config.LEVELS`, coarsest first.

    Rebuilt from the columns the recorder stored rather than by splitting
    ``node_key``: a program is free to contain a ``/`` and a positional split of
    the joined key would cut it in the wrong place.
    """
    by_program = f"{step.activity_class}/{step.program}"
    return (str(step.activity_class), by_program, step.node_key)


def key_at(step: EpisodicStep, level: Level) -> str:
    """*step*'s node key at *level*."""
    return keys_of(step)[level.depth]


def shares_a_file(before: EpisodicStep, after: EpisodicStep) -> bool:
    """Whether *after* touched any of the files *before* did (FR-030)."""
    return bool(set(before.files) & set(after.files))


def group_by_sequence(steps: Iterable[EpisodicStep]) -> dict[SequenceKey, tuple[EpisodicStep, ...]]:
    """*steps* grouped by the prompt they belong to, each group in carried-out order.

    Shared by `abstract._chains` and `sequence._chains`: both need the same
    rows in the same order — one decorates each group with process type and
    cleanliness, the other counts straight from it.
    """
    rows: dict[SequenceKey, list[EpisodicStep]] = {}
    for step in steps:
        rows.setdefault(step.sequence_key, []).append(step)
    return {key: tuple(sorted(group, key=lambda step: step.position)) for key, group in rows.items()}
