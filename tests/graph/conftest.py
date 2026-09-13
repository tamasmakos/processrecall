"""Shared builders for `graph/store.py` tests."""

from __future__ import annotations

from datetime import UTC, datetime

from processrecall.graph.abstract import AbstractGraph, TransitionEdge
from processrecall.graph.store import EpisodicStep, Sequence, SequenceKey
from processrecall.symbolic.packs import ActivityClass, ProcessType


def sequence(prompt_id: str) -> SequenceKey:
    """One prompt's key, named so a fixture reads as the prompt it is."""
    return SequenceKey(conversation_id="c1", session_epoch=0, prompt_id=prompt_id)


def make_step(*, dedup_key: str, sequence_key: SequenceKey, position: int = 0) -> EpisodicStep:
    """One recordable step, with only the fields a test varies exposed."""
    return EpisodicStep(
        dedup_key=dedup_key,
        sequence_key=sequence_key,
        position=position,
        node_key="Inspection/Read",
        activity_class=ActivityClass.INSPECTION,
        template="Read file_path",
        occurred_at=datetime(2026, 9, 13, 10, 0, 1, tzinfo=UTC),
    )


def make_aggregate_step(
    node_key: str,
    *,
    position: int,
    step_id: int,
    key: SequenceKey,
    outcome: str = "success",
) -> EpisodicStep:
    """One recorded row, named by the node key the recorder derived for it."""
    activity_class, program, _ = node_key.split("/")
    return EpisodicStep(
        dedup_key=f"{key.prompt_id}-{position}",
        sequence_key=key,
        position=position,
        node_key=node_key,
        activity_class=ActivityClass(activity_class),
        program=program,
        template=f"{program} <File>",
        occurred_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        outcome=outcome,
        step_id=step_id,
    )


def make_sequence(
    steps: tuple[EpisodicStep, ...],
    *,
    process_type: ProcessType = ProcessType.UNKNOWN,
    status: str = "closed",
) -> Sequence:
    """The sequence *steps* belong to, filling only the fields the fold reads."""
    return Sequence(
        key=steps[0].sequence_key,
        project_dir_key="proj",
        started_at=steps[0].occurred_at,
        process_type=process_type,
        status=status,
        ended_at=None if status == "open" else steps[-1].occurred_at,
        step_count=len(steps),
    )


def edge(graph: AbstractGraph, edge_key: str) -> TransitionEdge:
    """The one edge *edge_key* names, so a failure names the move and not an index."""
    return next(candidate for candidate in graph.edges if candidate.edge_key == edge_key)
