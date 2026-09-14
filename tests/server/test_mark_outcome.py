"""The agent's own verdict on a turn, stored beside the derived one (FR-035, FR-036).

Asserted over the transport the plugin actually runs, like `test_tools.py`: the
handler opens the store of whatever home it was launched into, so a spawned
server over a home of its own is the only seam that sees both halves of the
contract — what the call answers, and what the store then holds.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from processrecall.config import STORE_DIR
from processrecall.graph.episodic import open_index
from processrecall.graph.store import Sequence, SequenceKey, SQLiteEpisodicStore

pytestmark = pytest.mark.unit


def _database_path(home: Path) -> Path:
    """Where a server launched into *home* will find the episodic index."""
    return home / STORE_DIR / "episodes.db"


@contextmanager
def _store(home: Path) -> Iterator[SQLiteEpisodicStore]:
    """A store under *home*, closed on the way out."""
    with closing(open_index(_database_path(home))) as connection:
        yield SQLiteEpisodicStore(connection)


def _opened(store: SQLiteEpisodicStore, key: SequenceKey, derived: str) -> None:
    """Put one turn in *store*, already carrying the rule-derived verdict *derived*."""
    store.open_sequence(
        Sequence(
            key=key,
            project_dir_key="project",
            started_at=datetime(2026, 9, 14, 10, 0, tzinfo=UTC),
            derived_outcome=derived,
        )
    )


async def _called(arguments: Mapping[str, Any], home: Path) -> dict[str, Any] | None:
    """One `mark_outcome` call, answered by a server serving *home*."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "processrecall.server.mcp.stdio_server"],
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session,
    ):
        await session.initialize()
        return (await session.call_tool("mark_outcome", dict(arguments))).structuredContent


def test_a_declared_outcome_is_stored_beside_the_derived_one_and_both_are_reported(
    tmp_path: Path,
) -> None:
    """FR-035, FR-036: the derived verdict stays, so it can be recomputed."""
    key = SequenceKey("conversation", 1, "prompt-a")
    with _store(tmp_path) as store:
        _opened(store, key, derived="failure")

    reported = asyncio.run(_called({"outcome": "success", "prompt_id": "prompt-a"}, tmp_path))

    with _store(tmp_path) as store:
        stored = store.sequence(key)
    assert stored is not None
    assert (stored.declared_outcome, stored.derived_outcome) == ("success", "failure")
    assert reported == {
        "stored": True,
        "prompt_id": "prompt-a",
        "declared": "success",
        "derived": "failure",
        "state": "overridden",
    }


def test_a_prompt_the_store_never_opened_is_rejected_rather_than_created(
    tmp_path: Path,
) -> None:
    """FR-035: a verdict is about a turn that happened, so nothing is conjured."""
    with _store(tmp_path) as store:
        _opened(store, SequenceKey("conversation", 1, "prompt-a"), derived="neutral")

    reported = asyncio.run(_called({"outcome": "failure", "prompt_id": "prompt-b"}, tmp_path))

    with closing(open_index(_database_path(tmp_path))) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sequences").fetchone()[0] == 1
    assert reported == {
        "stored": False,
        "prompt_id": "prompt-b",
        "reason": (
            "no sequence was opened for prompt prompt-b; omit prompt_id to judge the current turn"
        ),
    }


def test_an_omitted_prompt_is_the_turn_running_now(tmp_path: Path) -> None:
    """FR-035: the agent declares an outcome for where it is, without naming it."""
    with _store(tmp_path) as store:
        _opened(store, SequenceKey("conversation", 1, "prompt-a"), derived="neutral")
        store.open_sequence(
            Sequence(
                key=SequenceKey("conversation", 1, "prompt-b"),
                project_dir_key="project",
                started_at=datetime(2026, 9, 14, 11, 0, tzinfo=UTC),
            )
        )

    reported = asyncio.run(_called({"outcome": "success"}, tmp_path))

    assert reported is not None
    assert reported["prompt_id"] == "prompt-b"
    with _store(tmp_path) as store:
        earlier = store.sequence(SequenceKey("conversation", 1, "prompt-a"))
        current = store.sequence(SequenceKey("conversation", 1, "prompt-b"))
    assert earlier is not None
    assert earlier.declared_outcome is None
    assert current is not None
    assert current.declared_outcome == "success"


def test_a_declaration_the_rules_already_agree_with_is_not_an_override(
    tmp_path: Path,
) -> None:
    """FR-035: an override is recorded as one, so agreement must not read as one."""
    with _store(tmp_path) as store:
        _opened(store, SequenceKey("conversation", 1, "prompt-a"), derived="success")

    reported = asyncio.run(_called({"outcome": "success", "prompt_id": "prompt-a"}, tmp_path))

    assert reported is not None
    assert reported["state"] == "confirmed"
