"""SC-010: enrichment adds labels to the graph and moves nothing in it.

The fixture corpus is recorded once and folded twice — once through
`NullClassifier`, which is the shipped path without the classify extra, and once
through the real classifier of FR-059 — and the two graphs are compared. Node
keys and `(source, target)` edges must be identical in both, because a node
identity that moved when a classifier was installed would make the optional
layer load-bearing (FR-060, FR-061); the only field allowed to differ is the
condition's `intended_activity`, and `symbol_ref`, which the fold never reads.

This exercises the fold's own additivity, not the install/uninstall of the
classify extra end to end: nothing under `processrecall/` yet writes
`rationale_label` on a recorded step (no caller reaches `load_classifiers`),
because the text a real `RationaleActivity` would read — the agent's stated
rationale — is never persisted past record time (`TrajectoryEvent.tool_call_arguments`
is read for the template and discarded). Labelling `corpus.steps` from
`step.template` here stands in for that missing write path rather than
exercising it; wiring a real one is outside this task.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from processrecall.graph.abstract import AbstractGraph, TransitionEdge, aggregate
from processrecall.graph.episodic import open_index
from processrecall.graph.keys import group_by_sequence
from processrecall.graph.record import record_event
from processrecall.graph.store import (
    EpisodicStep,
    Sequence,
    SequenceKey,
    SQLiteEpisodicStore,
)
from processrecall.symbolic.classify import Classifier, NullClassifier, RationaleActivity
from processrecall.symbolic.packs import ActivityClass
from processrecall.trajectory.transcript import TranscriptSource

#: The anonymised session the corpus is folded from (FR-073).
SESSION = Path(__file__).resolve().parents[1] / "fixtures" / "session.jsonl"

#: The finest level, so a difference in any part of a node key would show.
LEVEL = "class/program/ext"


class _AlwaysSearch:
    """The classify extra's model, reduced to the one answer this corpus needs."""

    def classify_text(self, text: str, tasks: Mapping[str, object]) -> Mapping[str, object]:
        return dict.fromkeys(tasks, str(ActivityClass.SEARCH))


class _NoOpCounters:
    """A counting sink with nowhere to count to, for `NullClassifier` in a test.

    Narrower than handing it the whole store: `NullClassifier` only ever calls
    `bump`, so that is the entire surface this exposes (ISP).
    """

    def bump(self, counter: str) -> None:
        """Do nothing: this corpus does not assert on counters."""


@dataclass(frozen=True, slots=True)
class _Corpus:
    """The recorded fixture session and what folding it needs (FR-020)."""

    steps: tuple[EpisodicStep, ...]
    sequences: Mapping[SequenceKey, Sequence]


@pytest.fixture
def corpus(tmp_path: Path) -> Iterator[_Corpus]:
    """The fixture session, recorded through the seam every source writes at."""
    connection = open_index(tmp_path / "episodes.db")
    store = SQLiteEpisodicStore(connection)
    try:
        for event in TranscriptSource(SESSION, store).events():
            record_event(event, connection)
        steps = tuple(store.iter_steps())
        opened = {key: store.sequence(key) for key in group_by_sequence(steps)}
        yield _Corpus(
            steps=steps,
            sequences={key: seq for key, seq in opened.items() if seq is not None},
        )
    finally:
        connection.close()


def _labelled(steps: tuple[EpisodicStep, ...], classifier: Classifier) -> tuple[EpisodicStep, ...]:
    """*steps* with whatever *classifier* read each of them as intending (FR-059)."""
    return tuple(replace(step, rationale_label=classifier.label(step.template)) for step in steps)


def _attributed(steps: tuple[EpisodicStep, ...]) -> tuple[EpisodicStep, ...]:
    """*steps* under FR-063's fallback attribution: the file the action landed in."""
    return tuple(replace(step, symbol_ref=step.files[0] if step.files else None) for step in steps)


def _unlabelled_edges(graph: AbstractGraph) -> dict[str, TransitionEdge]:
    """Every edge of *graph* with the one enriched field taken back off."""
    return {
        edge.edge_key: replace(edge, condition=replace(edge.condition, intended_activity=None))
        for edge in graph.edges
    }


def test_labelling_the_corpus_adds_a_condition_and_moves_no_node_or_edge(
    corpus: _Corpus,
) -> None:
    """SC-010: the classifier's labels land, and the graph around them is unchanged."""
    plain = aggregate(
        _labelled(corpus.steps, NullClassifier(_NoOpCounters())), LEVEL, corpus.sequences
    )
    enriched = aggregate(
        _labelled(corpus.steps, RationaleActivity(_AlwaysSearch())), LEVEL, corpus.sequences
    )

    assert set(plain.nodes) == set(enriched.nodes)
    assert {(edge.source, edge.target) for edge in plain.edges} == {
        (edge.source, edge.target) for edge in enriched.edges
    }
    assert {edge.condition.intended_activity for edge in plain.edges} == {None}
    assert {edge.condition.intended_activity for edge in enriched.edges} == {ActivityClass.SEARCH}
    assert _unlabelled_edges(plain) == _unlabelled_edges(enriched)


def test_attributing_a_step_to_its_artifact_changes_nothing_in_the_graph(
    corpus: _Corpus,
) -> None:
    """FR-061: `symbol_ref` enriches the episodic row, and the fold never reads it."""
    assert aggregate(corpus.steps, LEVEL, corpus.sequences) == aggregate(
        _attributed(corpus.steps), LEVEL, corpus.sequences
    )
