"""Shared builders for `guidance/` tests."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.symbolic.packs import ActivityClass

PAYLOADS = Path(__file__).resolve().parents[1] / "fixtures" / "payloads"

#: The synthetic project the corpus works in, rewritten at replay to a real one.
CORPUS_PROJECT = "/work/demo"


def corpus_payloads(*rewrites: tuple[str, str]) -> Iterator[Mapping[str, Any]]:
    """Every hook payload of the fixture corpus, with each ``(old, new)`` path rewritten."""
    for path in sorted(PAYLOADS.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for old, new in rewrites:
            text = text.replace(old, new)
        yield from json.loads(text)["payloads"]


def walk(*node_keys: str, prompt: str = "p1") -> tuple[EpisodicStep, ...]:
    """One prompt, named *prompt*, that performed *node_keys* in the order given."""
    key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id=prompt)
    return tuple(
        _step(node_key, position=position, key=key) for position, node_key in enumerate(node_keys)
    )


def _step(node_key: str, *, position: int, key: SequenceKey) -> EpisodicStep:
    """One recorded row, named by the node key the recorder derived for it."""
    activity_class, program, _ = node_key.split("/")
    return EpisodicStep(
        dedup_key=f"{key.prompt_id}-{position}",
        sequence_key=key,
        position=position,
        node_key=node_key,
        activity_class=ActivityClass(activity_class),
        program=program,
        template=f"{program} <File>",
        occurred_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
        step_id=_step_id(key.prompt_id, position),
    )


def _step_id(prompt_id: str, position: int) -> int:
    """A step id unique across prompts, derived from *prompt_id* and *position*.

    Episodic identities are unique across the whole store, so two prompts
    built here must not reuse one — an edge counts the distinct steps behind
    it. The ordinal comes from the prompt's own name (``"p1"`` -> ``1``),
    keeping two prompts distinct without a module-level counter.
    """
    ordinal = int(prompt_id.removeprefix("p"))
    return ordinal * 1000 + position + 1
