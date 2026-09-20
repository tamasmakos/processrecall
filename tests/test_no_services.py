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

Telemetry is the first channel that makes a listening port plausible here — a
collector speaks OTLP, and the easy way to consume it is to answer it — so the
ledger covers that path as well: the second test points the end-of-unit-of-work
pass at a collector file and drains it, which is where FR-004 puts the read.
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
from processrecall.trajectory.offset import OFFSET_NAME
from tests.conftest import PAYLOADS

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DECLARATION = REPO_ROOT / "hooks" / "hooks.json"

#: A collector's own output, as the fixture corpus spells it: the file FR-004
#: points the end-of-unit-of-work pass at, read here rather than copied because
#: the pass only reads it and records where it stopped.
TELEMETRY_CORPUS = REPO_ROOT / "tests" / "fixtures" / "telemetry" / "events_only.jsonl"

#: The synthetic project the corpus works in, rewritten at replay to a real one.
CORPUS_PROJECT = "/work/demo"

#: The corpus's second synthetic project — the one `excluded_project.json` is
#: opted out of (FR-058). Rewritten to a real directory for the same reason
#: CORPUS_PROJECT is: see :func:`_corpus`.
EXCLUDED_PROJECT = "/work/private-client"

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


def _corpus(project: Path, excluded: Path) -> Iterator[Mapping[str, Any]]:
    """Every synthetic hook payload, rewritten onto real directories.

    Both synthetic projects are rewritten, not just *project*. Leaving
    `EXCLUDED_PROJECT` as the literal `/work/private-client` made the replay
    write to a path outside the test's `tmp_path` — harmlessly on Linux, where
    the root is unwritable and the writes simply failed, but on Windows it
    resolves against the current drive and the corpus really did create
    `C:\\work\\private-client`. The two platforms then replayed different
    corpora, which is the whole reason this is spelled out here.

    `as_posix()`, and never `str()`: the rewrite happens in the JSON *text*,
    and a Windows path's backslashes are escapes inside a string literal —
    the `\\T` of `\\Temp` left the corpus unparseable.
    """
    rewrites = ((CORPUS_PROJECT, project.as_posix()), (EXCLUDED_PROJECT, excluded.as_posix()))
    for path in sorted(PAYLOADS.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for old, new in rewrites:
            text = text.replace(old, new)
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


def _opt_out_of_capture(excluded: Path) -> None:
    """Plant the marker `excluded_project.json` claims its project holds (FR-058).

    Planted before the replay, so `is_excluded` finds it on that fixture's very
    first payload. Without it the fixture's prompt is captured and answered
    like any other, and the corpus quietly stops exercising the exclusion its
    own description is about.
    """
    (excluded / STORE_DIR).mkdir(parents=True, exist_ok=True)
    (excluded / STORE_DIR / "optout").touch()


def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A ``$HOME``/``project`` pair redirected away from the developer's own store.

    The default database path binds at import, from the real ``Path.home()``,
    so isolating ``$HOME`` alone leaves every verb reading and writing the
    developer's own store; callers that need the index redirected too do that
    explicitly, past what this helper covers.
    """
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home, project


def _opt_into_enforcement(home: Path) -> None:
    """Turn ``enforce`` on the way an operator does — it is off by default (FR-049).

    ``min_support`` comes down to one with it, because of what the corpus is.
    `PAYLOADS` is the shared edge-case fixture — clear, compaction, malformed,
    a duplicate, an excluded project — and it carries one session in which each
    opening happens exactly once. Every `Start` edge derived from it therefore
    has support 1, and the default floor of 2 (FR-045a) filters all three away
    before `Triggers.fire` ever looks at them, so guidance is silent no matter
    how often the corpus is replayed: the rows carry fixed ids, capture is
    idempotent over them, and a second pass adds no support. The floor is
    lowered here rather than the assertion relaxed — one observation is
    evidence enough in *this* replay, and the served path stays the real one,
    fusion and triggers and rendering included.
    """
    config = home / STORE_DIR / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"enforce": True, "min_support": 1}), encoding="utf-8")


def _derive_the_graph(project: Path, index_path: Path) -> int:
    """Fold the captured rows into both snapshots, reporting how many rows folded.

    Guidance reads a snapshot and answers nothing without one, so a replay that
    skipped this would watch the fusion and render path never run.

    This is also the pass that drains the collector file (FR-004), which is why
    the telemetry replay below drives it rather than a reader of its own: the
    read happens inside this pass and nowhere else.
    """
    with closing(open_index(index_path)) as connection:
        store = SQLiteEpisodicStore(connection)
        config = load_config()
        rebuild(Derivation(store=store, project_dir=project, level=config.level, config=config))
        return len(tuple(store.iter_steps()))


def test_capture_and_guidance_bind_no_socket_and_spawn_no_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, project = _isolated_home(tmp_path, monkeypatch)
    excluded = tmp_path / "private-client"
    _opt_into_enforcement(home)
    _opt_out_of_capture(excluded)
    # The default database path binds at import, from the real ``Path.home()``,
    # so isolating $HOME alone leaves every verb reading and writing the
    # developer's own store; the index is redirected explicitly instead.
    index_path = home / STORE_DIR / "episodes.db"
    monkeypatch.setattr(hooks, "open_index", lambda path=index_path: open_index(path))
    # Named here too, so a verb that drained the collector file on this path —
    # the one FR-004 reserves for the end-of-unit-of-work pass — would leave an
    # offset behind and be caught below, rather than this module never looking.
    monkeypatch.setenv("PROCESSRECALL_TELEMETRY_PATH", str(TELEMETRY_CORPUS))

    attempts = _watch_for_services(monkeypatch)
    _replay(_corpus(project, excluded))
    assert not (home / STORE_DIR / OFFSET_NAME).exists(), (
        "an offset file exists before the end-of-unit-of-work pass ran: the hook path read "
        "the collector file FR-004 reserves for that pass"
    )
    recorded = _derive_the_graph(project, index_path)
    served = _replay(_corpus(project, excluded))

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


def test_telemetry_ingest_opens_no_socket_and_spawns_no_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, project = _isolated_home(tmp_path, monkeypatch)
    # The developer names the file their own collector writes; nothing here
    # installs, starts or supervises the collector itself (FR-004).
    monkeypatch.setenv("PROCESSRECALL_TELEMETRY_PATH", str(TELEMETRY_CORPUS))

    attempts = _watch_for_services(monkeypatch)
    _derive_the_graph(project, home / STORE_DIR / "episodes.db")

    assert attempts == [], (
        f"the telemetry ingest pass reached {len(attempts)} service channel(s): {attempts} "
        "— FR-004 has the collector file read in the end-of-unit-of-work pass, not by a "
        "listener answering OTLP and not by a collector this plugin starts"
    )
    # Without this the assertion above would hold over a pass that never opened
    # the file, which is the one way a telemetry ledger goes empty and means nothing.
    recorded = json.loads((home / STORE_DIR / OFFSET_NAME).read_text(encoding="utf-8"))
    assert recorded["path"] == str(TELEMETRY_CORPUS), (
        f"the ledger names {recorded['path']!r}, not {TELEMETRY_CORPUS}: an offset that "
        "does not pin the file it was taken from would tolerate a rotation or alias silently"
    )
    assert recorded["offset"] == TELEMETRY_CORPUS.stat().st_size, (
        f"the pass stopped at byte {recorded['offset']} of {TELEMETRY_CORPUS}: the drain "
        "read less than the collector wrote, so the ledger covers less than the whole file"
    )
