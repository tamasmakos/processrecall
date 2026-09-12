"""Removal-ledger contract: the fork's value is subtraction, so pin the subtraction.

R18 of `.claude/specs/005-procedural-graph-memory/research.md` is a ledger of what
survives the fork and what leaves. The prototypes are the awkward case: they are
worth keeping — they seed the deferred evaluation harness — but they cannot live
in the package, because the v3 module pulls in `rdflib` + SEON and nothing shipped
imports any of them. `research/` has no packaging obligations, so that is where
they go, unchanged.

Assertions read the tree as paths, not as imports: a prototype that is importable
from `processrecall` is exactly the defect being pinned, so importing to check
would be the bug. The additive half of the ledger is the opposite case: a new
layer's `__init__.py` existing on disk is not enough to rule out a namespace
package, so that half asserts importability instead.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "processrecall"
RESEARCH = REPO_ROOT / "research"

PROTOTYPE_GLOB = "prototype_procedural_graph_v*.py"
REPORT_GLOB = "*.html"
EXPECTED_PROTOTYPES = ["prototype_procedural_graph_v4.py", "prototype_procedural_graph_v5.py"]

# plan.md §Project Structure — the five new layers, each a package of its own.
PLANNED_LAYERS = ["artifacts", "graph", "guidance", "procedures", "trajectory"]
# Shipped knowledge is data, not code, so these carry JSON and no `__init__.py` —
# the shape `processrecall/packs/data/` already has.
PLANNED_DATA_DIRS = ["symbolic/data", "trajectory/vocab"]


def _strays(glob: str) -> list[Path]:
    return sorted(p.relative_to(REPO_ROOT) for p in PKG_ROOT.rglob(glob))


def test_prototypes_live_in_research() -> None:
    """R18: prototypes sit in `research/`, not the package.

    `[tool.hatch.build.targets.wheel] packages = ["processrecall"]`, so a file
    outside that directory cannot reach the wheel — moving is the exclusion.
    """
    strays = _strays(PROTOTYPE_GLOB)
    assert not strays, f"prototypes still inside the package, so they ship in the wheel: {strays}"

    stray_reports = _strays(REPORT_GLOB)
    assert not stray_reports, f"prototype reports still inside the package: {stray_reports}"

    assert RESEARCH.is_dir(), f"{RESEARCH}: missing — the prototypes have nowhere to live"

    prototypes = sorted(p.name for p in RESEARCH.glob(PROTOTYPE_GLOB))
    assert prototypes == EXPECTED_PROTOTYPES, f"{RESEARCH}: expected {EXPECTED_PROTOTYPES}, found {prototypes}"


def test_package_layout_matches_plan() -> None:
    """The other half of the ledger: what the fork *adds* is present and importable.

    A layer directory without `__init__.py` is a namespace package — it imports
    until the day two installs shadow each other, and `py.typed` never covers it.
    So the assertion is the import, not the path. Loaded from its file directly
    (not as `processrecall.{layer}`) so this stays a layout check: a dotted
    import would run `processrecall/__init__.py` first, and today that pulls in
    `Memory` and the rest of the pre-fork stack this test has nothing to do with.
    """
    for layer in PLANNED_LAYERS:
        init = PKG_ROOT / layer / "__init__.py"
        assert init.is_file(), (
            f"{init.relative_to(REPO_ROOT)}: missing — `{layer}` is a namespace package, not a layer"
        )
        spec = importlib.util.spec_from_file_location(f"_layout_check.{layer}", init)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(importlib.util.module_from_spec(spec))

    for data_dir in PLANNED_DATA_DIRS:
        path = PKG_ROOT / data_dir
        assert path.is_dir(), (
            f"{path.relative_to(REPO_ROOT)}: missing — shipped packs have nowhere to live"
        )

    marker = PKG_ROOT / "py.typed"
    assert marker.is_file(), (
        f"{marker.relative_to(REPO_ROOT)}: missing — the new layers ship untyped"
    )
