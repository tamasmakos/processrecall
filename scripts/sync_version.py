"""Rewrite every derived version from the one authoritative number.

`project.version` in `pyproject.toml` is the single fact (FR-008); the registry
entry and the plugin pin are copies of it. This script is the one command that
makes the copies agree again after `uv version` has moved the authoritative
number — `make bump` and `make version` run it, and
`tests/test_release_versions.py` is what fails when a copy drifts.

Two rules shape it:

- **The plugin pin names the most recent stable version** (FR-008a). A
  pre-release bump therefore leaves the plugin manifest and the server launch
  pin exactly where they are, so a release candidate never reaches marketplace
  users — and it says so on stdout rather than skipping in silence.
- **JSON is edited as JSON**, never by regular expression: a pattern that
  matches a version string matches it wherever it appears, including places
  that are not derived from this one.

Files that do not exist yet, and a launch declaration that carries no pin yet,
are reported and left alone: this tree grows those places one task at a time.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# a/b/rc attach with no separator ("1.2.0rc1"); dev needs one ("1.2.0.dev1"),
# per what `uv version --bump` actually emits. `post` is excluded on purpose:
# a post-release is not a pre-release. Stdlib stand-in for
# `packaging.version.Version(...).is_prerelease` — `packaging` is not one of
# the five declared dependencies (pyproject.toml, R15).
_PRERELEASE_SEGMENT = re.compile(r"(?:a|b|rc)\d+|\.dev\d+", re.IGNORECASE)

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
REGISTRY_ENTRY = REPO_ROOT / "server.json"
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
LAUNCH_DECLARATION = REPO_ROOT / ".claude-plugin" / "mcp.json"
DISTRIBUTION = "processrecall"


@dataclass
class SyncResult:
    """What one sync step did, kept apart from what it merely observed.

    `changes` are rewrites; `notes` are everything else worth telling a human
    (a file that doesn't exist yet, a pin this version deliberately skips) —
    conflating the two would make "every derived version already agrees"
    unreachable on any run that also has something to note.
    """

    changes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _read_json(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _retarget(document: MutableMapping[str, Any], key: str, version: str) -> str | None:
    """Point one version field at `version`, describing the move it made."""
    previous = document.get(key, "unset")
    if previous == version:
        return None
    document[key] = version
    return f"{previous} -> {version}"


def _is_prerelease(version: str) -> bool:
    """Whether `version` carries a pre-release or dev segment (PEP 440)."""
    return _PRERELEASE_SEGMENT.search(version) is not None


def authoritative_version() -> str:
    """The one version every other place in this tree is derived from."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    version: str = project["version"]
    return version


def sync_registry_entry(version: str) -> SyncResult:
    """Rewrite both version fields of the registry entry."""
    where = _relative(REGISTRY_ENTRY)
    if not REGISTRY_ENTRY.is_file():
        return SyncResult(notes=[f"{where}: absent, no registry entry to derive yet"])
    entry = _read_json(REGISTRY_ENTRY)
    package = entry["packages"][0]
    changes = [
        f"{where} {field}: {move}"
        for field, move in (
            ("version", _retarget(entry, "version", version)),
            ("packages[0].version", _retarget(package, "version", version)),
        )
        if move is not None
    ]
    if changes:
        _write_json(REGISTRY_ENTRY, entry)
    return SyncResult(changes=changes)


def sync_plugin_manifest(version: str) -> SyncResult:
    """Move the plugin pin in the manifest to a stable `version`.

    Unlike the registry entry and the launch pin, the manifest is mandatory:
    a plugin cannot run without it, so a missing file here is a real defect
    and raises rather than being reported as a note.
    """
    where = _relative(PLUGIN_MANIFEST)
    manifest = _read_json(PLUGIN_MANIFEST)
    move = _retarget(manifest, "version", version)
    if move is None:
        return SyncResult()
    _write_json(PLUGIN_MANIFEST, manifest)
    return SyncResult(changes=[f"{where} version: {move}"])


def sync_launch_pin(version: str) -> SyncResult:
    """Move the `processrecall==` pin the server launch declaration carries."""
    where = _relative(LAUNCH_DECLARATION)
    declaration = _read_json(LAUNCH_DECLARATION)
    arguments = declaration["mcpServers"][DISTRIBUTION]["args"]
    requirement = f"{DISTRIBUTION}=="
    pinned = [index for index, argument in enumerate(arguments) if argument.startswith(requirement)]
    if not pinned:
        return SyncResult(notes=[f"{where}: the launch declaration carries no pin yet"])
    changes = [
        f"{where} args[{index}]: {arguments[index]} -> {requirement}{version}"
        for index in pinned
        if arguments[index] != requirement + version
    ]
    for index in pinned:
        arguments[index] = requirement + version
    if changes:
        _write_json(LAUNCH_DECLARATION, declaration)
    return SyncResult(changes=changes)


def main() -> int:
    version = authoritative_version()
    result = sync_registry_entry(version)
    if _is_prerelease(version):
        result.notes.append(
            f"the plugin pin is left untouched: {version} is a pre-release, and the pin "
            "names the most recent stable version (FR-008a)"
        )
    else:
        for step in (sync_plugin_manifest(version), sync_launch_pin(version)):
            result.changes += step.changes
            result.notes += step.notes
    print(f"sync_version: authoritative version {version}")
    for change in result.changes:
        print(f"  {change}")
    for note in result.notes:
        print(f"  note: {note}")
    if not result.changes:
        print("  every derived version already agrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
