"""Provenance for `v1.db`: the one-off script that wrote it, not a fixture.

`test_store_fixtures.py` explains why the committed file is never rebuilt from
the current code — a rebuild would follow `SCHEMA_VERSION` up and the
migration test would then measure against a store already migrated. This
script is kept instead so the claim "a populated v1 store" is checkable: it
writes through the real schema-version-1 path — `open_index`,
`SequenceIdentity` and `SQLiteEpisodicStore` — and nothing else touches the
file it produces.

Run by hand, never by the test suite or any build step::

    uv run python tests/fixtures/stores/build_v1_fixture.py

Writes, in order:

- ``c1``/``p1`` (epoch 0, ``BugFix``, closed, success): 4 steps —
  Search/grep/py, Inspection/cat/py, ChangeImplementation/edit/py,
  ArtifactEvaluation/pytest/py.
- ``c1``/``p2`` (epoch 0, ``FeatureAddition``, closed, failure): 3 steps —
  Inspection/cat/md, ChangeImplementation/edit/py, ArtifactEvaluation/pytest/py.
- ``c2``/``p3`` (epoch 1, ``Investigation``, left open): 2 steps —
  Search/rg/--, Inspection/cat/py. The epoch comes from
  ``SequenceIdentity.begin("clear")``, the only way a v1 store ever gets a row
  in ``epochs`` — it leaves ``('c2', 1)`` behind.
- one annotation, on the ``c1``/``p1`` edge
  ``"Search/grep/py -> Inspection/cat/py"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from processrecall.config import ActivityClass, ProcessType
from processrecall.graph.annotations import Annotation
from processrecall.graph.episodic import SequenceIdentity, open_index
from processrecall.graph.store import EpisodicStep, Sequence, SequenceKey, SQLiteEpisodicStore

#: The frozen file this script's output replaces.
TARGET = Path(__file__).parent / "v1.db"

#: The one project every sequence below is recorded under.
PROJECT_KEY = "processrecall"


@dataclass(frozen=True, slots=True)
class _StepSpec:
    """Everything `EpisodicStep` needs but the sequence key and position."""

    node_key: str
    activity_class: ActivityClass
    program: str
    template: str
    files: tuple[str, ...]
    occurred_at: datetime


_P1_STEPS = (
    _StepSpec(
        "Search/grep/py",
        ActivityClass.SEARCH,
        "grep",
        "grep <File>",
        ("processrecall/graph/store.py",),
        datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC),
    ),
    _StepSpec(
        "Inspection/cat/py",
        ActivityClass.INSPECTION,
        "cat",
        "cat <File>",
        ("processrecall/graph/store.py",),
        datetime(2026, 9, 13, 10, 0, 1, tzinfo=UTC),
    ),
    _StepSpec(
        "ChangeImplementation/edit/py",
        ActivityClass.CHANGE_IMPLEMENTATION,
        "edit",
        "edit <File>",
        ("processrecall/graph/store.py",),
        datetime(2026, 9, 13, 10, 0, 2, tzinfo=UTC),
    ),
    _StepSpec(
        "ArtifactEvaluation/pytest/py",
        ActivityClass.ARTIFACT_EVALUATION,
        "pytest",
        "pytest <File>",
        ("tests/graph/test_store.py",),
        datetime(2026, 9, 13, 10, 0, 3, tzinfo=UTC),
    ),
)

_P2_STEPS = (
    _StepSpec(
        "Inspection/cat/md",
        ActivityClass.INSPECTION,
        "cat",
        "cat <File>",
        ("contracts/storage.md",),
        datetime(2026, 9, 13, 11, 0, 0, tzinfo=UTC),
    ),
    _StepSpec(
        "ChangeImplementation/edit/py",
        ActivityClass.CHANGE_IMPLEMENTATION,
        "edit",
        "edit <File>",
        ("processrecall/graph/episodic.py",),
        datetime(2026, 9, 13, 11, 0, 1, tzinfo=UTC),
    ),
    _StepSpec(
        "ArtifactEvaluation/pytest/py",
        ActivityClass.ARTIFACT_EVALUATION,
        "pytest",
        "pytest <File>",
        ("tests/graph/test_episodic.py",),
        datetime(2026, 9, 13, 11, 0, 2, tzinfo=UTC),
    ),
)

_P3_STEPS = (
    _StepSpec(
        "Search/rg/--",
        ActivityClass.SEARCH,
        "rg",
        "rg <Pattern>",
        (),
        datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC),
    ),
    _StepSpec(
        "Inspection/cat/py",
        ActivityClass.INSPECTION,
        "cat",
        "cat <File>",
        ("processrecall/guidance/render.py",),
        datetime(2026, 9, 13, 12, 0, 1, tzinfo=UTC),
    ),
)


def _write_steps(
    store: SQLiteEpisodicStore, key: SequenceKey, prompt_id: str, specs: tuple[_StepSpec, ...]
) -> None:
    """Record every one of *specs* against *key*, in position order."""
    for position, spec in enumerate(specs):
        store.record(
            EpisodicStep(
                dedup_key=f"{prompt_id}-{position}",
                sequence_key=key,
                position=position,
                node_key=spec.node_key,
                activity_class=spec.activity_class,
                template=spec.template,
                occurred_at=spec.occurred_at,
                program=spec.program,
                files=spec.files,
                result_snippet=f"{spec.program} finished success",
                outcome="success",
            )
        )


def build(path: Path) -> None:
    """Write the fixture at *path*, refusing to clobber one already there."""
    if path.exists():
        raise FileExistsError(f"{path} already exists; delete it before regenerating")
    connection = open_index(path)
    try:
        store = SQLiteEpisodicStore(connection)

        c1 = SequenceIdentity(connection, "c1")
        key_p1 = c1.key("p1")
        store.open_sequence(
            Sequence(
                key=key_p1,
                project_dir_key=PROJECT_KEY,
                started_at=datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC),
                process_type=ProcessType.BUG_FIX,
            )
        )
        _write_steps(store, key_p1, "p1", _P1_STEPS)
        store.derive_outcome(key_p1, "success")
        store.close_sequence(key_p1, datetime(2026, 9, 13, 10, 10, 0, tzinfo=UTC))

        key_p2 = c1.key("p2")
        store.open_sequence(
            Sequence(
                key=key_p2,
                project_dir_key=PROJECT_KEY,
                started_at=datetime(2026, 9, 13, 11, 0, 0, tzinfo=UTC),
                process_type=ProcessType.FEATURE_ADDITION,
            )
        )
        _write_steps(store, key_p2, "p2", _P2_STEPS)
        store.derive_outcome(key_p2, "failure")
        store.close_sequence(key_p2, datetime(2026, 9, 13, 11, 10, 0, tzinfo=UTC))

        c2 = SequenceIdentity(connection, "c2")
        c2.begin("clear")  # rotates c2 to epoch 1, leaving the `epochs` row (c2, 1)
        key_p3 = c2.key("p3")
        store.open_sequence(
            Sequence(
                key=key_p3,
                project_dir_key=PROJECT_KEY,
                started_at=datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC),
                process_type=ProcessType.INVESTIGATION,
            )
        )
        _write_steps(store, key_p3, "p3", _P3_STEPS)
        # p3 is left open, at the default `derived_outcome` of "neutral".

        store.write_annotation(
            PROJECT_KEY,
            Annotation(
                edge_key="Search/grep/py -> Inspection/cat/py",
                text="grep found the write path before cat confirmed the schema.",
                author="test-fixture",
                written_at=datetime(2026, 9, 13, 10, 5, 0, tzinfo=UTC),
            ),
        )

        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    finally:
        connection.close()


if __name__ == "__main__":
    build(TARGET)
