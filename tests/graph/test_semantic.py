"""The semantic rows: the keys that stay portable and the containment that stays total."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from processrecall.graph.semantic import (
    CodeEntity,
    CodeRelation,
    EntityKind,
    containment,
    entity_key,
)

SEEN = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def make_entity(key: str, kind: EntityKind) -> CodeEntity:
    """One code entity, with only the two fields the key rules concern exposed."""
    return CodeEntity(
        entity_key=key, kind=kind, fingerprint="sha256:1", first_seen=SEEN, last_seen=SEEN
    )


def test_entity_key_rejects_absolute_path() -> None:
    """An absolute path in the column is a privacy leak and a portability bug at once (FR-017)."""
    for absolute in (PurePosixPath("/home/dev/repo/pkg/graph.py"), PureWindowsPath(r"C:\repo\pkg")):
        with pytest.raises(ValueError, match="repository-relative"):
            entity_key(absolute)


def test_entity_key_rejects_a_dotdot_escape() -> None:
    """A `..` component escapes the repository the same way an absolute path does (FR-017)."""
    with pytest.raises(ValueError, match="repository-relative"):
        entity_key(PurePosixPath("../../home/dev/secret.py"))


def test_entity_key_rejects_a_hash_in_the_path() -> None:
    """A `#` in the path itself would make `file_key` split at the wrong place (FR-017)."""
    with pytest.raises(ValueError, match="repository-relative"):
        entity_key(PurePosixPath("processrecall/graph/weird#file.py"))


def test_entity_key_spells_a_symbol_with_forward_slashes_on_every_platform() -> None:
    """A key written on Windows has to read the same everywhere it travels (FR-017)."""
    key = entity_key(PureWindowsPath(r"processrecall\graph\semantic.py"), "CodeEntity.support")
    assert key == "processrecall/graph/semantic.py#CodeEntity.support"


def test_file_entity_refuses_a_symbol_key() -> None:
    """A `file` row carrying a `#` claims to be a symbol and a file at once (FR-017)."""
    with pytest.raises(ValueError, match="file"):
        make_entity("processrecall/graph/semantic.py#entity_key", "file")


def test_symbol_entity_refuses_a_file_key() -> None:
    """A symbol is keyed by the file it lives in and its own name, never the file alone."""
    with pytest.raises(ValueError, match="function"):
        make_entity("processrecall/graph/semantic.py", "function")


def test_symbol_without_its_file_entity_is_refused() -> None:
    """Containment is total: a symbol no file holds is unreachable from the tree (FR-018)."""
    with pytest.raises(ValueError, match="file entity"):
        containment([make_entity("processrecall/graph/semantic.py#CodeEntity", "class")])


def test_containment_relates_each_file_to_the_symbols_it_holds() -> None:
    """The `contains` rows are derived from the keys, not declared a second time (FR-018)."""
    rows = containment(
        [
            make_entity("processrecall/graph/semantic.py", "file"),
            make_entity("processrecall/graph/semantic.py#CodeEntity", "class"),
        ]
    )
    assert rows == (
        CodeRelation(
            source_key="processrecall/graph/semantic.py",
            relation="contains",
            target_key="processrecall/graph/semantic.py#CodeEntity",
        ),
    )
