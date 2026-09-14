"""`inspect`: what the graph holds, in counts rather than in payloads (FR-065).

The read half of Principle V. Every hook answers a failure by incrementing a
counter, which only pays off if something reads the counts back: `inspect` is
that reader for the agent, the way `processrecall show` is for an operator, and
it prints the full named set of R16 so a counter still at zero is visible
instead of being indistinguishable from a path that was never wired.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, cast

from processrecall.graph.episodic import open_index
from processrecall.graph.snapshot import Snapshot, served_snapshot
from processrecall.graph.store import EpisodicStore, SQLiteEpisodicStore, counter_table
from processrecall.server.mcp.arguments import InspectArguments

#: What a project whose snapshot cannot be served is answered with. A reason
#: rather than an empty graph, because "nothing has been built" and "the file is
#: unreadable" are the same emptiness to a reader, and the naming of the path
#: itself is left to `processrecall show graph`: this answer goes to an agent,
#: and an absolute path is the one thing a tool result may not carry (FR-054).
_NO_GRAPH: Mapping[str, Any] = {
    "reason": "no graph has been built for this project yet; run `processrecall rebuild`",
    "level": None,
    "generated_at": None,
    "episode_high_water": None,
    "nodes": [],
    "edges": [],
}


def inspect(arguments: InspectArguments) -> dict[str, Any]:
    """What this home's graph holds. *arguments* carries nothing: the graph is the answer."""
    with closing(open_index()) as connection:
        return _held(SQLiteEpisodicStore(connection), Path.cwd())


def _held(store: EpisodicStore, project_dir: Path) -> dict[str, Any]:
    """The graph served for *project_dir* and the counts *store* kept, as the caller sees it.

    The snapshot is read before the counters so that a snapshot this build
    cannot serve is already counted as ``snapshot_unreadable`` by the time the
    table is taken (R11).
    """
    snapshot = served_snapshot(project_dir, store)
    graph = _NO_GRAPH if snapshot is None else _graph(snapshot)
    return {**graph, "counters": counter_table(store)}


def _graph(snapshot: Snapshot) -> dict[str, Any]:
    """*snapshot* as the agent reads it: procedures, moves, and when they apply.

    The bodies cross the snapshot seam as JSON-native values, typed ``object``
    so the served form can change without this reader; each is narrowed to the
    fields named below rather than passed through, so a field a later fold adds
    cannot reach an agent without a reader deciding it should (FR-054).
    """
    nodes = cast("Mapping[str, Mapping[str, Any]]", snapshot.nodes)
    edges = cast("Iterable[Mapping[str, Any]]", snapshot.edges)
    return {
        "reason": None,
        "level": snapshot.level,
        "generated_at": snapshot.generated_at.isoformat(),
        "episode_high_water": snapshot.episode_high_water,
        "nodes": [_node(key, body) for key, body in nodes.items()],
        "edges": [_edge(body) for body in edges],
    }


def _node(key: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """One procedure, as its identity and the counts hanging off it."""
    return {
        "node": key,
        "support": body.get("support", 0),
        "outcome_counts": body.get("outcome_counts", {}),
        "last_seen": body.get("last_seen"),
    }


def _edge(body: Mapping[str, Any]) -> dict[str, Any]:
    """One move, named the way `remember` takes it, with the context it applies in.

    The notes an agent attached to the move are reported beside the counts, and
    an unannotated move reports an empty list rather than omitting the key: "no
    note here" is an answer, and a key that comes and goes is one a reader has
    to guard against (FR-038).
    """
    return {
        "edge": f"{body.get('source')} -> {body.get('target')}",
        "support": body.get("support", 0),
        "condition": body.get("condition"),
        "annotations": body.get("annotations", []),
    }
