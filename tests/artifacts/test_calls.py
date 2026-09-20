"""Call, unresolved-call and implementation relations read off source (FR-018).

Read through `extract_relations`, which is the whole seam: the files of one unit
of work as path-to-text, answered with the relations between the symbols they
define, each carrying how many sites it was seen at. The halves worth testing
are the ones resolution decides: a name that resolves to exactly one definition
is a `calls` edge, and every other name survives as an `unresolved_call` rather
than being dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from processrecall.artifacts.calls import CodeRef, Relation, extract_relations

PYTHON_SOURCE = """\
def helper(step):
    return step


def main(step):
    return helper(step)
"""

RECORDER = Path("src/recorder.py")


def _from(relations: tuple[Relation, ...], qualified_name: str | None) -> list[Relation]:
    """The relations whose source is the symbol called *qualified_name*."""
    return [r for r in relations if r.source.qualified_name == qualified_name]


def test_a_call_resolves_to_the_definition_it_names() -> None:
    """FR-018: a callee defined in the same file is a `calls` edge to it."""
    relations = extract_relations({RECORDER: PYTHON_SOURCE})

    assert _from(relations, "main") == [
        Relation(CodeRef(RECORDER, "main"), "calls", CodeRef(RECORDER, "helper"), None, 1)
    ]


def test_a_call_no_definition_answers_survives_as_its_name() -> None:
    """FR-018: an unresolved call keeps its target name rather than being dropped."""
    relations = extract_relations({RECORDER: "def main(step):\n    return transform(step)\n"})

    assert _from(relations, "main") == [
        Relation(CodeRef(RECORDER, "main"), "unresolved_call", None, "transform", 1)
    ]


PROTOCOL_SOURCE = """\
class Meta(type):
    pass


class Sink:
    def write(self, step):
        return step


class Recorder(Sink, metaclass=Meta):
    def write(self, step):
        return step
"""


def test_a_class_implements_the_base_it_names() -> None:
    """FR-018: a base resolving to one definition is an `implements` edge to it."""
    relations = extract_relations({RECORDER: PROTOCOL_SOURCE})

    assert _from(relations, "Recorder") == [
        Relation(CodeRef(RECORDER, "Recorder"), "implements", CodeRef(RECORDER, "Sink"), None, 1)
    ]


TYPESCRIPT_SOURCE = """\
class Base {
  write(step) {
    return step;
  }
}

class Recorder extends Base {
  record(step) {
    helper(step);
    this.write(step);
    return new Base();
  }
}

function helper(step) {
  return step;
}
"""


@pytest.mark.parametrize("suffix", [".ts", ".js"])
def test_the_typescript_family_reads_calls_and_bases_alike(suffix: str) -> None:
    """FR-018: TypeScript and JavaScript are one grammar, `new` included."""
    path = Path("src/sink").with_suffix(suffix)

    relations = extract_relations({path: TYPESCRIPT_SOURCE})

    assert _from(relations, "Recorder") == [
        Relation(CodeRef(path, "Recorder"), "implements", CodeRef(path, "Base"), None, 1)
    ]
    assert _from(relations, "Recorder.record") == [
        Relation(CodeRef(path, "Recorder.record"), "calls", CodeRef(path, "Base"), None, 1),
        Relation(CodeRef(path, "Recorder.record"), "calls", CodeRef(path, "Base.write"), None, 1),
        Relation(CodeRef(path, "Recorder.record"), "calls", CodeRef(path, "helper"), None, 1),
    ]


STORE = Path("src/store.py")
ANOTHER = Path("src/queue.py")
SINK = Path("src/sink.ts")

STORE_SOURCE = """\
def store(step):
    return step
"""

CALLER_SOURCE = """\
def main(step):
    return store(step)
"""


def test_repeated_calls_are_one_relation_carrying_its_site_count() -> None:
    """FR-018: sites count the occurrences, one relation however many there are."""
    source = f"{STORE_SOURCE}\n\ndef main(step):\n    return store(store(step))\n"

    relations = extract_relations({STORE: source})

    assert _from(relations, "main") == [
        Relation(CodeRef(STORE, "main"), "calls", CodeRef(STORE, "store"), None, 2)
    ]


def test_a_call_resolves_to_a_definition_in_another_file() -> None:
    """FR-018: resolution is across the sources, which is why they come together."""
    relations = extract_relations({RECORDER: CALLER_SOURCE, STORE: STORE_SOURCE})

    assert _from(relations, "main") == [
        Relation(CodeRef(RECORDER, "main"), "calls", CodeRef(STORE, "store"), None, 1)
    ]


def test_a_call_prefers_the_definition_in_its_own_file() -> None:
    """FR-018: a name its own file defines is that one, not the other file's."""
    relations = extract_relations(
        {RECORDER: STORE_SOURCE + "\n\n" + CALLER_SOURCE, STORE: STORE_SOURCE}
    )

    assert _from(relations, "main") == [
        Relation(CodeRef(RECORDER, "main"), "calls", CodeRef(RECORDER, "store"), None, 1)
    ]


def test_a_name_two_other_files_define_stays_unresolved() -> None:
    """FR-018: an ambiguous name keeps its name rather than guessing a target."""
    sources = {RECORDER: CALLER_SOURCE, STORE: STORE_SOURCE, ANOTHER: STORE_SOURCE}

    relations = extract_relations(sources)

    assert _from(relations, "main") == [
        Relation(CodeRef(RECORDER, "main"), "unresolved_call", None, "store", 1)
    ]


def test_a_name_only_another_language_defines_stays_unresolved() -> None:
    """FR-018: a definition in another language is not a target for this one."""
    relations = extract_relations(
        {SINK: "function main(step) {\n  return store(step);\n}\n", STORE: STORE_SOURCE}
    )

    assert _from(relations, "main") == [
        Relation(CodeRef(SINK, "main"), "unresolved_call", None, "store", 1)
    ]


def test_a_typescript_call_resolves_into_a_javascript_definition() -> None:
    """FR-018: TypeScript, its JSX dialect and JavaScript resolve into each other."""
    caller = Path("src/caller.ts")
    callee = Path("src/callee.js")
    relations = extract_relations(
        {
            caller: "function main(step) {\n  return helper(step);\n}\n",
            callee: "function helper(step) {\n  return step;\n}\n",
        }
    )

    assert _from(relations, "main") == [
        Relation(CodeRef(caller, "main"), "calls", CodeRef(callee, "helper"), None, 1)
    ]


def test_a_call_outside_every_definition_belongs_to_the_file() -> None:
    """FR-018: a call at module level relates from the file, which has no name."""
    relations = extract_relations({STORE: f"{STORE_SOURCE}\n\nstore(None)\n"})

    assert _from(relations, None) == [
        Relation(CodeRef(STORE), "calls", CodeRef(STORE, "store"), None, 1)
    ]


ATTRIBUTE_SOURCE = """class Store:
    def write(self, step):
        return step


def main(store, step):
    return store.write(step)
"""


def test_a_call_through_an_attribute_names_the_method_it_ends_in() -> None:
    """FR-018: `store.write` mentions `write`, which is what resolution works on."""
    relations = extract_relations({RECORDER: ATTRIBUTE_SOURCE})

    assert _from(relations, "main") == [
        Relation(CodeRef(RECORDER, "main"), "calls", CodeRef(RECORDER, "Store.write"), None, 1)
    ]
