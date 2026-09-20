"""The published measurement of each traversal against the baseline (FR-036, SC-010).

`scripts/measure_traversals.py` is a maintainer script rather than shipped code,
so what is asserted here is the seam its published numbers come out of: sessions
before the split time train, sessions after it test, and every ranking reported
with recall@1, recall@5 and MRR.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from processrecall.config import ActivityClass, Config
from processrecall.graph.store import EpisodicStep, SequenceKey
from processrecall.guidance.paths import USUAL_NEXT
from scripts.measure_traversals import TRAVERSALS, Ranking, Session, measure, significance
from tests.conftest import make_sequence

pytestmark = pytest.mark.unit

EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"

LEVEL = "class/program"

#: When the earliest prompt of the fixture corpus was carried out.
FIRST = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


def session(prompt: str, at: datetime, *node_keys: str) -> Session:
    """One recorded prompt, begun *at*, that performed *node_keys* in that order."""
    key = SequenceKey(conversation_id="c1", session_epoch=0, prompt_id=prompt)
    steps = tuple(
        _step(node_key, key=key, position=position, at=at + timedelta(minutes=position))
        for position, node_key in enumerate(node_keys)
    )
    return Session(steps=steps, sequence=make_sequence(steps))


def _step(node_key: str, *, key: SequenceKey, position: int, at: datetime) -> EpisodicStep:
    """One recorded row of *key*, named by the node key the recorder derived for it."""
    activity_class, program, _ = node_key.split("/")
    return EpisodicStep(
        dedup_key=f"{key.prompt_id}-{position}",
        sequence_key=key,
        position=position,
        node_key=node_key,
        activity_class=ActivityClass(activity_class),
        program=program,
        template=f"{program} <File>",
        occurred_at=at,
        step_id=int(key.prompt_id.removeprefix("p")) * 1000 + position + 1,
    )


def test_split_is_temporal_and_reports_recall_and_mrr() -> None:
    """The training half is entirely before the split time, and the baseline is measured."""
    corpus = (
        session("p1", FIRST, EDIT, TEST),
        session("p2", FIRST + timedelta(hours=1), EDIT, TEST),
        session("p3", FIRST + timedelta(days=1), EDIT, TEST),
    )

    report = measure(corpus, Config(level=LEVEL, min_support=1))

    assert [item.sequence.key.prompt_id for item in report.split.train] == ["p1", "p2"]
    assert [item.sequence.key.prompt_id for item in report.split.test] == ["p3"]
    assert all(item.started_at < report.split.at for item in report.split.train)
    assert all(item.started_at >= report.split.at for item in report.split.test)
    assert report.baseline.name == USUAL_NEXT
    assert report.baseline.queries == 2
    assert report.baseline.recall_at_1 == 1.0
    assert report.baseline.recall_at_5 == 1.0
    assert report.baseline.mrr == 1.0
    assert [row.alone.name for row in report.rows] == list(TRAVERSALS[1:])


def test_significance_is_certain_when_every_ask_ties() -> None:
    """Two rankings that never disagree have nothing for the sign test to read."""
    fused = Ranking(name="fused", ranks=(1, 2, 0))
    without = Ranking(name="without", ranks=(1, 2, 0))

    assert significance(fused, without) == 1.0


def test_significance_matches_a_hand_computed_sign_test() -> None:
    """One ask *fused* wins and four it loses: an exact two-sided binomial tail.

    Worked by hand rather than through `math.comb`: of the 2**5 = 32 equally
    likely win/loss patterns over 5 disagreements, the ones at least as lopsided
    as 1-for and 4-against are the 1 that is 0-for-5 and the 5 that are
    1-for-4-against, on either side — (1 + 5) * 2 = 12 of them, so p = 12/32.
    """
    fused = Ranking(name="fused", ranks=(1, 0, 0, 0, 0))
    without = Ranking(name="without", ranks=(0, 1, 1, 1, 1))

    assert significance(fused, without) == pytest.approx(0.375)
