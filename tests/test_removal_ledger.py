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

# R18's subtractive half, resolved to paths: every forked module the fork drops.
REMOVED_PATHS = [
    "channels",
    "ingestion",
    "retrieval",
    "storage",
    "models",
    "packs",
    "memory.py",
    "llm.py",
    "temporal.py",
    "nlp.py",
    "linguistics.py",
    "bounds.py",
    "symbolic/ontology",
    "symbolic/framenet",
    "symbolic/index.py",
    "symbolic/match.py",
    "symbolic/predicates.py",
    "integrations/client",
    "integrations/langgraph",
    # `server/mcp/` is the third partial deletion, and the subtlest: the ledger
    # names the tools package, but plan.md §Project Structure keeps it as
    # REWRITTEN (four tools, nothing else) and T054-T057 each put a file there.
    # So what R18 drops is the five forked tools that have no successor --
    # `recall.py` is deliberately absent from this list, because the fork keeps
    # the name and replaces the body.
    "server/mcp/tools/admin.py",
    "server/mcp/tools/corpus.py",
    "server/mcp/tools/ltm.py",
    "server/mcp/tools/query.py",
    "server/mcp/tools/stm.py",
    # `cli/` is named by the ledger and rewritten by the plan, so what R18 drops
    # is the forked module, not the package: plan.md §Project Structure keeps
    # `cli/` as REWRITTEN (bootstrap, backfill, rebuild, prune, show) and five
    # tasks put files there. Pinning the directory absent would have made every
    # one of them unpassable.
    "cli/memory.py",
]
# `ranking/` is the other partial deletion: RRF survives (T045 lifts it into
# `guidance/fusion.py`), everything that shared the package with it does not.
RANKING_SURVIVORS = ["__init__.py", "rrf.py"]

# plan.md §Project Structure — the five new layers, each a package of its own.
PLANNED_LAYERS = ["artifacts", "graph", "guidance", "procedures", "trajectory"]
# Shipped knowledge is data, not code, so these carry JSON and no `__init__.py` —
# the shape `processrecall/packs/data/` already has.
PLANNED_DATA_DIRS = ["symbolic/data", "trajectory/vocab"]

# design §6's last line, resolved to paths: the deployment and release machinery,
# deleted after everything it deployed. Repo-relative, not package-relative.
DEPLOYMENT_PATHS = [
    "deploy",
    "Dockerfile",
    "evaluation",
    "docs/adr",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
]
DEPLOYMENT_TESTS = ["tests/test_deploy_ops.py", "tests/test_offline.py"]
COMPOSE_GLOB = "docker-compose*.y*ml"


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


def test_removed_modules_are_absent() -> None:
    """R18: the forked modules the fork drops are gone from the tree.

    Paths, not imports: a dropped module is still a defect when it imports
    cleanly, and most of these no longer do. `ranking/` is asserted by its
    surviving contents instead, because the package itself stays.
    """
    survivors = sorted(str(Path(p)) for p in REMOVED_PATHS if (PKG_ROOT / p).exists())
    assert not survivors, f"R18 deletes these, but they are still in the package: {survivors}"

    ranking = sorted(p.name for p in (PKG_ROOT / "ranking").iterdir())
    assert ranking == RANKING_SURVIVORS, (
        f"processrecall/ranking: expected only {RANKING_SURVIVORS}, found {ranking}"
    )


def test_package_layout_matches_plan() -> None:
    """The other half of the ledger: what the fork *adds* is present and importable.

    A layer directory without `__init__.py` is a namespace package — it imports
    until the day two installs shadow each other, and `py.typed` never covers it.
    So the assertion is the import, not the path. Loaded from its file directly
    (not as `processrecall.{layer}`) so this stays a layout check: a dotted
    import would run `processrecall/__init__.py` first, which this test has
    nothing to do with.
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


def test_deployment_machinery_is_absent() -> None:
    """R18's last line: the deployment and release machinery is gone from the repo.

    None of this lives inside the package, so the paths are repo-relative rather
    than package-relative — a Dockerfile still ships nothing, but it still
    documents a stack this fork does not have.

    "with their tests" is asserted for the two suites whose entire subject is the
    deleted stack. `tests/test_repo_hygiene.py` and `tests/test_docs_shape.py` are
    deliberately absent from that list: each also pins files that survive, so what
    the deletion orphans there is assertions, not the file.
    """
    survivors = sorted(str(Path(p)) for p in DEPLOYMENT_PATHS if (REPO_ROOT / p).exists())
    assert not survivors, f"R18 deletes these, but they are still in the repo: {survivors}"

    # Globbed, not named: the `TestCompose` class this deletion orphans in
    # `test_repo_hygiene.py` also pinned that no *second* compose file
    # (`docker-compose.prod.yaml`) comes back, and that outlives the one R18 names.
    composes = sorted(p.name for p in REPO_ROOT.glob(COMPOSE_GLOB))
    assert not composes, f"a shipped deployment stack survives the fork: {composes}"

    stray_tests = sorted(str(Path(p)) for p in DEPLOYMENT_TESTS if (REPO_ROOT / p).exists())
    assert not stray_tests, f"tests of the deleted deployment stack survive it: {stray_tests}"
