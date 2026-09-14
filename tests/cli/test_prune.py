"""`processrecall prune`: the only path that deletes episodic history (FR-057).

Every test below drives the command line, because that is the whole of the
surface: pruning is an operator's decision, and a cutoff, a confirmation and a
re-derived graph are what an operator sees of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.cli.conftest import record_turn, run_cli, snapshot_document

#: The two turns every test records: one old enough to prune, one not.
OLD = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
RECENT = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)

#: The cutoff `--before` is given, between the two.
CUTOFF = "2026-09-10"


def edges_without_row_ids(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The edges of *document*, minus the episodic rows each was derived from.

    `supporting_steps` names rows by their ``step_id``, which counts every row
    a store ever held — pruned ones included. Two stores holding the same turns
    therefore number them differently, so it is a reference into the index
    rather than a property of the graph folded out of it.
    """
    return [
        {name: value for name, value in edge.items() if name != "supporting_steps"}
        for edge in document["edges"]
    ]


def record_both_turns(home: Path, project: Path) -> None:
    """The fixture history: one turn before `CUTOFF` and one after it."""
    record_turn(home, project, "Inspection/Read", "Search/Grep", prompt_id="old", started_at=OLD)
    record_turn(home, project, "ChangeImplementation/Edit", prompt_id="recent", started_at=RECENT)


def test_prune_removes_the_episodes_before_the_cutoff_and_reports_the_counts(
    tmp_path: Path,
) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_both_turns(home, project)

    finished = run_cli(home, "prune", "--project", str(project), "--before", CUTOFF, "--yes")

    assert finished.returncode == 0, finished.stderr
    assert "steps=2" in finished.stdout
    assert "sequences=1" in finished.stdout


def test_the_graph_left_behind_is_the_one_the_retained_episodes_derive(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_both_turns(home, project)
    finished = run_cli(home, "prune", "--project", str(project), "--before", CUTOFF, "--yes")
    assert finished.returncode == 0, finished.stderr

    pruned = snapshot_document(project / ".processrecall" / "graph.json")

    retained_only = tmp_path / "retained-only"
    record_turn(
        retained_only, project, "ChangeImplementation/Edit", prompt_id="recent", started_at=RECENT
    )
    assert run_cli(retained_only, "rebuild", "--project", str(project)).returncode == 0
    from_scratch = snapshot_document(project / ".processrecall" / "graph.json")
    # `edges` and `episode_high_water` are excluded from the whole-document
    # comparison: `supporting_steps` names rows by `step_id`, which the pruned
    # store and a from-scratch one number differently for the same turns
    # (see `edges_without_row_ids`), and `episode_high_water` names the last
    # `step_id` each store's own history reached — both are index references,
    # not properties of the graph folded out of the index.
    ignored = {"edges", "episode_high_water"}
    assert {k: v for k, v in pruned.items() if k not in ignored} == {
        k: v for k, v in from_scratch.items() if k not in ignored
    }
    assert edges_without_row_ids(pruned) == edges_without_row_ids(from_scratch)


def test_an_unanswered_confirmation_removes_nothing(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_both_turns(home, project)

    finished = run_cli(home, "prune", "--project", str(project), "--before", CUTOFF)

    assert finished.returncode == 0, finished.stderr
    assert "steps=2" in finished.stdout
    assert "nothing removed" in finished.stdout
    still_there = run_cli(home, "prune", "--project", str(project), "--before", CUTOFF, "--yes")
    assert "steps=2" in still_there.stdout


def test_answering_the_confirmation_removes_the_episodes(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_both_turns(home, project)

    finished = run_cli(home, "prune", "--project", str(project), "--before", CUTOFF, answer="y\n")

    assert finished.returncode == 0, finished.stderr
    assert "removed" in finished.stdout
    afterwards = run_cli(home, "prune", "--project", str(project), "--before", CUTOFF, "--yes")
    assert "steps=0" in afterwards.stdout
    assert "sequences=0" in afterwards.stdout


def test_prune_refuses_to_run_without_a_cutoff(tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    record_both_turns(home, project)

    finished = run_cli(home, "prune", "--project", str(project), "--yes")

    assert finished.returncode != 0
    assert "--before" in finished.stderr
