"""The plugin manifest (FR-067).

The repository root is simultaneously the installable plugin and the Python
package, and `.claude-plugin/plugin.json` is the half of that claim the harness
reads: the name it installs under, and where the tool server and skills live.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

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


def test_the_manifest_names_the_package(manifest: dict[str, Any]) -> None:
    """One tree, one name: the harness installs the plugin under it."""
    assert manifest["name"] == "processrecall", manifest["name"]


def test_the_manifest_declares_the_tool_server_and_the_skills_but_never_the_hooks(
    manifest: dict[str, Any],
) -> None:
    """FR-067: the harness finds every plugin component through the manifest.

    Every component except the hooks, which the harness loads from the
    conventional `hooks/hooks.json` on its own: declaring that path here too
    is a duplicate the loader rejects, and it fails the whole plugin, not just
    the hooks. The tool server is the opposite case — it no longer sits at the
    root path the harness would find by convention, so it must be named.
    """
    assert "hooks" not in manifest, (
        f"manifest re-declares the conventional hooks path ({manifest.get('hooks')}); "
        "the harness loads hooks/hooks.json itself and refuses the duplicate"
    )
    assert manifest["mcpServers"] == "./.claude-plugin/mcp.json", manifest.get("mcpServers")
    assert manifest["skills"] == ["./skills/remember"], manifest.get("skills")


def test_the_declared_skill_directory_is_shipped(manifest: dict[str, Any]) -> None:
    """A declaration pointing at nothing installs a plugin with no guidance."""
    for declared in manifest["skills"]:
        skill = REPO_ROOT / declared
        assert (skill / "SKILL.md").is_file(), f"{declared} ships no SKILL.md"


def test_the_manifest_metadata_matches_pyproject(
    manifest: dict[str, Any], project: dict[str, Any]
) -> None:
    """Author, homepage, license and description have one source of truth.

    The description used to be the one field the two were allowed to disagree
    on; FR-007 ended that — the package now carries the manifest's user-facing
    sentence, because that is the text the registry publishes.
    """
    author = project["authors"][0]
    assert manifest["author"] == {"name": author["name"], "email": author["email"]}, manifest.get(
        "author"
    )
    assert manifest["homepage"] == project["urls"]["Homepage"], manifest.get("homepage")
    assert manifest["license"] == project["license"], manifest.get("license")
    assert manifest["description"] == project["description"], manifest.get("description")
