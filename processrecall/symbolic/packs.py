"""The concept index: the curated activity vocabulary, loaded from data.

FR-019 fixes the activity classes and FR-024 makes them a hand-edited pack —
digested once from SEON's relevant classes rather than reasoned over at runtime,
so nothing here imports a semantic-web library and nothing here reaches the
network. The closed sets the rest of the package spells are
:class:`~processrecall.config.ActivityClass` and
:class:`~processrecall.config.ProcessType` — declared at the bottom of the
layering because the trajectory reads them (R17); the pack here is where each
member's meaning and parent live.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from processrecall.config import ActivityClass, ProcessType
from processrecall.exceptions import PackError

#: The one pack this package ships, beside the code that reads it.
SEON_ACTIVITIES = Path(__file__).parent / "data" / "seon_activities.json"

#: A curated dataclass — either ``Concept`` or ``Relation`` — for `_index_by_label`.
_Entry = TypeVar("_Entry")


@dataclass(frozen=True, slots=True)
class Concept:
    """One curated class: what the label means and what it specialises."""

    label: str
    definition: str
    parent: str | None


@dataclass(frozen=True, slots=True)
class Relation:
    """One curated predicate, with the classes it may hold between."""

    label: str
    definition: str
    domain: str | None = None
    range: str | None = None


@dataclass(frozen=True, slots=True)
class ConceptPack:
    """A loaded pack: its classes and its predicates, both keyed by label."""

    version: int
    concepts: Mapping[str, Concept]
    relations: Mapping[str, Relation]


def load_pack(path: Path = SEON_ACTIVITIES) -> ConceptPack:
    """Read a curated pack from *path*.

    Plain ``json`` and nothing else: the digest from SEON happened by hand,
    once, so no RDF library, reasoner or download participates at runtime.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        pack = ConceptPack(
            version=raw["version"],
            concepts=_index_by_label(Concept, raw["concepts"], path),
            relations=_index_by_label(Relation, raw["relations"], path),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as defect:
        raise PackError(str(path), str(defect)) from defect
    _require_every_symbol_defined(pack, path)
    return pack


def _index_by_label(cls: type[_Entry], entries: list[dict], path: Path) -> Mapping[str, _Entry]:
    """Build a label-keyed mapping, refusing a pack that repeats a label."""
    index: dict[str, _Entry] = {}
    for entry in entries:
        instance = cls(**entry)
        label = entry["label"]
        if label in index:
            raise PackError(str(path), f"duplicate label {label}")
        index[label] = instance
    return index


def _require_every_symbol_defined(pack: ConceptPack, path: Path) -> None:
    """Refuse *pack* unless it defines every member of both closed sets.

    The enums are what the code spells and the pack is where those symbols mean
    something; a hand-edit that drops one leaves a class the graph can produce
    and nobody can explain. Loading is all-or-nothing, so this is a refusal
    rather than a gap.
    """
    vocabulary = {str(member) for member in (*ActivityClass, *ProcessType)}
    if undefined := vocabulary - pack.concepts.keys():
        raise PackError(str(path), f"no concept defines {', '.join(sorted(undefined))}")
