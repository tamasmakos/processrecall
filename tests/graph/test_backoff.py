"""Next-procedure prediction: the variable-order back-off (FR-028, R6).

Read through `fit` and the model it returns, because that is the whole seam:
the back-off tables are counted from the same episodic rows the abstract graph
is folded from, and a prediction is only ever asked for from a position — the
steps just carried out.
"""

from __future__ import annotations

from processrecall.config import Config
from processrecall.graph.sequence import Prediction, fit
from processrecall.graph.store import EpisodicStep

from .conftest import make_aggregate_step, sequence

READ = "Inspection/Read/py"
EDIT = "ChangeImplementation/Edit/py"
TEST = "ArtifactEvaluation/pytest/py"
DEPLOY = "ScriptExecution/Run/py"
SEARCH = "Search/Find/py"
ON_A: tuple[str, ...] = ("a.py",)
ON_B: tuple[str, ...] = ("b.py",)
NO_FILE: tuple[str, ...] = ()


def walk_over(prompt_id: str, *steps: tuple[str, tuple[str, ...]]) -> tuple[EpisodicStep, ...]:
    """One prompt that performed each node key in order, on the files beside it."""
    key = sequence(prompt_id)
    return tuple(
        make_aggregate_step(node_key, position=position, step_id=0, key=key, files=files)
        for position, (node_key, files) in enumerate(steps)
    )


def walk(prompt_id: str, *node_keys: str) -> tuple[EpisodicStep, ...]:
    """One prompt that performed *node_keys* in order, touching no file at all."""
    return walk_over(prompt_id, *((node_key, NO_FILE) for node_key in node_keys))


def test_the_commonest_successor_of_the_last_procedure_ranks_first() -> None:
    """FR-028: order 1 is the bottom of the back-off — what followed this one."""
    model = fit(
        (*walk("p1", READ, EDIT), *walk("p2", READ, EDIT), *walk("p3", READ, TEST)),
        level="class/program",
    )

    ranked = model.rank(walk("q1", READ))

    assert [prediction.key for prediction in ranked] == [
        "ChangeImplementation/Edit",
        "ArtifactEvaluation/pytest",
    ]
    assert ranked[0].support == 2
    assert ranked[0].order == 1


def test_a_longer_context_outranks_the_bigram_that_disagrees_with_it() -> None:
    """FR-028: the back-off starts from the last three, not from the last one."""
    model = fit(
        (
            *walk("p1", READ, EDIT, TEST),
            *walk("p2", READ, EDIT, TEST),
            *walk("p3", TEST, EDIT, READ),
            *walk("p4", TEST, EDIT, READ),
            *walk("p5", TEST, EDIT, READ),
        ),
        level="class/program",
    )

    after_edit = model.rank(walk("q1", EDIT))
    after_reading_then_editing = model.rank(walk("q2", READ, EDIT))

    assert after_edit[0].key == "Inspection/Read"
    assert after_reading_then_editing[0] == Prediction(
        key="ArtifactEvaluation/pytest", support=2, order=2
    )


def test_the_back_off_reaches_order_three_when_the_corpus_supports_it() -> None:
    """FR-028: the back-off starts from the last three, not just the last two."""
    model = fit(
        (
            *walk("p1", READ, EDIT, TEST, DEPLOY),
            *walk("p2", READ, EDIT, TEST, DEPLOY),
            *walk("p3", SEARCH, EDIT, TEST, READ),
            *walk("p4", SEARCH, EDIT, TEST, READ),
            *walk("p5", SEARCH, EDIT, TEST, READ),
        ),
        level="class/program",
    )

    by_bigram = model.rank(walk("q1", EDIT, TEST))
    by_trigram = model.rank(walk("q2", READ, EDIT, TEST))

    assert by_bigram[0].key == "Inspection/Read"
    assert by_trigram[0] == Prediction(key="ScriptExecution/Run", support=2, order=3)


def test_the_order_the_backoff_starts_from_is_the_configured_one() -> None:
    """R6: order 3 is a default a later measurement revises, not a constant."""
    corpus = (
        *walk("p1", READ, EDIT, TEST),
        *walk("p2", READ, EDIT, TEST),
        *walk("p3", TEST, EDIT, READ),
        *walk("p4", TEST, EDIT, READ),
        *walk("p5", TEST, EDIT, READ),
    )

    bigram = fit(corpus, level="class/program", config=Config(backoff_order=1))

    ranked = bigram.rank(walk("q1", READ, EDIT))

    assert ranked[0] == Prediction(key="Inspection/Read", support=3, order=1)


def test_a_context_below_the_support_floor_backs_off_to_a_shorter_one() -> None:
    """FR-028: back off, rather than predict from a context seen once."""
    model = fit(
        (
            *walk("p1", READ, EDIT, TEST),
            *walk("p2", TEST, EDIT, READ),
            *walk("p3", TEST, EDIT, READ),
            *walk("p4", TEST, EDIT, READ),
        ),
        level="class/program",
    )

    ranked = model.rank(walk("q1", READ, EDIT))

    assert ranked[0] == Prediction(key="Inspection/Read", support=3, order=1)


def test_a_move_that_stayed_on_the_file_predicts_differently_from_one_that_did_not() -> None:
    """FR-028: the same two procedures are two contexts, split by the file."""
    model = fit(
        (
            *walk_over("p1", (READ, ON_A), (EDIT, ON_A), (TEST, NO_FILE)),
            *walk_over("p2", (READ, ON_A), (EDIT, ON_A), (TEST, NO_FILE)),
            *walk_over("p3", (READ, ON_A), (EDIT, ON_B), (READ, ON_A)),
            *walk_over("p4", (READ, ON_A), (EDIT, ON_B), (READ, ON_A)),
            *walk_over("p5", (READ, ON_A), (EDIT, ON_B), (READ, ON_A)),
        ),
        level="class/program",
    )

    stayed = model.rank(walk_over("q1", (READ, ON_A), (EDIT, ON_A)))
    moved_on = model.rank(walk_over("q2", (READ, ON_A), (EDIT, ON_B)))

    assert stayed[0] == Prediction(key="ArtifactEvaluation/pytest", support=2, order=2)
    assert moved_on[0] == Prediction(key="Inspection/Read", support=3, order=2)


def test_the_file_conditioning_is_off_when_it_is_configured_off() -> None:
    """R6: E1 cost top-1 what it bought top-3 — a default, not a constant."""
    corpus = (
        *walk_over("p1", (READ, ON_A), (EDIT, ON_A), (TEST, NO_FILE)),
        *walk_over("p2", (READ, ON_A), (EDIT, ON_A), (TEST, NO_FILE)),
        *walk_over("p3", (READ, ON_A), (EDIT, ON_B), (READ, ON_A)),
        *walk_over("p4", (READ, ON_A), (EDIT, ON_B), (READ, ON_A)),
        *walk_over("p5", (READ, ON_A), (EDIT, ON_B), (READ, ON_A)),
    )
    unconditioned = Config(same_file_conditioning=False)

    model = fit(corpus, level="class/program", config=unconditioned)

    stayed = model.rank(walk_over("q1", (READ, ON_A), (EDIT, ON_A)))
    moved_on = model.rank(walk_over("q2", (READ, ON_A), (EDIT, ON_B)))

    assert stayed[0] == Prediction(key="Inspection/Read", support=3, order=2)
    assert stayed == moved_on
