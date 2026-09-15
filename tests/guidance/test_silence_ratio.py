"""Silence is the default, and it is measured rather than promised (SC-005).

`guidance_served` and `guidance_silent` are a pair for exactly this reason
(R16): "guidance rarely speaks" is then a ratio a test can read off the
counter table instead of a property a reviewer has to believe. The corpus is
recorded through the real capture path, the memory it leaves behind is
derived, and every prompt the corpus submitted is then put to the `prompt`
verb against that memory — the replay a developer's second week is. The pair
is kept at one occasion per prompt submission, so the ratio it reads off is
prompt occasions, not SC-005's literal "recorded steps" — narrower than the
corpus, and the margin below is against that narrower count.

Both halves of FR-045 are one assertion each. No more than one occasion in
five may be served, which is silence as the default; and at least one must
be, because a top-down channel that never speaks scores a perfect silence
ratio while being dead.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from processrecall.cli.rebuild import Derivation, rebuild
from processrecall.config import STORE_DIR, home_dir, load_config
from processrecall.graph.episodic import DEFAULT_DATABASE_PATH, open_index
from processrecall.graph.store import SQLiteEpisodicStore
from processrecall.integrations.claude_code.hooks import capture, open_prompt
from tests.guidance.conftest import CORPUS_PROJECT, corpus_payloads

#: The project `excluded_project.json` works in, rewritten at replay to one of
#: this test's own — marked opted out there, never CORPUS_PROJECT's (FR-058).
EXCLUDED_PROJECT = "/work/private-client"

#: The hook event a prompt is submitted by, and the one occasion the served
#: and silent counters are kept at (FR-047).
PROMPT_EVENT = "UserPromptSubmit"

#: The session a second, independent real project's own opening is recorded
#: under — never the corpus's own, so the two are never folded into one
#: sequence (FR-047 keys a sequence by session and prompt id alone).
OTHER_SESSION = "sess-other"

#: SC-005's ceiling: guidance fires on no more than one in five.
SERVED_CEILING = 0.2


def _record(corpus: Sequence[Mapping[str, Any]], connection: sqlite3.Connection) -> None:
    """Record the whole corpus, each payload through the verb that captures it."""
    for payload in corpus:
        capture(payload, connection)


def _submit_prompts(corpus: Sequence[Mapping[str, Any]], connection: sqlite3.Connection) -> None:
    """Put every prompt the corpus submitted to the verb that answers one."""
    for payload in corpus:
        if payload.get("hook_event_name") == PROMPT_EVENT:
            open_prompt(payload, connection)


def _other_project_opening(other: Path) -> tuple[Mapping[str, Any], ...]:
    """A second, independent real project's own opening of a source file.

    FR-048's cross-project graph is folded from every project's steps, not
    just this corpus's. Without a second, genuine occurrence of the corpus's
    own opening move, that graph would carry only the single one this corpus
    already contributes and could never honestly clear `Config.min_support`
    — the margin this test's one served occasion depends on would then have
    to be borrowed from the excluded fixture instead, which FR-058 says must
    never reach the store at all.
    """
    return (
        {
            "hook_event_name": PROMPT_EVENT,
            "session_id": OTHER_SESSION,
            "prompt_id": "prompt-1",
            "cwd": other.as_posix(),
        },
        {
            "hook_event_name": "PostToolUse",
            "session_id": OTHER_SESSION,
            "prompt_id": "prompt-1",
            "cwd": other.as_posix(),
            "tool_use_id": "toolu_other",
            "tool_name": "Read",
            "tool_input": {"file_path": (other / "src" / "main.py").as_posix()},
            "tool_result": "1  x = 1\n",
            "agent_id": "",
            "agent_type": "",
        },
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The project the corpus is replayed in, under a home directory of its own."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return tmp_path / "demo"


@pytest.fixture
def excluded(project: Path) -> Path:
    """Where `excluded_project.json` is replayed, marked opted out (FR-058).

    Planted before the corpus is recorded, so `is_excluded` finds it on the
    fixture's very first payload: without the marker, that fixture's prompt
    would be recorded and answered like any other, not suppressed entirely as
    its own description claims.
    """
    path = project.parent / "private-client"
    (path / STORE_DIR).mkdir(parents=True)
    (path / STORE_DIR / "optout").touch()
    return path


@pytest.fixture
def other(project: Path) -> Path:
    """A second real project, of this test's own making, next to *project*."""
    return project.parent / "other"


@pytest.fixture
def index(project: Path) -> Iterator[sqlite3.Connection]:
    """The episodic index the replay records into, named rather than defaulted.

    `open_index` binds its default at import, so an in-process replay that
    left it out would record into the real home directory instead of this
    test's.
    """
    with closing(open_index(home_dir() / DEFAULT_DATABASE_PATH.name)) as connection:
        yield connection


def test_guidance_is_served_on_no_more_than_one_occasion_in_five_of_the_corpus(
    project: Path, excluded: Path, other: Path, index: sqlite3.Connection
) -> None:
    """SC-005 read off the counter pair, after the corpus is recorded and asked.

    The snapshot is derived by `rebuild` because that is the writer the
    guidance path reads (FR-053); the prompts follow it, so each is answered
    from the memory the corpus left rather than from its own turn.

    Within *project*'s own graph, no opening clears the support floor: the
    corpus repeats a session and prompt id across several fixtures, so they
    fold into one continuing sequence rather than several independent
    openings, and every genuine opening in it is seen once. The one occasion
    that is served is a project with no graph of its own yet, answered from
    the cross-project fallback (FR-048) — which only has evidence because
    *other* gives the corpus's own opening move a second, independent home to
    have happened in. That is the whole of the margin here: a corpus that
    repeated one of its own openings would raise the served count, not the
    ceiling.
    """
    store = SQLiteEpisodicStore(index)
    corpus = tuple(
        corpus_payloads(
            (CORPUS_PROJECT, project.as_posix()), (EXCLUDED_PROJECT, excluded.as_posix())
        )
    )
    config = load_config()

    _record(corpus, index)
    _record(_other_project_opening(other), index)
    rebuild(Derivation(store=store, project_dir=project, level=config.level, config=config))

    _submit_prompts(corpus, index)
    open_prompt(
        {
            "hook_event_name": PROMPT_EVENT,
            "session_id": "sess-asker",
            "prompt_id": "prompt-1",
            "cwd": str(project.parent / "asker"),
        },
        index,
    )

    counted = store.counters()
    served, silent = counted.get("guidance_served", 0), counted.get("guidance_silent", 0)
    assert served > 0, "a channel that never speaks is dead, not silent"
    assert served / (served + silent) <= SERVED_CEILING
