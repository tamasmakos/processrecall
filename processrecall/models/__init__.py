"""Cross-cutting domain models — the vocabulary shared by every layer.

Used by ingestion, retrieval and every endpoint. Layer-neutral: these depend on
nothing else in the package.
"""

from processrecall.models.fact import (
    Fact,
    FactState,
    Mention,
    Modality,
    Polarity,
    Validity,
)
from processrecall.models.hit import (
    MAX_CONTEXT_CHARS,
    Hit,
    RenderedMemories,
    date_in,
    render_memories,
)
from processrecall.models.ingest import IngestResult
from processrecall.models.ingest_options import IngestOptions
from processrecall.models.memory_class import MemoryClass
from processrecall.models.message import Message, MessageInput, normalize_messages
from processrecall.models.report import (
    Counters,
    Evidence,
    FactWithEvidence,
    IngestReport,
    RecallBudget,
    RecallResult,
)
from processrecall.models.scope import MemoryScope
from processrecall.models.segment import Segment, SegmentKind
from processrecall.models.source import Source
from processrecall.models.symbols import ConceptRef, PredicateRef, SymbolKind

__all__ = [
    "MAX_CONTEXT_CHARS",
    "ConceptRef",
    "Counters",
    "Evidence",
    "Fact",
    "FactState",
    "FactWithEvidence",
    "Hit",
    "IngestOptions",
    "IngestReport",
    "IngestResult",
    "MemoryClass",
    "MemoryScope",
    "Mention",
    "Message",
    "MessageInput",
    "Modality",
    "Polarity",
    "PredicateRef",
    "RecallBudget",
    "RecallResult",
    "RenderedMemories",
    "Segment",
    "SegmentKind",
    "Source",
    "SymbolKind",
    "Validity",
    "date_in",
    "normalize_messages",
    "render_memories",
]
