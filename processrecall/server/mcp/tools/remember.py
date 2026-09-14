"""`remember`: the note an agent writes about a move, screened first (FR-038).

The one write path by which meaning, rather than frequency, enters the graph:
everything else in it is counted from episodic rows, so this is where a run says
what it *learned* about a transition instead of how often it took it.

The note is checked against the graph this project is actually served before a
row exists (FR-038a) — an unknown move is refused with the nearest real ones
rather than created, because inventing the edge would put a move in the abstract
layer that no episodic row stands behind. It lands in the annotations table of
its own, not on the edge, so a full rebuild puts it back rather than deriving it
away.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from processrecall.exceptions import AnnotationRejected
from processrecall.graph.abstract import edge_key
from processrecall.graph.annotations import Annotation, AnnotationValidator
from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import served_snapshot
from processrecall.graph.store import EpisodicStore, SQLiteEpisodicStore
from processrecall.server.mcp.arguments import RememberArguments
from processrecall.trajectory.paths import project_key

#: Who a note written through this tool is attributed to. The tool is the
#: agent's own hand: a person's notes reach the same table through the CLI.
AUTHOR = "agent"


def remember(arguments: RememberArguments) -> dict[str, Any]:
    """Attach the note *arguments* carries to the move it names, over this home's store."""
    project_dir = Path.cwd()
    with closing(open_index()) as connection:
        store = SQLiteEpisodicStore(connection)
        try:
            text = _screen(store, project_dir).accept(arguments.edge, arguments.note)
        except AnnotationRejected as rejected:
            return _refused(arguments.edge, rejected)
        note = Annotation(
            edge_key=arguments.edge,
            text=text,
            author=AUTHOR,
            written_at=datetime.now(UTC),
        )
        store.write_annotation(project_key(str(project_dir)), note)
    return _attached(note)


def _screen(store: EpisodicStore, project_dir: Path) -> AnnotationValidator:
    """The checks of FR-038a, over the moves the graph served for *project_dir* holds."""
    snapshot = served_snapshot(project_dir, store)
    edges = () if snapshot is None else cast("Iterable[Mapping[str, Any]]", snapshot.edges)
    return AnnotationValidator((edge_key(body["source"], body["target"]) for body in edges), store)


def _refused(edge: str, rejected: AnnotationRejected) -> dict[str, Any]:
    """Why *edge* was not annotated, in a form the agent can retry from (FR-038a).

    ``reason`` is the code a caller branches on; ``detail`` is human/agent-
    readable prose that makes it actionable — the nearest real moves, the
    length, the credential pattern — for an agent to read, not to parse.
    """
    return {
        "stored": False,
        "edge": edge,
        "reason": rejected.reason,
        "detail": rejected.detail,
    }


def _attached(note: Annotation) -> dict[str, Any]:
    """*note* as the caller reads it back: attributed and timestamped (FR-038)."""
    return {
        "stored": True,
        "edge": note.edge_key,
        "author": note.author,
        "written_at": note.written_at.isoformat(),
    }
