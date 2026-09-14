"""FR-004/SC-011: a second harness drives the whole pipeline, through its own pack.

What US8 claims is that nothing below the trajectory seam knows which harness
produced an action. A harness brings two things: an adapter that yields
:class:`~processrecall.trajectory.event.TrajectoryEvent`s, and a vocabulary pack
that names its own tools. `FakeSource` is that second harness and
``tests/fixtures/vocab_fake.json`` is its pack — a deliberately different
vocabulary, in which ``Read`` is ``open_file`` and ``Bash`` is ``run_command``.

The proof is a comparison rather than an assertion: one scripted session,
recorded twice — once in the fixture harness's spelling through its own pack,
once in Claude Code's spelling through the shipped one — must leave the same
episodic rows behind, fold to the same graph and leave guidance the same
neighbourhood to serve. Only the fields that carry a harness's own name for a
tool may differ, which is exactly what the pack exists to absorb.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from processrecall.graph import record as record_module
from processrecall.graph.abstract import AbstractGraph, aggregate
from processrecall.graph.episodic import open_index
from processrecall.graph.keys import group_by_sequence
from processrecall.graph.record import record_event
from processrecall.graph.store import EpisodicStep, SQLiteEpisodicStore
from processrecall.guidance.locate import locate
from processrecall.guidance.neighborhood import extract
from processrecall.trajectory.vocabulary import CLAUDE_CODE, load_vocabulary
from tests.trajectory.factories import FakeSource

#: The second harness's own vocabulary pack, beside the fixture corpus (FR-004).
FAKE_PACK = Path(__file__).resolve().parents[1] / "fixtures" / "vocab_fake.json"

#: The coarsest level: the one at which two harnesses' node keys are comparable
#: at all, since a key below it is named after the tool each harness ran.
LEVEL = "class"

#: The project the scripted session works in.
PROJECT_DIR = "/work/demo"


@dataclass(frozen=True, slots=True)
class _Call:
    """One tool call of the scripted session, as its harness spells it."""

    tool_name: str
    arguments: Mapping[str, object]
    result: str


#: What each harness calls the tool that performs the same activity: the fixture
#: harness's name, which only its own pack tables, against Claude Code's.
_SAME_TOOL = {
    "open_file": "Read",
    "find_in_files": "Grep",
    "apply_patch": "Edit",
    "run_command": "Bash",
}

#: One session of work, written in the second harness's vocabulary: a look, a
#: search, an edit and a test run, twice, so every move is observed more than
#: once. ``run_command`` is the entry that defers to the command grammar, so the
#: session also crosses the one tool a pack cannot classify by name (FR-017).
_SESSION = (
    _Call("open_file", {"file_path": f"{PROJECT_DIR}/app.py"}, "def main() -> None: ..."),
    _Call("find_in_files", {"pattern": "main", "path": PROJECT_DIR}, "app.py:1"),
    _Call("apply_patch", {"file_path": f"{PROJECT_DIR}/app.py"}, "Applied 1 edit"),
    _Call("run_command", {"command": "pytest -q"}, "1 passed"),
    _Call("open_file", {"file_path": f"{PROJECT_DIR}/app.py"}, "def main() -> None: ..."),
    _Call("find_in_files", {"pattern": "main", "path": PROJECT_DIR}, "app.py:1"),
    _Call("apply_patch", {"file_path": f"{PROJECT_DIR}/app.py"}, "Applied 1 edit"),
    _Call("run_command", {"command": "pytest -q"}, "1 passed"),
)


@dataclass(frozen=True, slots=True)
class _Harness:
    """One harness driving the pipeline: its adapter, its pack, its store."""

    source: FakeSource
    pack: Path
    root: Path


@dataclass(frozen=True, slots=True)
class _Run:
    """What one harness's session left behind: the rows, and what they fold to."""

    steps: tuple[EpisodicStep, ...]
    graph: AbstractGraph


def _drive(harness: _Harness) -> _Run:
    """Record everything *harness* has to say, through its own pack, and fold it."""
    connection = open_index(harness.root / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    try:
        with pytest.MonkeyPatch.context() as pointed_at:
            pointed_at.setattr(record_module, "load_vocabulary", lambda: load_vocabulary(harness.pack))
            for event in harness.source.events():
                record_event(event, connection)
        steps = tuple(store.iter_steps())
        assert len(steps) == len(_SESSION), "the session must have actually landed"
        opened = {key: store.sequence(key) for key in group_by_sequence(steps)}
        run = _Run(
            steps=steps,
            graph=aggregate(
                steps, LEVEL, {key: seq for key, seq in opened.items() if seq is not None}
            ),
        )
        assert run.graph.nodes, "a landed session must fold to a non-empty graph"
        return run
    finally:
        connection.close()


def _in_claude_code(calls: Sequence[_Call]) -> tuple[_Call, ...]:
    """*calls* as the shipped harness spells them — the same work, its own names."""
    return tuple(replace(call, tool_name=_SAME_TOOL[call.tool_name]) for call in calls)


def _without_tool_names(steps: Sequence[EpisodicStep]) -> tuple[EpisodicStep, ...]:
    """*steps* with the three fields that hold the harness's own tool name blanked.

    A second harness is allowed to call its editor ``apply_patch``, and allowed
    nothing else that differs: everything left — the activity, the files, the
    outcome, the position in the prompt — must match the shipped harness's row.
    """
    return tuple(replace(step, program="", template="", node_key="") for step in steps)


@pytest.fixture
def runs(tmp_path: Path) -> tuple[_Run, _Run]:
    """The scripted session recorded by each harness, through each one's pack."""
    return (
        _drive(_Harness(FakeSource(_SESSION), FAKE_PACK, tmp_path / "fixture-harness")),
        _drive(_Harness(FakeSource(_in_claude_code(_SESSION)), CLAUDE_CODE, tmp_path / "shipped")),
    )


def _topology(graph: AbstractGraph) -> tuple[tuple[str, ...], frozenset[tuple[str, str]]]:
    """The nodes of *graph* and the moves between them — what no harness may move."""
    return tuple(graph.nodes), frozenset((edge.source, edge.target) for edge in graph.edges)


def _served(run: _Run) -> tuple[str, tuple[str, ...]]:
    """Where *run* leaves the agent standing, and the moves guidance may read there."""
    position = locate(run.steps, LEVEL)
    return position.key, tuple(edge.edge_key for edge in extract(run.graph, position, h=1).edges)


def test_the_second_harness_records_the_same_activities_through_its_own_pack(
    runs: tuple[_Run, _Run],
) -> None:
    """FR-004: recording reads the pack, not the harness, so the rows match."""
    fixture_harness, shipped = runs
    assert _without_tool_names(fixture_harness.steps) == _without_tool_names(shipped.steps)


def test_the_second_harness_folds_to_the_same_graph(runs: tuple[_Run, _Run]) -> None:
    """SC-011: the abstract layer is the harness-independent one (FR-023)."""
    fixture_harness, shipped = runs
    assert _topology(fixture_harness.graph) == _topology(shipped.graph)


def test_guidance_reads_the_same_neighbourhood_whichever_harness_recorded_it(
    runs: tuple[_Run, _Run],
) -> None:
    """SC-011: the top-down channel too — same position, same moves to serve."""
    fixture_harness, shipped = runs
    assert _served(fixture_harness) == _served(shipped)
