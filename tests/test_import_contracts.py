"""The architecture contract itself, read as a declaration.

FR-006 asks for the layering to be "enforced by an automated architecture
contract, not by convention". `lint-imports` enforces it, but only over the
contracts `.importlinter` happens to declare — a contract silently dropped
from that file takes its enforcement with it and every run stays green. So
the declaration is asserted here, against the set R17 fixed:

  * `guidance-is-rules-only` — the layers contract alone would *permit*
    `guidance -> symbolic`, which is what makes FR-061 ("classifier output
    only enriches") a habit rather than a structure;
  * `hot-path-stdlib-only` — with `include_external_packages`, without which
    `torch` arrives on the hot path through an innocent-looking helper and no
    internal-only contract can see it (R9's latency budget);
  * `artifacts-off-the-hot-path` — FR-064.

Read with stdlib `configparser`, which is the same parser import-linter uses
for this file, so a declaration this test can see is one `lint-imports` can
see too. That `lint-imports` then *passes* over the finished layout is a
different assertion with a different cost, and it lives beside this one.
"""

from __future__ import annotations

import configparser
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
IMPORTLINTER = REPO_ROOT / ".importlinter"

#: R17 / plan §Project Structure: the layer order, outermost first. Each entry
#: is one layer; siblings inside a layer are independent peers, so order among
#: them and exact spacing don't matter — only membership and layer order do.
EXPECTED_LAYERS = (
    frozenset({"processrecall.cli", "processrecall.server", "processrecall.integrations"}),
    frozenset({"processrecall.guidance"}),
    frozenset({"processrecall.graph"}),
    frozenset({"processrecall.artifacts", "processrecall.symbolic"}),
    frozenset({"processrecall.procedures"}),
    frozenset({"processrecall.trajectory"}),
    frozenset({"processrecall.config"}),
    frozenset({"processrecall.exceptions"}),
)

#: R17: the three forbidden contracts, as {section id: (sources, forbidden)}.
EXPECTED_FORBIDDEN = {
    "guidance-is-rules-only": (
        {"processrecall.guidance"},
        {"processrecall.symbolic", "processrecall.artifacts"},
    ),
    "hot-path-stdlib-only": (
        {
            "processrecall.integrations.claude_code",
            "processrecall.trajectory",
            "processrecall.procedures",
            "processrecall.graph",
            "processrecall.guidance",
            "processrecall.config",
        },
        {
            "pydantic",
            "pydantic_settings",
            "mcp",
            "tree_sitter",
            "tree_sitter_language_pack",
            "gliner2",
            "torch",
            "transformers",
        },
    ),
    "artifacts-off-the-hot-path": (
        {"processrecall.graph", "processrecall.trajectory"},
        {"processrecall.artifacts"},
    ),
}


def _contracts() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read_string(IMPORTLINTER.read_text(encoding="utf-8"))
    return parser


def _module_names(section: configparser.SectionProxy, option: str) -> set[str]:
    """The module list under *option*, however it is wrapped or comma-joined."""
    raw = section.get(option, "").replace(",", "\n")
    return {line.strip() for line in raw.splitlines() if line.strip()}


def test_importlinter_declares_the_r17_contracts() -> None:
    contracts = _contracts()
    assert contracts["importlinter"]["root_package"] == "processrecall"
    assert contracts["importlinter"].getboolean("include_external_packages") is True

    layers = contracts["importlinter:contract:layers"]
    assert layers["type"] == "layers"
    declared = tuple(
        frozenset(module.strip() for module in line.split("|"))
        for line in layers["layers"].splitlines()
        if line.strip()
    )
    assert declared == EXPECTED_LAYERS

    for contract_id, (sources, forbidden) in EXPECTED_FORBIDDEN.items():
        section_id = f"importlinter:contract:{contract_id}"
        assert contracts.has_section(section_id), f"{IMPORTLINTER}: no [{section_id}]"
        section = contracts[section_id]
        assert section["type"] == "forbidden"
        assert _module_names(section, "source_modules") == sources
        assert _module_names(section, "forbidden_modules") == forbidden

    hot_path = contracts["importlinter:contract:hot-path-stdlib-only"]
    assert hot_path.getboolean("include_external_packages") is True


def test_lint_imports_passes_on_the_new_layout() -> None:
    """The declaration above, actually enforced over the finished tree (SC-011).

    Declaring a contract and honouring it are two claims: the assertion above
    reads `.importlinter` and would stay green over a layout that violates
    every line of it. This one runs the enforcer — the same console script
    `make gate` runs, so a green suite and a green gate cannot disagree — and
    lets its own report be the failure message.
    """
    lint_imports = shutil.which("lint-imports")
    assert lint_imports is not None, "import-linter is not installed in this environment"

    result = subprocess.run(
        [lint_imports],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
