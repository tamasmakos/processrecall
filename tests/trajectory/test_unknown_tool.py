"""FR-022: a tool no pack tables is residue the store keeps, not a dropped action.

``Unknown`` is a member of the closed set of classes, not a failure mode: an
action whose class no rule can determine still lands on its prompt's chain, with
the whole invocation template the harness spelled, and ``class_unknown`` counts
it so the residue stays visible rather than quietly shrinking the graph.

What "reclassified later without re-ingesting" means is that the row is enough
on its own: once a hand-edited pack tables the tool, the class and the node key
follow from the stored step's own program and template, with the transcript
nowhere in reach.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.record import record_event
from processrecall.graph.store import EpisodicStep, SQLiteEpisodicStore
from processrecall.procedures.step import SubActivity
from processrecall.procedures.taxonomy import identify_procedure
from processrecall.symbolic.packs import ActivityClass
from processrecall.trajectory.vocabulary import load_vocabulary
from tests.trajectory.factories import make_event

#: A Claude Code tool the shipped pack deliberately does not table (FR-005).
UNTABLED_TOOL = "TodoWrite"


@pytest.fixture
def recorded(tmp_path: Path) -> Iterator[tuple[EpisodicStep, SQLiteEpisodicStore]]:
    """One untabled action, recorded, with the store that took it."""
    connection = open_index(tmp_path / "episodes.db")
    try:
        landed = record_event(
            make_event(
                tool_name=UNTABLED_TOOL,
                tool_call_arguments={"todos": "write the test first", "merge": True},
            ),
            connection,
        )
        assert len(landed) == 1, "an unclassifiable action still lands on the chain"
        yield landed[0], SQLiteEpisodicStore(connection)
    finally:
        connection.close()


def test_an_untabled_tool_lands_in_unknown_with_its_full_template(
    recorded: tuple[EpisodicStep, SQLiteEpisodicStore],
) -> None:
    step, store = recorded
    stored = store.steps(step.sequence_key)[0]

    # test_vocabulary.py already pins TodoWrite -> UNKNOWN at the seam; what
    # only this end-to-end path can show is the node key, the full template
    # and the counter as the store itself persisted them.
    assert stored.node_key == f"{ActivityClass.UNKNOWN.value}/{UNTABLED_TOOL}/--"
    # Every argument the harness named, not the first one or a truncation: the
    # template is what a later reclassification has to work from.
    assert stored.template == f"{UNTABLED_TOOL} merge todos"
    assert store.counters()["class_unknown"] == 1


def _pack_tabling(directory: Path) -> Path:
    """A hand-edited pack that tables `UNTABLED_TOOL`, as a later operator would write it."""
    pack = directory / "hand_edited.json"
    pack.write_text(
        json.dumps(
            {
                "version": 1,
                "harness": "claude_code",
                "tools": {
                    UNTABLED_TOOL: {
                        "class": ActivityClass.DELEGATION.value,
                        "program": UNTABLED_TOOL,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return pack


def test_the_stored_row_alone_reclassifies_once_a_pack_tables_the_tool(
    recorded: tuple[EpisodicStep, SQLiteEpisodicStore], tmp_path: Path
) -> None:
    """No shipped path re-derives a node from a stored row on its own; this pins
    that the row's ``program`` and ``files`` are sufficient inputs for one to,
    by rebuilding the identity by hand from those two fields alone.
    """
    step, store = recorded
    stored = store.steps(step.sequence_key)[0]
    pack = _pack_tabling(tmp_path)

    # The row's own program is the join key into the edited pack: no transcript
    # is read, no action is recorded a second time, and the residue counter
    # stays where ingest left it.
    reclassified = load_vocabulary(pack).activity_for(stored.program, store.bump)
    assert reclassified is ActivityClass.DELEGATION
    assert store.counters()["class_unknown"] == 1

    identity = identify_procedure(
        SubActivity(
            ordinal=stored.position,
            activity_class=reclassified,
            program=stored.program,
            tokens=(),
            files=stored.files,
        )
    )
    assert identity.key == f"{ActivityClass.DELEGATION.value}/{UNTABLED_TOOL}/--"
