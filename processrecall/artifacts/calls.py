"""Who calls whom, read off the same sources parsing already claimed (FR-018).

One seam, :func:`extract_relations`: the files of one unit of work as
path-to-text, answered with the ``calls``, ``unresolved_call`` and ``implements``
relations between the symbols they define, each carrying the number of sites it
was seen at. The whole set is taken at once because resolution is by name across
files: a callee is a relation to a definition only when exactly one definition
answers to the name, in the grammar family that mentioned it — Python's own, or
the one TypeScript, its JSX dialect and JavaScript share, so a call in one
resolves into a definition in another. Where a name is defined in several files
and one of them is the caller's own, that definition wins deliberately: a file
naming its own symbol is taken at its word over a same-named one elsewhere.

The definitions come from :func:`~processrecall.artifacts.parse.parse_source`,
so a relation can only ever name a symbol that module also reports; what is read
here is the half parsing does not cover, the call sites and the base types,
both of them captured by the queries :mod:`processrecall.artifacts.tags` vendors.
tree-sitter is imported here, in those two modules, and nowhere else.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from processrecall.artifacts.parse import Symbol, enclosing_symbol, parse_tree
from processrecall.artifacts.tags import LANGUAGES, tag_captures

if TYPE_CHECKING:
    from tree_sitter import Node

#: What a relation is, in the vocabulary the semantic layer stores it under.
RelationKind = Literal["calls", "unresolved_call", "implements"]


@dataclass(frozen=True, slots=True)
class CodeRef:
    """One end of a relation: a file, or one symbol within it.

    ``qualified_name`` is ``None`` for the file itself, which is what a call
    made outside every definition — at module level — belongs to.
    """

    path: Path
    qualified_name: str | None = None


@dataclass(frozen=True, slots=True)
class Relation:
    """One relation between two code entities, with its occurrence count.

    ``kind`` is the relation the semantic layer stores in its ``relation``
    column, and ``sites`` the number of places in the sources it was seen at,
    which is what ranks one caller above another.
    """

    source: CodeRef
    kind: RelationKind
    target: CodeRef | None
    target_name: str | None
    sites: int


#: The grammar family each language resolves names in: languages sharing a
#: grammar — TypeScript, its JSX dialect and JavaScript — resolve into one
#: another rather than only among themselves. Which languages relations are
#: read from at all is :data:`~processrecall.artifacts.tags.LANGUAGES`, not this
#: mapping, so a language named only here still reads none.
_FAMILIES: Mapping[str, str] = {
    "python": "python",
    "typescript": "typescript",
    "tsx": "typescript",
    "javascript": "typescript",
}

#: What a capture of the vendored queries means as a relation.
_CAPTURE_KINDS: Mapping[str, RelationKind] = {
    "name.reference.call": "calls",
    "name.reference.implements": "implements",
}


@dataclass(frozen=True, slots=True)
class _Parsed:
    """One file ready to be read for the names it mentions.

    ``language`` selects the vendored query the names are captured with, and
    ``family`` is the one resolution keys them on.
    """

    path: Path
    symbols: tuple[Symbol, ...]
    root: Node
    language: str
    family: str


@dataclass(frozen=True, slots=True)
class _Reference:
    """A bare name one entity mentions, and what relating to it would mean."""

    source: CodeRef
    resolved_kind: RelationKind
    name: str
    family: str


#: Every definition of the sources, by the grammar family and bare name naming it.
_Definitions = Mapping[tuple[str, str], tuple[CodeRef, ...]]


def extract_relations(sources: Mapping[Path, str]) -> tuple[Relation, ...]:
    """The relations *sources* spell out, each path mapped to its text."""
    parsed = tuple(
        one for path, text in sources.items() if (one := _parsed(path, text)) is not None
    )
    counted = Counter(_edges(parsed, _definitions(parsed)))
    relations = (replace(edge, sites=sites) for edge, sites in counted.items())
    return tuple(sorted(relations, key=_order))


def _parsed(path: Path, text: str) -> _Parsed | None:
    """*text* parsed for relations, or ``None`` for a language without them."""
    parsed, root = parse_tree(path, text)
    language = parsed.language
    if (
        language is None
        or root is None
        or language not in LANGUAGES
        or (family := _FAMILIES.get(language)) is None
    ):
        return None
    return _Parsed(path, parsed.symbols, root, language, family)


def _definitions(parsed: Iterable[_Parsed]) -> _Definitions:
    """Every symbol the sources define, indexed by grammar family and bare name."""
    index: dict[tuple[str, str], list[CodeRef]] = defaultdict(list)
    for one in parsed:
        for symbol in one.symbols:
            bare = symbol.qualified_name.rsplit(".", 1)[-1]
            index[(one.family, bare)].append(CodeRef(symbol.path, symbol.qualified_name))
    return {key: tuple(refs) for key, refs in index.items()}


def _edges(parsed: Iterable[_Parsed], defined: _Definitions) -> Iterator[Relation]:
    """One edge per occurrence of a name that stands for a relation."""
    for one in parsed:
        for reference in _mentions(one):
            if (edge := _edge(reference, defined)) is not None:
                yield edge


def _edge(reference: _Reference, defined: _Definitions) -> Relation | None:
    """The relation *reference* stands for, or ``None`` when it stands for none."""
    if (target := _target(reference, defined)) is not None:
        return Relation(reference.source, reference.resolved_kind, target, None, sites=1)
    if reference.resolved_kind == "calls":
        return Relation(reference.source, "unresolved_call", None, reference.name, sites=1)
    return None


def _target(reference: _Reference, defined: _Definitions) -> CodeRef | None:
    """The one definition *reference* names, or ``None`` when none or many do."""
    candidates = defined.get((reference.family, reference.name), ())
    within_file = tuple(ref for ref in candidates if ref.path == reference.source.path)
    narrowed = within_file or candidates
    return narrowed[0] if len(narrowed) == 1 else None


def _mentions(parsed: _Parsed) -> Iterator[_Reference]:
    """Every name the vendored query captures in *parsed*, by what encloses it."""
    captured = tag_captures(parsed.language, parsed.root)
    for capture, kind in _CAPTURE_KINDS.items():
        for node in captured.get(capture, ()):
            yield _Reference(_encloser(parsed, node), kind, _text(node), parsed.family)


def _encloser(parsed: _Parsed, node: Node) -> CodeRef:
    """The symbol *node* sits in, or the file itself when none does."""
    symbol = enclosing_symbol(parsed.symbols, node.start_point[0] + 1)
    return CodeRef(parsed.path) if symbol is None else CodeRef(symbol.path, symbol.qualified_name)


def _order(relation: Relation) -> tuple[str, str, str, str]:
    """A stable order: by the entity relating, then by what it relates to."""
    target = relation.target_name or ""
    if relation.target is not None:
        target = f"{relation.target.path.as_posix()}#{relation.target.qualified_name}"
    return (
        relation.source.path.as_posix(),
        relation.source.qualified_name or "",
        relation.kind,
        target,
    )


def _text(node: Node) -> str:
    """*node*'s source text, decoded."""
    return "" if node.text is None else node.text.decode("utf-8", errors="replace")
