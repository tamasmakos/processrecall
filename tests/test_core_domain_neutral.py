"""SC-001: no benchmark, dialogue, speaker or turn-regex term in core code.

`.importlinter` stops the core *importing* a pack; this stops the core
*naming* one domain. Only real code is scanned — a docstring may still say
"the dialogue pack" — so the check is over Python tokens, not raw text.

The quarantine list only shrinks: a new offender fails the first test, and a
cleaned-up file fails the second until it is struck off. R18 emptied it in one
go — every module that carried the dialogue vocabulary was a module the fork
deletes — so nothing is skipped today.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "processrecall"

DOMAIN_TERM = re.compile(
    r"benchmark|dialogue|speaker|utterance|locomo|longmem|turn_(id|index|span|regex)",
    re.IGNORECASE,
)

# Dialogue-shaped modules still awaiting a cleanup. Strike each off when it lands.
PENDING_CUTOVER: frozenset[str] = frozenset()


def domain_terms(module: Path) -> list[str]:
    """Domain-specific names used as code in `module`, comments and strings aside."""
    tokens = tokenize.generate_tokens(io.StringIO(module.read_text(encoding="utf-8")).readline)
    return [
        token.string
        for token in tokens
        if token.type not in (tokenize.COMMENT, tokenize.STRING)
        and DOMAIN_TERM.search(token.string)
    ]


def core_modules() -> list[Path]:
    return sorted(path for path in CORE.rglob("*.py") if "packs/" not in path.as_posix())


@pytest.mark.parametrize("module", core_modules(), ids=lambda path: path.name)
def test_core_module_names_no_domain(module: Path) -> None:
    relative = module.relative_to(CORE).as_posix()
    if relative in PENDING_CUTOVER:
        pytest.skip("dialogue-shaped until the cutover (T037)")
    assert not domain_terms(module), f"{relative} names a domain the core must not know"


def test_quarantine_holds_only_modules_that_still_offend() -> None:
    clean = {name for name in PENDING_CUTOVER if not domain_terms(CORE / name)}
    assert not clean, f"cleaned up — drop from PENDING_CUTOVER: {sorted(clean)}"
