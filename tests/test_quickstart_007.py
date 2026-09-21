"""The validation guide must stay runnable commands, not prose (Principle I).

Every scenario in this feature's `quickstart.md` is a `pytest` invocation a
reader is told to run. A node id that no longer collects — a renamed module, a
test moved between packages — turns the scenario into a paragraph that describes
a check nobody can run, and the guide reports nothing when it is followed.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = REPO_ROOT / ".claude" / "specs" / "007-otel-graph-schema-v2" / "quickstart.md"

#: How the quickstart spells a pytest invocation inside its bash fences: at the
#: start of a line, optionally through `uv run` and `python -m`. Prose that
#: mentions pytest in backticks never starts a line with the word.
PYTEST_COMMAND = re.compile(r"^(?:uv run )?(?:python -m )?pytest .*$", re.MULTILINE)

#: The node ids on such a line — the arguments rooted at the test package. Every
#: other word is a flag or a flag's value.
NODE_ID = re.compile(r"tests/\S+")


def _named_node_ids() -> tuple[str, ...]:
    """The node ids the quickstart tells a reader to run, first mention first."""
    body = QUICKSTART.read_text(encoding="utf-8")
    named = (nid for command in PYTEST_COMMAND.findall(body) for nid in NODE_ID.findall(command))
    return tuple(dict.fromkeys(named))


def _collected_node_ids(named: tuple[str, ...]) -> tuple[str, ...]:
    """What pytest collects for *named*, asked from the repository root."""
    completed = subprocess.run(  # nosec B603
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", *named],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return tuple(
        line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if "::" in line
    )


def test_the_quickstart_names_pytest_node_ids() -> None:
    """Guard the parse: a guide this test read as prose would pass vacuously."""
    assert _named_node_ids(), f"no pytest invocation found in {QUICKSTART.name}"


def test_every_named_node_id_is_collectable() -> None:
    """A scenario whose node id collects nothing is prose, not a command."""
    named = _named_node_ids()
    collected = _collected_node_ids(named)
    uncollectable = [
        node_id for node_id in named if not any(item.startswith(node_id) for item in collected)
    ]
    assert not uncollectable, f"named in {QUICKSTART.name} but collects nothing: {uncollectable}"
