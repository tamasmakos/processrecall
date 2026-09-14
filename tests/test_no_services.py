"""The headline property, asserted where it can actually be observed (FR-070, SC-009).

"No background service, no listening port, no network" is a claim about a
*running* system, and a green suite is the wrong evidence for it: every other
module here exercises one seam, so a socket opened three layers down in the one
path nobody drives end to end would never show up. This module drives that path
— the fixture corpus replayed through the verbs ``hooks/hooks.json`` actually
invokes — with every channel that could acquire a port, reach the network or
leave a child behind replaced by a recorder that files the attempt and refuses
it. The assertion is that the ledger is empty.

Two events are deliberately out of the replay. ``SessionStart`` runs
``bin/bootstrap.sh``, which is installation rather than capture or guidance, and
``SessionEnd`` is where FR-050 puts the detached enrichment job — a process it is
*meant* to leave behind. FR-070 constrains the turn the developer is in, and
that is what is replayed.

The corpus records what a session did, so it holds no ``PreToolUse``: those are
derived here, because ``enforce`` is half of guidance and skipping it would
leave the deny path — snapshot read, fusion, render — unwatched.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from processrecall.cli.rebuild import Derivation, rebuild
from processrecall.config import STORE_DIR, load_config
from processrecall.graph.episodic import open_index
from processrecall.graph.store import SQLiteEpisodicStore
from processrecall.integrations.claude_code import hooks
from processrecall.integrations.claude_code.hooks import VERBS, Verb
from tests.conftest import PAYLOADS

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DECLARATION = REPO_ROOT / "hooks" / "hooks.json"

#: The synthetic project the corpus works in, rewritten at replay to a real one.
CORPUS_PROJECT = "/work/demo"

#: The harness events that make up one turn, and so the ones FR-070 speaks of.
TURN_EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop")

#: Every channel this process could take a port, an outbound request or a child
#: through. ``subprocess.Popen`` covers ``run`` and ``call``, which go through
#: it; ``os.fork`` and ``os.posix_spawn`` cover the ways around it.
GUARDED: tuple[tuple[Any, str], ...] = (
    (socket.socket, "bind"),
    (socket.socket, "connect"),
    (socket.socket, "connect_ex"),
    (socket, "create_connection"),
    (subprocess, "Popen"),
    (os, "fork"),
    (os, "posix_spawn"),
)


def _refusing(attempts: list[str], channel: str) -> Callable[..., Any]:
    """A stand-in for *channel* that files the call in *attempts* and refuses it.

    Refused, because a test that established "no outbound request" by making
    one would be its own counter-example. Filed as well as refused, because the
    property is about the ledger: a raise names the first channel reached, from
    whichever frame reached it, and dies there if a caller happens to catch it.
    """

    def refuse(*args: Any, **kwargs: Any) -> Any:
        attempts.append(f"{channel}{args!r}")
        raise AssertionError(f"{channel} reached during capture or guidance")

    return refuse


def _watch_for_services(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every guarded channel with a recorder, returning the ledger it fills.

    A channel this platform does not offer — ``os.fork``/``os.posix_spawn`` on
    Windows — is skipped rather than patched: there is no attribute to stand
    in for, and no way to reach it either.
    """
    attempts: list[str] = []
    for target, name in GUARDED:
        if not hasattr(target, name):
            continue
        monkeypatch.setattr(target, name, _refusing(attempts, f"{target.__name__}.{name}"))
    return attempts


def _verb_named_in(command: str) -> Verb:
    """The verb *command* invokes, as the shipped declaration spells it."""
    named = re.search(r"claude_code (\w+)", command)
    assert named, f"{HOOKS_DECLARATION}: {command!r} invokes no verb of this plugin"
    return VERBS[named.group(1)]


def _verbs_by_event() -> dict[str, Verb]:
    """Each turn event mapped to the verb ``hooks/hooks.json`` runs for it.

    Read off the declaration rather than written out here: a verb renamed in one
    place and not the other must fail this replay, not leave it driving a path
    the harness no longer takes.
    """
    declared = json.loads(HOOKS_DECLARATION.read_text(encoding="utf-8"))["hooks"]
    return {
        event: _verb_named_in(entries[0]["hooks"][0]["command"])
        for event, entries in declared.items()
        if event in TURN_EVENTS
    }


def _corpus(project: Path) -> Iterator[Mapping[str, Any]]:
    """Every synthetic hook payload, rewritten onto the real *project* directory."""
    for path in sorted(PAYLOADS.glob("*.json")):
        text = path.read_text(encoding="utf-8").replace(CORPUS_PROJECT, str(project))
        yield from json.loads(text)["payloads"]


def _asked_before(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``PreToolUse`` the harness sent before the completed action *payload*."""
    intended = {key: value for key, value in payload.items() if key != "tool_result"}
    return {**intended, "hook_event_name": "PreToolUse"}


def _as_the_harness_sends_it(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """*payload*, preceded by the question the harness asks before an action."""
    if payload["hook_event_name"] == "PostToolUse":
        yield _asked_before(payload)
    yield payload


def _replay(payloads: Iterable[Mapping[str, Any]]) -> list[tuple[str, Mapping[str, Any]]]:
    """Drive *payloads* through their verbs, collecting everything served back.

    Each response is kept with the event that produced it, so a caller can
    tell a guidance response (``UserPromptSubmit``, ``PreToolUse``) apart from
    the end-of-work nudge that ``Stop``/``SubagentStop`` serve unconditionally.
    """
    verbs = _verbs_by_event()
    served: list[tuple[str, Mapping[str, Any]]] = []
    for payload in payloads:
        for sent in _as_the_harness_sends_it(payload):
            event = str(sent["hook_event_name"])
            verb = verbs.get(event)
            if verb is not None and (response := verb(sent)) is not None:
                served.append((event, response))
    return served


def _opt_into_enforcement(home: Path) -> None:
    """Turn ``enforce`` on the way an operator does — it is off by default (FR-049)."""
    config = home / STORE_DIR / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"enforce": True}), encoding="utf-8")


def _derive_the_graph(project: Path, index_path: Path) -> int:
    """Fold the captured rows into both snapshots, reporting how many rows folded.

    Guidance reads a snapshot and answers nothing without one, so a replay that
    skipped this would watch the fusion and render path never run.
    """
    with closing(open_index(index_path)) as connection:
        store = SQLiteEpisodicStore(connection)
        config = load_config()
        rebuild(Derivation(store=store, project_dir=project, level=config.level, config=config))
        return len(tuple(store.iter_steps()))


def test_capture_and_guidance_bind_no_socket_and_spawn_no_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _opt_into_enforcement(home)
    # The default database path binds at import, from the real ``Path.home()``,
    # so isolating $HOME alone leaves every verb reading and writing the
    # developer's own store; the index is redirected explicitly instead.
    index_path = home / STORE_DIR / "episodes.db"
    monkeypatch.setattr(hooks, "open_index", lambda path=index_path: open_index(path))

    attempts = _watch_for_services(monkeypatch)
    _replay(_corpus(project))
    recorded = _derive_the_graph(project, index_path)
    served = _replay(_corpus(project))

    assert attempts == [], (
        f"the capture-and-guidance replay reached {len(attempts)} service channel(s): "
        f"{attempts} — FR-070 promises no listening port, no outbound request and no "
        "background process for the turn the developer is in"
    )
    # Without the two below, the assertion above would hold over a replay that
    # did nothing, which is the one way this test goes green and means nothing.
    assert recorded, "the replay captured no step: the verbs ran over an empty pipeline"
    guidance = {event for event, _ in served if event in ("UserPromptSubmit", "PreToolUse")}
    assert guidance, "the replay served no guidance: neither prompt nor enforce answered"
