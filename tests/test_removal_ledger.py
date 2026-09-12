"""Removal-ledger contract: the fork's value is subtraction, so pin the subtraction.

R18 of `.claude/specs/005-procedural-graph-memory/research.md` is a ledger of what
survives the fork and what leaves. The prototypes are the awkward case: they are
worth keeping — they seed the deferred evaluation harness — but they cannot live
in the package, because the v3 module pulls in `rdflib` + SEON and nothing shipped
imports any of them. `research/` has no packaging obligations, so that is where
they go, unchanged.

Assertions read the tree as paths, not as imports: a prototype that is importable
from `processrecall` is exactly the defect being pinned, so importing to check
would be the bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "processrecall"
RESEARCH = REPO_ROOT / "research"

PROTOTYPE_GLOB = "prototype_procedural_graph_v*.py"
REPORT_GLOB = "*.html"
EXPECTED_PROTOTYPES = ["prototype_procedural_graph_v4.py", "prototype_procedural_graph_v5.py"]


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
