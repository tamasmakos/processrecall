"""README install section must tell a `pip install processrecall` user what they get.

What survives the fork are the claims that hold for any wheel: the install
section names the console entry points `[project.scripts]` declares, its links
are absolute because the README is the PyPI long description, a `scripts/`
path is qualified as checkout-only — the wheel ships only the `processrecall`
package (`[tool.hatch.build.targets.wheel] packages = ["processrecall"]`), so
`scripts/` does not exist after a pip install — and the section documents the
direct install route (`pip`/`uv tool install`) alongside the Claude Code
plugin route.

The two ArcadeDB assertions that stood here are gone with the service. R18
drops the graph database, the five runtime dependencies do not include it, and
a test demanding the README document `GRAPHKNOWS_ARCADEDB_URL` would have made
the documentation rewrite unpassable. The entry-point and link assertions are
deliberately untouched: they read `[project.scripts]` and the link targets at
run time, so they follow the rewrite instead of pinning what it replaces.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _install_section() -> str:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"## Install\b(.*?)(?=\n## )", readme, re.DOTALL)
    assert match, "README.md has no '## Install' section followed by another '## ' heading"
    return match.group(1)


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_states_console_entry_points() -> None:
    section = _install_section()
    scripts = _pyproject()["project"]["scripts"]
    assert scripts, "pyproject.toml [project.scripts] is empty"
    missing = [name for name in scripts if name not in section]
    assert not missing, (
        f"Install section never mentions console script(s) {missing} declared in "
        "pyproject.toml [project.scripts], so it doesn't say what a pip install actually gives you"
    )


def test_links_are_absolute() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", readme)
    relative = [t for t in targets if not t.startswith(("http://", "https://", "mailto:", "#"))]
    assert not relative, (
        'README.md is the PyPI long description (readme = "README.md" in '
        "pyproject.toml); relative links resolve against pypi.org and 404 there. "
        "Internal targets must be absolute GitHub blob URLs under "
        "https://github.com/tamasmakos/processrecall/blob/main/ . Offending "
        f"targets: {relative}"
    )


def test_scripts_path_is_qualified_as_checkout_only() -> None:
    section = _install_section()
    if "scripts/" not in section:
        return
    lowered = section.lower()
    qualifiers = ("clone", "checkout", "repo")
    assert any(q in lowered for q in qualifiers), (
        "Install section references a scripts/ path (e.g. 'python scripts/bake_models.py'), "
        "but the wheel ships only the processrecall package "
        '([tool.hatch.build.targets.wheel] packages = ["processrecall"]), so scripts/ does not '
        "exist after `pip install processrecall` — the section must qualify this as requiring a "
        "repo clone/checkout (no mention of: " + ", ".join(qualifiers) + ")"
    )


def test_documents_direct_install_route() -> None:
    section = _install_section()
    distribution = _pyproject()["project"]["name"]
    installer = rf"(?:pip|pipx|uv pip|uv tool)\s+install\s+{re.escape(distribution)}\b"
    assert re.search(installer, section), (
        f"Install section never shows how to install {distribution!r} from the index "
        "(e.g. `uv tool install processrecall`); it documents only the Claude Code "
        "plugin route, so a reader outside Claude Code is not told the package is "
        "installable on its own"
    )
