"""Docs that name code must match the code they name."""

from __future__ import annotations

from pathlib import Path

from processrecall.cli.__main__ import COMMANDS, SUBJECTS
from processrecall.guidance.triggers import Trigger
from processrecall.integrations.claude_code.hooks import DENY_LIST, OPTOUT_MARKER
from processrecall.server.mcp.stdio_server import TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
RECOVERY_HEADING = "## Recovering a broken release"


def _package_source() -> str:
    """The concatenated text of every module under `processrecall/`."""
    return "\n".join(
        p.read_text(encoding="utf-8") for p in (REPO_ROOT / "processrecall").rglob("*.py")
    )


def test_docs_name_no_absent_symbols() -> None:
    """Docs must not describe code that does not exist (FR-026, SC-017).

    Greps `processrecall/` for each symbol FR-026 named as removed — the
    `community_boost` / `shape_score` ranking stage and `ArcadeDBSTMStore` /
    `ArcadeDBLTMStore` — so the removal claim is checked against the code, not
    assumed. Then greps `docs/architecture.md` for the same symbols, plus
    "the web server", a prose claim rather than a single token.
    """
    absent_symbols = {"community_boost", "shape_score", "ArcadeDBSTMStore", "ArcadeDBLTMStore"}

    code_text = _package_source()
    for symbol in absent_symbols:
        assert symbol not in code_text, (
            f"{symbol} exists in processrecall/ — the FR-026 doc claim is stale"
        )

    for doc_name in ("architecture.md",):
        doc_path = REPO_ROOT / "docs" / doc_name
        text = doc_path.read_text(encoding="utf-8")
        for symbol in absent_symbols:
            assert symbol not in text, (
                f"{doc_name} still names {symbol}, absent from processrecall/"
            )
        assert "web server" not in text.lower(), f"{doc_name} still names the web server"


def test_docs_describe_the_plugin_not_the_service() -> None:
    """The README must document every surface a reader can reach (T081).

    Read off the code rather than written out here: the four `Trigger`
    occasions, the five subcommands `processrecall.cli.__main__` registers,
    the four `TOOLS` the stdio server exposes, the subjects `show` reports —
    the counters among them — and the two exclusion markers capture is
    suppressed by. A rename that misses the README must fail here.
    """
    assert len(COMMANDS) == 5, f"expected five subcommands, found {COMMANDS}"

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    named = (
        tuple(trigger.value for trigger in Trigger)
        + COMMANDS
        + tuple(tool.name for tool in TOOLS)
        + SUBJECTS
        + (OPTOUT_MARKER.as_posix(), DENY_LIST)
    )
    missing = [name for name in named if name not in readme]
    assert not missing, f"README.md documents none of {missing}, which the plugin ships"


def test_docs_name_no_removed_service() -> None:
    """The documentation must describe the plugin a reader installs, not the fork's source.

    Checked the way `test_docs_name_no_absent_symbols` checks its own:
    `ingest_memory` and `recall_memory` are absent from `processrecall/`, so a
    document naming either instructs a reader to call code that isn't there.
    The dated design record is exempt: what the fork dropped is part of what
    it records.
    """
    absent = {"ingest_memory", "recall_memory"}
    code_text = _package_source()
    for symbol in absent:
        assert symbol not in code_text, f"{symbol} is back in processrecall/ — this check is stale"

    service_words = absent | {"ArcadeDB", "docker compose"}
    documents = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]
    for document in documents:
        if document.name == "design.md":
            continue
        text = document.read_text(encoding="utf-8")
        still_sold = sorted(word for word in service_words if word in text)
        assert not still_sold, (
            f"{document.relative_to(REPO_ROOT)} still documents the service: {still_sold}"
        )


def _section_body(text: str, heading: str) -> str:
    """The text under `heading`, up to the next heading of that level — empty if absent."""
    if (start := text.find(f"\n{heading}\n")) == -1:
        return ""
    body = text[start + len(heading) + 2 :]
    end = body.find("\n## ")
    return body if end == -1 else body[:end]


def test_docs_document_the_recovery_route() -> None:
    """Recovery from a broken published version must be written down (FR-015a).

    The route has five parts and only the whole of it is correct: a new patch
    version supersedes the broken one, the plugin pin advances to it, the broken
    version is withdrawn from the index, withdrawal alone is not the fix
    because a plugin pinned to that exact number still resolves it, and no
    version is ever deleted or its number reused. Each part is asserted on its
    own so a rewrite that drops one fails here naming which.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = _section_body(readme, RECOVERY_HEADING)
    assert section, f"README.md has no {RECOVERY_HEADING!r} section"

    required = {
        "the superseding patch version": "patch version",
        "the plugin pin advanced to it": ".claude-plugin/plugin.json",
        "withdrawing the broken version from the index": "yank",
        "that withdrawal alone is not the fix": "not the fix",
        "that no version is deleted": "deleted",
        "that no version number is reused": "reused",
    }
    missing = sorted(part for part, token in required.items() if token not in section)
    assert not missing, f"{RECOVERY_HEADING} does not document {missing}"
