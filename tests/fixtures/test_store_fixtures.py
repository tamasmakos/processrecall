"""The v1 store fixture checks itself: still stamped `1`, and still whole to the reader.

`tests/fixtures/stores/v1.db` is a frozen artifact — a populated episodic index exactly
as schema version `1` wrote it — and it is the input the forward migration is tested
against. It is never regenerated from the current declaration: a fixture rebuilt by the
build under test would follow `SCHEMA_VERSION` up, and the migration would then be
measured against a store that had already migrated. Its provenance is
`tests/fixtures/stores/build_v1_fixture.py`, the one-off script that wrote it through
`open_index`, `SequenceIdentity` and `SQLiteEpisodicStore` at v1 — not something this
test suite runs, since running it again is exactly the regeneration the fixture must
never undergo.

Every assertion runs on a copy under ``tmp_path``. Opening the index is a write —
`open_index` stamps `meta`, and once the version moves it will migrate — so a test that
opened the committed file would consume the fixture every other test shares.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from processrecall.graph.episodic import open_index
from processrecall.graph.store import SQLiteEpisodicStore

#: The frozen store, as committed.
V1_STORE = Path(__file__).parent / "stores" / "v1.db"

#: What the fixture was populated with. Spelled here because a migration that loses rows
#: has to fail as a count, not as a matter of interpretation.
SEQUENCE_COUNT = 3
STEP_COUNT = 9

#: Steps recorded per prompt, taken from the frozen file rather than from `sequence` or
#: `steps` themselves — `Sequence.step_count` is computed from the same `steps` rows
#: `SQLiteEpisodicStore.steps` returns, so comparing the two against each other can never
#: fail regardless of what either one actually holds.
STEPS_PER_PROMPT = {"p1": 4, "p2": 3, "p3": 2}

#: The one project every turn in the fixture was recorded under.
PROJECT_KEY = "processrecall"

#: The fixture's one annotation, on the `c1`/`p1` edge from grep to cat
#: (`build_v1_fixture.py`). Spelled here so the reader test checks the row that was
#: written, not merely that some row came back.
ANNOTATION_EDGE_KEY = "Search/grep/py -> Inspection/cat/py"

#: Later than every turn in the fixture. `sequences_before` is the only enumeration of a
#: whole project the reader offers, so its cutoff has to be out of the way.
AFTER_THE_FIXTURE = datetime(2100, 1, 1, tzinfo=UTC)

#: The tables schema version `2` adds. None of them may be here: a fixture that already
#: carried one would let a migration test pass over a store it had not migrated.
V2_TABLES = frozenset(
    {"code_entities", "code_relations", "inferences", "agents", "step_touches", "step_consumes"}
)


@pytest.fixture
def v1_copy(tmp_path: Path) -> Path:
    """A scratch copy of the frozen store, so a test may open — and so write — it."""
    copy = tmp_path / "v1.db"
    shutil.copyfile(V1_STORE, copy)
    return copy


def test_the_fixture_is_stamped_at_schema_version_one(v1_copy: Path) -> None:
    """The stamp is what `open_index` refuses a store on, so it is the fixture's premise."""
    with closing(sqlite3.connect(v1_copy)) as connection:
        stamp = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    assert stamp == ("1",)


def test_the_fixture_carries_none_of_the_v2_tables(v1_copy: Path) -> None:
    """A store already holding a v2 table is a migrated store wearing the v1 stamp."""
    with closing(sqlite3.connect(v1_copy)) as connection:
        names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert not names & V2_TABLES


def test_the_current_reader_reads_every_turn_and_step_of_the_fixture(v1_copy: Path) -> None:
    """A v1 store the shipped reader cannot enumerate is no use as a migration input."""
    with closing(open_index(v1_copy)) as connection:
        store = SQLiteEpisodicStore(connection)
        keys = store.sequences_before(AFTER_THE_FIXTURE, PROJECT_KEY)
        assert len(keys) == SEQUENCE_COUNT
        for key in keys:
            turn = store.sequence(key)
            assert turn is not None, key
            recorded = store.steps(key)
            expected = STEPS_PER_PROMPT[key.prompt_id]
            assert len(recorded) == expected, key
            assert turn.step_count == expected, key
        assert len(tuple(store.iter_steps())) == STEP_COUNT


def test_the_current_reader_reads_the_fixtures_annotation(v1_copy: Path) -> None:
    """An annotation the migration must carry forward has to be there to lose."""
    with closing(open_index(v1_copy)) as connection:
        store = SQLiteEpisodicStore(connection)
        recorded = store.annotations_for(PROJECT_KEY)
    assert len(recorded) == 1
    assert recorded[0].edge_key == ANNOTATION_EDGE_KEY
