"""Shared pytest configuration.

sys.path is managed via pytest.ini ``pythonpath`` entries; the project-root
insert below is belt-and-suspenders for direct invocations.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure project root is in sys.path (belt-and-suspenders alongside pytest.ini pythonpath)
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
PAYLOADS = REPO_ROOT / "tests" / "fixtures" / "payloads"


def manifest_declared(key: str) -> dict[str, Any]:
    """Load the manifest-declared file for ``key``, asserting it ships.

    Shared by the hooks and MCP declaration tests: both resolve a path out of
    ``plugin.json`` and read the JSON it points at.
    """
    declared = json.loads(MANIFEST.read_text(encoding="utf-8"))[key]
    path = REPO_ROOT / declared
    assert path.is_file(), f"{declared} is declared by the manifest but not shipped"
    return json.loads(path.read_text(encoding="utf-8"))

# Belt-and-suspenders against a model download on first extract: whichever
# module ends up loading one reads the offline switches at import, before this
# module's own import can react, so the session sets them here, before any
# import in the run. setdefault: an operator who wants the Hub keeps it.
for _offline_switch in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
    os.environ.setdefault(_offline_switch, "1")


@pytest.fixture(autouse=True)
def _reset_settings_singletons():
    """Drop the process settings around each test, so env-patching stays isolated.

    ``get_settings`` is an ``lru_cache``; without this a cached instance built
    under one environment would leak into the next test, and tests would go back
    to poking module globals to get a fresh one.
    """
    from processrecall.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Live-ArcadeDB guard, shared by every integration module
# ---------------------------------------------------------------------------


def _arcadedb_reachable() -> bool:
    """Return True when ArcadeDB answers at the configured URL."""
    import httpx

    from processrecall.settings import GraphKnowsSettings

    try:
        s = GraphKnowsSettings()
        r = httpx.get(
            f"{s.arcadedb_url}/api/v1/exists/mem",
            auth=(s.arcadedb_user, s.arcadedb_password.get_secret_value()),
            timeout=3,
        )
    except Exception:
        return False
    else:
        return r.status_code == 200


def require_arcadedb() -> None:
    """Skip the caller when ArcadeDB is down — fail when the run demands it.

    ``GRAPHKNOWS_REQUIRE_ARCADEDB=1`` is CI's promise that a service container
    is up; a skip there would report a suite that never ran as green.
    """
    if _arcadedb_reachable():
        return
    if os.environ.get("GRAPHKNOWS_REQUIRE_ARCADEDB") == "1":
        pytest.fail("ArcadeDB not reachable and GRAPHKNOWS_REQUIRE_ARCADEDB=1")
    pytest.skip("ArcadeDB not reachable")


@pytest.fixture
def arcadedb_required() -> None:
    """Fixture form of :func:`require_arcadedb`."""
    require_arcadedb()
