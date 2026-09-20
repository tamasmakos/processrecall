"""The semantic index: code entities and the relations between them (FR-017, FR-018).

The Tensor Brain's concept index over a repository — a file or a symbol earns a
symbol of its own, keyed by where it lives, and everything else attaches to that
key. Rows only, standard library only: the parsing that finds these entities is
`artifacts/parse.py` and `artifacts/calls.py`, which this module may not import
(`artifacts` sits below `graph`), so `cli/derive.py` is where a parsed symbol
becomes a row here.

`entity_key` is the single door onto the key column: repository-relative with
forward slashes on every platform, because an absolute path is a privacy leak
and a portability bug at once. `trajectory/paths.py::lexical_path` normalises a
host path lexically, collapsing a `..` rather than refusing it; a key column
has no such path to fall back on, so `entity_key` rejects instead of rewrites.

Example:
    from processrecall.graph.semantic import entity_key

    key = entity_key(PurePosixPath("processrecall/graph/semantic.py"))
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath
from typing import Literal

#: What a code entity is, in the vocabulary `code_entities.kind` holds (FR-017).
EntityKind = Literal["file", "module", "class", "function", "method"]

#: What one code entity does to another, in the vocabulary `code_relations.relation`
#: holds (FR-018). `contains` is this module's own — the other three are extracted
#: in `artifacts/calls.py`, which `graph` may not import, so the vocabulary is
#: spelled on both sides of that boundary.
RelationKind = Literal["contains", "calls", "unresolved_call", "implements"]

#: The one kind keyed by a path alone. Every other kind names a symbol inside a
#: file, so its key carries that symbol's name after a ``#``.
FILE_KIND: EntityKind = "file"

#: The one relation whose target was never resolved. It is its own relation rather
#: than a `calls` row with no target (R16), so a traversal that wants only resolved
#: calls filters by the type instead of by nullness.
UNRESOLVED_CALL: RelationKind = "unresolved_call"


def entity_key(path: PurePath, qualified_name: str | None = None) -> str:
    """The `code_entities` key for *path*, or for the symbol it declares.

    *qualified_name* is `None` for the file itself and the symbol's dotted name
    otherwise, which the key carries after a ``#``.

    Raises:
        ValueError: *path* is not repository-relative — absolute, carrying a
            drive, escaping with ``..``, or itself containing a ``#``.
            Rejected here rather than at the store, so no caller can write a
            host path, an escape out of the repository, or a key that
            `file_key` would split at the wrong place, into a column that is
            meant to travel.
    """
    if path.is_absolute() or path.drive:
        raise ValueError(
            f"entity key {str(path)!r}: a code entity is keyed repository-relative, so an "
            "absolute path is refused rather than stored and leaked"
        )
    relative = path.as_posix()
    if ".." in path.parts:
        raise ValueError(
            f"entity key {str(path)!r}: a code entity is keyed repository-relative, so a '..' "
            "escape is refused rather than stored and leaked"
        )
    if "#" in relative:
        raise ValueError(
            f"entity key {str(path)!r}: a code entity is keyed repository-relative, so a '#' in "
            "the path is refused rather than stored where it would be read as a symbol name"
        )
    return relative if qualified_name is None else f"{relative}#{qualified_name}"


@dataclass(frozen=True, slots=True)
class CodeEntity:
    """One `code_entities` row: a file, or one symbol declared inside one.

    Attributes:
        entity_key: The key `entity_key` builds — the file's path, with the
            symbol's name after a ``#`` when the row is a symbol.
        kind: What the entity is, from `EntityKind`.
        fingerprint: The content hash derivation is incremental on (FR-019): an
            unchanged file is skipped rather than re-parsed.
        first_seen: When work first touched the entity, UTC.
        last_seen: When work last touched it, UTC.
        extension: The file's extension, lower-case and without the dot; empty
            when it has none.
        language: The grammar that parsed the file, empty when nothing did.
        support: How many steps touched the entity.
        start_line: The first line a symbol spans; `None` for a file.
        end_line: The last line a symbol spans; `None` for a file.
    """

    entity_key: str
    kind: EntityKind
    fingerprint: str
    first_seen: datetime
    last_seen: datetime
    extension: str = ""
    language: str = ""
    support: int = 0
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        """Refuse a row whose kind and key disagree on whether it names a symbol.

        Raises:
            ValueError: A `file` row carries a ``#``, or a symbol row carries
                none. Either way the key no longer says which entity the row is
                about, and a touched edge resolved against it answers the wrong
                one (FR-017).
        """
        names_symbol = self.file_key != self.entity_key
        if self.kind == FILE_KIND and names_symbol:
            raise ValueError(
                f"entity key {self.entity_key!r} of kind {self.kind!r}: a file is keyed by its "
                "path alone, so a '#' in the key means the row is really about a symbol"
            )
        if self.kind != FILE_KIND and not names_symbol:
            raise ValueError(
                f"entity key {self.entity_key!r} of kind {self.kind!r}: a symbol is keyed by its "
                f"file and its own name, as {self.entity_key}#name"
            )

    @property
    def file_key(self) -> str:
        """The key of the file this entity lives in; its own key, for a file."""
        return self.entity_key.split("#", 1)[0]


@dataclass(frozen=True, slots=True)
class CodeRelation:
    """One `code_relations` row: what one code entity does to another.

    Attributes:
        source_key: The entity the relation is from.
        relation: What it does, from `RelationKind`.
        target_key: The entity it is to; `None` when the target was never
            resolved, which is the one case `target_name` answers instead.
        target_name: The bare name an unresolved call mentioned, preserved
            rather than dropped (FR-018); `None` on every resolved relation.
        sites: How many places in the sources the relation was seen at, which is
            what ranks one caller above another. Containment is one site per
            symbol: a file holds a symbol once.
    """

    source_key: str
    relation: RelationKind
    target_key: str | None = None
    target_name: str | None = None
    sites: int = 1

    def __post_init__(self) -> None:
        """Refuse a row that is dishonest about resolution.

        Raises:
            ValueError: the two target columns disagree with the relation. Only
                `UNRESOLVED_CALL` names a target it could not resolve, and it
                always names one: a resolved relation with no `target_key` would
                be indistinguishable from a parse failure, and an unresolved one
                without its `target_name` drops what FR-018 keeps.
        """
        if self.relation == UNRESOLVED_CALL and (
            self.target_key is not None or self.target_name is None
        ):
            raise ValueError(
                f"relation {self.relation!r} from {self.source_key!r}: an unresolved call is "
                "keyed by the bare name it mentioned alone, never by a target"
            )
        if self.relation != UNRESOLVED_CALL and (
            self.target_name is not None or self.target_key is None
        ):
            raise ValueError(
                f"relation {self.relation!r} from {self.source_key!r}: a resolved relation names "
                f"the entity it is to, so a missing target means {UNRESOLVED_CALL!r} instead"
            )


def containment(entities: Iterable[CodeEntity]) -> tuple[CodeRelation, ...]:
    """The `contains` row for every symbol in *entities*, from its file to it.

    Derived from the keys rather than declared beside them: the key already says
    which file a symbol lives in, and a second statement of it could disagree.

    Raises:
        ValueError: A symbol whose file entity is not among *entities*.
            Containment is total, so a symbol with no file to hold it would sit
            in the store unreachable from the tree it belongs to (FR-018).
    """
    rows = tuple(entities)
    files = frozenset(row.entity_key for row in rows if row.kind == FILE_KIND)
    return tuple(_contains(row, files) for row in rows if row.kind != FILE_KIND)


def _contains(symbol: CodeEntity, files: frozenset[str]) -> CodeRelation:
    """The `contains` row holding *symbol*, given the file keys *files* declares."""
    if symbol.file_key not in files:
        raise ValueError(
            f"symbol {symbol.entity_key!r} has no file entity for {symbol.file_key!r}: a symbol "
            "is written with the file that contains it, never on its own"
        )
    return CodeRelation(
        source_key=symbol.file_key, relation="contains", target_key=symbol.entity_key
    )
