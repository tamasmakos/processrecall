"""`processrecall prune` — the only path that deletes episodic history (FR-057).

Nothing ages out on its own: no schedule, no ceiling on rows and no store size
that starts dropping turns. History leaves only because an operator named a
cutoff here, which is why the cutoff has no default and the deletion asks
before it runs.

What is deleted is whole turns, not the rows inside them, and what follows the
deletion is a re-derivation of the abstract graph from the turns retained
(`processrecall.cli.rebuild`). The graph is an aggregation of episodic rows, so
a snapshot left standing over deleted rows is one the index can no longer
account for — the disagreement SC-004 is measured against.

Example:
    from processrecall.cli.prune import episodes_before, prune

    print(prune(derivation, episodes_before(store, cutoff, project_dir)))
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from processrecall.cli.rebuild import rebuild
from processrecall.graph.derive import Derivation
from processrecall.graph.store import EpisodicStore, SequenceKey
from processrecall.trajectory.paths import project_key


@dataclass(frozen=True, slots=True)
class Removal:
    """The episodes one prune covers, and how many steps they hold.

    Attributes:
        sequences: The turns to delete, oldest first.
        steps: How many recorded steps those turns hold between them.
    """

    sequences: tuple[SequenceKey, ...]
    steps: int

    def __str__(self) -> str:
        return f"steps={self.steps}  sequences={len(self.sequences)}"


def episodes_before(store: EpisodicStore, cutoff: datetime, project_dir: Path) -> Removal:
    """The whole turns *project_dir* holds in *store* that began before *cutoff*.

    Whole turns and never a slice of one: an episode is the unit the graph is
    folded from, so a half-deleted turn would re-derive a first step that was
    never anybody's first step.

    Scoped to *project_dir* rather than the whole store: `prune` re-derives
    only that project's snapshot, so a deletion reaching another project's
    turns would leave that project's snapshot folded from rows no longer
    there (FR-057).
    """
    sequences = store.sequences_before(cutoff, project_key(str(project_dir)))
    return Removal(sequences=sequences, steps=sum(len(store.steps(key)) for key in sequences))


def prune(source: Derivation, removal: Removal) -> str:
    """Delete *removal*'s episodes, re-deriving the graph from the ones retained.

    The re-derivation is part of the deletion rather than a second command an
    operator may forget (FR-057): between the two, the snapshots on disk are
    folded from rows that no longer exist.
    """
    source.store.delete_sequences(removal.sequences)
    return f"removed  {removal}\n{rebuild(source)}"
