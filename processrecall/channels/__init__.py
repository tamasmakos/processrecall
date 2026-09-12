"""channels — the plugin axis for optional memory signals.

Each channel implements the symmetric :class:`~processrecall.channels.base.Channel`
contract (write at ingest/flush, read at query).
:func:`~processrecall.channels.registry.core_channels` is the fixed core set; it
runs on both the ingestion and retrieval paths.
"""
