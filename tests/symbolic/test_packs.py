"""The concept index seam: an activity vocabulary that is data, not code.

FR-019 freezes the activity classes; FR-024 says they live in a hand-edited pack
loadable with no semantic-web runtime. So the seam has two halves worth testing:
the closed sets are exactly the ones the spec names, and a pack that has drifted
from them — or that a hand-edit broke — is refused whole rather than half-loaded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from processrecall.config import ActivityClass, ProcessType
from processrecall.exceptions import PackError
from processrecall.symbolic.packs import SEON_ACTIVITIES, load_pack

#: The vocabulary of FR-019, spelled out rather than derived from the enum: a
#: label added or renamed here is a spec change, and the test is where it shows.
FR_019_ACTIVITY_CLASSES = {
    "Inspection",
    "Search",
    "ChangeImplementation",
    "ArtifactEvaluation",
    "ScriptExecution",
    "Checkin",
    "Checkout",
    "EnvironmentConfiguration",
    "NetworkRetrieval",
    "Delegation",
    "Unknown",
}


#: The prompt-level vocabulary of `data-model.md` §4 — what a `Start` node's
#: condition may say a prompt is about.
PROCESS_TYPES = {
    "BugFix",
    "FeatureAddition",
    "Enhancement",
    "Investigation",
    "Documentation",
    "ReleaseManagement",
    "Unknown",
}


def test_activity_class_is_exactly_the_closed_set_fr_019_names() -> None:
    assert {member.value for member in ActivityClass} == FR_019_ACTIVITY_CLASSES


def test_process_type_is_exactly_the_prompt_level_vocabulary() -> None:
    assert {member.value for member in ProcessType} == PROCESS_TYPES


def test_shipped_pack_gives_every_vocabulary_member_a_definition() -> None:
    """The enums are the symbols; the pack is where their meaning is curated."""
    pack = load_pack()
    vocabulary = FR_019_ACTIVITY_CLASSES | PROCESS_TYPES

    assert vocabulary <= pack.concepts.keys()
    assert all(pack.concepts[label].definition for label in vocabulary)


def test_load_pack_imports_no_semantic_web_library() -> None:
    """FR-024: the digest happened by hand, so loading reaches no RDF stack."""
    load_pack()

    assert not {"rdflib", "owlready2", "pyshacl"} & sys.modules.keys()


def test_unparseable_pack_raises_pack_error_naming_the_file(tmp_path: Path) -> None:
    """A hand-edit that broke the JSON is reported, not surfaced as a json error."""
    broken = tmp_path / "seon_activities.json"
    broken.write_text('{"version": 1, "concepts": [', encoding="utf-8")

    with pytest.raises(PackError) as raised:
        load_pack(broken)

    assert raised.value.pack == str(broken)


def test_entry_off_the_curated_shape_raises_pack_error(tmp_path: Path) -> None:
    """label/definition/parent is the shape; a concept missing one is refused."""
    drifted = tmp_path / "seon_activities.json"
    drifted.write_text(
        json.dumps(
            {
                "version": 1,
                "concepts": [{"label": "Inspection", "parent": "EngineeringActivity"}],
                "relations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PackError) as raised:
        load_pack(drifted)

    assert "definition" in raised.value.problem


def test_pack_that_dropped_a_vocabulary_member_is_refused_whole(tmp_path: Path) -> None:
    """The enums are closed, so a hand-edit that thins the pack is a defect."""
    thinned = json.loads(SEON_ACTIVITIES.read_text(encoding="utf-8"))
    thinned["concepts"] = [c for c in thinned["concepts"] if c["label"] != "Delegation"]
    edited = tmp_path / "seon_activities.json"
    edited.write_text(json.dumps(thinned), encoding="utf-8")

    with pytest.raises(PackError) as raised:
        load_pack(edited)

    assert "Delegation" in raised.value.problem
