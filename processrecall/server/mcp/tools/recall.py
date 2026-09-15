"""`recall`: guidance asked for, rather than served unbidden (FR-065).

The same top-down read the `UserPromptSubmit` hook makes, put under the agent's
own hand: a position in the graph becomes the moves usually made from it, each
with the episodes behind it. What differs is the occasion — the hook must decide
whether speaking is worth the interruption, while a call here *is* the occasion,
so no trigger stands between the neighbourhood and the answer. FR-045a's support
floor is a different guard, about evidence rather than occasion, and still
applies: a single episode is still silence, asked for or not.

Knowing nothing about a position is an ordinary answer here, not a fault: a
project whose graph has never recorded the procedure asked about is the first
case every project passes through, and the caller is told so in a reason rather
than by an error it would have to catch.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from processrecall.config import STORE_DIR, home_dir, load_config
from processrecall.graph.abstract import TransitionEdge
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import SNAPSHOT_NAME
from processrecall.graph.store import EpisodicStore, SQLiteEpisodicStore
from processrecall.guidance.fusion import FusedCandidates, FusedEdge, Fusion
from processrecall.guidance.locate import locate
from processrecall.guidance.render import GuidanceStatement
from processrecall.guidance.successors import successors_from
from processrecall.server.mcp.arguments import RecallArguments
from processrecall.trajectory.paths import project_key

#: What a position neither graph holds a move out of is answered with. A
#: reason rather than a bare empty list, because "this has never been done"
#: and "you named something the graph does not know" are the same emptiness
#: to a reader — and neither is an error (FR-065).
_NOTHING_RECORDED = "no move out of {source} has been recorded, here or in any other project"


@dataclass(frozen=True, slots=True)
class _Ask:
    """One resolved call: the position asked about, and the graphs to answer from.

    Attributes:
        source: The node guidance is asked for, whether the caller named it or
            it was read off where the work already stands.
        level: How general the keys of that graph are spelled (FR-023). Only
            shapes *source* when it is read off the current position: a
            *source* the caller named is matched as spelled, against whatever
            level the graph on disk was built at.
        store: The home's store, which is also where every fallback is counted.
        project_dir: The project whose own graph is consulted (FR-048).
    """

    source: str
    level: str
    store: EpisodicStore
    project_dir: Path


def recall(arguments: RecallArguments) -> dict[str, Any]:
    """Guidance for the position *arguments* names, over this home's graphs."""
    with closing(open_index()) as connection:
        return _answer(_asked(SQLiteEpisodicStore(connection), arguments))


def _asked(store: EpisodicStore, arguments: RecallArguments) -> _Ask:
    """*arguments* resolved against *store*: the position and level actually served."""
    level = arguments.level or load_config().level
    project_dir = Path.cwd()
    return _Ask(
        source=arguments.procedure or _standing_at(store, level, project_dir),
        level=level,
        store=store,
        project_dir=project_dir,
    )


def _standing_at(store: EpisodicStore, level: str, project_dir: Path) -> str:
    """Where the work already stands, spelled at *level* (FR-043).

    The turn running now is the most recently opened one *store* still holds
    open for *project_dir*, which is the reading `mark_outcome` already makes
    of an omitted prompt id, narrowed to this project: *store* holds every
    project the harness has ever seen, and an open turn left running in
    another one is not this project's position. A turn that has carried out no
    step yet — or no open turn at all — stands at the start node, whose
    successors are what usually opens a prompt (FR-047).
    """
    sequence = store.latest_sequence_for_project(project_key(str(project_dir)))
    steps = () if sequence is None else store.steps(sequence.key)
    return locate(steps, level).key


def _answer(ask: _Ask) -> dict[str, Any]:
    """The moves made from *ask*'s position, as the caller reads them back."""
    fused = _fused(ask)
    statements = [
        statement
        for candidate in _supported(fused.edges, ask.store)
        for statement in _statements(candidate.edge)
    ]
    return {
        "procedure": ask.source,
        "level": ask.level,
        "scope": str(fused.scope),
        "statements": [_said(statement) for statement in statements],
        "reason": None if statements else _NOTHING_RECORDED.format(source=ask.source),
    }


def _supported(candidates: tuple[FusedEdge, ...], counters: EpisodicStore) -> tuple[FusedEdge, ...]:
    """*candidates* whose edge clears the configured support floor (FR-045a).

    A call here is the occasion no trigger gates, but a single observation is
    still silence: an edge below the floor exists without being evidence
    enough to serve, the same reading `Triggers._cleared` gives it.
    """
    minimum = load_config().min_support
    supported = tuple(candidate for candidate in candidates if candidate.edge.support >= minimum)
    if candidates and not supported:
        counters.bump("guidance_below_support")
    return supported


def _fused(ask: _Ask) -> FusedCandidates:
    """This project's moves out of *ask*'s position, the other projects' behind them (FR-048).

    The same fusion the hook serves from, so a procedure this project has
    never recorded is still answered — marked as somebody else's experience
    and counted as the fallback it is.
    """
    return Fusion(ask.store).fuse(
        _successors(ask.project_dir / STORE_DIR / SNAPSHOT_NAME, ask),
        _successors(home_dir() / SNAPSHOT_NAME, ask),
    )


def _successors(path: Path, ask: _Ask) -> tuple[TransitionEdge, ...]:
    """The moves out of *ask*'s position in the snapshot at *path*.

    No process type to restrict to: unlike the hook's opening moves, a call
    here is not scoped to what starts a prompt.
    """
    return successors_from(path, ask.source, ask.store)


def _statement(edge: TransitionEdge) -> GuidanceStatement:
    """*edge* as the claim it is served as, and the episodes behind it (FR-044).

    Template-derived like every other statement guidance is made of: the moves
    are counted and ordered here, and nothing writes the sentence but this line.
    """
    return GuidanceStatement(
        text=f"after {edge.source} the work usually goes to {edge.target}",
        support=edge.support,
    )


def _statements(edge: TransitionEdge) -> tuple[GuidanceStatement, ...]:
    """*edge*'s statistical claim, and any note the `remember` tool attached to it (FR-039).

    The note's support is the move's, not its own: `edge.annotations` carries
    no count of its own to render.
    """
    return (
        _statement(edge),
        *(
            GuidanceStatement.from_annotation(annotation, edge.support)
            for annotation in edge.annotations
        ),
    )


def _said(statement: GuidanceStatement) -> dict[str, Any]:
    """One statement as the caller reads it: the claim, its evidence, and its origin."""
    return {
        "text": statement.text,
        "support": statement.support,
        "origin": str(statement.origin),
    }
