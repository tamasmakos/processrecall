"""Nothing the corpus fed in ever comes back out: the leak detector (SC-007).

FR-051 lets served text carry abstracted action templates, counts, conditions
and annotations and nothing else. FR-054 says the same of the per-project
snapshot, which is the half a developer may deliberately commit. SC-007 counts
the occurrences allowed across the fixture corpus: zero.

The corpus is replayed through the real capture path into a store of this
test's own, in a project directory of this test's own, so that the absolute
paths the detector hunts for are this machine's and not a fixture's spelling of
one. The two control tests are what keep a detector that finds nothing from
being a detector that sees nothing: what it reads as clean on both served
surfaces it must read as dirty when shown the payloads themselves.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterator, Mapping
from contextlib import closing
from dataclasses import asdict
from itertools import chain
from pathlib import Path
from typing import Any

import pytest

from processrecall.cli.rebuild import Derivation, _sequences, rebuild
from processrecall.config import STORE_DIR, Config, home_dir
from processrecall.graph.abstract import aggregate
from processrecall.graph.episodic import open_index
from processrecall.graph.keys import group_by_sequence
from processrecall.graph.snapshot import SNAPSHOT_NAME
from processrecall.graph.store import EpisodicStep, SQLiteEpisodicStore
from processrecall.guidance.locate import locate
from processrecall.guidance.neighborhood import extract
from processrecall.guidance.triggers import Firing, Triggers
from processrecall.integrations.claude_code.hooks import capture
from tests.guidance.conftest import CORPUS_PROJECT, corpus_payloads

#: An absolute path anywhere in a served string, by either of R4's spellings:
#: POSIX-rooted or drive-lettered. A node key (``"Inspection/Read/py"``) carries
#: no separator at its head and is not one.
ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|(?<![\w.])/)[\w.\-/\\]+")

#: The shapes a credential is written in: the issued-token prefixes, and any
#: assignment to a name that announces itself as a secret.
CREDENTIAL = re.compile(
    r"sk-[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|(?i:api[_-]?key|secret|token|password)\s*[:=]\s*\S+"
)

#: The payload fields carrying what a reader may never be served back: the
#: developer's own words, and the raw text an action read or wrote.
PRIVATE_FIELDS = ("prompt", "tool_result", "transcript_path")

#: Below this, a `tool_input` string is a generic token (a flag, a short
#: path segment) too common to prove a leak by; above it, it is content.
MIN_SECRET_LENGTH = 12


def leaks(text: str, secrets: Collection[str]) -> tuple[str, ...]:
    """Every forbidden string *text* carries, in the order they were looked for."""
    found = [secret for secret in secrets if secret in text]
    found += ABSOLUTE_PATH.findall(text)
    found += CREDENTIAL.findall(text)
    return tuple(found)


def _payloads(project: Path) -> Iterator[Mapping[str, Any]]:
    """Every hook payload of the corpus, working in *project* rather than `/work/demo`."""
    return corpus_payloads((CORPUS_PROJECT, project.as_posix()))


def _strings(value: Any) -> Iterator[str]:
    """Every raw string nested inside *value*, however deep — never its `repr`.

    ``str(some_dict)`` escapes the newlines inside its values, so it would
    hide a secret that carries one; walking to the leaves keeps every string
    byte-for-byte as the corpus wrote it.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        yield from chain.from_iterable(_strings(item) for item in value.values())
    elif isinstance(value, list):
        yield from chain.from_iterable(_strings(item) for item in value)


def _private(payload: Mapping[str, Any]) -> Iterator[str]:
    """The strings *payload* fed in that nothing may serve back (SC-007)."""
    for field in PRIVATE_FIELDS:
        if value := str(payload.get(field) or ""):
            yield value
    for argument in (payload.get("tool_input") or {}).values():
        if isinstance(argument, str) and len(argument) >= MIN_SECRET_LENGTH:
            yield argument


def _firings(store: SQLiteEpisodicStore, config: Config) -> tuple[Firing, ...]:
    """Every occasion guidance would speak on, replaying each prompt step by step."""
    steps = tuple(store.iter_steps())
    prompts = group_by_sequence(steps)
    sequences = _sequences(store, steps)
    graph = aggregate(steps, config.level, sequences, config)
    triggers = Triggers(config, store)
    fired = (
        triggers.fire(carried_out, extract(graph, locate(carried_out, config.level), h=config.h))
        for rows in prompts.values()
        for carried_out in _prefixes(rows)
    )
    return tuple(firing for firing in fired if firing is not None)


def _prefixes(rows: tuple[EpisodicStep, ...]) -> Iterator[tuple[EpisodicStep, ...]]:
    """The prompt as guidance sees it at each position: nothing done yet, then more."""
    return (rows[:carried_out] for carried_out in range(len(rows) + 1))


def _servable(firing: Firing) -> str:
    """Every string *firing* puts within a renderer's reach.

    A renderer assembles served text out of the edges it is handed and adds
    only its own punctuation, so reading the edges whole reads a superset of
    what is ever served — and one no character budget can quietly drop a leak
    from before the detector sees it.
    """
    return json.dumps([asdict(edge) for edge in firing.edges], default=str)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The project the corpus is replayed in, under a home directory of its own."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return tmp_path / "demo"


@pytest.fixture
def store(project: Path) -> Iterator[SQLiteEpisodicStore]:
    """The whole corpus, recorded through the capture path the harness uses.

    The index is named rather than defaulted: `open_index` binds its default
    at import, so an in-process replay that left it out would record into the
    real home directory instead of this test's.
    """
    with closing(open_index(home_dir() / "episodes.db")) as connection:
        for payload in _payloads(project):
            capture(payload, connection)
        yield SQLiteEpisodicStore(connection)


@pytest.fixture
def config() -> Config:
    """The tuning both served surfaces are folded under, at the same generality."""
    return Config()


@pytest.fixture
def secrets(project: Path) -> tuple[str, ...]:
    """Everything the corpus fed in that a leak would show, without repeats."""
    return tuple(dict.fromkeys(chain.from_iterable(_private(p) for p in _payloads(project))))


def test_the_detector_reads_the_payloads_the_corpus_fed_in_as_the_leak_they_are(
    project: Path, secrets: tuple[str, ...]
) -> None:
    """The control for both surfaces: shown the raw input, the detector must object."""
    fed_in = "\n".join(chain.from_iterable(_strings(payload) for payload in _payloads(project)))

    found = leaks(fed_in, secrets)

    assert set(secrets) <= set(found), "every private string the corpus fed in"
    assert any(item.startswith(project.as_posix()) for item in found), "an absolute path"


@pytest.mark.parametrize(
    "credential",
    (
        "sk-0123456789abcdefghij",
        "ghp_0123456789abcdefghij",
        "AKIAIOSFODNN7EXAMPLE",
        "api_key = 0123456789abcdef",
    ),
)
def test_the_detector_reads_a_credential_shaped_string_as_a_leak(credential: str) -> None:
    """The corpus carries no credential of its own, so this clause is proved on samples.

    Without it the credential half of SC-007 would be a pattern nothing has
    ever matched, and the day a fixture carries a token it would pass.
    """
    assert leaks(f"- the deploy step uses {credential}", ()) == (credential,)


def test_the_project_snapshot_carries_nothing_the_corpus_fed_in(
    project: Path, store: SQLiteEpisodicStore, config: Config, secrets: tuple[str, ...]
) -> None:
    """FR-054: the shareable half is names, templates, counts and annotations only."""
    rebuild(Derivation(store=store, project_dir=project, level=config.level))

    text = (project / STORE_DIR / SNAPSHOT_NAME).read_text(encoding="utf-8")

    assert json.loads(text)["nodes"], "the replay recorded something that could leak"
    assert leaks(text, secrets) == ()


def test_no_guidance_the_corpus_earns_is_servable_with_something_it_fed_in(
    store: SQLiteEpisodicStore, config: Config, secrets: tuple[str, ...]
) -> None:
    """FR-051: served text is abstracted templates, counts, conditions, annotations."""
    firings = _firings(store, config)

    assert firings, "guidance spoke over the corpus, so there was something to leak"
    assert leaks("\n".join(_servable(firing) for firing in firings), secrets) == ()
