"""Next-procedure prediction: a variable-order back-off over what came before.

The abstract graph is a bigram — one edge per observed move — so it cannot say
what follows *read, then edit*. This module counts the same episodic rows into
one table per context length and predicts from the longest context the corpus
actually supports, backing off to a shorter one where it does not (FR-028).

The Tensor Brain reading: this is the sampling side. Attention averages over
every label, a prediction commits to an order, and the order it commits to is
the longest one with evidence behind it.

Example:
    from processrecall.graph.sequence import fit

    model = fit(store.iter_steps(), level=config.level, config=config)
    ranked = model.rank(recent_steps)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from processrecall.config import Config
from processrecall.graph.abstract import Level
from processrecall.graph.keys import group_by_sequence, key_at, shares_a_file
from processrecall.graph.store import EpisodicStep

#: One context: the node keys preceding the move, most recent last, followed by
#: the marker below. A key is ``<class>/<program>``, so neither marker can be
#: mistaken for one.
Context = tuple[str, ...]

#: The move into the context's last step stayed on a file the step before it
#: had touched (FR-028, R6), or did not.
SAME_FILE = "@same-file"
OTHER_FILE = "@other-file"


@dataclass(frozen=True, slots=True)
class Prediction:
    """One candidate next procedure, with what it was predicted from.

    Attributes:
        key: The candidate's node key at the model's level.
        support: Observed moves into it from the context that was used.
        order: How many preceding procedures that context named.
    """

    key: str
    support: int
    order: int


@dataclass(frozen=True, slots=True)
class SequenceModel:
    """The counted contexts, one table per order, longest first at prediction.

    Attributes:
        level: The generality every key in the tables is spelled at.
        tables: What followed each context, counted, one table per order:
            the table for order *n* is at index *n* - 1.
        config: The tuning `rank` reads.
    """

    level: Level
    tables: tuple[Mapping[Context, Counter[str]], ...]
    config: Config

    def rank(self, recent: Sequence[EpisodicStep]) -> tuple[Prediction, ...]:
        """What is likeliest to follow *recent*, commonest first.

        *recent* is the position the prediction is made from: the steps just
        carried out, in the order they were, most recent last. The longest
        context it ends with that was observed `Config.min_support` times is
        what the prediction is made from; a context seen less often than that
        is backed off from rather than predicted from, and a position no
        context covers yields nothing.

        Deliberately the same floor an edge needs to serve guidance rather
        than a back-off-specific one: both ask "has this been seen often
        enough to act on", and giving them one knob means revising it moves
        both at once instead of two tunings drifting apart unnoticed.
        """
        keys = tuple(key_at(step, self.level) for step in recent)
        marker = _same_file_marker(recent, self.config)
        for order in range(min(len(keys), len(self.tables)), 0, -1):
            counts = self.tables[order - 1].get((*keys[-order:], *marker))
            if counts is not None and counts.total() >= self.config.min_support:
                return _ranked(counts, order)
        return ()


def fit(
    steps: Iterable[EpisodicStep],
    level: str,
    config: Config | None = None,
) -> SequenceModel:
    """Count every move in *steps* into the back-off tables at *level*.

    *config* is the tuning the model is counted and read under: how many
    preceding procedures the back-off starts from, and whether a context is
    additionally conditioned on the file the move stayed on (FR-028, R6). Both
    are counted into the tables, so a model fitted under one tuning cannot be
    read under another. The shipped defaults when absent, so a caller with no
    configuration of its own still fits the same model.

    Raises:
        ValueError: *level* names none of the materialised `LEVELS`.
    """
    lvl = Level.of(level)
    tuning = config or Config()
    tables: tuple[dict[Context, Counter[str]], ...] = tuple({} for _ in range(tuning.backoff_order))
    for chain in _chains(steps):
        keys = tuple(key_at(step, lvl) for step in chain)
        for position, target in enumerate(keys[1:], start=1):
            marker = _same_file_marker(chain[:position], tuning)
            for order in range(1, min(position, len(tables)) + 1):
                context = (*keys[position - order : position], *marker)
                tables[order - 1].setdefault(context, Counter())[target] += 1
    return SequenceModel(level=lvl, tables=tables, config=tuning)


def _same_file_marker(recent: Sequence[EpisodicStep], config: Config) -> Context:
    """Whether the last of *recent* stayed on a file the one before it touched.

    The one thing outside the node keys a context is conditioned on (FR-028):
    *edit, having just read that same file* is a different position from *edit,
    having read another*. It is read from the steps already carried out rather
    than from the candidate, because at the moment a prediction is asked for
    the candidate has no files yet.

    Nothing at all when the conditioning is configured off, which leaves the
    two positions one context again (R6).
    """
    if not config.same_file_conditioning:
        return ()
    stayed = len(recent) > 1 and shares_a_file(recent[-2], recent[-1])
    return (SAME_FILE if stayed else OTHER_FILE,)


def _ranked(counts: Counter[str], order: int) -> tuple[Prediction, ...]:
    """*counts* as predictions, commonest first, ties broken on the key.

    Ties are broken so that two models fitted from the same rows in a
    different order rank identically (FR-032).
    """
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(Prediction(key=key, support=count, order=order) for key, count in ordered)


def _chains(steps: Iterable[EpisodicStep]) -> Iterator[tuple[EpisodicStep, ...]]:
    """The rows of *steps* grouped by prompt, each in the order carried out.

    `group_by_sequence` (shared with `abstract._chains`) does the grouping and
    ordering; a context is counted from that order and from nothing the
    sequence carries, so the process type and the cleanliness the abstract
    fold decorates it with are two arguments this one has no use for.
    """
    yield from group_by_sequence(steps).values()
