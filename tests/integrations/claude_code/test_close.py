"""`close`: the end-of-work remember nudge (FR-040, T060).

Run through the real process, as in the other verb tests here, because
`close` now opens the episodic index — a home isolated by monkeypatching
alone would still resolve to the module's already-imported default path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from processrecall.integrations.claude_code import hooks

from .conftest import PAYLOADS, hook

pytestmark = pytest.mark.unit


def _payloads(hook_event_name: str) -> list[dict[str, Any]]:
    """Every payload of *hook_event_name* in the synthetic corpus, in order."""
    envelope = json.loads((PAYLOADS / "replayed_duplicate.json").read_text(encoding="utf-8"))
    return [p for p in envelope["payloads"] if p["hook_event_name"] == hook_event_name]


def test_the_end_of_work_nudge_is_the_close_verb_s(tmp_path: Path) -> None:
    """FR-040: `Stop` is where the agent is told a note may be worth writing."""
    result = hook("close", _payloads("Stop")[0], home=tmp_path)

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["additionalContext"]
    assert "remember" in context.lower(), context
    assert len(context) <= 1200, "the nudge exceeds the harness ceiling of R8"


def test_no_step_is_nudged() -> None:
    """FR-040: the nudge is `close`'s alone, never emitted on a per-step verb.

    A payload-driven check of `record` would be vacuous today: it answers
    nothing at all, per its own docstring, until the guidance half of its
    contract lands. This asserts the durable invariant instead — the nudge
    text is referenced by `close` and nowhere else in the module — so it stays
    live once `record` starts answering something.
    """
    import inspect

    assert "REMEMBER_NUDGE" in inspect.getsource(hooks.close)
    for name, verb in hooks.VERBS.items():
        if name != "close":
            assert "REMEMBER_NUDGE" not in inspect.getsource(verb), name
