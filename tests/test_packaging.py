"""Packaging invariants: what the tree ships is what pyproject.toml declares.

The fork's first phase is subtraction, and subtraction is only real when the
package's declared surface shrinks with it (FR-074, R15):

  * the runtime dependency set is exactly five names, each with a lower
    bound — a sixth is a decision, never a drift;
  * `classify` is the only extra (FR-060, FR-071);
  * the two packs are data, not code (FR-024) — the backend ships everything
    under the module root and cannot silently drop them, so what is asserted
    here is the backend pin, and the archive check that proves it stands beside.

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

#: FR-024 / R15: the two data packs the built archive has to carry.
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

#: FR-007 / R7: the owner's `mcp-publisher validate` run reported a 100-character
#: cap the registry docs do not state. Treated as real — the cost is a shorter
#: sentence, the cost of being wrong is a spent version number.
REGISTRY_DESCRIPTION_LIMIT = 100


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


def test_the_description_fits_the_registry_limit() -> None:
    """FR-007: one user-facing sentence, short enough for the registry to take.

    Whether that sentence matches the plugin manifest's is
    `test_plugin_manifest.py`'s invariant to hold; this test only bounds its length.
    """
    description = _pyproject()["project"]["description"]
    assert len(description) <= REGISTRY_DESCRIPTION_LIMIT, (
        f"description is {len(description)} characters, over the registry's "
        f"{REGISTRY_DESCRIPTION_LIMIT}: {description!r}"
    )


def test_the_backend_is_pinned_and_the_flat_layout_is_declared() -> None:
    """R2: the packs ship because of which backend builds them, so it is pinned.

    `uv_build` packages the whole module directory and never consults the VCS,
    which is why no force-include list remains. That guarantee belongs to a
    version range: unbounded, a future major could change the default contents
    with nothing here to notice. The flat layout is not its default either —
    without an empty `module-root` the backend looks for `src/` and finds none.
    """
    build_system = _pyproject()["build-system"]
    assert build_system["build-backend"] == "uv_build"

    pins = [spec for spec in build_system["requires"] if _requirement_name(spec) == "uv-build"]
    assert len(pins) == 1, f"expected exactly one uv_build requirement, got {pins}"

    operators = set(re.findall(r"[<>]=?", pins[0]))
    assert {">", ">="} & operators and {"<", "<="} & operators, (
        f"build backend requirement {pins[0]!r} needs both a lower and an upper bound"
    )

    build_backend = _pyproject()["tool"]["uv"]["build-backend"]
    assert build_backend["module-root"] == "", (
        "this repository is a flat layout: module-root must be empty, not the default src/"
    )


def test_wheel_under_ceiling_and_ships_both_packs(tmp_path: Path) -> None:
    """FR-074 / SC-012: the built wheel is the measurable outcome of the subtraction.

    The test above reads pyproject.toml; this one reads the archive the backend
    actually produced, which is the only place both facts are true at once —
    the size a stranger downloads, and the packs being inside it.

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
