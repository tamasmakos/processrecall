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
here is the half parsing does not cover, the call sites and the base types.
tree-sitter is imported here and in that module and nowhere else in the package.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from processrecall.artifacts.parse import Symbol, enclosing_symbol, parse_tree

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


@dataclass(frozen=True, slots=True)
class _CallGrammar:
    """What one language calls a call site and a list of base types.

    ``family`` is what resolution keys definitions and references on, so
    languages that share a grammar — TypeScript, its JSX dialect and
    JavaScript — resolve into one another rather than only among themselves.
    ``calls`` maps a node type to the field holding the expression being called;
    the bare name resolution works on is the last name in it, so
    ``self.store.write`` mentions ``write``. ``heritage`` maps a class node type
    to the field — or, where the grammar gives it no field, the child type —
    holding the types it derives from. ``clauses`` are the nodes inside that one
    which group the types rather than naming one, ``extends`` and ``implements``.
    """

    family: str
    calls: Mapping[str, str]
    heritage: Mapping[str, str]
    clauses: frozenset[str] = frozenset()


#: TypeScript, its JSX dialect and JavaScript are one grammar family with one set
#: of node types, so they share the record rather than repeating it.
_TYPESCRIPT = _CallGrammar(
    family="typescript",
    calls={"call_expression": "function", "new_expression": "constructor"},
    heritage={"class_declaration": "class_heritage"},
    clauses=frozenset({"extends_clause", "implements_clause"}),
)

_CALL_GRAMMARS: Mapping[str, _CallGrammar] = {
    "python": _CallGrammar(
        family="python",
        calls={"call": "function"},
        heritage={"class_definition": "superclasses"},
    ),
    "typescript": _TYPESCRIPT,
    "tsx": _TYPESCRIPT,
    "javascript": _TYPESCRIPT,
}

#: The node types that spell a name rather than wrap one.
_NAME_TYPES = frozenset({"identifier", "type_identifier", "property_identifier"})

#: The node types a base type may be spelled as; a metaclass keyword argument or
#: a list of type arguments is none of them, so neither is read as a base.
_NAMED_TYPES = _NAME_TYPES | {"attribute", "member_expression"}


@dataclass(frozen=True, slots=True)
class _Parsed:
    """One file ready to be read for the names it mentions."""

    path: Path
    symbols: tuple[Symbol, ...]
    root: Node
    grammar: _CallGrammar


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
    if language is None or root is None or (grammar := _CALL_GRAMMARS.get(language)) is None:
        return None
    return _Parsed(path, parsed.symbols, root, grammar)


def _definitions(parsed: Iterable[_Parsed]) -> _Definitions:
    """Every symbol the sources define, indexed by grammar family and bare name."""
    index: dict[tuple[str, str], list[CodeRef]] = defaultdict(list)
    for one in parsed:
        for symbol in one.symbols:
            bare = symbol.qualified_name.rsplit(".", 1)[-1]
            index[(one.grammar.family, bare)].append(CodeRef(symbol.path, symbol.qualified_name))
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
    """Every name in *parsed* that may relate, attributed to what encloses it.

    An explicit stack rather than recursion: a real source file can nest deep
    enough to exceed CPython's recursion limit, and this runs unattended.
    """
    stack = [parsed.root]
    while stack:
        node = stack.pop()
        if node.type in parsed.grammar.calls:
            yield from _called(node, parsed)
        elif node.type in parsed.grammar.heritage:
            yield from _derived(node, parsed)
        stack.extend(reversed(node.named_children))


def _called(node: Node, parsed: _Parsed) -> Iterator[_Reference]:
    """The name *node* calls, as a reference from whatever encloses the site."""
    field = parsed.grammar.calls[node.type]
    callee = node.child_by_field_name(field)
    if callee is not None and (name := _name_of(callee)):
        yield _Reference(_encloser(parsed, node), "calls", name, parsed.grammar.family)


def _derived(node: Node, parsed: _Parsed) -> Iterator[_Reference]:
    """The types the class at *node* derives from, as references from the class."""
    heritage = _held(node, parsed.grammar.heritage[node.type])
    if heritage is None:
        return
    source = _encloser(parsed, node)
    for name in _base_names(heritage, parsed.grammar.clauses):
        yield _Reference(source, "implements", name, parsed.grammar.family)


def _held(node: Node, held: str) -> Node | None:
    """*node*'s child in field *held*, else its first child of that type."""
    if (field := node.child_by_field_name(held)) is not None:
        return field
    return next((child for child in node.named_children if child.type == held), None)


def _base_names(heritage: Node, clauses: frozenset[str]) -> Iterator[str]:
    """The names *heritage* lists, looking through the clauses that group them.

    Recursion is bounded by the grammar here — a clause holds names, not further
    clauses — so it needs no explicit stack.
    """
    for child in heritage.named_children:
        if child.type in clauses:
            yield from _base_names(child, clauses)
        elif child.type in _NAMED_TYPES and (name := _name_of(child)):
            yield name


def _encloser(parsed: _Parsed, node: Node) -> CodeRef:
    """The symbol *node* sits in, or the file itself when none does."""
    symbol = enclosing_symbol(parsed.symbols, node.start_point[0] + 1)
    return CodeRef(parsed.path) if symbol is None else CodeRef(symbol.path, symbol.qualified_name)


def _name_of(node: Node) -> str:
    """The bare name *node* ends in: ``self.store.write`` names ``write``."""
    while node.type not in _NAME_TYPES and node.named_children:
        node = node.named_children[-1]
    return _text(node) if node.type in _NAME_TYPES else ""


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
