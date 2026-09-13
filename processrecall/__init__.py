"""processrecall — procedural graph memory as an importable library.

The fork (R18) has emptied this module of the pre-fork stack it re-exported:
``Memory`` and the ``models`` value objects went with the channels, ingestion,
retrieval and storage layers they belonged to. What is left is what every
surviving layer shares — the errors and settings — so that importing
the package costs nothing and binds no layer to another::

    from processrecall import BootstrapError, GraphKnowsSettings
"""

from __future__ import annotations

import logging

from processrecall._version import __version__
from processrecall.exceptions import (
    AnnotationRejected,
    BootstrapError,
    PackError,
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
    "AnnotationRejected",
    "BootstrapError",
    "GraphKnowsSettings",
    "MemoryMode",
    "PackError",
    "TopicMode",
    "__version__",
]
