"""`processrecall show` — the cold-path reader that keeps the rest of the package honest.

Principle V asks that nothing fail silently; the hooks answer it by counting,
which only pays off if a human can read the counts back. This module is that
reader, and its one rule is that it prints *shape* — counts, conditions,
provenance — and never a payload (FR-054): everything it can reach holds
prompts and result snippets, and an operator pasting its output into an issue
must not be pasting those.

Example:
    from processrecall.cli.show import Inspection, show

    print(show("counters", Inspection(store=store, project_dir=Path.cwd())))
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from processrecall.config import STORE_DIR, config_sources, load_config
from processrecall.graph.snapshot import SNAPSHOT_NAME, SnapshotFile
from processrecall.graph.store import EpisodicStore, SequenceKey, counter_table
from processrecall.graph.store import Sequence as RecordedSequence
from processrecall.trajectory.paths import project_key


@dataclass(frozen=True, slots=True)
class Inspection:
    """What one `show` reads from.

    Attributes:
        store: The episodic index, opened by the caller and closed by it.
        project_dir: The project a subject is asked about, for the snapshot
            that belongs to it and the sequences filed under it.
    """

    store: EpisodicStore
    project_dir: Path


def show(subject: str, view: Inspection) -> str:
    """What `show <subject>` prints for *view*, as one block of text."""
    if subject == "counters":
        return counter_report(view.store)
    if subject == "config":
        return config_report()
    if subject == "graph":
        return graph_report(view)
    if subject == "sequences":
        return sequence_report(view)
    raise ValueError(f"{subject!r} names nothing this command can show")


def _table(rows: Mapping[str, object]) -> str:
    """*rows* as the aligned ``name value`` block every subject prints its shape as."""
    width = max(len(name) for name in rows)
    return "\n".join(f"{name:<{width}} {value}" for name, value in rows.items())


def counter_report(store: EpisodicStore) -> str:
    """Every counter the package can increment, against what *store* kept (R16)."""
    return _table(counter_table(store))


def config_report() -> str:
    """Every configuration value, against the value and where it came from (R6).

    Provenance rather than the value alone: the defaults are the shipped
    tuning, so "3" tells an operator nothing about whether their config file
    was read, and a key the file spells wrong is dropped with only a log line
    to say so.
    """
    config = load_config()
    return _table(
        {name: f"{getattr(config, name)} {source}" for name, source in config_sources().items()}
    )


def graph_report(view: Inspection) -> str:
    """The served graph of *view*'s project: how much of it there is, and what each part holds.

    A snapshot that cannot be served is already counted by the reader as
    ``snapshot_unreadable`` (R11); here it is also said out loud, because the
    operator asking is the one person who can do something about it.

    Annotations are part of `contracts/cli.md`'s "nodes and edges with counts,
    conditions and annotations"; they ride the snapshot's own ``edge["annotations"]``
    key rather than a second read of the store, since `abstract.served` already
    carries every note `reattach` put back on rebuild.
    """
    path = view.project_dir / STORE_DIR / SNAPSHOT_NAME
    snapshot = SnapshotFile(path, view.store).read()
    if snapshot is None:
        return f"{path} is missing or unreadable; counted snapshot_unreadable"
    shape = {
        "level": snapshot.level,
        "generated_at": snapshot.generated_at.isoformat(),
        "episode_high_water": snapshot.episode_high_water,
        "nodes": len(snapshot.nodes),
        "edges": len(snapshot.edges),
    }
    # The bodies cross the snapshot seam as JSON-native values, typed `object`
    # so the served form can change without this reader; the format they are
    # written in is the mapping `contracts/storage.md` fixes.
    nodes = cast("Mapping[str, Mapping[str, Any]]", snapshot.nodes)
    edges = cast("Iterable[Mapping[str, Any]]", snapshot.edges)
    return "\n".join(
        [
            _table(shape),
            *(_node_line(key, body) for key, body in nodes.items()),
            *(_edge_line(edge) for edge in edges),
        ]
    )


def _node_line(key: str, body: Mapping[str, Any]) -> str:
    """One procedure, as its key and the support/level body it carries."""
    fields = "  ".join(f"{name}={value}" for name, value in body.items())
    return f"{key}  {fields}"


def _edge_line(edge: Mapping[str, Any]) -> str:
    """One transition, as its endpoints, its support and the condition it fires under."""
    condition = " ".join(f"{name}={value}" for name, value in edge["condition"].items())
    return f"{edge['source']} -> {edge['target']}  support={edge['support']}  {condition}"


def sequence_report(view: Inspection) -> str:
    """Every turn recorded for *view*'s project, newest first: its shape, never its text.

    A sequence is where the prompt and the result snippets hang, so this is
    the subject most able to leak one: what it prints is the identity, the
    counts and the two outcomes, and nothing a step stored (FR-054).

    `contracts/cli.md` asks for "recent sequences" but fixes no count to bound
    them to; ordering newest first is the recency this reads without inventing
    an unspecified limit.
    """
    wanted = project_key(str(view.project_dir))
    recorded = (view.store.sequence(key) for key in _recorded_keys(view.store))
    matching = [
        sequence
        for sequence in recorded
        if sequence is not None and sequence.project_dir_key == wanted
    ]
    matching.sort(key=lambda sequence: sequence.started_at, reverse=True)
    return (
        "\n".join(_sequence_line(sequence) for sequence in matching)
        if matching
        else f"no sequences recorded for {view.project_dir}"
    )


def _recorded_keys(store: EpisodicStore) -> tuple[SequenceKey, ...]:
    """Every sequence the store holds a step for, in the order they were started.

    Walked off the steps because the store seam lists no sequences
    (`contracts/python-api.md`) and a sequence with no step has no shape to
    report: this is the cold path, so the walk costs nothing a hook pays.
    """
    return tuple(dict.fromkeys(step.sequence_key for step in store.iter_steps()))


def _sequence_line(sequence: RecordedSequence) -> str:
    """One turn, as its identity and the counts hanging off it."""
    key = sequence.key
    identity = "/".join(
        part
        for part in (key.conversation_id, str(key.session_epoch), key.prompt_id, key.agent_id)
        if part
    )
    return (
        f"{identity}  process={sequence.process_type}  steps={sequence.step_count}"
        f"  status={sequence.status}  derived={sequence.derived_outcome}"
        f"  declared={sequence.declared_outcome}"
    )
