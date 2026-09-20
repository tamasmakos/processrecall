"""Source text read as the symbols and imports FR-062 asks every language for.

One seam, :func:`parse_source`: a path for the language and the text already in
hand, answered with qualified names beside the path they came from, line ranges
and imported modules. What
differs between Python, TypeScript/JavaScript, Go, Rust and shell is only which
node types a grammar calls a definition and which field holds the name, so that
pair is the whole per-language record and the traversal over it is written once.

tree-sitter is imported here and in :mod:`processrecall.artifacts.calls` and
nowhere else in the package: parsing is the one thing FR-064 keeps off the hot
path, so nothing on it may import either module.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from tree_sitter import Parser
from tree_sitter_language_pack import get_language

if TYPE_CHECKING:
    from tree_sitter import Language, Node


@dataclass(frozen=True, slots=True)
class Symbol:
    """A definition an edit can be attributed to, by name and by line.

    ``path`` is the file it was read from, exactly as :func:`parse_source` was
    given it: a symbol carries it beside its qualified name so the pair that
    identifies it travels together (FR-017).

    ``start_line`` and ``end_line`` are 1-based and inclusive, the numbering an
    editor reports, so a line located in the file compares against them
    directly (FR-063).
    """

    path: Path
    qualified_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ParsedSource:
    """What one file defines and what it depends on.

    ``language`` is ``None`` for a suffix no grammar claims; the symbols and
    imports are then empty, which is the case FR-063 falls back to the file for
    rather than an error.

    ``imports`` is unresolved specifier text (``"./step"``, ``"std::fmt"``), not
    yet read by any task in this feature; FR-062's own glossary names its reader
    — files relating to each other by import in the graph — as later work.
    """

    language: str | None
    symbols: tuple[Symbol, ...]
    imports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Grammar:
    """What one language calls the two things FR-062 asks for.

    ``symbols`` and ``imports`` map a tree-sitter node type to the field holding
    the text wanted from it: the definition's name, or the imported module.
    ``qualifiers`` is for the languages that attach a definition to a type
    without nesting it inside one — Go's method receiver — and names the field
    whose type qualifies the symbol. ``import_names`` is for shell, where
    sourcing another file is an ordinary command rather than a statement of its
    own, so the node type alone does not make it an import: it holds the
    ``name`` field texts that do.
    """

    symbols: Mapping[str, str]
    imports: Mapping[str, str]
    qualifiers: Mapping[str, str] = MappingProxyType({})
    import_names: Mapping[str, frozenset[str]] = MappingProxyType({})


#: TypeScript, its JSX dialect and JavaScript are one grammar family with one
#: set of node types, so they share the record rather than repeating it.
_TYPESCRIPT = _Grammar(
    symbols={
        "class_declaration": "name",
        "function_declaration": "name",
        "method_definition": "name",
    },
    imports={"import_statement": "source"},
)

_GRAMMARS: Mapping[str, _Grammar] = {
    "python": _Grammar(
        symbols={"class_definition": "name", "function_definition": "name"},
        imports={"import_statement": "name", "import_from_statement": "module_name"},
    ),
    "go": _Grammar(
        symbols={
            "type_spec": "name",
            "function_declaration": "name",
            "method_declaration": "name",
        },
        imports={"import_spec": "path"},
        qualifiers={"method_declaration": "receiver"},
    ),
    "rust": _Grammar(
        symbols={
            "struct_item": "name",
            "enum_item": "name",
            "trait_item": "name",
            "mod_item": "name",
            "impl_item": "type",
            "function_item": "name",
        },
        imports={"use_declaration": "argument"},
    ),
    "bash": _Grammar(
        symbols={"function_definition": "name"},
        imports={"command": "argument"},
        import_names={"command": frozenset({"source", "."})},
    ),
    "typescript": _TYPESCRIPT,
    "tsx": _TYPESCRIPT,
    "javascript": _TYPESCRIPT,
}

#: Which grammar a file gets, by suffix. A suffix absent here is a file this
#: layer has no opinion about rather than one it guesses at.
_LANGUAGE_BY_SUFFIX: Mapping[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".sh": "bash",
    ".bash": "bash",
}


def parse_source(path: Path, text: str) -> ParsedSource:
    """The symbols and imports of *text*, read with the grammar *path* selects."""
    return parse_tree(path, text)[0]


def parse_tree(path: Path, text: str) -> tuple[ParsedSource, Node | None]:
    """*text*'s parsed source, alongside the root node it was read from.

    Public so :mod:`processrecall.artifacts.calls` can walk the same tree for
    call sites, which parsing itself does not report, without tree-sitting the
    file a second time. ``root`` is ``None`` exactly when ``language`` is.
    """
    language = _LANGUAGE_BY_SUFFIX.get(path.suffix)
    if language is None:
        return ParsedSource(language=None, symbols=(), imports=()), None
    tree = Parser(_language(language)).parse(text.encode("utf-8"))
    reading = _Reading(_GRAMMARS[language], path)
    reading.visit(tree.root_node)
    parsed = ParsedSource(language, tuple(reading.symbols), tuple(reading.imports))
    return parsed, tree.root_node


def enclosing_symbol(symbols: tuple[Symbol, ...], line: int) -> Symbol | None:
    """The narrowest of *symbols* whose range contains *line*, if any.

    The one way a position in a file becomes a symbol, whether the position is
    an edit's (FR-063) or a call site's (FR-018).
    """
    containing = [symbol for symbol in symbols if symbol.start_line <= line <= symbol.end_line]
    if not containing:
        return None
    return min(containing, key=lambda symbol: symbol.end_line - symbol.start_line)


class _Reading:
    """One traversal of one tree, carrying the enclosing names down with it."""

    def __init__(self, grammar: _Grammar, path: Path) -> None:
        self._grammar = grammar
        self._path = path
        self.symbols: list[Symbol] = []
        self.imports: list[str] = []

    def __repr__(self) -> str:
        return f"{type(self).__name__}(symbols={len(self.symbols)}, imports={len(self.imports)})"

    def visit(self, node: Node, prefix: str = "") -> None:
        """Record every node reachable from *node*, depth-first, under *prefix*.

        An explicit stack rather than recursion: a real source file can nest
        deep enough (minified JS, long expression chains) to exceed CPython's
        recursion limit, and this runs unattended (FR-064).
        """
        stack: list[tuple[Node, str]] = [(node, prefix)]
        while stack:
            current, current_prefix = stack.pop()
            inner = self._record(current, current_prefix)
            stack.extend((child, inner) for child in reversed(current.named_children))

    def _record(self, node: Node, prefix: str) -> str:
        """Note *node* if the grammar names it; return the prefix its children carry."""
        if node.type in self._grammar.symbols:
            return self._define(node, prefix)
        if (field := self._grammar.imports.get(node.type)) is not None and self._imports(node):
            self.imports.extend(_field_texts(node, field))
        return prefix

    def _imports(self, node: Node) -> bool:
        """Whether *node* really imports, where its type alone does not say so."""
        spelled = self._grammar.import_names.get(node.type)
        if spelled is None:
            return True
        named = node.child_by_field_name("name")
        return named is not None and _text(named) in spelled

    def _qualifier(self, node: Node) -> str:
        """What *node* hangs off without nesting inside it, as a name prefix."""
        held = self._grammar.qualifiers.get(node.type)
        if held is None or (receiver := node.child_by_field_name(held)) is None:
            return ""
        named = _first_of_type(receiver, "type_identifier")
        return "" if named is None else f"{_text(named)}."

    def _define(self, node: Node, prefix: str) -> str:
        """Keep *node* as a symbol qualified by *prefix*; return its own prefix."""
        named = node.child_by_field_name(self._grammar.symbols[node.type])
        if named is None:
            return prefix
        qualified = f"{prefix}{self._qualifier(node)}{_text(named)}"
        self.symbols.append(
            Symbol(self._path, qualified, node.start_point[0] + 1, node.end_point[0] + 1)
        )
        return f"{qualified}."


@cache
def _language(language: str) -> Language:
    """The pack's grammar for *language*, built once per process.

    Immutable and safe to share; the :class:`~tree_sitter.Parser` built from it
    is not, so each :func:`parse_source` call gets its own.
    """
    return get_language(language)


def _field_texts(node: Node, field: str) -> Iterator[str]:
    """The text of every *field* child of *node*, in source order.

    An ``aliased_import`` (Python's ``import x as y``) names the binding, not
    the module, so its own ``name`` field is read instead.
    """
    for child in node.children_by_field_name(field):
        if child.type == "aliased_import" and (name := child.child_by_field_name("name")):
            child = name
        yield _unquoted(_text(child))


def _first_of_type(node: Node, node_type: str) -> Node | None:
    """The first descendant of *node* of *node_type*, depth-first.

    An explicit stack rather than recursion, for the same unbounded-nesting
    reason as :meth:`_Reading.visit`.
    """
    stack = list(reversed(node.named_children))
    while stack:
        child = stack.pop()
        if child.type == node_type:
            return child
        stack.extend(reversed(child.named_children))
    return None


def _unquoted(text: str) -> str:
    """*text* without the quotes a string-literal import path carries."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        return text[1:-1]
    return text


def _text(node: Node) -> str:
    """*node*'s source text, decoded."""
    return "" if node.text is None else node.text.decode("utf-8", errors="replace")
