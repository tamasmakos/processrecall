"""Packaging invariants: what the tree ships is what pyproject.toml declares.

The fork's first phase is subtraction, and subtraction is only real when the
package's declared surface shrinks with it (FR-074, R15):

  * the runtime dependency set is exactly five names, each with a lower
    bound — a sixth is a decision, never a drift;
  * `classify` is the only extra (FR-060, FR-071);
  * the two packs are data, not code, and hatchling ships data only when told
    to (FR-024) — so the declaration is asserted here, and the archive check
    that proves it reaches the wheel sits beside it.

The metadata assertions read pyproject.toml with tomllib and run in
milliseconds; only the wheel assertion builds anything.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
import zipfile
from importlib.metadata import packages_distributions
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "processrecall"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: Design §5 / R15: the default install closure, by name. Adding a sixth is a
#: person's decision, recorded in pyproject.toml, never a side effect of an import.
RUNTIME_DEPENDENCIES = frozenset(
    {"pydantic", "pydantic-settings", "tree-sitter", "tree-sitter-language-pack", "mcp"}
)

#: FR-024 / R15: the two data packs hatchling has to be told to ship.
PACK_GLOBS = (
    "processrecall/symbolic/data/**/*.json",
    "processrecall/trajectory/vocab/**/*.json",
)

#: The directories those globs live under, as the archive spells them.
PACK_DIRS = tuple(glob.split("**")[0] for glob in PACK_GLOBS)

#: R15 / FR-074: the recorded ceiling for the built wheel. Like the coverage
#: floor, it may be ratcheted down but never raised — raising it is the moment
#: the dependency reduction stops being measurable.
WHEEL_CEILING_BYTES = 1024 * 1024


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _requirement_name(spec: str) -> str:
    """Normalise one PEP 508 spec to a comparable distribution name."""
    return re.split(r"[<>=!~\[; ]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _requirement_names(specs: list[str]) -> set[str]:
    return {name for spec in specs if (name := _requirement_name(spec))}


def _declared_distributions() -> set[str]:
    """Every distribution the *installed package* declares: core + all extras."""
    proj = _pyproject()["project"]
    declared = _requirement_names(proj.get("dependencies", []))
    for extra_specs in proj.get("optional-dependencies", {}).values():
        declared |= _requirement_names(extra_specs)
    return declared


def _build_wheel(out_dir: Path) -> Path:
    """Build the project's wheel into *out_dir* and return the built file."""
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(REPO_ROOT)], check=True
    )
    built = sorted(out_dir.glob("*.whl"))
    assert len(built) == 1, f"expected exactly one wheel in {out_dir}, got {built}"
    return built[0]


def _source_files() -> list[Path]:
    return list(PKG_ROOT.rglob("*.py"))


def _top_level_imports(path: Path) -> set[str]:
    """Top-level module names imported by *path*, including inside functions.

    Lazy imports inside a `try:` or a function body are how extras are guarded
    (FR-060), so a module-header-only scan would miss exactly the imports that
    break at runtime.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - vendored/broken files are excluded
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # skip relative imports
                names.add(node.module.split(".")[0])
    return names


def _is_first_party(mod: str) -> bool:
    return mod == "processrecall" or (PKG_ROOT / mod).exists()


def test_runtime_dependencies_are_exactly_the_five_named() -> None:
    """`[project.dependencies]` is the five names of design §5, each with a floor.

    The name set is asserted exactly, in both directions: a stray sixth is the
    install footprint every user pays for forever, and a missing one is a
    ModuleNotFoundError on a stranger's machine. The floor is Principle VII —
    an unbounded spec resolves to whatever is newest the day someone installs.
    """
    specs = _pyproject()["project"]["dependencies"]
    names = _requirement_names(specs)
    assert names == set(RUNTIME_DEPENDENCIES), (
        f"[project.dependencies] must name exactly {sorted(RUNTIME_DEPENDENCIES)}: "
        f"unexpected={sorted(names - RUNTIME_DEPENDENCIES)}, "
        f"missing={sorted(RUNTIME_DEPENDENCIES - names)}"
    )

    unbounded = [spec for spec in specs if ">=" not in spec]
    assert not unbounded, f"runtime dependencies without a lower bound: {unbounded}"


def test_classify_is_the_only_extra() -> None:
    """FR-060 / FR-071: enrichment is the one optional surface, and it has a floor."""
    extras = _pyproject()["project"].get("optional-dependencies", {})
    assert set(extras) == {"classify"}, f"`classify` must be the only extra, found {sorted(extras)}"

    unbounded = [spec for spec in extras["classify"] if ">=" not in spec]
    assert not unbounded, f"[classify] specs without a lower bound: {unbounded}"


def test_both_packs_are_declared_to_hatchling() -> None:
    """The concept index and the tool vocabularies are JSON, and JSON is easy to lose.

    `packages` ships the package directory, but hatchling drops anything the
    VCS ignores; only `artifacts` force-includes the packs. Without this line a
    future `*.json` ignore rule produces an installed package whose loaders have
    nothing to load — and nothing but the wheel check would notice.
    """
    wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["processrecall"]

    declared = set(wheel.get("artifacts", []))
    missing = [glob for glob in PACK_GLOBS if glob not in declared]
    assert not missing, f"pack data not declared to the build backend: {missing}"


def test_wheel_under_ceiling_and_ships_both_packs(tmp_path: Path) -> None:
    """FR-074 / SC-012: the built wheel is the measurable outcome of the subtraction.

    The declaration test above reads pyproject.toml; this one reads the archive
    hatchling actually produced, which is the only place both facts are true at
    once — the size a stranger downloads, and the packs being inside it.

    This builds a wheel and takes seconds, not milliseconds; the cost is
    accepted on every run rather than gated behind an opt-in marker, since
    neither the gate script nor CI deselects `slow` today.
    """
    wheel = _build_wheel(tmp_path)

    size = wheel.stat().st_size
    assert size < WHEEL_CEILING_BYTES, (
        f"{wheel.name} is {size} bytes, over the {WHEEL_CEILING_BYTES}-byte ceiling; "
        "the ceiling ratchets down, so this is something shipped that should not be"
    )

    with zipfile.ZipFile(wheel) as archive:
        shipped = archive.namelist()
    missing = [d for d in PACK_DIRS if not any(name.startswith(d) for name in shipped)]
    assert not missing, f"{wheel.name} ships no pack data under: {missing}"


def test_every_imported_third_party_module_is_declared() -> None:
    """No processrecall module may import a distribution the package never declares.

    It resolves each imported top-level module to its installed distribution
    and compares against the union of core dependencies and all extras.
    """
    declared = _declared_distributions()
    stdlib = sys.stdlib_module_names
    mod_to_dists = packages_distributions()

    undeclared: dict[str, set[str]] = {}
    for path in _source_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for mod in _top_level_imports(path):
            if mod in stdlib or _is_first_party(mod) or mod.startswith("_"):
                continue
            dists = {d.lower().replace("_", "-") for d in mod_to_dists.get(mod, [])}
            if not dists:
                # Not installed at all: we cannot map it to a distribution, so
                # fall back to the module name (right for the common case, and
                # a false positive here is still a real thing to look at).
                dists = {mod.lower().replace("_", "-")}
            if not (dists & declared):
                undeclared.setdefault(sorted(dists)[0], set()).add(rel)

    assert not undeclared, "Imported but not declared in pyproject.toml:\n  " + "\n  ".join(
        f"{dist}  <- {', '.join(sorted(files))}" for dist, files in sorted(undeclared.items())
    )
