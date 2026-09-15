"""The harness vocabulary seam: tool names are data, not a match statement.

FR-003 puts the mapping from one harness's tool names to abstract procedure
classes in a per-harness pack that can be hand-edited without touching code, and
FR-022 says a name the pack does not table is recorded as ``Unknown`` and
counted rather than guessed at or dropped. Both halves are what this file holds
to: the shipped Claude Code pack resolves the tools it tables, routes ``Bash``
to the command grammar, and leaves everything else visible as residue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from processrecall.config import ActivityClass
from processrecall.exceptions import PackError
from processrecall.trajectory.vocabulary import CLAUDE_CODE, load_vocabulary


def _no_op(_counter_name: str) -> None:
    """A `bump` for tests that don't care about the `class_unknown` counter."""


def test_shipped_pack_tables_claude_codes_own_tool_names() -> None:
    vocabulary = load_vocabulary(CLAUDE_CODE)

    assert vocabulary.harness == "claude_code"
    assert vocabulary.activity_for("Read", _no_op) is ActivityClass.INSPECTION
    assert vocabulary.activity_for("Grep", _no_op) is ActivityClass.SEARCH
    assert vocabulary.activity_for("Edit", _no_op) is ActivityClass.CHANGE_IMPLEMENTATION


def test_bash_defers_to_the_command_grammar_instead_of_the_table() -> None:
    vocabulary = load_vocabulary(CLAUDE_CODE)

    assert vocabulary.tools["Bash"].decompose == "shell"
    # `None` is how `steps_from` hears "the grammar names the class, not me".
    assert vocabulary.activity_for("Bash", _no_op) is None


def test_an_untabled_tool_name_lands_in_unknown_and_is_counted() -> None:
    counted: list[str] = []
    vocabulary = load_vocabulary(CLAUDE_CODE)

    # A harness tool nobody has classified yet: never guessed, never dropped.
    assert vocabulary.activity_for("TodoWrite", counted.append) is ActivityClass.UNKNOWN
    assert counted == ["class_unknown"]

    assert vocabulary.activity_for("Read", counted.append) is ActivityClass.INSPECTION
    assert counted == ["class_unknown"]


def test_a_row_naming_neither_a_class_nor_a_grammar_is_refused(tmp_path: Path) -> None:
    pack = tmp_path / "half_edited.json"
    pack.write_text(
        json.dumps(
            {
                "version": 1,
                "harness": "half_edited",
                "tools": {"Sorcery": {"class": None, "program": None}},
            }
        ),
        encoding="utf-8",
    )

    # Without a `decompose`, a null class is a hand-edit that stopped halfway,
    # not a deferral: loading it would route the tool nowhere in silence.
    with pytest.raises(PackError) as refusal:
        load_vocabulary(pack)
    assert refusal.value.pack == str(pack)
    assert "Sorcery" in refusal.value.problem
