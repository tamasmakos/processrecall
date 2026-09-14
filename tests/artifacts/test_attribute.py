"""An edit placed in the symbol that encloses it, or in the file (FR-063).

Read through `attribute_edit`, which is the whole seam: the path, the file text as it
stood at record time and the text the edit touched, answered with the
`path::qualified.name` a step carries as its `symbol_ref`. The halves worth
testing are the ones that decide which name comes back: nesting, so the
narrowest enclosing symbol wins over the one around it; and the three ways a
symbol fails to resolve, each of which is the file itself rather than a raise.
"""

from __future__ import annotations

from pathlib import Path

from processrecall.artifacts.attribute import Edit, attribute_edit

PYTHON_SOURCE = '''\
import os


class Recorder:
    """Doc."""

    def record(self, step):
        return step


def main():
    return Recorder()
'''


def test_edit_is_attributed_to_the_narrowest_enclosing_symbol() -> None:
    """FR-063: an edit inside a method is the method's, not its class's."""
    edit = Edit(Path("src/recorder.py"), PYTHON_SOURCE, "        return step\n")

    assert attribute_edit(edit) == "src/recorder.py::Recorder.record"


def test_an_edit_between_definitions_is_the_file_itself() -> None:
    """FR-063: a line inside no symbol falls back to the file."""
    edit = Edit(Path("src/recorder.py"), PYTHON_SOURCE, "import os\n")

    assert attribute_edit(edit) == "src/recorder.py"


def test_a_language_no_grammar_claims_is_the_file_itself() -> None:
    """FR-063: an unsupported language falls back to the file, not a raise."""
    edit = Edit(Path("notes.md"), "# Notes\n\nsecond line\n", "second line\n")

    assert attribute_edit(edit) == "notes.md"


def test_text_no_longer_in_the_file_is_the_file_itself() -> None:
    """FR-063: edited text that cannot be located falls back to the file."""
    edit = Edit(Path("src/recorder.py"), PYTHON_SOURCE, "        return other\n")

    assert attribute_edit(edit) == "src/recorder.py"


def test_text_found_more_than_once_is_the_file_itself() -> None:
    """FR-063: an ambiguous fragment falls back rather than picking one occurrence."""
    source = PYTHON_SOURCE + "\n\n    def other(self):\n        return step\n"
    edit = Edit(Path("src/recorder.py"), source, "        return step\n")

    assert attribute_edit(edit) == "src/recorder.py"


def test_empty_edited_text_is_the_file_itself() -> None:
    """FR-063: an edit with no text to locate falls back to the file, not line 1."""
    edit = Edit(Path("src/recorder.py"), PYTHON_SOURCE, "")

    assert attribute_edit(edit) == "src/recorder.py"
