"""The end-of-unit-of-work pass' pre-fold steps, drained from the collector (FR-004).

Telemetry reaches the memory as a file the developer's own collector writes, and
this is the one place that file is read: inside the detached job `SessionEnd`
spawns, the same pass the snapshots are folded in. Never on a hook path, which
has a timeout the file has no bound to respect, and never from a process that
stays up to watch it — that would be the listener the standing no-listening-port
decision rules out.

The drain is meant to run before the fold, because the records it takes are
episodic rows like any other and the snapshots this pass writes should be
folded from them too. That wiring waits on the OTLP reader and the
record-to-step ingest of `trajectory/telemetry.py` (T013, T014, T016); until
those land, this module only advances the persisted offset and reports the
lines it passed over.

Resuming from the persisted offset is what keeps the pass proportional to what
the last unit of work appended rather than to everything the session ever
emitted (`processrecall.trajectory.offset`).

The same pass derives the semantic layer (FR-019), which is why this module is
the one that sees both planes: `artifacts` parses source and may not produce
`graph` types, `graph` may not import the parser, and the translation between
them happens here, above both. Off the hot path, which is the only reason a
third-party parser may be reached at all.

Example:
    from processrecall.cli.derive import drain
    from processrecall.config import load_config

    print(drain(load_config().telemetry_path, store))
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath

from processrecall.artifacts.calls import CodeRef, Relation, extract_relations
from processrecall.artifacts.parse import parse_source
from processrecall.config import Counters, home_dir
from processrecall.graph.semantic import (
    FILE_KIND,
    UNRESOLVED_CALL,
    CodeEntity,
    CodeRelation,
    entity_key,
)
from processrecall.graph.store import EpisodicStep
from processrecall.trajectory.offset import OFFSET_NAME, OffsetFile
from processrecall.trajectory.paths import EXTERNAL_ROOT, HOME_ROOT


def drain(telemetry_path: str, store: Counters) -> str | None:
    """Take what the collector at *telemetry_path* appended since the last pass.

    ``None`` when no collector file is configured: that is the shipped state
    (`processrecall.config.Config.telemetry_path`), and a pass with no
    telemetry source has nothing to say about one rather than a count of zero.

    A configured file that is not there yet is a cold start — the developer
    named it before their collector wrote it — so it is counted and reported,
    not raised: this pass also folds the snapshots, and none of that work may
    be lost to a telemetry source being late. Ageing a file that has gone
    stale is T061's, not this guard's.
    """
    if not telemetry_path:
        return None
    target = Path(telemetry_path)
    if not target.is_file():
        store.bump("telemetry_absent")
        return f"{target}  absent"
    offsets = OffsetFile(home_dir() / OFFSET_NAME, store)
    return f"{target}  lines={sum(1 for _ in offsets.appended_lines(target))}"


@dataclass(frozen=True, slots=True)
class SemanticPass:
    """What one semantic derivation reads, and what it already derived before.

    Attributes:
        project_dir: The repository the touched paths are relative to, and the
            one place a host path enters: the keys themselves stay
            repository-relative, so nothing this pass returns carries it.
        steps: The episodic rows whose files work touched — the semantic layer
            is derived over what the episodic layer says was touched, never over
            the tree (FR-019).
        fingerprints: The content fingerprint already derived for each file
            entity key, which is what an unchanged file is recognised by.
    """

    project_dir: Path
    steps: tuple[EpisodicStep, ...]
    fingerprints: Mapping[str, str] = field(default_factory=dict)


def derive_semantic(work: SemanticPass, store: Counters) -> tuple[CodeEntity, ...]:
    """The file entity of every file *work* touched whose content has changed.

    Incremental on the content fingerprint (FR-019): a file whose fingerprint
    matches the one already derived is skipped rather than parsed a second time,
    which is what keeps the pass proportional to the unit of work instead of to
    the repository.

    The symbols a changed file declares become rows of their own in T091, which
    carries a parsed symbol's line range into an entity; this pass establishes
    the file row each of them hangs off, and the fingerprint that says whether
    the file needs reading at all.
    """
    derived = []
    texts = _touched_texts(work, store)
    for touched in _touched_files(work.steps):
        text = texts.get(touched.key)
        if text is None:
            continue
        source = work.project_dir / touched.key
        fingerprint = _fingerprint(text)
        if work.fingerprints.get(touched.key) == fingerprint:
            store.bump("semantic_files_skipped")
            continue
        try:
            parsed = parse_source(source, text)
        except Exception:
            store.bump("semantic_parse_failed")
            continue
        store.bump("semantic_files_parsed")
        derived.append(touched.entity(fingerprint, parsed.language or ""))
    return tuple(derived)


def derive_relations(work: SemanticPass, store: Counters) -> tuple[CodeRelation, ...]:
    """The `code_relations` rows the files *work* touched spell out (FR-018).

    Read over every touched file rather than only the changed ones, because
    resolution is by name across the set: a call in a file that changed resolves
    into a definition in one that did not only while both are in the text
    handed to the parser.

    A call no definition answers keeps the name it mentioned as its own
    relation and bumps ``semantic_unresolved_call``, so the count says how much
    of the call graph is name-only rather than leaving it to be read off null
    targets that a parse failure would look the same as (R16).

    Re-derives every relation each pass rather than skipping by fingerprint the
    way `derive_semantic` does: resolution depends on the whole touched set, so
    an unchanged file's relations are not provably unchanged on their own.
    Making relation derivation itself incremental is T037/SC-008's concern, not
    this one's.
    """
    rows = tuple(_code_relation(parsed) for parsed in extract_relations(_sources(work, store)))
    for row in rows:
        if row.relation == UNRESOLVED_CALL:
            store.bump("semantic_unresolved_call")
    return rows


def _touched_texts(work: SemanticPass, store: Counters) -> dict[str, str]:
    """The text of every file *work* touched that this pass can read, keyed relative.

    The one read of the touched set that `derive_semantic` and `_sources` both
    need, so a touched file is read off disk once rather than the same loop
    over `_touched_files` repeated per consumer. A file that cannot be read is
    counted here rather than dropped silently, whichever derivation asked for it.
    """
    readable = {}
    for touched in _touched_files(work.steps):
        text = _readable_text(work.project_dir / touched.key)
        if text is None:
            store.bump("semantic_parse_failed")
            continue
        readable[touched.key] = text
    return readable


def _sources(work: SemanticPass, store: Counters) -> dict[Path, str]:
    """The text of every file *work* touched that this pass can read, keyed relative.

    The keys stay repository-relative so the refs the parser hands back key
    straight through `processrecall.graph.semantic.entity_key`, with
    `SemanticPass.project_dir` the only place the host path is joined on.
    """
    return {Path(key): text for key, text in _touched_texts(work, store).items()}


def _code_relation(parsed: Relation) -> CodeRelation:
    """*parsed* as the row the semantic layer stores it as.

    The relation vocabulary is spelled on both sides of the `artifacts` / `graph`
    boundary, so the kind travels as it is; what this translates is the ends,
    from paths the parser read to the keys the column holds. Branches on the
    kind itself rather than on whether a target was resolved, so a parser that
    contradicts itself is caught by `CodeRelation`'s own validation instead of
    being read here as if nullness were what decided the relation's type (R16).
    """
    if parsed.kind == UNRESOLVED_CALL:
        return CodeRelation(
            source_key=_ref_key(parsed.source),
            relation=parsed.kind,
            target_name=parsed.target_name,
            sites=parsed.sites,
        )
    return CodeRelation(
        source_key=_ref_key(parsed.source),
        relation=parsed.kind,
        target_key=_ref_key(parsed.target) if parsed.target is not None else None,
        sites=parsed.sites,
    )


def _ref_key(ref: CodeRef) -> str:
    """The `code_entities` key for one end of a parsed relation."""
    return entity_key(ref.path, ref.qualified_name)


@dataclass(frozen=True, slots=True)
class _TouchedFile:
    """One file the episodic layer says work touched, with the steps that touched it."""

    key: str
    steps: tuple[EpisodicStep, ...]

    def entity(self, fingerprint: str, language: str) -> CodeEntity:
        """The `code_entities` row for the file, as this pass read it.

        The window comes from the steps rather than from the clock: what the
        semantic layer knows about a file is when work touched it, and how often.
        """
        touched_at = [step.occurred_at for step in self.steps]
        return CodeEntity(
            entity_key=self.key,
            kind=FILE_KIND,
            fingerprint=fingerprint,
            first_seen=min(touched_at),
            last_seen=max(touched_at),
            extension=PurePosixPath(self.key).suffix.removeprefix(".").lower(),
            language=language,
            support=len(self.steps),
        )


def _touched_files(steps: Iterable[EpisodicStep]) -> tuple[_TouchedFile, ...]:
    """Every file *steps* touched, each with the steps that touched it, once.

    Keyed through `processrecall.graph.semantic.entity_key`, the single door onto
    the key column, so a path the episodic layer recorded is refused here rather
    than stored if it is not repository-relative. A path
    `trajectory/paths.normalise_path` spelled outside the repository — under
    `HOME_ROOT`, under `EXTERNAL_ROOT`, or the bare `"."` for the project
    directory itself — is not a code entity at all, so it is dropped before the
    key is ever built rather than keyed and failed as a code entity.
    """
    touching: defaultdict[str, list[EpisodicStep]] = defaultdict(list)
    for step in steps:
        for path in step.files:
            if path == "." or PurePosixPath(path).parts[:1] in ((HOME_ROOT,), (EXTERNAL_ROOT,)):
                continue
            touching[entity_key(PurePosixPath(path))].append(step)
    return tuple(_TouchedFile(key, tuple(group)) for key, group in touching.items())


def _readable_text(source: Path) -> str | None:
    """*source*'s text, or ``None`` when this pass cannot read it as source.

    A file the pass cannot open or decode is passed over, not raised on: the
    step that touched it is recorded already, an editor may have deleted or
    renamed it since, and the fold this same pass does may not be lost to it.
    """
    try:
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _fingerprint(text: str) -> str:
    """The content hash the derivation is incremental on, algorithm named (FR-019)."""
    return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"
