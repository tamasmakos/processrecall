"""The tags queries themselves, vendored rather than asked of the grammars (FR-018).

One seam, :func:`tag_captures`: a language and a parsed root, answered with the
nodes the vendored query captured, by capture name. The names follow the
tree-sitter tags convention — ``name.reference.call``,
``name.reference.implements`` — and the patterns capture the *name* node rather
than the site, because the bare name is what resolution in
:mod:`processrecall.artifacts.calls` keys definitions on.

The queries live here because the grammars do not ship the ones FR-018 needs:
``tree_sitter_language_pack`` bundles no ``.scm`` files at all, and the tags
query it answers with for the TypeScript family captures definitions and types
but no call site. Only the reference patterns are vendored — a definition is
already read, with its line range, by :mod:`processrecall.artifacts.parse`.

Captures also move: tree-sitter 0.25 took ``captures`` off the query and put it
on a cursor. ``pyproject.toml`` pins ``tree-sitter>=0.26.0``, so only the
post-0.25 spelling — a cursor's ``captures`` — is ever reached here.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import TYPE_CHECKING

from tree_sitter import Query, QueryCursor
from tree_sitter_language_pack import get_language

if TYPE_CHECKING:
    from tree_sitter import Node

_PYTHON_TAGS = """
(call
  function: [
    (identifier) @name.reference.call
    (attribute
      attribute: (identifier) @name.reference.call)
  ])

; A base type is named positionally in the superclass list, so a metaclass
; keyword argument and a list of type arguments — neither a direct child of it —
; are not read as one.
(class_definition
  superclasses: (argument_list
    [
      (identifier) @name.reference.implements
      (attribute
        attribute: (identifier) @name.reference.implements)
    ]))
"""

#: TypeScript, its JSX dialect and JavaScript spell a call site identically, so
#: the three share these patterns and differ only in their class heritage.
_TYPESCRIPT_FAMILY_CALLS = """
(call_expression
  function: [
    (identifier) @name.reference.call
    (member_expression
      property: (property_identifier) @name.reference.call)
  ])

(new_expression
  constructor: [
    (identifier) @name.reference.call
    (member_expression
      property: (property_identifier) @name.reference.call)
  ])
"""

_TYPESCRIPT_TAGS = f"""{_TYPESCRIPT_FAMILY_CALLS}
(class_declaration
  (class_heritage
    [
      (extends_clause
        value: [
          (identifier) @name.reference.implements
          (member_expression
            property: (property_identifier) @name.reference.implements)
        ])
      (implements_clause
        (type_identifier) @name.reference.implements)
    ]))
"""

#: JavaScript's grammar gives the heritage no clause node: what a class extends
#: is the expression directly inside it.
_JAVASCRIPT_TAGS = f"""{_TYPESCRIPT_FAMILY_CALLS}
(class_declaration
  (class_heritage
    [
      (identifier) @name.reference.implements
      (member_expression
        property: (property_identifier) @name.reference.implements)
    ]))
"""

#: The vendored query per language. A language absent here is one FR-018 reads
#: no relations from, rather than one whose relations are silently empty.
_TAGS: Mapping[str, str] = {
    "python": _PYTHON_TAGS,
    "typescript": _TYPESCRIPT_TAGS,
    "tsx": _TYPESCRIPT_TAGS,
    "javascript": _JAVASCRIPT_TAGS,
}

#: The languages a query is vendored for — the one authority on whether
#: :mod:`processrecall.artifacts.calls` can read relations from a language, so
#: that module's own grouping of them can never admit one this module lacks.
LANGUAGES: frozenset[str] = frozenset(_TAGS)


def tag_captures(language: str, root: Node) -> Mapping[str, tuple[Node, ...]]:
    """What the vendored *language* query captures under *root*, by capture name.

    Args:
        language: The grammar name a vendored query is keyed by.
        root: The root node of a tree parsed with that grammar.

    Returns:
        Every captured node, grouped by the capture naming it. A capture no node
        matched is absent rather than empty.

    Raises:
        KeyError: If no query is vendored for *language*.
    """
    captured = _captures(_query(language), root)
    return {name: tuple(nodes) for name, nodes in captured.items()}


@cache
def _query(language: str) -> Query:
    """The vendored query for *language*, compiled once per process."""
    return Query(get_language(language), _TAGS[language])


def _captures(query: Query, root: Node) -> Mapping[str, list[Node]]:
    """*query*'s captures over *root*, by capture name."""
    return QueryCursor(query).captures(root)
