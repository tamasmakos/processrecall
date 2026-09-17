"""One version, derived everywhere, and the registry entry that carries it (FR-008..FR-011).

There is exactly one authoritative version — ``project.version`` in
``pyproject.toml`` — and every other copy of it in this repository is derived.
A channel added without a test that keeps its copy honest is a channel that
will publish a stale number, so each derived copy is pinned here rather than in
the test file of whatever feature introduced it.

``pyproject.toml`` is parsed with ``tomllib`` and everything else as JSON or
text: this project declares no YAML dependency, for the reason
``tests/test_ci_workflow.py`` gives.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.version import Version

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
SERVER_ENTRY = REPO_ROOT / "server.json"
README = REPO_ROOT / "README.md"

#: The namespace GitHub authentication grants this owner (R5).
REGISTRY_NAME = "io.github.tamasmakos/processrecall"

#: `<!-- mcp-name: ... -->` on its own line. The registry matches the readme's
#: marker against the entry's name to prove ownership, and its matcher is
#: unforgiving about trailing punctuation, so the pattern is anchored.
MCP_NAME_MARKER = re.compile(r"^<!-- mcp-name: (?P<name>\S+) -->$", re.MULTILINE)


def _project() -> dict[str, Any]:
    """The `[project]` table, the source every other number is derived from."""
    with PYPROJECT.open("rb") as handle:
        table: dict[str, Any] = tomllib.load(handle)["project"]
    return table


def _authoritative_version() -> str:
    """The one version this repository publishes under."""
    version: str = _project()["version"]
    return version


def _json(path: Path) -> dict[str, Any]:
    """*path* as JSON, named in the failure when it is not there yet."""
    assert path.is_file(), f"{path.relative_to(REPO_ROOT).as_posix()} is not in the tree"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def test_the_plugin_manifest_pin_is_stable_and_tracks_the_authoritative_version() -> None:
    """FR-008a: the marketplace pin is never a pre-release, and otherwise tracks.

    The one divergence the rule permits is what keeps a release candidate off
    marketplace users: while the authoritative version is a pre-release the pin
    stays at the last stable one, and the moment it is stable again the two are
    equal. Compared with `Version.is_prerelease` rather than by string matching,
    because `0.2.0rc1` and `0.2.0` differ in ways a substring test gets wrong.
    """
    authoritative = Version(_authoritative_version())
    pinned = Version(_json(PLUGIN_MANIFEST)["version"])

    assert not pinned.is_prerelease, (
        f"{PLUGIN_MANIFEST.name} pins {pinned}, a pre-release: the marketplace installs this "
        "for every plugin user, and a release candidate is not what they asked for"
    )
    if not authoritative.is_prerelease:
        assert pinned == authoritative, (
            f"{PLUGIN_MANIFEST.name} pins {pinned} while pyproject declares the stable "
            f"{authoritative}: a stable authoritative version has no reason to diverge"
        )


def test_the_registry_entry_carries_the_authoritative_version() -> None:
    """FR-009: `server.json`'s two version fields are both the authoritative one."""
    authoritative = _authoritative_version()
    entry = _json(SERVER_ENTRY)

    assert entry["version"] == authoritative
    assert entry["packages"][0]["version"] == authoritative


def test_the_registry_entry_publishes_this_distribution() -> None:
    """FR-010: the entry names the package that is actually on the index.

    `identifier` is `project.name` and not the readable title: it is what the
    registry resolves against the index, and `runtimeHint` is what tells a
    client to run it with `uvx` — which is only correct because the bare
    distribution name is a console script, so the entry needs no
    `packageArguments`.
    """
    entry = _json(SERVER_ENTRY)
    package = entry["packages"][0]

    assert package["identifier"] == _project()["name"]
    assert package["runtimeHint"] == "uvx"
    assert package["registryType"] == "pypi"
    assert package["transport"]["type"] == "stdio"
    assert "packageArguments" not in package
    assert "runtimeArguments" not in package


def test_the_registry_entry_describes_what_pyproject_describes() -> None:
    """FR-011: one description, so the registry listing cannot drift from the index."""
    assert _json(SERVER_ENTRY)["description"] == _project()["description"]


def test_the_registry_entry_is_owned_by_the_readme_marker() -> None:
    """R5: the readme's `mcp-name:` marker is what proves the namespace is ours.

    The registry captures the description at publication and matches this
    marker against the entry's name; a mismatch is rejected there, after the
    version number has been spent. Asserted on the readme's text rather than on
    the published listing, because that is the only copy available before the
    tag exists.
    """
    entry = _json(SERVER_ENTRY)
    found = MCP_NAME_MARKER.search(README.read_text(encoding="utf-8"))

    assert found is not None, (
        "README.md carries no `<!-- mcp-name: ... -->` line of its own: without it the "
        "registry cannot tell that this repository owns the name it is publishing under"
    )
    assert found.group("name") == entry["name"] == REGISTRY_NAME
