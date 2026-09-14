"""Source files read as symbols and imports, one grammar per language (FR-062).

Read through `parse_source`, which is the whole seam: a path (for the language)
and the text already in hand at record time, answered with the qualified names
and line ranges an edit can be attributed to, plus what the file imports. The
halves worth testing are the ones a grammar gets wrong: nesting, so a method is
qualified by its class; line ranges, so the range really spans the body; and a
language nobody declared, which is an answer rather than a raise (FR-063).
"""

from __future__ import annotations

from pathlib import Path

from processrecall.artifacts.parse import ParsedSource, Symbol, parse_source

PYTHON_SOURCE = '''\
import os
from collections import Counter


class Recorder:
    """Doc."""

    def record(self, step):
        return step


def main():
    return Recorder()
'''


def _named(parsed: ParsedSource, qualified_name: str) -> Symbol:
    """The one symbol called *qualified_name*, asserted to be there."""
    matches = [symbol for symbol in parsed.symbols if symbol.qualified_name == qualified_name]
    assert matches, f"{qualified_name} not among {[s.qualified_name for s in parsed.symbols]}"
    return matches[0]


def test_python_symbols_are_qualified_by_their_nesting() -> None:
    """FR-062: a method's qualified name nests it under its class."""
    parsed = parse_source(Path("recorder.py"), PYTHON_SOURCE)

    assert parsed.language == "python"
    assert {symbol.qualified_name for symbol in parsed.symbols} == {
        "Recorder",
        "Recorder.record",
        "main",
    }


def test_python_line_range_spans_the_whole_definition() -> None:
    """FR-062: a symbol's line range covers its whole body, not just its header."""
    parsed = parse_source(Path("recorder.py"), PYTHON_SOURCE)

    record = _named(parsed, "Recorder.record")
    assert (record.start_line, record.end_line) == (8, 9)


def test_python_imports_name_the_module_not_the_binding() -> None:
    """FR-062: an import names the module it pulls in, not the local binding."""
    parsed = parse_source(Path("recorder.py"), PYTHON_SOURCE)

    assert parsed.imports == ("os", "collections")


def test_python_aliased_import_names_the_module_not_the_alias() -> None:
    """FR-062: ``import numpy as np`` is recorded as ``numpy``, not ``numpy as np``."""
    parsed = parse_source(Path("recorder.py"), "import numpy as np\n")

    assert parsed.imports == ("numpy",)


TYPESCRIPT_SOURCE = """\
import { Step } from "./step";
import recall from "../recall";

export class Recorder {
  record(step: Step): Step {
    return step;
  }
}

export function main(): Recorder {
  return new Recorder();
}
"""


def test_typescript_methods_are_qualified_by_their_class() -> None:
    """FR-062: a method's qualified name nests it under its class."""
    parsed = parse_source(Path("recorder.ts"), TYPESCRIPT_SOURCE)

    assert parsed.language == "typescript"
    assert "Recorder.record" in {symbol.qualified_name for symbol in parsed.symbols}
    assert _named(parsed, "main").start_line == 10


def test_typescript_imports_are_the_module_paths_unquoted() -> None:
    """FR-062: an import specifier is recorded without its quoting."""
    parsed = parse_source(Path("recorder.ts"), TYPESCRIPT_SOURCE)

    assert parsed.imports == ("./step", "../recall")


def test_javascript_shares_the_typescript_grammar_shape() -> None:
    """FR-062: JavaScript reuses the TypeScript grammar record."""
    parsed = parse_source(
        Path("recorder.mjs"), 'import recall from "./recall";\nfunction main() {}\n'
    )

    assert parsed.language == "javascript"
    assert parsed.imports == ("./recall",)
    assert _named(parsed, "main").start_line == 2


GO_SOURCE = """\
package recorder

import (
	"fmt"
	"example.com/step"
)

type Recorder struct {
	name string
}

func (r *Recorder) Record(step string) string {
	return fmt.Sprint(step)
}
"""


def test_go_method_is_qualified_by_its_receiver_type() -> None:
    """FR-062: a method's qualified name nests it under its receiver type."""
    parsed = parse_source(Path("recorder.go"), GO_SOURCE)

    assert parsed.language == "go"
    assert {symbol.qualified_name for symbol in parsed.symbols} == {
        "Recorder",
        "Recorder.Record",
    }
    record = _named(parsed, "Recorder.Record")
    assert (record.start_line, record.end_line) == (12, 14)


def test_go_imports_are_the_quoted_paths() -> None:
    """FR-062: an import path is recorded without its quoting."""
    parsed = parse_source(Path("recorder.go"), GO_SOURCE)

    assert parsed.imports == ("fmt", "example.com/step")


RUST_SOURCE = """\
use std::fmt;
use crate::step::Step;

pub struct Recorder {
    name: String,
}

impl Recorder {
    pub fn record(&self, step: Step) -> Step {
        step
    }
}
"""


def test_rust_method_is_qualified_by_the_impl_it_sits_in():
    """FR-062, FR-063: the ``impl`` block is itself a symbol named after the
    type it implements, so ``Recorder`` appears twice — once for the struct
    body (4-6), once for the impl block (8-12) — with disjoint line ranges.
    FR-063 attributes a line to the narrowest symbol whose range contains it,
    so the two same-named entries never compete for the same line.
    """
    parsed = parse_source(Path("recorder.rs"), RUST_SOURCE)

    assert parsed.language == "rust"
    assert [(s.qualified_name, s.start_line, s.end_line) for s in parsed.symbols] == [
        ("Recorder", 4, 6),
        ("Recorder", 8, 12),
        ("Recorder.record", 9, 11),
    ]


def test_rust_imports_are_the_use_paths() -> None:
    """FR-062: a ``use`` path is recorded as-is."""
    parsed = parse_source(Path("recorder.rs"), RUST_SOURCE)

    assert parsed.imports == ("std::fmt", "crate::step::Step")


SHELL_SOURCE = """\
#!/usr/bin/env bash
source ./lib/common.sh
. ./lib/log.sh

record() {
  echo "$1"
}

echo not_an_import
"""


def test_shell_functions_are_symbols() -> None:
    """FR-062: a shell function definition is a symbol."""
    parsed = parse_source(Path("record.sh"), SHELL_SOURCE)

    assert parsed.language == "bash"
    record = _named(parsed, "record")
    assert (record.start_line, record.end_line) == (5, 7)


def test_shell_imports_are_only_the_sourced_files() -> None:
    """FR-062: only ``source``/``.`` commands count as imports, not any command."""
    parsed = parse_source(Path("record.sh"), SHELL_SOURCE)

    assert parsed.imports == ("./lib/common.sh", "./lib/log.sh")


def test_a_language_no_grammar_claims_is_an_answer_not_a_raise() -> None:
    """FR-063: an unrecognised suffix answers with an empty result, not a raise."""
    parsed = parse_source(Path("notes.md"), "# not source\n")

    assert parsed == ParsedSource(language=None, symbols=(), imports=())
