"""The semantic rows: the keys that stay portable and the containment that stays total."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import perf_counter

import pytest

from processrecall.cli.derive import SemanticPass, derive_relations, derive_semantic
from processrecall.graph.semantic import (
    CodeEntity,
    CodeRelation,
    EntityKind,
    TouchedEntity,
    containment,
    entity_key,
    touched_entities,
)
from processrecall.graph.store import EpisodicStep

from .conftest import make_aggregate_step, sequence

SEEN = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class FakeCounters:
    """A counter sink that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def edit_of(*files: str) -> tuple[EpisodicStep, ...]:
    """One recorded edit of *files*, which is what the episodic layer says was touched."""
    step = make_aggregate_step(
        "ChangeImplementation/Edit/py", position=0, step_id=1, key=sequence("p1"), files=files
    )
    return (step,)


def make_entity(key: str, kind: EntityKind) -> CodeEntity:
    """One code entity, with only the two fields the key rules concern exposed."""
    return CodeEntity(
        entity_key=key, kind=kind, fingerprint="sha256:1", first_seen=SEEN, last_seen=SEEN
    )


LINES_PER_FILE = 100
SYNTHETIC_LINES = 50_000
SYNTHETIC_FILES = SYNTHETIC_LINES // LINES_PER_FILE
CALLS_PER_FILE = (LINES_PER_FILE // 4) // 2


def synthetic_source(index: int) -> str:
    """One module of the synthetic tree: helpers, and workers that call them by name.

    Every function is four lines including the blank line after it, and the
    text ends in a trailing newline, so a module is exactly `LINES_PER_FILE`
    lines and the tree is exactly `SYNTHETIC_LINES`. The calls resolve inside
    the module, so relation derivation does the resolution work a real tree
    asks of it rather than only the parse.
    """
    lines: list[str] = []
    for number in range(LINES_PER_FILE // 4):
        if number % 2 == 0:
            lines += [f"def helper_{index}_{number}(step):", "    return step", "", ""]
        else:
            lines += [
                f"def work_{index}_{number}(step):",
                f"    return helper_{index}_{number - 1}(step)",
                "",
                "",
            ]
    return "\n".join(lines) + "\n"


def write_synthetic_tree(root: Path) -> tuple[str, ...]:
    """A `SYNTHETIC_LINES`-line tree under *root*, as the keys work touched."""
    package = root / "pkg"
    package.mkdir()
    keys = []
    for index in range(SYNTHETIC_FILES):
        (package / f"module_{index}.py").write_text(synthetic_source(index), encoding="utf-8")
        keys.append(f"pkg/module_{index}.py")
    return tuple(keys)


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


def test_calls_relation_refuses_a_null_target() -> None:
    """A resolved `calls` row with no target is indistinguishable from a parse failure (R16)."""
    with pytest.raises(ValueError, match="unresolved"):
        CodeRelation(source_key="pkg/module.py#work", relation="calls", target_key=None)


def test_unresolved_call_refuses_a_target_key() -> None:
    """An unresolved call is keyed by the bare name alone, never by a resolved target (R16)."""
    with pytest.raises(ValueError, match="bare name"):
        CodeRelation(
            source_key="pkg/module.py#work",
            relation="unresolved_call",
            target_key="pkg/module.py#helper",
            target_name="helper",
        )


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


def test_touched_edges_survive_entity_deletion() -> None:
    """A deleted symbol leaves its touched edges readable rather than dangling (Edge Cases)."""
    surviving = make_entity("processrecall/graph/semantic.py", "file")

    read = touched_entities(
        ["processrecall/graph/semantic.py#deleted", "processrecall/graph/semantic.py"],
        [surviving],
    )

    assert read == (
        TouchedEntity(
            entity_key="processrecall/graph/semantic.py#deleted", entity=None, file=surviving
        ),
        TouchedEntity(
            entity_key="processrecall/graph/semantic.py", entity=surviving, file=surviving
        ),
    )


def test_renamed_file_does_not_repoint_a_touched_edge() -> None:
    """A rename is a new entity, never a re-pointing of the old key (Edge Cases)."""
    renamed = make_entity("processrecall/graph/renamed.py", "file")

    read = touched_entities(["processrecall/graph/semantic.py"], [renamed])

    assert read == (
        TouchedEntity(entity_key="processrecall/graph/semantic.py", entity=None, file=None),
    )


def test_renamed_file_stays_unmatched_through_derivation(tmp_path: Path) -> None:
    """A rename derives a new key even with unchanged content; the old key stays unmatched.

    Exercises the real derivation pipeline (`derive_semantic`, `_touched_files`,
    `_readable_text`) rather than a hand-built entity list, so the rename Edge
    Case is pinned on the path it actually runs: the fingerprint alone cannot
    repoint a touched edge, because matching is by key (Edge Cases).
    """
    source = tmp_path / "pkg" / "module.py"
    source.parent.mkdir()
    source.write_text("def work() -> None:\n    return None\n", encoding="utf-8")
    counters = FakeCounters()
    before = derive_semantic(
        SemanticPass(project_dir=tmp_path, steps=edit_of("pkg/module.py")), counters
    )

    source.rename(tmp_path / "pkg" / "renamed.py")

    after = derive_semantic(
        SemanticPass(project_dir=tmp_path, steps=edit_of("pkg/renamed.py")), counters
    )

    assert [row.entity_key for row in after] == ["pkg/renamed.py"]
    assert after[0].fingerprint == before[0].fingerprint

    read = touched_entities(["pkg/module.py"], after)
    assert read == (TouchedEntity(entity_key="pkg/module.py", entity=None, file=None),)


def test_unchanged_file_is_skipped_on_second_pass(tmp_path: Path) -> None:
    """Derivation is incremental on content: an unchanged file is not parsed twice (FR-019)."""
    source = tmp_path / "pkg" / "module.py"
    source.parent.mkdir()
    source.write_text("def work() -> None:\n    return None\n", encoding="utf-8")
    counters = FakeCounters()
    touched = edit_of("pkg/module.py")

    first = derive_semantic(SemanticPass(project_dir=tmp_path, steps=touched), counters)
    assert [(row.entity_key, row.kind, row.extension, row.language) for row in first] == [
        ("pkg/module.py", "file", "py", "python")
    ]
    assert first[0].fingerprint.startswith("sha256:")
    assert first[0].support == 1

    unchanged = {row.entity_key: row.fingerprint for row in first}
    second = derive_semantic(
        SemanticPass(project_dir=tmp_path, steps=touched, fingerprints=unchanged), counters
    )
    assert second == ()
    assert counters.counted == Counter({"semantic_files_parsed": 1, "semantic_files_skipped": 1})

    source.write_text("def work() -> None:\n    return 1\n", encoding="utf-8")
    third = derive_semantic(
        SemanticPass(project_dir=tmp_path, steps=touched, fingerprints=unchanged), counters
    )
    assert len(third) == 1
    assert third[0].fingerprint != first[0].fingerprint
    assert counters.counted["semantic_files_parsed"] == 2


def test_file_that_cannot_be_read_is_counted_and_passed_over(tmp_path: Path) -> None:
    """A touched file gone since the step is counted, not raised on (FR-019)."""
    counters = FakeCounters()

    derived = derive_semantic(
        SemanticPass(project_dir=tmp_path, steps=edit_of("pkg/deleted.py")), counters
    )

    assert derived == ()
    assert counters.counted == Counter({"semantic_parse_failed": 1})


CALLING_SOURCE = """def work(step):
    return helper(transform(step))


def helper(step):
    return step
"""


def test_unresolved_call_keeps_target_name(tmp_path: Path) -> None:
    """FR-018: a call nothing answers is its own relation, never a `calls` row with no target."""
    source = tmp_path / "pkg" / "module.py"
    source.parent.mkdir()
    source.write_text(CALLING_SOURCE, encoding="utf-8")
    counters = FakeCounters()

    rows = derive_relations(
        SemanticPass(project_dir=tmp_path, steps=edit_of("pkg/module.py")), counters
    )

    assert rows == (
        CodeRelation(
            source_key="pkg/module.py#work",
            relation="calls",
            target_key="pkg/module.py#helper",
        ),
        CodeRelation(
            source_key="pkg/module.py#work",
            relation="unresolved_call",
            target_name="transform",
        ),
    )
    assert counters.counted == Counter({"semantic_unresolved_call": 1})


@pytest.mark.slow
def test_fifty_thousand_line_tree_derives_within_sixty_seconds(tmp_path: Path) -> None:
    """SC-008: a 50,000-line tree derives inside the budget, and re-derives by skipping."""
    touched = edit_of(*write_synthetic_tree(tmp_path))
    first_pass = SemanticPass(project_dir=tmp_path, steps=touched)
    counters = FakeCounters()

    started = perf_counter()
    entities = derive_semantic(first_pass, counters)
    relations = derive_relations(first_pass, counters)
    elapsed = perf_counter() - started

    assert len(entities) == SYNTHETIC_FILES
    assert counters.counted["semantic_unresolved_call"] == 0
    assert len(relations) >= CALLS_PER_FILE * SYNTHETIC_FILES
    assert elapsed < 60.0

    skipping = FakeCounters()
    second_pass = SemanticPass(
        project_dir=tmp_path,
        steps=touched,
        fingerprints={row.entity_key: row.fingerprint for row in entities},
    )

    assert derive_semantic(second_pass, skipping) == ()
    assert skipping.counted["semantic_files_skipped"] == SYNTHETIC_FILES
    assert skipping.counted["semantic_files_parsed"] == 0
