"""The five seams of `contracts/python-api.md`, and who implements them.

A protocol earns its place by having more than one implementation, or by a
requirement that demands the substitution. This test is the pin on that claim:
every seam the contract names is checked against the implementations it names,
so a seam that quietly lost its second implementation, or an implementation
that dropped one of the protocol's named members, fails here rather than at
the harness. It checks that each member exists, not its signature or return
type.

Conformance is read off the class, not off an instance: constructing a store
wants a connection and a renderer wants counters, and neither says anything
about whether the seam is honoured.
"""

from __future__ import annotations

import sys
from collections import Counter

import pytest

from processrecall.graph.store import EpisodicStore, SQLiteEpisodicStore
from processrecall.guidance.render import BulletRenderer, Renderer
from processrecall.integrations.claude_code.hooks import AdditionalContextSink, HookSource
from processrecall.symbolic.classify import (
    Classifier,
    NullClassifier,
    PromptProcessType,
    RationaleActivity,
    UnknownCommandActivity,
    load_classifiers,
)
from processrecall.trajectory.protocol import GuidanceSink, ListSink, NullSink, TrajectorySource
from processrecall.trajectory.transcript import TranscriptSource
from tests.trajectory.test_fake_source import FakeSource

pytestmark = pytest.mark.unit

#: The contract's table: each seam against the implementations it names.
SEAMS: dict[type, tuple[type, ...]] = {
    TrajectorySource: (HookSource, TranscriptSource, FakeSource),
    GuidanceSink: (AdditionalContextSink, NullSink, ListSink),
    EpisodicStore: (SQLiteEpisodicStore,),
    Classifier: (
        RationaleActivity,
        PromptProcessType,
        UnknownCommandActivity,
        NullClassifier,
    ),
    Renderer: (BulletRenderer,),
}


def _protocol_members(protocol: type) -> set[str]:
    """*protocol*'s own named members — public, and not one of Protocol's dunders."""
    return {name for name in vars(protocol) if not name.startswith("_")}


def _unimplemented(implementation: type, protocol: type) -> set[str]:
    """The members of *protocol* that *implementation* does not have."""
    return {name for name in _protocol_members(protocol) if not hasattr(implementation, name)}


@pytest.mark.parametrize(
    ("protocol", "implementation"),
    [(protocol, one) for protocol, named in SEAMS.items() for one in named],
    ids=lambda argument: argument.__name__,
)
def test_every_seam_has_the_implementations_the_contract_names(
    protocol: type, implementation: type
) -> None:
    """FR-002: every implementation the contract names conforms to its seam."""
    assert not _unimplemented(implementation, protocol)


class _CountingCounters:
    """A counters stand-in that keeps what was bumped, so a test can read it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


def test_null_classifier_is_what_loads_without_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-059, FR-060: absent the extra, all three seats hold the null classifier, counted.

    A ``None`` in ``sys.modules`` is what an uninstalled extra looks like from
    inside the import system: the import raises, and nothing else is disturbed.
    """
    monkeypatch.setitem(sys.modules, "gliner2", None)
    counters = _CountingCounters()

    classifiers = load_classifiers(counters)

    assert [
        type(classifiers.rationale_activity),
        type(classifiers.prompt_process_type),
        type(classifiers.unknown_command_activity),
    ] == [NullClassifier] * 3
    classifiers.rationale_activity.label("anything")
    assert counters.counted["enrichment_unavailable"] == 1
