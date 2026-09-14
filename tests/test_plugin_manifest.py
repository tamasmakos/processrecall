"""The plugin manifest (FR-067).

The repository root is simultaneously the installable plugin and the Python
package, and `.claude-plugin/plugin.json` is the half of that claim the harness
reads: the name it installs under, the version `bin/bootstrap.sh` compares its
`.ready` marker against (R14), and where the hooks, tool server and skills live.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from processrecall import __version__

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    """The shipped manifest, parsed."""
    assert MANIFEST.is_file(), f"{MANIFEST} is not shipped"
    parsed: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return parsed


@pytest.fixture(scope="module")
def project() -> dict[str, Any]:
    """`[project]` from pyproject.toml — source of truth for package metadata."""
    parsed: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return parsed["project"]


def test_the_manifest_names_the_package_and_its_version(manifest: dict[str, Any]) -> None:
    """One tree, one name, one version: bootstrap's `.ready` compares against it."""
    assert manifest["name"] == "processrecall", manifest["name"]
    assert manifest["version"] == __version__, (
        f"manifest version {manifest['version']} != package {__version__}"
    )


def test_the_manifest_declares_hooks_the_tool_server_and_the_skills(
    manifest: dict[str, Any],
) -> None:
    """FR-067: the harness finds every plugin component through the manifest."""
    assert manifest["hooks"] == "./hooks/hooks.json", manifest.get("hooks")
    assert manifest["mcpServers"] == "./.mcp.json", manifest.get("mcpServers")
    assert manifest["skills"] == ["./skills/remember"], manifest.get("skills")


def test_the_declared_skill_directory_is_shipped(manifest: dict[str, Any]) -> None:
    """A declaration pointing at nothing installs a plugin with no guidance."""
    for declared in manifest["skills"]:
        skill = REPO_ROOT / declared
        assert (skill / "SKILL.md").is_file(), f"{declared} ships no SKILL.md"


def test_the_manifest_metadata_matches_pyproject(
    manifest: dict[str, Any], project: dict[str, Any]
) -> None:
    """Author, homepage and license have one source of truth; the plugin
    description is user-facing and intentionally distinct from the package's."""
    author = project["authors"][0]
    assert manifest["author"] == {"name": author["name"], "email": author["email"]}, manifest.get(
        "author"
    )
    assert manifest["homepage"] == project["urls"]["Homepage"], manifest.get("homepage")
    assert manifest["license"] == project["license"], manifest.get("license")
