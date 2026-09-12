"""processrecall — agentic memory as an importable library.

Any agent project can integrate processrecall without HTTP, hardcoded endpoints,
or framework-specific glue. :class:`Memory` is the single transport-neutral
entrypoint for all memory operations::

    from processrecall import Memory

    mem = Memory()                                    # builds from env / .env
    await mem.add(messages, user_id="u1", run_id="r7")
    hits = await mem.search("what did she decide?", user_id="u1")
    await mem.flush()                                  # identity + dead-weight report

The same class backs the MCP stdio server, so every transport sees identical
behaviour. For out-of-process use over MCP, see
:mod:`processrecall.integrations.client`.
"""

from __future__ import annotations

import logging

from processrecall._version import __version__
from processrecall.exceptions import (
    ConfigurationError,
    GraphKnowsError,
    MissingExtraError,
    StoreError,
)
from processrecall.memory import Memory
from processrecall.models import (
    MAX_CONTEXT_CHARS,
    Hit,
    IngestResult,
    MemoryScope,
    Message,
    RenderedMemories,
    date_in,
    normalize_messages,
    render_memories,
)
from processrecall.settings import (
    GraphKnowsSettings,
    MemoryMode,
    TopicMode,
)

# A library must not configure logging handlers; attach a NullHandler so records
# are dropped unless the embedding application configures the root logger.
logging.getLogger("processrecall").addHandler(logging.NullHandler())

__all__ = [
    "MAX_CONTEXT_CHARS",
    "ConfigurationError",
    "GraphKnowsError",
    "GraphKnowsSettings",
    "Hit",
    "IngestResult",
    "Memory",
    "MemoryMode",
    "MemoryScope",
    "Message",
    "MissingExtraError",
    "RenderedMemories",
    "StoreError",
    "TopicMode",
    "__version__",
    "date_in",
    "normalize_messages",
    "render_memories",
]
