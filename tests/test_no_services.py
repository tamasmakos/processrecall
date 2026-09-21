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

A receiver no caller reaches yet would leave no trace in either replay, so the
third test — Transport B stays declared and not built (FR-005) — reads the
package's syntax instead of the ledger.
"""

from __future__ import annotations

import ast
import json
import os
import re
import socket
import subprocess
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from processrecall.cli.rebuild import Derivation, rebuild
from processrecall.config import STORE_DIR, Config, load_config
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


#: The package whose source the Transport B pin below reads.
PACKAGE = REPO_ROOT / "processrecall"

#: Transport B's declaration, and the heading that carries it.
TRANSPORT_CONTRACT = (
    REPO_ROOT
    / ".claude"
    / "specs"
    / "007-otel-graph-schema-v2"
    / "contracts"
    / "collector-transport.md"
)
TRANSPORT_B_HEADING = "## Transport B — an in-plugin OTLP receiver (declared, not built)"

#: Every framework whose bind happens inside its own code, where no call below
#: appears. ``socket`` and ``grpc`` are deliberately not here: a bare import of
#: either is legitimate (a hostname lookup, an OTLP *exporter* client) and
#: `PORT_ACQUIRING_CALLS` already catches the bind/listen call itself. Prefixes:
#: ``http.server`` is one of these and ``http`` is not.
SERVER_MODULES = (
    "socketserver",
    "http.server",
    "wsgiref",
    "xmlrpc.server",
    "uvicorn",
    "flask",
    "fastapi",
    "aiohttp",
)

#: Every call that turns a socket into a listening one. ``stdio_server`` and the
#: MCP ``Server`` are deliberately not here: FR-065 serves JSON-RPC over stdin
#: and stdout, which takes no port.
PORT_ACQUIRING_CALLS = frozenset(
    {"bind", "listen", "serve_forever", "create_server", "start_server"}
)

#: The words a setting would carry to name a receiver's endpoint. Matched against
#: a field name's underscore-separated parts and never as substrings, because
#: ``min_support`` contains "port".
RECEIVER_SETTING_WORDS = frozenset({"receiver", "listener", "listen", "port", "endpoint", "bind"})

#: What a branch or a stub would have to name, together, to be anticipating the
#: undeclared receiver: OTLP *and* receiving or listening for it, or the
#: contract's own name for it outright. "receiv" alone is not enough —
#: `artifacts/parse.py` walrus-binds a `receiver` for a Go method receiver,
#: unrelated to OTLP — so the topic and the action are matched separately and
#: both required, case-insensitively, as substrings of the branch's own
#: unparsed source.
RECEIVER_ANTICIPATION_TOPIC = "otlp"
RECEIVER_ANTICIPATION_ACTIONS = ("receiv", "listen")
RECEIVER_ANTICIPATION_NAMES = ("transport b", "transport_b")


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


def _package_syntax_trees() -> Iterator[tuple[Path, ast.Module]]:
    """Every shipped module of the package, parsed.

    Parsed rather than searched as text: "receiver" is ordinary code here —
    `artifacts/parse.py` reads a Go method receiver — and prose that *mentions*
    a receiver is the declaration FR-005 asks for, not a breach of it. An
    import, a call and a branch are syntax, so that is what is read.
    """
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _modules_imported(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    """The dotted module names *node* imports."""
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    return (node.module or "",)


def _server_imports(tree: ast.Module) -> Iterator[str]:
    """Every socket or server module *tree* imports, with the line importing it."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for module in _modules_imported(node):
            if any(module == named or module.startswith(f"{named}.") for named in SERVER_MODULES):
                yield f"line {node.lineno}: imports {module}"


def _port_acquiring_calls(tree: ast.Module) -> Iterator[str]:
    """Every call in *tree* that would leave a port bound, with the line calling it."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            called = node.func.attr
        elif isinstance(node.func, ast.Name):
            called = node.func.id
        else:
            continue
        if called in PORT_ACQUIRING_CALLS:
            yield f"line {node.lineno}: calls {called}()"


def _raises_not_implemented(node: ast.Raise) -> bool:
    """Whether *node* raises ``NotImplementedError``, bare or called."""
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"


def _names_the_undeclared_receiver(segment: str) -> bool:
    """Whether *segment* ties itself to OTLP receiving, listening or Transport B."""
    lowered = segment.lower()
    topic_and_action = RECEIVER_ANTICIPATION_TOPIC in lowered and any(
        action in lowered for action in RECEIVER_ANTICIPATION_ACTIONS
    )
    return topic_and_action or any(name in lowered for name in RECEIVER_ANTICIPATION_NAMES)


def _receiver_anticipating_branches(tree: ast.Module) -> Iterator[str]:
    """Every branch or stub in *tree* whose source names the undeclared receiver.

    A live ``if transport == "b": ...`` or a ``raise NotImplementedError`` that
    already names OTLP receiving, listening or Transport B acquires no port and
    enables no setting, and would still slip past both checks above — this is
    the anticipation FR-005 rules out as well.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            segment = ast.unparse(node.test)
        elif isinstance(node, ast.Match):
            segment = ast.unparse(node.subject)
        elif isinstance(node, ast.Raise) and _raises_not_implemented(node):
            segment = ast.unparse(node)
        else:
            continue
        if _names_the_undeclared_receiver(segment):
            yield f"line {node.lineno}: {segment}"


def _settings_that_would_enable_a_receiver() -> list[str]:
    """Every ``Config`` field whose name would configure a receiver's endpoint.

    The field list is the whole surface: the file and environment readers
    recognise ``Config`` fields and discard every other key, so a receiver the
    operator could switch on has to appear here first.
    """
    return [
        field.name
        for field in fields(Config)
        if RECEIVER_SETTING_WORDS & set(field.name.split("_"))
    ]


def test_no_inplugin_otlp_receiver_exists() -> None:
    """Transport B stays a declaration and nothing more (FR-005).

    The shipped transport is a file the developer's own collector writes. The
    alternative — the plugin answering OTLP itself on a loopback port — is
    declared in `contracts/collector-transport.md` and deliberately not built,
    because a port that exists is a port whether or not a setting enables it.

    The two replays above watch a *running* turn, and a half-built receiver no
    caller reaches yet would leave no trace in one, so this reads the source
    instead: nothing in the package acquires a port, no setting names one, and
    the declaration the standing decision would be reopened against is still
    written down.
    """
    acquiring = {
        path.relative_to(REPO_ROOT).as_posix(): found
        for path, tree in _package_syntax_trees()
        if (found := [*_server_imports(tree), *_port_acquiring_calls(tree)])
    }
    assert acquiring == {}, (
        f"the package reaches for a listening socket in {acquiring} — FR-005 leaves the "
        "in-plugin OTLP receiver declared and not built, and adopting it reopens the "
        "standing no-listening-port decision rather than landing as an import"
    )

    enabling = _settings_that_would_enable_a_receiver()
    assert enabling == [], (
        f"Config declares {enabling}: a setting naming a receiver's endpoint is Transport B "
        "already half-built, and FR-005 has the decision reopened in docs/design.md rather "
        "than taken as a settings default"
    )

    anticipating = {
        path.relative_to(REPO_ROOT).as_posix(): found
        for path, tree in _package_syntax_trees()
        if (found := list(_receiver_anticipating_branches(tree)))
    }
    assert anticipating == {}, (
        f"the package anticipates the receiver in {anticipating} — FR-005 leaves Transport B "
        "declared and not built, and a branch or stub already naming it is the receiver "
        "half-built rather than merely documented"
    )

    assert TRANSPORT_B_HEADING in TRANSPORT_CONTRACT.read_text(encoding="utf-8"), (
        f"{TRANSPORT_CONTRACT.name} no longer declares Transport B under "
        f"{TRANSPORT_B_HEADING!r}: FR-005 asks for the contract to be written down, so that "
        "adopting the transport later is a decision and not a re-derivation"
    )
