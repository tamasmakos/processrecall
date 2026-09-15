"""What the located agent may do next: the h-hop neighbourhood (FR-043, R7).

Extraction follows the arrows. A move *into* the position is what the agent
has already done and there is nothing to advise about it; the moves out of it,
and out of those, are the ones guidance can be made of.

`h` is the radius, and it is a budget rather than a performance decision (R7):
one hop is already more statements than the renderer's ceiling fits, so
extraction never grows its own default. Like `locate`'s `level`, `h` is a
parameter here, not a read of `Config`: the callers this feeds — T043's
triggers and T044's render — are the ones that pass `config.h`.

On the hot path, so the standard library only.

Example:
    from processrecall.guidance.neighborhood import extract

    neighborhood = extract(graph, position, h=config.h)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from processrecall.graph.abstract import AbstractGraph, TransitionEdge
from processrecall.guidance.locate import Position


@dataclass(frozen=True, slots=True)
class Neighborhood:
    """The slice of the graph guidance for one position may be made of.

    Attributes:
        center: The node key extraction ran from — the located position.
        edges: The transitions within the radius, nearest hop first.
    """

    center: str
    edges: tuple[TransitionEdge, ...]


def extract(graph: AbstractGraph, position: Position, *, h: int) -> Neighborhood:
    """The moves of *graph* reachable from *position* within *h* hops.

    Breadth-first, so the returned edges run nearest hop first and a renderer
    that keeps only its first few keeps the most immediate advice. A node is
    expanded once however many moves lead back into it, which is what makes a
    procedure that loops on itself terminate rather than fill the budget with
    the same transition.

    A *position* the graph holds no node for — a procedure this project has not
    recorded yet — has no outgoing moves, so it yields an empty neighbourhood
    rather than a fault.
    """
    outgoing = _outgoing(graph.edges)
    edges: list[TransitionEdge] = []
    reached = {position.key}
    frontier: tuple[str, ...] = (position.key,)
    for _ in range(h):
        hop = tuple(edge for key in frontier for edge in outgoing.get(key, ()))
        edges.extend(hop)
        frontier = tuple(dict.fromkeys(edge.target for edge in hop if edge.target not in reached))
        reached.update(frontier)
    return Neighborhood(center=position.key, edges=tuple(edges))


def _outgoing(edges: Iterable[TransitionEdge]) -> Mapping[str, tuple[TransitionEdge, ...]]:
    """*edges* against the node each one leaves, in the order the graph holds them."""
    grouped: dict[str, list[TransitionEdge]] = {}
    for edge in edges:
        grouped.setdefault(edge.source, []).append(edge)
    return {source: tuple(group) for source, group in grouped.items()}
