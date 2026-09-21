"""Docs that name code must match the code they name."""

from __future__ import annotations

import re
import tomllib
from dataclasses import fields
from pathlib import Path

from processrecall.cli.__main__ import COMMANDS, SUBJECTS
from processrecall.cli.doctor import COLLECTOR_CONFIG
from processrecall.config import Config
from processrecall.graph.schema import LAYERS
from processrecall.guidance.triggers import Trigger
from processrecall.integrations.claude_code.hooks import DENY_LIST, OPTOUT_MARKER
from processrecall.server.mcp.stdio_server import TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
RECOVERY_HEADING = "## Recovering a broken release"
RETIRED_BUILD_BACKEND = "hatchling"
EVENT_SOURCES_HEADING = "### 3.2 Event sources"
DEFERRED_HEADING = "### 3.13 Deferred"
DEPENDENCIES_HEADING = "## 5. Dependencies"
LEDGER_HEADING = "## 6. Removal ledger"
LAYERS_HEADING = "## The three layers"
INGEST_HEADING = "## The ingest path"
SETTINGS_HEADING = "## The settings"
TELEMETRY_HEADING = "## Telemetry"

#: How the shipped example configuration spells a harness variable it tells a
#: reader to export, in the comment block above the collector pipeline.
HARNESS_EXPORT = re.compile(r"^#\s+export (\w+)=", re.MULTILINE)

#: The one export among those the shipped configuration marks as the gate that
#: puts commands and tool inputs on the records.
TOOL_DETAILS_GATE = re.compile(r"^#\s+export (\w+)=\S*\s+# the tool-details gate", re.MULTILINE)

#: The readme's promise about what the memory keeps, whose limit FR-013 names.
STORAGE_PROMISE = "never prompt text, file contents or credentials"


def _package_source() -> str:
    """The concatenated text of every module under `processrecall/`."""
    return "\n".join(
        p.read_text(encoding="utf-8") for p in (REPO_ROOT / "processrecall").rglob("*.py")
    )


def test_docs_name_no_absent_symbols() -> None:
    """Docs must not describe code that does not exist (FR-026, SC-017).

    Greps `processrecall/` for each symbol FR-026 named as removed — the
    `community_boost` / `shape_score` ranking stage and `ArcadeDBSTMStore` /
    `ArcadeDBLTMStore` — so the removal claim is checked against the code, not
    assumed. Then greps `docs/architecture.md` for the same symbols, plus
    "the web server", a prose claim rather than a single token.
    """
    absent_symbols = {"community_boost", "shape_score", "ArcadeDBSTMStore", "ArcadeDBLTMStore"}

    code_text = _package_source()
    for symbol in absent_symbols:
        assert symbol not in code_text, (
            f"{symbol} exists in processrecall/ — the FR-026 doc claim is stale"
        )

    for doc_name in ("architecture.md",):
        doc_path = REPO_ROOT / "docs" / doc_name
        text = doc_path.read_text(encoding="utf-8")
        for symbol in absent_symbols:
            assert symbol not in text, (
                f"{doc_name} still names {symbol}, absent from processrecall/"
            )
        assert "web server" not in text.lower(), f"{doc_name} still names the web server"


def test_docs_describe_the_plugin_not_the_service() -> None:
    """The README must document every surface a reader can reach (T081).

    Read off the code rather than written out here: the four `Trigger`
    occasions, the six subcommands `processrecall.cli.__main__` registers,
    the four `TOOLS` the stdio server exposes, the subjects `show` reports —
    the counters among them — and the two exclusion markers capture is
    suppressed by. A rename that misses the README must fail here.
    """
    assert len(COMMANDS) == 6, f"expected six subcommands, found {COMMANDS}"

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    named = (
        tuple(trigger.value for trigger in Trigger)
        + COMMANDS
        + tuple(tool.name for tool in TOOLS)
        + SUBJECTS
        + (OPTOUT_MARKER.as_posix(), DENY_LIST)
    )
    missing = [name for name in named if name not in readme]
    assert not missing, f"README.md documents none of {missing}, which the plugin ships"


def test_docs_name_no_removed_service() -> None:
    """The documentation must describe the plugin a reader installs, not the fork's source.

    Checked the way `test_docs_name_no_absent_symbols` checks its own:
    `ingest_memory` and `recall_memory` are absent from `processrecall/`, so a
    document naming either instructs a reader to call code that isn't there.
    The dated design record is exempt: what the fork dropped is part of what
    it records.
    """
    absent = {"ingest_memory", "recall_memory"}
    code_text = _package_source()
    for symbol in absent:
        assert symbol not in code_text, f"{symbol} is back in processrecall/ — this check is stale"

    service_words = absent | {"ArcadeDB", "docker compose"}
    documents = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]
    for document in documents:
        if document.name == "design.md":
            continue
        text = document.read_text(encoding="utf-8")
        still_sold = sorted(word for word in service_words if word in text)
        assert not still_sold, (
            f"{document.relative_to(REPO_ROOT)} still documents the service: {still_sold}"
        )


def _section_body(text: str, heading: str) -> str:
    """The text under `heading`, up to the next heading of that depth or shallower.

    Empty if the heading is absent.
    """
    if (start := text.find(f"\n{heading}\n")) == -1:
        return ""
    body = text[start + len(heading) + 2 :]
    depth = len(heading) - len(heading.lstrip("#"))
    following = re.compile(rf"^#{{1,{depth}}} ", re.MULTILINE).search(body)
    return body if following is None else body[: following.start()]


def test_docs_document_the_recovery_route() -> None:
    """Recovery from a broken published version must be written down (FR-015a).

    The route has five parts and only the whole of it is correct: a new patch
    version supersedes the broken one, the plugin pin advances to it, the broken
    version is withdrawn from the index, withdrawal alone is not the fix
    because a plugin pinned to that exact number still resolves it, and no
    version is ever deleted or its number reused. Each part is asserted on its
    own so a rewrite that drops one fails here naming which.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = _section_body(readme, RECOVERY_HEADING)
    assert section, f"README.md has no {RECOVERY_HEADING!r} section"

    required = {
        "the superseding patch version": "patch version",
        "the plugin pin advanced to it": ".claude-plugin/plugin.json",
        "withdrawing the broken version from the index": "yank",
        "that withdrawal alone is not the fix": "not the fix",
        "that no version is deleted": "deleted",
        "that no version number is reused": "reused",
    }
    missing = sorted(part for part, token in required.items() if token not in section)
    assert not missing, f"{RECOVERY_HEADING} does not document {missing}"


def test_design_docs_name_the_declared_build_backend() -> None:
    """The toolchain the design record lists must be the one that builds the package (FR-028).

    The backend is read off `pyproject.toml` rather than written out here, so a
    later switch fails this check instead of ageing the design record in
    silence. Only the dependency section is read: it must name the declared
    backend and must not list the retired one among the tools it keeps.
    """
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    backend = manifest["build-system"]["build-backend"]

    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    dependencies = _section_body(design, DEPENDENCIES_HEADING)
    assert dependencies, f"docs/design.md has no {DEPENDENCIES_HEADING!r} section"
    assert backend in dependencies, f"{DEPENDENCIES_HEADING} does not name the {backend} backend"
    assert RETIRED_BUILD_BACKEND not in dependencies, (
        f"{DEPENDENCIES_HEADING} still keeps {RETIRED_BUILD_BACKEND}, which no longer builds"
    )


def test_removal_ledger_records_what_replaced_the_release_machinery() -> None:
    """The ledger may not still say the release machinery went with nothing after it (FR-028).

    It recorded the release CI jobs as removed and unreplaced, which spec 006
    made false. The amendment has three parts and only the whole of it is
    correct: a version tag runs the release workflow, that workflow publishes
    to PyPI, and it lists the server in the MCP registry. Each part is asserted
    on its own so a rewrite that drops one fails here naming which.
    """
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    ledger = _section_body(design, LEDGER_HEADING)
    assert ledger, f"docs/design.md has no {LEDGER_HEADING!r} section"

    required = {
        "the workflow a version tag runs": ".github/workflows/release.yml",
        "the index it publishes to": "PyPI",
        "the registry it lists the server in": "MCP registry",
    }
    missing = sorted(part for part, token in required.items() if token not in ledger)
    assert not missing, f"{LEDGER_HEADING} does not record {missing}"


def test_design_record_names_telemetry_the_primary_event_source() -> None:
    """The design record must rank telemetry first and stop deferring it (FR-001).

    3.2 ranked OTel last, behind hooks, as later work, and 3.13 listed the OTel
    adapter as deferred. This feature builds the episodic layer out of the
    telemetry stream, so both statements are false and either one still standing
    sends a reader to the wrong source. Each half is asserted on its own so a
    partial amendment fails here naming which.
    """
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    sources = _section_body(design, EVENT_SOURCES_HEADING)
    assert sources, f"docs/design.md has no {EVENT_SOURCES_HEADING!r} section"
    assert "primary source for the episodic layer" in sources, (
        f"{EVENT_SOURCES_HEADING} does not name telemetry the primary source"
    )
    assert "Later: OTel" not in sources, f"{EVENT_SOURCES_HEADING} still ranks OTel as later"

    deferred = _section_body(design, DEFERRED_HEADING)
    assert deferred, f"docs/design.md has no {DEFERRED_HEADING!r} section"
    assert "OTel adapter" not in deferred, f"{DEFERRED_HEADING} still defers the OTel adapter"


def test_architecture_names_every_declared_layer() -> None:
    """The architecture doc must name the three declared layers (FR-015).

    The names are read off `processrecall.graph.schema.LAYERS` rather than
    written out here, so a layer renamed in the contract fails this instead of
    ageing quietly in prose. Each is required in its layer-naming spelling, so
    a section that mentions "procedural" only in passing does not pass for
    documenting the layer.
    """
    architecture = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    section = _section_body(architecture, LAYERS_HEADING)
    assert section, f"docs/architecture.md has no {LAYERS_HEADING!r} section"

    missing = sorted(
        f"{layer.name} layer" for layer in LAYERS if f"{layer.name} layer" not in section
    )
    assert not missing, f"{LAYERS_HEADING} does not document {missing}"


def test_architecture_documents_the_ingest_path() -> None:
    """The architecture doc must say what reads telemetry, and when (FR-004).

    Telemetry arrives as a file someone else's collector writes, so a reader
    who cannot see which module opens it, and in which pass, has no way to tell
    the memory from a listener. Each part is asserted on its own, so a rewrite
    that drops one fails here naming which.
    """
    architecture = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    section = _section_body(architecture, INGEST_HEADING)
    assert section, f"docs/architecture.md has no {INGEST_HEADING!r} section"

    required = {
        "the configured collector file": "telemetry_path",
        "the reader every record comes through": "trajectory.telemetry",
        "the pass that drains it": "cli.derive",
        "the hook that spawns that pass": "SessionEnd",
        "that no hook path reads the file": "hook path",
        "the offset the next pass resumes from": "offset",
    }
    missing = sorted(part for part, token in required.items() if token not in section)
    assert not missing, f"{INGEST_HEADING} does not document {missing}"


def test_configuration_documents_every_setting() -> None:
    """The settings table must list every field `Config` resolves (FR-004).

    The names are read off `processrecall.config.Config` rather than written out
    here, so a field added to the dataclass fails this instead of going
    undocumented — an operator who cannot find `telemetry_path` in the table has
    nowhere to learn that naming the collector's file is a setting at all.
    """
    configuration = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    section = _section_body(configuration, SETTINGS_HEADING)
    assert section, f"docs/configuration.md has no {SETTINGS_HEADING!r} section"

    missing = sorted(
        f"`{field.name}`" for field in fields(Config) if f"`{field.name}`" not in section
    )
    assert not missing, f"{SETTINGS_HEADING} does not document {missing}"


def test_configuration_documents_the_collector_requirement() -> None:
    """Configuration must say what writes the telemetry file, and what fills it (FR-004).

    Naming `telemetry_path` points the memory at a file nothing writes yet: the
    collector is the developer's to run, and the harness exports nothing to it
    until its own gates are on. The gate names are read off the shipped example
    rather than written out here, so one renamed there cannot leave this page
    telling a reader to export the old spelling. Each prose part is asserted on
    its own so a rewrite that drops one fails here naming which.
    """
    configuration = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    section = _section_body(configuration, TELEMETRY_HEADING)
    assert section, f"docs/configuration.md has no {TELEMETRY_HEADING!r} section"

    required = {
        "the shipped example configuration": "otel-collector.yaml",
        "the flag that prints it": "--collector-config",
        "that the memory runs no collector of its own": "never installs",
        "the readiness check on the named file": "doctor",
    }
    missing = sorted(part for part, token in required.items() if token not in section)
    assert not missing, f"{TELEMETRY_HEADING} does not document {missing}"

    gates = set(HARNESS_EXPORT.findall(COLLECTOR_CONFIG.read_text(encoding="utf-8")))
    assert gates, f"{COLLECTOR_CONFIG.name} tells a reader to export nothing"
    undocumented = sorted(gate for gate in gates if gate not in section)
    assert not undocumented, f"{TELEMETRY_HEADING} does not document {undocumented}"


def test_readme_separates_what_is_stored_from_what_the_collector_sees() -> None:
    """The readme's privacy promise must name its own limit (FR-013).

    The lead paragraph promises the memory keeps no prompt text, file contents
    or credentials. That promise covers what is *stored*; the tool-details gate
    the plugin requires exports commands and tool inputs to the developer's own
    collector before any of it reaches the memory. The gate is read off the
    shipped collector configuration so a rename fails here too, and each part
    of the distinction is asserted on its own.
    """
    gate = TOOL_DETAILS_GATE.search(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    assert gate, f"{COLLECTOR_CONFIG.name} no longer names the tool-details gate"

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    lead = readme.split("\n## ", 1)[0]
    assert STORAGE_PROMISE in lead, f"README.md lead no longer promises {STORAGE_PROMISE!r}"

    required = {
        "the gate that exports commands and tool inputs": gate.group(1),
        "the collector they are exported to": "collector",
        "that the promise covers what the memory stores": "stores",
        "not what the collector sees": "sees",
    }
    missing = sorted(part for part, token in required.items() if token not in lead)
    assert not missing, f"README.md's privacy promise does not state {missing}"
