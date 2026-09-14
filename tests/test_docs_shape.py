"""Docs that name code must match the code they name."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_docs_name_no_absent_symbols() -> None:
    """Docs must not describe code that does not exist (FR-026, SC-017).

    Greps `processrecall/` for each symbol FR-026 named as removed — the
    `community_boost` / `shape_score` ranking stage and `ArcadeDBSTMStore` /
    `ArcadeDBLTMStore` — so the removal claim is checked against the code, not
    assumed. Then greps `docs/architecture.md` for the same symbols, plus
    "the web server", a prose claim rather than a single token.
    """
    absent_symbols = {"community_boost", "shape_score", "ArcadeDBSTMStore", "ArcadeDBLTMStore"}

    code_text = "\n".join(
        p.read_text(encoding="utf-8") for p in (REPO_ROOT / "processrecall").rglob("*.py")
    )
    for symbol in absent_symbols:
        assert symbol not in code_text, (
            f"{symbol} exists in processrecall/ — the FR-026 doc claim is stale"
        )

    for doc_name in ("architecture.md",):
        doc_path = REPO_ROOT / "docs" / doc_name
        text = doc_path.read_text(encoding="utf-8")
        for symbol in absent_symbols:
            assert symbol not in text, f"{doc_name} still names {symbol}, absent from processrecall/"
        assert "web server" not in text.lower(), f"{doc_name} still names the web server"
