"""Persistence layer: the ArcadeDB backend, embeddings, and namespace-aware store factories.

The factories build unconnected stores from
:class:`~processrecall.settings.GraphKnowsSettings`
and a namespace; the caller awaits ``connect()`` on its own event loop. They are
mode-neutral — the relation extractor is injected by the ingestion layer, never
built here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from processrecall.settings import GraphKnowsSettings
from processrecall.storage.namespace import db_name

if TYPE_CHECKING:
    from processrecall.storage.arcadedb.client import ArcadeDBClient
    from processrecall.storage.arcadedb.graph_store import GraphStore


def build_arcadedb_client(settings: GraphKnowsSettings) -> ArcadeDBClient:
    """Build an ArcadeDBClient from settings (not yet connected)."""
    from processrecall.storage.arcadedb.client import ArcadeDBClient

    return ArcadeDBClient(
        base_url=settings.arcadedb_url,
        user=settings.arcadedb_user,
        password=settings.arcadedb_password.get_secret_value(),
    )


def build_graph_store(settings: GraphKnowsSettings, namespace: str = "") -> GraphStore:
    """Build a single-database :class:`GraphStore` for a namespace (golden layer).

    The store is not yet connected — the caller awaits ``connect()`` (and
    ``ensure_schema(dims)`` on write paths) on its own event loop.
    """
    from processrecall.storage.arcadedb.graph_store import GraphStore

    return GraphStore(client=build_arcadedb_client(settings), db=db_name(namespace))


def __getattr__(name: str) -> Any:
    """Lazily expose the ArcadeDB adapters + embedder without importing heavy deps.

    (sentence-transformers) at ``import processrecall.storage`` time.
    """
    lazy = {
        "ArcadeDBClient": ("processrecall.storage.arcadedb", "ArcadeDBClient"),
        "GraphStore": ("processrecall.storage.arcadedb.graph_store", "GraphStore"),
        "embed": ("processrecall.storage.embedder", "embed"),
        "embed_one": ("processrecall.storage.embedder", "embed_one"),
    }
    target = lazy.get(name)
    if target is None:
        raise AttributeError(f"module 'processrecall.storage' has no attribute {name!r}")
    import importlib

    module, attr = target
    return getattr(importlib.import_module(module), attr)


__all__ = [
    "build_arcadedb_client",
    "build_graph_store",
    "db_name",
]
