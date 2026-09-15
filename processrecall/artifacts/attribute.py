"""An edit placed where it landed: the enclosing symbol, else the file (FR-063).

One seam, :func:`attribute_edit`: the edit as it was recorded, answered with the
``path::qualified.name`` a step carries as its ``symbol_ref``. The edited text
is located in the file text captured at record time, since the file on disk has
moved on by the time this layer runs (FR-064), and the line that lands in picks
the narrowest symbol whose range contains it.

A symbol that does not resolve — an unclaimed language, an edit with no text to
locate, text that is not findable or not uniquely findable, a line outside every
definition — is the file itself rather than an error: a coarser attribution is
still an attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from processrecall.artifacts.parse import Symbol, parse_source


@dataclass(frozen=True, slots=True)
class Edit:
    """One edit to one file, as much of it as attribution needs.

    ``text`` is the file as it stood at record time, and ``edited_text`` is the
    fragment the edit named within it — the text replaced, which is what places
    the edit in the version being read. Callers that only have a transcript
    record reconstruct ``text`` from it (the pre-edit file plus the recorded
    replacement) before building an ``Edit``.
    """

    path: Path
    text: str
    edited_text: str


def attribute_edit(edit: Edit) -> str:
    """Where *edit* landed, as ``path::qualified.name`` or the path alone."""
    reference = edit.path.as_posix()
    if (line := _edited_line(edit)) is None:
        return reference
    enclosing = _enclosing(parse_source(edit.path, edit.text).symbols, line)
    return reference if enclosing is None else f"{reference}::{enclosing.qualified_name}"


def _edited_line(edit: Edit) -> int | None:
    """The 1-based line *edit* starts at, or ``None`` if it is not locatable."""
    if not edit.edited_text.strip():
        # No text to locate — nothing to search for, so nowhere to land.
        return None
    if (found := edit.text.find(edit.edited_text)) < 0:
        return None
    if edit.text.find(edit.edited_text, found + 1) >= 0:
        # Ambiguous: the fragment recurs, so no single location is correct.
        return None
    return edit.text.count("\n", 0, found) + 1


def _enclosing(symbols: tuple[Symbol, ...], line: int) -> Symbol | None:
    """The narrowest of *symbols* whose range contains *line*, if any."""
    containing = [symbol for symbol in symbols if symbol.start_line <= line <= symbol.end_line]
    if not containing:
        return None
    return min(containing, key=lambda symbol: symbol.end_line - symbol.start_line)
