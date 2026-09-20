"""The store seam: episodic rows in, episodic rows out (FR-056).

`contracts/python-api.md` puts persistence behind a protocol so an alternative
backend can be substituted without graph or guidance knowing. This module is
that protocol and the one implementation that ships: SQLite, over the index
`episodic.py` opens.

The dataclasses here are the episodic plane of `data-model.md` — a sequence, its
identity, and the steps hanging off it — and they are what crosses the seam in
both directions, so no caller ever spells a column name.

On the hot path, so the standard library only.

Example:
    from processrecall.graph.episodic import open_index
    from processrecall.graph.store import SQLiteEpisodicStore

    store = SQLiteEpisodicStore(open_index())
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

from processrecall.config import RESULT_CEILING, ActivityClass, ProcessType, home_dir
from processrecall.graph.annotations import Annotation
from processrecall.graph.schema import CaptureSource, DecisionSource, StepDecision, StepResult
from processrecall.trajectory.records import CONSUMED_EVENT_NAMES
from processrecall.trajectory.telemetry import (
    PROMPT_ATTRIBUTE,
    RECORD_TYPE_ATTRIBUTE,
    SEQUENCE_ATTRIBUTE,
    SESSION_ATTRIBUTE,
    TIMESTAMP_ATTRIBUTE,
    TelemetryRecord,
    optional_bool,
    optional_integer,
    optional_text,
    parse_instant,
)

if TYPE_CHECKING:
    from processrecall.trajectory.event import TrajectoryEvent

_logger = logging.getLogger("processrecall")

#: Bytes at which the R16 fallback log rotates aside rather than growing
#: unbounded.
_LOG_ROTATE_BYTES = 5 * 1024 * 1024

#: The R16 fallback log, under :func:`~processrecall.config.home_dir`: where a
#: counter goes when the store that would have kept it is the thing that would
#: not open. Named here rather than at each caller because every one of them
#: reaches it through :func:`log_fallback`, which lives in this module.
FALLBACK_LOG = Path("log") / "hooks.jsonl"

#: `RESULT_CEILING` is imported above from :mod:`processrecall.config`, where
#: every source that produces a `TrajectoryEvent` reads the same ceiling.
#: FR-010's other half — 600 characters of a prompt — has no counterpart here:
#: no field of `EpisodicStep` or the `steps`/`sequences` schema stores prompt
#: text at all (R13), so there is nothing to bound.

#: Every counter the package can increment (R16), whether or not a given store
#: has ever seen one. The list is the reader half of Principle V, and it lives
#: beside `bump` because that is what it is the vocabulary of: both readers —
#: `processrecall show counters` and the `inspect` tool — print a name that is
#: still at zero, since a capture path that never ran looks identical to one
#: that never existed unless the name is printed anyway.
COUNTERS: tuple[str, ...] = (
    "annotation_rejected_credential",
    "annotation_rejected_no_edge",
    "annotation_rejected_too_long",
    "backfill_records_skipped",
    "capture_excluded",
    "capture_payload_malformed",
    "capture_store_busy",
    "class_unknown",
    "enrichment_unavailable",
    "gap_agent_nesting",
    "gap_error_class",
    "gap_permission_wait",
    "gap_stop_reason",
    "gap_tool_details",
    "gap_ttft",
    "gap_version_floor",
    "guidance_below_support",
    "guidance_deadline_exceeded",
    "guidance_diversity_dropped",
    "guidance_exploration_slot",
    "guidance_fallback_global",
    "guidance_over_budget",
    "guidance_served",
    "guidance_silent",
    "inference_adjacent",
    "inference_unlinked",
    "inferences_recorded",
    "migration_v1_v2",
    "path_after_callers",
    "path_after_callers_used",
    "path_after_change",
    "path_after_change_used",
    "path_frequent_episode",
    "path_frequent_episode_used",
    "path_generalised",
    "path_generalised_used",
    "path_on_entity",
    "path_on_entity_used",
    "path_ppr_neighbourhood",
    "path_ppr_neighbourhood_used",
    "path_prompt_start",
    "path_prompt_start_used",
    "path_usual_next",
    "path_usual_next_used",
    "path_usually_refused",
    "path_usually_refused_used",
    "process_type_derived",
    "process_type_unknown",
    "semantic_files_parsed",
    "semantic_files_skipped",
    "semantic_parse_failed",
    "semantic_unresolved_call",
    "snapshot_unreadable",
    "snapshot_write_failed",
    "snapshot_written",
    "steps_duplicate",
    "steps_from_hook",
    "steps_from_telemetry",
    "steps_recorded",
    "telemetry_absent",
    "telemetry_content_stripped",
    "telemetry_hook_disagreement",
    "telemetry_identity_stripped",
    "telemetry_offset_reset",
    "telemetry_record_partial",
    "telemetry_record_unknown",
    "telemetry_records_read",
    "telemetry_session_unbound",
    "telemetry_stale",
    "touched_file_only",
    "touched_symbol_resolved",
)


def _canonical_json(arguments: Mapping[str, object]) -> str:
    """*arguments* as one string that two passes over the same record agree on.

    Sorted keys and no whitespace: a mapping's iteration order is an accident of
    how it was parsed, and a derived key that changed with it would record the
    same backfilled action twice (R3).
    """
    return json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class SequenceKey:
    """What identifies one prompt's chain of actions (R2).

    Four fields rather than one id because no harness issues one: a conversation
    is resumed, compacted and forked, and sub-agents run inside their parent's
    turn. ``agent_id`` is ``""`` for the main agent.
    """

    conversation_id: str
    session_epoch: int
    prompt_id: str
    agent_id: str = ""

    def dedup_key(self, event: TrajectoryEvent, ordinal: int) -> str:
        """What identifies one sub-activity of *event*, the *ordinal*-th it decomposed into.

        The harness's own tool-call id when it issued one (FR-008); otherwise the
        R3 derivation, a pure function of the record so that backfilling the same
        source twice derives one key rather than two (SC-003). The ``syn-``
        prefix keeps a derived key out of the space of harness-issued ones.

        The ordinal discriminates both branches, because one action is not one
        step: a compound shell command decomposes into a sub-activity per simple
        command, and every one of them asks this for a key. Keyed on the
        tool-call id alone they would collide, and all but the first would be
        discarded as a replay — the chain would keep the `cd` and lose the
        `pytest` after it. It is the ordinal *within the action* and never the
        position within the sequence: the sequence position counts steps already
        stored, so a genuinely replayed payload would land a second time under
        a shifted position, which is the duplicate FR-008 exists to refuse.
        """
        if event.tool_call_id:
            return f"{event.tool_call_id}#{ordinal}"
        material = "|".join(
            (
                self.conversation_id,
                str(self.session_epoch),
                self.prompt_id,
                self.agent_id,
                str(ordinal),
                event.tool_name,
                _canonical_json(event.tool_call_arguments),
            )
        )
        return f"syn-{sha256(material.encode()).hexdigest()[:24]}"


#: The status a closed sequence carries (`close_sequence`), spelled once so
#: nothing outside this module compares against the raw string.
CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Sequence:
    """One prompt's chain: the steps of a single user turn, and its outcome.

    ``step_count`` is read from the store rather than kept in step with the
    rows — a stored count that drifts from the rows is the kind of quietly wrong
    number Principle V exists to prevent.

    ``head_revision`` and ``head_branch`` are the commit and branch the turn ran
    on where the harness observed them (FR-023), and ``None`` where it did not.
    ``prompt_length``, ``command_name``, ``command_source``, ``workflow_run_id``,
    ``workflow_name`` and ``app_version`` are likewise ``None`` where the record
    the turn was opened from did not carry them, and nothing backfills one (R14).
    """

    key: SequenceKey
    project_dir_key: str
    started_at: datetime
    process_type: ProcessType = ProcessType.UNKNOWN
    status: str = "open"
    ended_at: datetime | None = None
    step_count: int = 0
    derived_outcome: str = "neutral"
    declared_outcome: str | None = None
    prompt_length: int | None = None
    command_name: str | None = None
    command_source: str | None = None
    workflow_run_id: str | None = None
    workflow_name: str | None = None
    app_version: str | None = None
    head_revision: str | None = None
    head_branch: str | None = None
    source: CaptureSource | None = None


@dataclass(frozen=True, slots=True)
class EpisodicStep:
    """One concrete recorded action — the Tensor Brain's episodic index, on disk.

    ``step_id`` is the store's own rowid and the snapshot's high-water mark: it
    is ``0`` on a step that has not been recorded yet, and the store assigns the
    real one.

    ``result`` and ``decision`` are FR-022's two independent axes — whether the
    call could do the thing, and whether it was allowed to try — and nothing
    collapses them into one value: a refusal is a policy signal and a failure a
    capability signal. Each field from ``result`` down is ``None`` when the
    record the step was captured from did not carry it, and nothing backfills
    one (R14).
    """

    dedup_key: str
    sequence_key: SequenceKey
    position: int
    node_key: str
    activity_class: ActivityClass
    template: str
    occurred_at: datetime
    program: str = ""
    files: tuple[str, ...] = ()
    result_snippet: str = ""
    outcome: str = "neutral"
    record_ref: str = ""
    rationale_label: str | None = None
    symbol_ref: str | None = None
    result: StepResult | None = None
    kind: str | None = None
    decision: StepDecision | None = None
    decision_source: DecisionSource | None = None
    duration_ms: int | None = None
    error_type: str | None = None
    input_size_bytes: int | None = None
    result_size_bytes: int | None = None
    tool_source: str | None = None
    source: CaptureSource | None = None
    step_id: int = 0


@dataclass(frozen=True, slots=True)
class Inference:
    """One model call the harness reported — a row of `inferences` (FR-002).

    ``outcome`` is which of the three model-call records the row was built from,
    so the events-only stream produces it whole and no span attribute is
    consulted to reach it (R9).

    Each field from ``model`` down is ``None`` when the record did not carry it,
    and nothing backfills one (R14): an error reports no token counts, and a
    refusal neither a status code nor a duration. ``first_content_ms`` and
    ``error_class`` are columns no event fills at all, so they are not fields
    here — span enrichment is what writes them.
    """

    inference_id: str
    sequence_key: SequenceKey
    outcome: str
    occurred_at: datetime
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_micros: int | None = None
    duration_ms: int | None = None
    speed: str | None = None
    effort: str | None = None
    query_source: str | None = None
    status_code: int | None = None
    attempt: int | None = None
    stop_reason: str | None = None


#: Which of the two actors an `agents` row is (R7): the main thread, or a
#: sub-agent it spawned. Events-only identity reaches a kind and no name, so the
#: pair is the whole vocabulary of `agents.kind`.
AgentKind = Literal["main", "subagent"]


@dataclass(frozen=True, slots=True)
class Agent:
    """One actor the harness reported — a row of `agents` (FR-020).

    ``agent_type``, ``agent_source``, ``is_built_in``, ``is_async`` and the
    workflow pair come from `subagent_completed`, the only events-mode record
    naming an agent type (R7). ``parent_agent_id`` is the `spawned` edge, and
    span enrichment is its only source (R7), so a row built from events alone
    leaves it unset rather than deriving a parent from adjacency.

    Attributes:
        agent_id: The identity the observation gave the actor.
        kind: Whether the row is the main thread or a sub-agent of one.
        first_seen: Where this observation places the actor. One observation sets
            it and ``last_seen`` to the same instant; `record_agent` widens the
            stored pair as further observations of the actor arrive.
        last_seen: The other end of that interval.
        agent_type: The category of sub-agent, where a record named one.
        agent_source: Where the harness says the sub-agent definition came from.
        is_built_in: Whether the sub-agent is one of the harness's own, where known.
        is_async: Whether the sub-agent ran asynchronously, where known.
        workflow_run_id: The workflow run the actor belongs to, where it does.
        workflow_name: That run's name.
        parent_agent_id: The actor that spawned this one, where enrichment saw it.
    """

    agent_id: str
    kind: AgentKind
    first_seen: datetime
    last_seen: datetime
    agent_type: str | None = None
    agent_source: str | None = None
    is_built_in: bool | None = None
    is_async: bool | None = None
    workflow_run_id: str | None = None
    workflow_name: str | None = None
    parent_agent_id: str | None = None


#: What a step did to the entity it touched, in the vocabulary `step_touches.mode`
#: holds (FR-021): the two are kept apart because a step that only read a file
#: says nothing about the file having changed.
TouchMode = Literal["read", "modified"]

#: How far a touch resolved, in the vocabulary `step_touches.resolution` holds
#: (FR-021). The honesty column of the edge: `file` is what a touch says when no
#: symbol could be established, rather than naming one it did not find.
TouchResolution = Literal["symbol", "file"]


@dataclass(frozen=True, slots=True)
class StepTouch:
    """One `step_touches` row: what one recorded step did to one code entity.

    Attributes:
        step_id: The recorded step that did the touching, as the store assigned
            it; a touch is written after the step it hangs off, never with it,
            because the symbol is resolved off the hot path (FR-019).
        entity_key: The `code_entities` key touched, as
            `semantic.entity_key` builds it — a file's path, or the symbol
            inside it after a ``#``.
        mode: Whether the step read the entity or modified it.
        resolution: How far the touch resolved. Defaults to `file`, the honest
            answer at record time (R16): matching an edit's position against a
            symbol's line range is derivation-time work, done later.
    """

    step_id: int
    entity_key: str
    mode: TouchMode
    resolution: TouchResolution = "file"


#: How a `consumed` edge was established, in the vocabulary `step_consumes.link`
#: holds (FR-003). The honesty column of that edge: no telemetry attribute joins a
#: tool result to the model call that asked for it (R8), so `adjacent` is what the
#: derivation writes, and `observed` waits for a harness that emits a join key.
ConsumesLink = Literal["observed", "adjacent"]


@dataclass(frozen=True, slots=True)
class StepConsumes:
    """One `step_consumes` row: which model call one recorded step consumed.

    Attributes:
        step_id: The recorded step that consumed the call, as the store assigned
            it; the edge is written after the step it hangs off, never with it,
            because the call it consumed is found by adjacency (R8).
        inference_id: The `inferences` row consumed, as `_inference_id` built it.
        link: How the edge was established. Carries no default, so a derived edge
            cannot reach the row reading as an observed one (FR-003).
    """

    step_id: int
    inference_id: str
    link: ConsumesLink


@runtime_checkable
class EpisodicStore(Protocol):
    """Everything the pipeline asks of persistence, and nothing about a backend.

    A structural protocol, like the two harness seams: a replacement backend
    conforms by answering these questions, not by inheriting (FR-056).
    """

    def close_sequence(self, key: SequenceKey, at: datetime) -> None:
        """Mark the sequence *key* names as having ended at *at*."""
        ...

    def derive_outcome(self, key: SequenceKey, outcome: str) -> None:
        """Record the rules' verdict *outcome* on *key* (FR-034, FR-036)."""
        ...

    def record_head(self, key: SequenceKey, record: TelemetryRecord) -> None:
        """Record the commit and branch *record* observed *key*'s turn on (FR-023)."""
        ...

    def record(self, step: EpisodicStep) -> bool:
        """Write *step*, reporting whether it landed.

        ``False`` when the dedup key already existed; never raises on one. When
        the existing row came from the other capture path, it is folded with
        *step* into the union of both readings rather than left alone (FR-007).
        """
        ...

    def sequence(self, key: SequenceKey) -> Sequence | None:
        """The sequence *key* names, or ``None`` when nothing opened it."""
        ...

    def sequence_for_prompt(self, prompt_id: str) -> Sequence | None:
        """The turn *prompt_id* names, or ``None`` when nothing opened it."""
        ...

    def latest_sequence(self) -> Sequence | None:
        """The most recently opened turn that has not yet closed."""
        ...

    def latest_sequence_for_project(self, project_key: str) -> Sequence | None:
        """The most recently opened, not-yet-closed turn *project_key* names."""
        ...

    def declare_outcome(self, key: SequenceKey, outcome: str) -> None:
        """Record *outcome* on *key* beside its derived verdict (FR-035)."""
        ...

    def steps(self, key: SequenceKey) -> tuple[EpisodicStep, ...]:
        """Every step of *key*, in the order it was carried out."""
        ...

    def iter_steps(self, since: int = 0) -> Iterator[EpisodicStep]:
        """Every step recorded after ``step_id`` *since*, oldest first."""
        ...

    def sequences_before(self, cutoff: datetime, project_key: str) -> tuple[SequenceKey, ...]:
        """Every turn *project_key* names that began before *cutoff*, oldest first."""
        ...

    def delete_sequences(self, keys: Iterable[SequenceKey]) -> None:
        """Delete the turns *keys* names with every step of them.

        The only deletion this seam offers, and nothing calls it but `prune`
        (FR-057): history is kept indefinitely unless an operator says otherwise.
        """
        ...

    def record_inference(self, inference: Inference) -> None:
        """Write *inference* as the model call it is, and count it (FR-002)."""
        ...

    def inferences_for(self, key: SequenceKey) -> tuple[Inference, ...]:
        """Every model call recorded under *key*, in the order they were written."""
        ...

    def record_agent(self, agent: Agent) -> None:
        """Write *agent*, folding a repeated observation of it into the one row (FR-020)."""
        ...

    def agent(self, agent_id: str) -> Agent | None:
        """The actor *agent_id* names, or ``None`` when nothing observed it."""
        ...

    def record_touch(self, touch: StepTouch) -> None:
        """Write *touch* against the step it names, and count how far it resolved (FR-021)."""
        ...

    def touches_for(self, step_id: int) -> tuple[StepTouch, ...]:
        """Every entity the step *step_id* names touched, oldest touch first."""
        ...

    def derive_consumes(self, step: EpisodicStep) -> StepConsumes | None:
        """Write the `consumed` edge *step* earns by adjacency, or count it unlinked (R8)."""
        ...

    def consumes_for(self, step_id: int) -> tuple[StepConsumes, ...]:
        """Every model call the step *step_id* names consumed, oldest edge first."""
        ...

    def write_annotation(self, project_key: str, annotation: Annotation) -> None:
        """Store *annotation* against *project_key*, in the annotations table of its own (FR-038)."""
        ...

    def annotations_for(self, project_key: str | None) -> tuple[Annotation, ...]:
        """Every annotation *project_key* names, or every one stored when it is ``None``.

        ``None`` is what the cross-project snapshot reattaches against: it
        folds every project's rows into one graph, so its reattachment needs
        every annotation regardless of which project wrote it.
        """
        ...

    def bump(self, counter: str) -> None:
        """Add one to *counter* (R16).

        Never raises: a store that cannot keep the count says so elsewhere.
        """
        ...

    def counters(self) -> Mapping[str, int]:
        """Every counter this store has kept, against its value."""
        ...


def counter_table(store: EpisodicStore) -> dict[str, int]:
    """Every counter the package can increment, against what *store* kept.

    Counters the store holds but :data:`COUNTERS` does not name are reported
    too: a count this build cannot explain is still a count, and hiding it is
    the one thing Principle V forbids. Shared by `processrecall show counters`
    and the `inspect` tool, the two readers of Principle V's counter half.
    """
    kept = store.counters()
    return {name: kept.get(name, 0) for name in sorted({*COUNTERS, *kept})}


#: The step columns, in the order `_step_from_row` reads them back.
_STEP_COLUMNS = (
    "dedup_key, conversation_id, session_epoch, prompt_id, agent_id, position, node_key,"
    " activity_class, template, occurred_at, program, files, result_snippet, outcome,"
    " record_ref, rationale_label, symbol_ref, step_id, result, kind, decision,"
    " decision_source, duration_ms, error_type, input_size_bytes, result_size_bytes,"
    " tool_source, source"
)

#: `_STEP_COLUMNS` minus `step_id`, in the same order: the columns `record` writes.
#: `step_id` is sqlite's own rowid and is never inserted, so deriving from the one
#: list `_step_from_row` already reads keeps the columns, the placeholders and the
#: values tuple from drifting apart as a fourth place to edit.
_STEP_WRITE_COLUMNS = tuple(
    name for name in (column.strip() for column in _STEP_COLUMNS.split(",")) if name != "step_id"
)

#: The predicate every `SequenceKey` lookup shares — a fifth key field would
#: otherwise mean editing this in four places.
#:
#: This, `_STEP_COLUMNS` and `_STEP_WRITE_COLUMNS` are the only things any
#: statement in this module interpolates, which is why each of their call
#: sites carries `# nosec B608`: all are module constants of column names and
#: `?` placeholders, fixed at import and reachable by no caller. Every value a
#: caller supplies is bound through the parameter tuple, never formatted into
#: the text.
_SEQUENCE_WHERE = (
    " WHERE conversation_id = ? AND session_epoch = ? AND prompt_id = ? AND agent_id = ?"
)


def _key_params(key: SequenceKey) -> tuple[str, int, str, str]:
    """The bind parameters for `_SEQUENCE_WHERE`, in its column order."""
    return (key.conversation_id, key.session_epoch, key.prompt_id, key.agent_id)


def _source_counter(source: CaptureSource | None) -> str | None:
    """The counter a row written from *source* bumps, or ``None`` when it did not say.

    ``BOTH`` counts as telemetry: the row was written from a telemetry record and
    the hook only agreed with it, which keeps `steps_from_hook` the count of
    steps telemetry never saw (`contracts/counters.md`).
    """
    if source is None:
        return None
    return "steps_from_hook" if source is CaptureSource.HOOK else "steps_from_telemetry"


#: The step fields a merge leaves to the store rather than to either capture:
#: the identity the row was found by, sqlite's own rowid, and the bookkeeping
#: value that says which paths saw it.
_UNMERGED_FIELDS = frozenset({"dedup_key", "step_id", "source"})

#: Everything two captures of one tool call are reconciled over, in the order
#: `EpisodicStep` declares it, so a new field joins the merge by being declared.
#: Restricted to the fields R14 declares nullable — a ``None`` default is what
#: "the record did not carry it" means on this dataclass — so identity and
#: ordering values (`position`, `occurred_at`, `node_key`, `template`,
#: `sequence_key`) and the defaulted non-Optional fields (`program`, `files`,
#: `result_snippet`, `outcome`, `record_ref`) are never merged and never
#: counted as a clash: each path derives them on its own and a fresh default
#: is not a reading either source carried.
_MERGED_FIELDS = tuple(
    field.name
    for field in fields(EpisodicStep)
    if field.name not in _UNMERGED_FIELDS and field.default is None
)

#: The pair of sources a merge resolves: one capture from each path. A replay of
#: the same path is a plain duplicate and carries nothing new (FR-007).
_CROSS_SOURCE = frozenset({CaptureSource.TELEMETRY, CaptureSource.HOOK})


@dataclass(frozen=True, slots=True)
class _MergedStep:
    """The one step two captures of a tool call leave behind, and their quarrel.

    ``disagreement_count`` is what `telemetry_hook_disagreement` counts: one per
    field both sources carried with different values, so "telemetry wins" never
    quietly means "the hook's reading was hidden" (R20).
    """

    step: EpisodicStep
    disagreement_count: int


def _merge_captures(*, telemetry: EpisodicStep, hook: EpisodicStep) -> _MergedStep:
    """*telemetry* and *hook* as one step, telemetry winning every clash (FR-007).

    Telemetry is the primary source, so the hook fills only the fields telemetry
    left as ``None`` — which is exactly what "the record did not carry it" means
    on an `EpisodicStep` (R14).
    """
    disagreements = 0
    for name in _MERGED_FIELDS:
        primary, fallback = getattr(telemetry, name), getattr(hook, name)
        if primary is not None and fallback is not None and primary != fallback:
            disagreements += 1
    filled = _gaps_filled_from(telemetry, hook)
    return _MergedStep(replace(telemetry, source=CaptureSource.BOTH, **filled), disagreements)


def _gaps_filled_from(primary: EpisodicStep, fallback: EpisodicStep) -> dict[str, Any]:
    """The nullable fields *primary* did not carry, as *fallback* read them (R14)."""
    return {
        name: getattr(fallback, name) for name in _MERGED_FIELDS if getattr(primary, name) is None
    }


def _is_bare_verdict(step: EpisodicStep) -> bool:
    """Whether *step* is a permission verdict with no run of its own behind it.

    `claude_code.tool_decision` carries the decision axis and nothing the call
    did: the result axis (`result`) arrives on `tool_result` alone and has no
    hook fallback (`contracts/telemetry-records.md`), so a verdict still
    waiting for its result is a step that says how the call was decided and not
    how it went.
    """
    return step.decision is not None and step.result is None


def _collapsed_verdict(stored: EpisodicStep, arriving: EpisodicStep) -> EpisodicStep | None:
    """The one step an accept verdict and the result after it make, or ``None`` (FR-008).

    An accepted decision and the `tool_result` that follows it are two records of
    a single action under one tool-use id, and the store keeps one step for it.
    The result's row is the one kept, because the touched edges ride on it and a
    step collapsed onto the verdict's row would keep the action and lose every
    file it touched; the verdict fills only the fields the result left as
    ``None`` (R14), the decision axis among them.

    ``None`` when neither step is a bare verdict, when both are, or when the
    verdict rejected the call: FR-008 forbids touched edges on a refused step,
    so a reject verdict colliding with a result row is left a plain duplicate
    rather than collapsed.
    """
    if _is_bare_verdict(stored) == _is_bare_verdict(arriving):
        return None
    verdict, ran = (stored, arriving) if _is_bare_verdict(stored) else (arriving, stored)
    if verdict.decision is not StepDecision.ACCEPTED:
        return None
    return replace(ran, **_gaps_filled_from(ran, verdict))


def _reconciled(stored: EpisodicStep, arriving: EpisodicStep) -> _MergedStep | None:
    """The one step *stored* and *arriving* resolve to, and how they disagreed.

    A re-seen dedup key means one of two things: an accept verdict and its
    result under one tool-use id (FR-008), tried first regardless of which
    capture path each side came from — a verdict and result split across
    telemetry and the hook are still one action, not a corroboration — or,
    failing that, two different capture paths reporting the same call
    (FR-007). ``None`` when neither applies: a plain replay carries nothing new.
    """
    if (collapsed := _collapsed_verdict(stored, arriving)) is not None:
        return _MergedStep(collapsed, disagreement_count=0)
    if {stored.source, arriving.source} != _CROSS_SOURCE:
        return None
    from_hook = arriving.source is CaptureSource.HOOK
    return _merge_captures(
        telemetry=stored if from_hook else arriving,
        hook=arriving if from_hook else stored,
    )


def _step_params(step: EpisodicStep) -> tuple[Any, ...]:
    """*step*'s values in `_STEP_WRITE_COLUMNS` order, for a write to bind."""
    return (
        step.dedup_key,
        step.sequence_key.conversation_id,
        step.sequence_key.session_epoch,
        step.sequence_key.prompt_id,
        step.sequence_key.agent_id,
        step.position,
        step.node_key,
        step.activity_class,
        step.template,
        step.occurred_at.isoformat(),
        step.program,
        json.dumps(list(step.files)),
        step.result_snippet[:RESULT_CEILING],
        step.outcome,
        step.record_ref,
        step.rationale_label,
        step.symbol_ref,
        step.result,
        step.kind,
        step.decision,
        step.decision_source,
        step.duration_ms,
        step.error_type,
        step.input_size_bytes,
        step.result_size_bytes,
        step.tool_source,
        step.source,
    )


def _step_from_row(row: tuple[Any, ...]) -> EpisodicStep:
    """Rebuild a step from one `_STEP_COLUMNS` row."""
    return EpisodicStep(
        dedup_key=str(row[0]),
        sequence_key=SequenceKey(str(row[1]), int(row[2]), str(row[3]), str(row[4])),
        position=int(row[5]),
        node_key=str(row[6]),
        activity_class=ActivityClass(row[7]),
        template=str(row[8]),
        occurred_at=datetime.fromisoformat(str(row[9])),
        program=str(row[10]),
        files=tuple(json.loads(str(row[11]))),
        result_snippet=str(row[12]),
        outcome=str(row[13]),
        record_ref=str(row[14]),
        rationale_label=None if row[15] is None else str(row[15]),
        symbol_ref=None if row[16] is None else str(row[16]),
        step_id=int(row[17]),
        result=None if row[18] is None else StepResult(row[18]),
        kind=None if row[19] is None else str(row[19]),
        decision=None if row[20] is None else StepDecision(row[20]),
        decision_source=None if row[21] is None else DecisionSource(row[21]),
        duration_ms=None if row[22] is None else int(row[22]),
        error_type=None if row[23] is None else str(row[23]),
        input_size_bytes=None if row[24] is None else int(row[24]),
        result_size_bytes=None if row[25] is None else int(row[25]),
        tool_source=None if row[26] is None else str(row[26]),
        source=None if row[27] is None else CaptureSource(row[27]),
    )


#: Every `inferences` column a write fills, in `_inference_params` order.
#: `recorded_at` is bound after them, from the store's own clock rather than from
#: the record.
_INFERENCE_WRITE_COLUMNS = (
    "inference_id",
    "sequence_key",
    "outcome",
    "occurred_at",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "cost_micros",
    "duration_ms",
    "speed",
    "effort",
    "query_source",
    "status_code",
    "attempt",
    "stop_reason",
)

#: What a read selects, in `_inference_from_row` order: the written columns minus
#: the sequence the reader asked by and so already holds.
_INFERENCE_READ_COLUMNS = tuple(
    column for column in _INFERENCE_WRITE_COLUMNS if column != "sequence_key"
)

#: The three model-call records, unpacked out of `CONSUMED_EVENT_NAMES` the same
#: way `graph.schema` does: the names are `trajectory.records`'s to declare, and
#: unpacking the whole tuple — rather than slicing — means a name inserted
#: ahead of `api_request` fails loudly here instead of silently re-binding the
#: three outcomes below.
(
    _USER_PROMPT,
    _API_REQUEST,
    _API_ERROR,
    _API_REFUSAL,
    _TOOL_RESULT,
    _TOOL_DECISION,
    _SUBAGENT_COMPLETED,
) = CONSUMED_EVENT_NAMES

#: The `outcome` each of the three leaves on the row. Derived from which event
#: fired and from nothing else, which is what makes the field events-sourced:
#: `stop_reason` and `error_class` refine an outcome the row already carries (R9).
_OUTCOME_BY_RECORD: Mapping[str, str] = {
    _API_REQUEST: "ok",
    _API_ERROR: "error",
    _API_REFUSAL: "refusal",
}


def _sequence_text(key: SequenceKey) -> str:
    """*key* as the single text column `inferences.sequence_key` holds it in.

    The four parts joined rather than spread over columns of their own, because
    one text column is the shape the declaration gives the reference. Nothing
    reads it back apart: every reader of the table asks by a key it already has.
    """
    return "|".join((key.conversation_id, str(key.session_epoch), key.prompt_id, key.agent_id))


def _inference_id(record: TelemetryRecord) -> str:
    """The identity *record* gives the model call it reports.

    The harness's own request id where it issued one, `client_request_id` where
    only that is present, and otherwise a key derived from the record — the
    `syn-` prefix and the digest are `SequenceKey.dedup_key`'s, so a collector
    file read twice derives one id rather than two
    (`contracts/telemetry-records.md`).
    """
    for attribute in ("request_id", "client_request_id"):
        if issued := record.get(attribute):
            return str(issued)
    material = "|".join(
        str(record.get(attribute, ""))
        for attribute in (
            RECORD_TYPE_ATTRIBUTE,
            SESSION_ATTRIBUTE,
            PROMPT_ATTRIBUTE,
            TIMESTAMP_ATTRIBUTE,
            SEQUENCE_ATTRIBUTE,
        )
    )
    return f"syn-{sha256(material.encode()).hexdigest()[:24]}"


def inference_from_record(record: TelemetryRecord, key: SequenceKey) -> Inference:
    """The inference an `api_request`, `api_error` or `api_refusal` *record* is (FR-002).

    ``outcome`` is read off which of the three fired, so a stream carrying no
    spans still says how every call went (R9). A refusal is the one `stop_reason`
    those events observe and carries it; every other reading of that field is
    span enrichment and is left unset here.

    Raises:
        KeyError: *record* is not one of the three model-call records, or carries
            no `event.timestamp` to place it at.
        ValueError: `event.timestamp` will not parse as an instant. Both are the
            caller's to place, as they are for a step (`in_record_order`).
    """
    record_type = str(record[RECORD_TYPE_ATTRIBUTE])
    return Inference(
        inference_id=_inference_id(record),
        sequence_key=key,
        outcome=_OUTCOME_BY_RECORD[record_type],
        occurred_at=parse_instant(str(record[TIMESTAMP_ATTRIBUTE])),
        model=optional_text(record, "model"),
        input_tokens=optional_integer(record, "input_tokens"),
        output_tokens=optional_integer(record, "output_tokens"),
        cache_read_tokens=optional_integer(record, "cache_read_tokens"),
        cache_creation_tokens=optional_integer(record, "cache_creation_tokens"),
        cost_micros=optional_integer(record, "cost_usd_micros"),
        duration_ms=optional_integer(record, "duration_ms"),
        speed=optional_text(record, "speed"),
        effort=optional_text(record, "effort"),
        query_source=optional_text(record, "query_source"),
        status_code=optional_integer(record, "status_code"),
        attempt=optional_integer(record, "attempt"),
        stop_reason="refusal" if record_type == _API_REFUSAL else None,
    )


#: The two `query_source` values naming the turn's own main thread (R7); every
#: other value — including a sub-agent's own name, which identifies no instance
#: — reads as `subagent`, the one kind events-only mode can still tell apart.
_MAIN_THREAD_QUERY_SOURCES = frozenset({"repl_main_thread", "compact"})


def _kind_from_query_source(query_source: str | None) -> AgentKind:
    """The agent kind *query_source* names, the degraded signal R7 leaves for it."""
    return "main" if query_source in _MAIN_THREAD_QUERY_SOURCES else "subagent"


def agent_from_record(record: TelemetryRecord, key: SequenceKey) -> Agent:
    """The actor a `subagent_completed`, `api_request`, `api_error` or `api_refusal` *record* names (FR-020, R7).

    Only `subagent_completed` names an `agent_type`, `agent_source`, `is_built_in`
    or `is_async` — `contracts/telemetry-records.md` gives it no `query_source`, so
    a row built from it is a sub-agent's by definition. The other three carry no
    agent fields at all, only the `query_source` that separates the turn's own
    main thread from sub-agent work without naming an instance (R7); `kind` is all
    a row built from one of them can hold.

    Neither shape carries `agent_id` — it is a span attribute only (R7) — so the
    row's identity is *key*'s: the same `SequenceKey.agent_id` the turn's steps and
    inferences are already filed under.

    `workflow_run_id` and `workflow_name` are left unset here: `contracts/telemetry-records.md`
    names no attribute of either shape that carries them.

    Raises:
        KeyError: *record* is none of the four record types an agent is read
            from, or carries no `event.timestamp` to place it at.
        ValueError: `event.timestamp` will not parse as an instant. Both are the
            caller's to place, as they are for a step (`in_record_order`).
    """
    record_type = str(record[RECORD_TYPE_ATTRIBUTE])
    occurred_at = parse_instant(str(record[TIMESTAMP_ATTRIBUTE]))
    if record_type == _SUBAGENT_COMPLETED:
        return Agent(
            agent_id=key.agent_id,
            kind="subagent",
            first_seen=occurred_at,
            last_seen=occurred_at,
            agent_type=optional_text(record, "agent_type"),
            agent_source=optional_text(record, "agent.source"),
            is_built_in=optional_bool(record, "is_built_in"),
            is_async=optional_bool(record, "is_async"),
        )
    if record_type not in _OUTCOME_BY_RECORD:
        raise KeyError(record_type)
    return Agent(
        agent_id=key.agent_id,
        kind=_kind_from_query_source(optional_text(record, "query_source")),
        first_seen=occurred_at,
        last_seen=occurred_at,
    )


def _inference_params(inference: Inference) -> tuple[Any, ...]:
    """*inference*'s values in `_INFERENCE_WRITE_COLUMNS` order, for a write to bind."""
    return (
        inference.inference_id,
        _sequence_text(inference.sequence_key),
        inference.outcome,
        inference.occurred_at.isoformat(),
        inference.model,
        inference.input_tokens,
        inference.output_tokens,
        inference.cache_read_tokens,
        inference.cache_creation_tokens,
        inference.cost_micros,
        inference.duration_ms,
        inference.speed,
        inference.effort,
        inference.query_source,
        inference.status_code,
        inference.attempt,
        inference.stop_reason,
    )


def _inference_from_row(row: tuple[Any, ...], key: SequenceKey) -> Inference:
    """Rebuild the inference one `_INFERENCE_READ_COLUMNS` row of *key* holds."""
    return Inference(
        inference_id=str(row[0]),
        sequence_key=key,
        outcome=str(row[1]),
        occurred_at=datetime.fromisoformat(str(row[2])),
        model=None if row[3] is None else str(row[3]),
        input_tokens=None if row[4] is None else int(row[4]),
        output_tokens=None if row[5] is None else int(row[5]),
        cache_read_tokens=None if row[6] is None else int(row[6]),
        cache_creation_tokens=None if row[7] is None else int(row[7]),
        cost_micros=None if row[8] is None else int(row[8]),
        duration_ms=None if row[9] is None else int(row[9]),
        speed=None if row[10] is None else str(row[10]),
        effort=None if row[11] is None else str(row[11]),
        query_source=None if row[12] is None else str(row[12]),
        status_code=None if row[13] is None else int(row[13]),
        attempt=None if row[14] is None else int(row[14]),
        stop_reason=None if row[15] is None else str(row[15]),
    )


#: Every `agents` column a write fills, in `_agent_params` order.
_AGENT_WRITE_COLUMNS = (
    "agent_id",
    "kind",
    "agent_type",
    "agent_source",
    "is_built_in",
    "is_async",
    "workflow_run_id",
    "workflow_name",
    "parent_agent_id",
    "first_seen",
    "last_seen",
)

#: What a read selects, in `_agent_from_row` order: the written columns minus the
#: identity the reader asked by and so already holds.
_AGENT_READ_COLUMNS = tuple(column for column in _AGENT_WRITE_COLUMNS if column != "agent_id")

#: How a second observation of one actor folds into the row the first left.
#: `COALESCE` in this direction keeps the two sources independent: the
#: `subagent_completed` record names no parent, and arriving after enrichment it
#: must not erase the one enrichment saw. The interval widens rather than moves,
#: because records reach the store in neither the order they happened nor a
#: stable one (R5).
_AGENT_MERGE = (
    "kind = excluded.kind,"
    " agent_type = COALESCE(excluded.agent_type, agent_type),"
    " agent_source = COALESCE(excluded.agent_source, agent_source),"
    " is_built_in = COALESCE(excluded.is_built_in, is_built_in),"
    " is_async = COALESCE(excluded.is_async, is_async),"
    " workflow_run_id = COALESCE(excluded.workflow_run_id, workflow_run_id),"
    " workflow_name = COALESCE(excluded.workflow_name, workflow_name),"
    " parent_agent_id = COALESCE(excluded.parent_agent_id, parent_agent_id),"
    " first_seen = MIN(first_seen, excluded.first_seen),"
    " last_seen = MAX(last_seen, excluded.last_seen)"
)


def _agent_params(agent: Agent) -> tuple[Any, ...]:
    """*agent*'s values in `_AGENT_WRITE_COLUMNS` order, for a write to bind."""
    return (
        agent.agent_id,
        agent.kind,
        agent.agent_type,
        agent.agent_source,
        agent.is_built_in,
        agent.is_async,
        agent.workflow_run_id,
        agent.workflow_name,
        agent.parent_agent_id,
        agent.first_seen.isoformat(),
        agent.last_seen.isoformat(),
    )


def _agent_from_row(row: tuple[Any, ...], agent_id: str) -> Agent:
    """Rebuild the actor one `_AGENT_READ_COLUMNS` row of *agent_id* holds.

    The stored `kind` is trusted as the vocabulary it was written in, for the
    reason `touches_for` trusts a mode: `record_agent` is its only writer, and it
    takes an `Agent` whose field is an `AgentKind` already.
    """
    return Agent(
        agent_id=agent_id,
        kind=cast("AgentKind", row[0]),
        first_seen=datetime.fromisoformat(str(row[8])),
        last_seen=datetime.fromisoformat(str(row[9])),
        agent_type=None if row[1] is None else str(row[1]),
        agent_source=None if row[2] is None else str(row[2]),
        is_built_in=None if row[3] is None else bool(row[3]),
        is_async=None if row[4] is None else bool(row[4]),
        workflow_run_id=None if row[5] is None else str(row[5]),
        workflow_name=None if row[6] is None else str(row[6]),
        parent_agent_id=None if row[7] is None else str(row[7]),
    )


def _preceding_inference(
    inferences: Iterable[Inference], occurred_at: datetime
) -> Inference | None:
    """The latest of *inferences* placed at or before *occurred_at*, or ``None``.

    At or before rather than strictly before: a call and the result it produced
    are routinely stamped at the same instant by a coarse clock. Compared as
    instants rather than as the text the column holds, for the reason
    `inferences_for` does not order by it: two processes reporting one session
    need not agree on a UTC offset (R5).

    Ties on that instant break on *inferences*' own order — `inferences_for`'s
    rowid order stands in for `event.sequence` (`data-model.md`) — so the one
    written last, not the one that happens to sort first, wins.
    """
    before = [
        (position, inference)
        for position, inference in enumerate(inferences)
        if inference.occurred_at <= occurred_at
    ]
    if not before:
        return None
    return max(before, key=lambda pair: (pair[1].occurred_at, pair[0]))[1]


class SQLiteEpisodicStore:
    """The shipped store: one short transaction per write, over `open_index`.

    Takes an open connection rather than a path so the caller decides the store's
    lifetime — a hook opens one per invocation (R10), a test opens one per
    temporary directory.
    """

    def __init__(self, connection: sqlite3.Connection, log_path: Path | None = None) -> None:
        self._connection = connection
        self._log_path = log_path if log_path is not None else home_dir() / FALLBACK_LOG

    def __repr__(self) -> str:
        return f"{type(self).__name__}(connection={self._connection!r})"

    def open_sequence(self, sequence: Sequence) -> None:
        """Record *sequence* as open, or leave the one already there alone.

        Idempotent because the first step of a turn is what opens it and a turn
        has many steps: re-opening must not reset a sequence's start.
        """
        with self._connection:
            self._connection.execute(
                "INSERT INTO sequences (conversation_id, session_epoch, prompt_id, agent_id,"
                " project_dir_key, process_type, status, derived_outcome, declared_outcome,"
                " started_at, prompt_length, command_name, command_source, workflow_run_id,"
                " workflow_name, app_version, head_revision, head_branch, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (
                    sequence.key.conversation_id,
                    sequence.key.session_epoch,
                    sequence.key.prompt_id,
                    sequence.key.agent_id,
                    sequence.project_dir_key,
                    sequence.process_type,
                    sequence.status,
                    sequence.derived_outcome,
                    sequence.declared_outcome,
                    sequence.started_at.isoformat(),
                    sequence.prompt_length,
                    sequence.command_name,
                    sequence.command_source,
                    sequence.workflow_run_id,
                    sequence.workflow_name,
                    sequence.app_version,
                    sequence.head_revision,
                    sequence.head_branch,
                    sequence.source,
                ),
            )

    def close_sequence(self, key: SequenceKey, at: datetime) -> None:
        """Mark the sequence *key* names as having ended at *at*.

        Only a turn the harness saw end is closed; marking one abandoned
        mid-flight ``incomplete`` instead belongs to whatever opens the next
        sequence (T026), since that is where "abandoned" is detected.
        """
        with self._connection:
            self._connection.execute(
                f"UPDATE sequences SET status = 'closed', ended_at = ?{_SEQUENCE_WHERE}",  # nosec B608
                (at.isoformat(), *_key_params(key)),
            )

    def derive_outcome(self, key: SequenceKey, outcome: str) -> None:
        """Write the rules' verdict *outcome* on *key*, leaving the declared one alone.

        The mirror of `declare_outcome`: the two verdicts live in their own
        columns so that a rule change re-derives one without touching what an
        agent said about the turn (FR-036).
        """
        with self._connection:
            self._connection.execute(
                f"UPDATE sequences SET derived_outcome = ?{_SEQUENCE_WHERE}",  # nosec B608
                (outcome, *_key_params(key)),
            )

    def record_head(self, key: SequenceKey, record: TelemetryRecord) -> None:
        """Record on *key* the commit and branch *record* observed the turn on (FR-023).

        Each of `vcs.ref.head.revision` and `vcs.ref.head.name` is written only
        where the record carries it, so an observation naming no branch — the
        harness once the branch has been deleted, or one below the pair's
        version floor — leaves the name the turn ran on standing rather than
        writing the absence over it (R14). Neither value is resolved against the
        repository again: the row says where the work happened, not what still
        exists.
        """
        with self._connection:
            self._connection.execute(
                "UPDATE sequences SET head_revision = coalesce(?, head_revision),"
                f" head_branch = coalesce(?, head_branch){_SEQUENCE_WHERE}",  # nosec B608
                (
                    optional_text(record, "vcs.ref.head.revision"),
                    optional_text(record, "vcs.ref.head.name"),
                    *_key_params(key),
                ),
            )

    def record(self, step: EpisodicStep) -> bool:
        """Write *step*, reporting whether it landed.

        ``False`` means the dedup key was already there (FR-008). A replayed
        harness payload is an expected, countable event rather than an error, so
        the conflict is resolved by the database and reported as a value — and
        counted as ``steps_duplicate`` (R3), because a duplicate nobody counted
        reads exactly like an action that was never sent. When the stored row
        came from the other capture path, it is not left alone: *step* is folded
        into it, telemetry winning any disagreement (FR-007).

        The result snippet is cut to :data:`RESULT_CEILING` on the way in
        (FR-010): the ceiling is a property of what is stored, not of the
        adapter that happened to produce the step.

        ``recorded_at`` is stamped from the store's own clock, beside
        ``occurred_at``: the pair is what lets a rebuild reconstruct what the
        graph believed at a past time (FR-022, FR-044).
        """
        with self._connection:
            cursor = self._connection.execute(
                f"INSERT INTO steps ({', '.join(_STEP_WRITE_COLUMNS)}, recorded_at)"
                f" VALUES ({', '.join('?' * len(_STEP_WRITE_COLUMNS))}, ?)"
                " ON CONFLICT (dedup_key) DO NOTHING",  # nosec B608
                (*_step_params(step), datetime.now(UTC).isoformat()),
            )
        if cursor.rowcount == 1:
            self.bump("steps_recorded")
            if counter := _source_counter(step.source):
                self.bump(counter)
            return True
        self.bump("steps_duplicate")
        self._merge_with_stored(step)
        return False

    def _merge_with_stored(self, arriving: EpisodicStep) -> None:
        """Fold *arriving* into the step already under its dedup key (FR-007, FR-008).

        Only once the duplicate itself is counted: the merge is how one tool
        call seen twice stays one step carrying the union of both readings, in
        either arrival order (SC-004). Which reconciliation applies —
        `_reconciled`'s question, not this method's. The read and the rewrite
        share one transaction so a concurrent merge of the same key cannot land
        between them.
        """
        with self._connection:
            stored = self._step_by_key(arriving.dedup_key)
            if stored is None or (merged := _reconciled(stored, arriving)) is None:
                return
            self._rewrite_step(merged.step)
        for _ in range(merged.disagreement_count):
            self.bump("telemetry_hook_disagreement")
        if stored.source is CaptureSource.HOOK and merged.step.source is CaptureSource.BOTH:
            # The initial insert counted this row under steps_from_hook before
            # telemetry had seen it; now that it has, the count moves with it
            # (contracts/counters.md), so both arrival orders end alike.
            self._correct("steps_from_hook")
            self.bump("steps_from_telemetry")

    def _step_by_key(self, dedup_key: str) -> EpisodicStep | None:
        """The step stored under *dedup_key*, or ``None`` when none is."""
        row = self._connection.execute(
            f"SELECT {_STEP_COLUMNS} FROM steps WHERE dedup_key = ?",  # nosec B608
            (dedup_key,),
        ).fetchone()
        return None if row is None else _step_from_row(row)

    def _rewrite_step(self, step: EpisodicStep) -> None:
        """Overwrite the row under *step*'s dedup key with *step*.

        Runs inside the caller's transaction (`_merge_with_stored`), not its
        own: the select that finds the row to overwrite must not be split from
        this write. ``recorded_at`` is left as first written — FR-044 keeps it
        beside ``occurred_at`` so a rebuild can reconstruct what the graph
        believed at a past time, and a merge revising a row's fields is not the
        graph coming to believe it for the first time.

        ``step_id`` does not move either, so `derive.py`'s incremental fold —
        which reads `iter_steps(since=episode_high_water)` by that id — will not
        revisit a row a merge later revised; only a full rebuild will. Left as a
        follow-up rather than fixed here, since T019 asked only for the merge.
        """
        assignments = ", ".join(f"{column} = ?" for column in _STEP_WRITE_COLUMNS)
        self._connection.execute(
            f"UPDATE steps SET {assignments} WHERE dedup_key = ?",  # nosec B608
            (*_step_params(step), step.dedup_key),
        )

    def sequence(self, key: SequenceKey) -> Sequence | None:
        """The sequence *key* names, or ``None`` when no turn opened it.

        ``None`` rather than an empty sequence: a turn nothing ever opened and a
        turn opened with no steps on it are different facts, and a caller that
        cannot tell them apart is the silent failure Principle V forbids.
        """
        row = self._connection.execute(
            "SELECT project_dir_key, process_type, status, started_at, ended_at,"
            " derived_outcome, declared_outcome, prompt_length, command_name,"
            " command_source, workflow_run_id, workflow_name, app_version, head_revision,"
            " head_branch, source,"
            " (SELECT count(*) FROM steps WHERE steps.conversation_id = sequences.conversation_id"
            "  AND steps.session_epoch = sequences.session_epoch"
            "  AND steps.prompt_id = sequences.prompt_id"
            "  AND steps.agent_id = sequences.agent_id) FROM sequences"
            f"{_SEQUENCE_WHERE}",  # nosec B608
            _key_params(key),
        ).fetchone()
        if row is None:
            return None
        return Sequence(
            key=key,
            project_dir_key=str(row[0]),
            started_at=datetime.fromisoformat(str(row[3])),
            process_type=ProcessType(row[1]),
            status=str(row[2]),
            ended_at=None if row[4] is None else datetime.fromisoformat(str(row[4])),
            derived_outcome=str(row[5]),
            declared_outcome=None if row[6] is None else str(row[6]),
            prompt_length=None if row[7] is None else int(row[7]),
            command_name=None if row[8] is None else str(row[8]),
            command_source=None if row[9] is None else str(row[9]),
            workflow_run_id=None if row[10] is None else str(row[10]),
            workflow_name=None if row[11] is None else str(row[11]),
            app_version=None if row[12] is None else str(row[12]),
            head_revision=None if row[13] is None else str(row[13]),
            head_branch=None if row[14] is None else str(row[14]),
            source=None if row[15] is None else CaptureSource(row[15]),
            step_count=int(row[16]),
        )

    def sequence_for_prompt(self, prompt_id: str) -> Sequence | None:
        """The turn *prompt_id* names, or ``None`` when nothing opened it.

        A prompt id is not a key: the same harness prompt id can appear in two
        conversations, and a caller that has only the id — `mark_outcome` is the
        one — cannot spell the other three fields. The most recently started
        match is the turn it means, and ``None`` says no such turn was ever
        opened rather than opening one.
        """
        row = self._connection.execute(
            "SELECT conversation_id, session_epoch, prompt_id, agent_id FROM sequences"
            " WHERE prompt_id = ? ORDER BY started_at DESC LIMIT 1",
            (prompt_id,),
        ).fetchone()
        if row is None:
            return None
        return self.sequence(SequenceKey(str(row[0]), int(row[1]), str(row[2]), str(row[3])))

    def latest_sequence(self) -> Sequence | None:
        """The most recently opened turn that has not yet closed.

        This store is home-wide (FR-052's one root): it holds every
        conversation and project the harness has ever seen, and a caller with
        no ``prompt_id`` — `mark_outcome` is the one — has no conversation
        context of its own to narrow it with. "Running now" therefore means
        the most recently started sequence that is not `CLOSED`; a finished
        turn started after it would otherwise outrank the one still open.
        """
        row = self._connection.execute(
            "SELECT conversation_id, session_epoch, prompt_id, agent_id FROM sequences"
            " WHERE status != ? ORDER BY started_at DESC LIMIT 1",
            (CLOSED,),
        ).fetchone()
        if row is None:
            return None
        return self.sequence(SequenceKey(str(row[0]), int(row[1]), str(row[2]), str(row[3])))

    def latest_sequence_for_project(self, project_key: str) -> Sequence | None:
        """The most recently opened, not-yet-closed turn *project_key* names.

        `latest_sequence`'s own query, narrowed to one project: a caller that
        does know which project it is answering for — `recall` is the one —
        must not read a position off a turn some other project left open.
        """
        row = self._connection.execute(
            "SELECT conversation_id, session_epoch, prompt_id, agent_id FROM sequences"
            " WHERE status != ? AND project_dir_key = ? ORDER BY started_at DESC LIMIT 1",
            (CLOSED, project_key),
        ).fetchone()
        if row is None:
            return None
        return self.sequence(SequenceKey(str(row[0]), int(row[1]), str(row[2]), str(row[3])))

    def declare_outcome(self, key: SequenceKey, outcome: str) -> None:
        """Record *outcome* on *key* beside its derived verdict (FR-035).

        An update and never an insert: the declared verdict is a column on a
        turn that happened, so a key nothing opened stays unwritten instead of
        conjuring a turn. ``derived_outcome`` is untouched, because FR-036 keeps
        the rules' verdict recomputable.
        """
        with self._connection:
            self._connection.execute(
                f"UPDATE sequences SET declared_outcome = ?{_SEQUENCE_WHERE}",  # nosec B608
                (outcome, *_key_params(key)),
            )

    def steps(self, key: SequenceKey) -> tuple[EpisodicStep, ...]:
        """Every step of *key*, in the order it was carried out."""
        rows = self._connection.execute(
            f"SELECT {_STEP_COLUMNS} FROM steps{_SEQUENCE_WHERE} ORDER BY position",  # nosec B608
            _key_params(key),
        )
        return tuple(_step_from_row(row) for row in rows)

    def bump(self, counter: str) -> None:
        """Add one to *counter*, creating it at one if it is new (R16).

        A hook process has no return value to carry a result, so a counter is
        the only way a failed lookup can be told apart from an empty one — which
        is what Principle V asks of every path here. When the store itself is
        what failed, the count goes to the hook log instead (R16): the one thing
        that may not happen is the increment vanishing.
        """
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO counters (name, value) VALUES (?, 1)"
                    " ON CONFLICT (name) DO UPDATE SET value = value + 1",
                    (counter,),
                )
        except sqlite3.Error as exc:
            self._log_fallback(counter, exc)

    def _correct(self, counter: str) -> None:
        """Subtract one from *counter*: `bump`'s mirror, for a count later revised.

        Only `_merge_with_stored` calls this, to move a step's count from
        `steps_from_hook` to `steps_from_telemetry` once telemetry turns out to
        have seen it too, so the two counters read alike whichever capture path
        wrote the row first (contracts/counters.md, SC-004).
        """
        try:
            with self._connection:
                self._connection.execute(
                    "UPDATE counters SET value = value - 1 WHERE name = ?",
                    (counter,),
                )
        except sqlite3.Error as exc:
            self._log_fallback(counter, exc)

    def counters(self) -> Mapping[str, int]:
        """Every counter this store has ever incremented, against its value.

        The read side is the cold path — `show counters` and `inspect` — so a
        store it cannot read raises rather than answering with an empty mapping
        that reads as "nothing ever happened".
        """
        return {
            str(name): int(value)
            for name, value in self._connection.execute("SELECT name, value FROM counters")
        }

    def iter_steps(self, since: int = 0) -> Iterator[EpisodicStep]:
        """Every step recorded after ``step_id`` *since*, oldest first.

        An iterator, and a high-water mark rather than a date: this is how the
        snapshot is built incrementally over a store that does not fit in
        memory.
        """
        rows = self._connection.execute(
            f"SELECT {_STEP_COLUMNS} FROM steps WHERE step_id > ? ORDER BY step_id",  # nosec B608
            (since,),
        )
        return (_step_from_row(row) for row in rows)

    def sequences_before(self, cutoff: datetime, project_key: str) -> tuple[SequenceKey, ...]:
        """Every turn *project_key* names that began before *cutoff*, oldest first.

        Scoped to one project rather than the whole store: `prune` re-derives
        only the snapshot of the project it was pointed at, so a turn this
        misses would leave that snapshot correct while a turn it caught from
        another project would leave that other project's snapshot stale.

        Compared as stored text, like every other ordering here: the column
        holds `datetime.isoformat` output, which sorts by instant as long as it
        is written in one zone — and everything written through this store is.
        """
        rows = self._connection.execute(
            "SELECT conversation_id, session_epoch, prompt_id, agent_id FROM sequences"
            " WHERE started_at < ? AND project_dir_key = ? ORDER BY started_at",
            (cutoff.isoformat(), project_key),
        )
        return tuple(
            SequenceKey(str(row[0]), int(row[1]), str(row[2]), str(row[3])) for row in rows
        )

    def delete_sequences(self, keys: Iterable[SequenceKey]) -> None:
        """Delete the turns *keys* names with every step of them.

        Steps first and both in one transaction: a turn whose rows outlived it
        would be a step the foreign key says belongs to nothing, and a crash
        between the two deletions must leave the store as it was.
        """
        parameters = [_key_params(key) for key in keys]
        with self._connection:
            self._connection.executemany(f"DELETE FROM steps{_SEQUENCE_WHERE}", parameters)  # nosec B608
            self._connection.executemany(f"DELETE FROM sequences{_SEQUENCE_WHERE}", parameters)  # nosec B608

    def record_inference(self, inference: Inference) -> None:
        """Write *inference* as a row of `inferences`, and count it (FR-002).

        ``recorded_at`` is stamped from the store's own clock beside
        ``occurred_at``, for the same reason a step's is (FR-044).

        A collector file read twice derives the same `inference_id` for the
        repeated record (`_inference_id`), so the conflict is resolved by the
        database and the second write is a no-op rather than an
        `IntegrityError`, the same shape `record`'s dedup key gives a step
        (FR-008). `contracts/counters.md` names no inference-duplicate counter,
        so unlike `steps_duplicate` this is left uncounted rather than invented.
        """
        with self._connection:
            cursor = self._connection.execute(
                f"INSERT INTO inferences ({', '.join(_INFERENCE_WRITE_COLUMNS)}, recorded_at)"  # nosec B608
                f" VALUES ({', '.join('?' * len(_INFERENCE_WRITE_COLUMNS))}, ?)"
                " ON CONFLICT (inference_id) DO NOTHING",
                (*_inference_params(inference), datetime.now(UTC).isoformat()),
            )
        if cursor.rowcount == 1:
            self.bump("inferences_recorded")

    def inferences_for(self, key: SequenceKey) -> tuple[Inference, ...]:
        """Every model call recorded under *key*, in the order they were written.

        By rowid rather than by `occurred_at`: that column holds the instant as
        the record wrote it, and two processes reporting one session need not
        agree on an offset, so ordering by the text would interleave them wrongly.
        """
        rows = self._connection.execute(
            f"SELECT {', '.join(_INFERENCE_READ_COLUMNS)} FROM inferences"  # nosec B608
            " WHERE sequence_key = ? ORDER BY rowid",
            (_sequence_text(key),),
        )
        return tuple(_inference_from_row(row, key) for row in rows)

    def record_agent(self, agent: Agent) -> None:
        """Write *agent* as a row of `agents`, folding a repeat observation into it (FR-020).

        Nothing here looks the parent up. The `spawned` edge is the child's own
        `parent_agent_id` (`contracts/graph-schema-v2.md`), so a sub-agent
        observed before anything had observed its parent forms the edge anyway,
        and the parent's own row arriving later neither completes nor repairs it.
        """
        with self._connection:
            self._connection.execute(
                f"INSERT INTO agents ({', '.join(_AGENT_WRITE_COLUMNS)})"  # nosec B608
                f" VALUES ({', '.join('?' * len(_AGENT_WRITE_COLUMNS))})"
                f" ON CONFLICT (agent_id) DO UPDATE SET {_AGENT_MERGE}",
                _agent_params(agent),
            )

    def agent(self, agent_id: str) -> Agent | None:
        """The actor *agent_id* names, or ``None`` when nothing observed it."""
        row = self._connection.execute(
            f"SELECT {', '.join(_AGENT_READ_COLUMNS)} FROM agents WHERE agent_id = ?",  # nosec B608
            (agent_id,),
        ).fetchone()
        return None if row is None else _agent_from_row(row, agent_id)

    def record_touch(self, touch: StepTouch) -> None:
        """Write *touch* against the step it names, and count it if it got no further than the file (FR-021).

        The row keeps its `resolution` as a column of its own rather than
        leaving a reader to infer it from the key: a touch that got no further
        than the file is written against the file and counted in
        `touched_file_only`, so the count says how much of the touched history
        is precise enough to reason over a symbol with. `touched_symbol_resolved`
        is not bumped here — that reading is decided at derivation time, against
        the line ranges the code entities carry (R16).
        """
        with self._connection:
            self._connection.execute(
                "INSERT INTO step_touches (step_id, entity_key, mode, resolution)"
                " VALUES (?, ?, ?, ?)",
                (touch.step_id, touch.entity_key, touch.mode, touch.resolution),
            )
        if touch.resolution == "file":
            self.bump("touched_file_only")

    def touches_for(self, step_id: int) -> tuple[StepTouch, ...]:
        """Every entity the step *step_id* names touched, oldest touch first.

        The stored `mode` and `resolution` are trusted as the vocabulary they
        were written in: `record_touch` is the only writer of either column,
        and it takes a `StepTouch` whose fields are `TouchMode` and
        `TouchResolution` already.
        """
        rows = self._connection.execute(
            "SELECT entity_key, mode, resolution FROM step_touches WHERE step_id = ? ORDER BY rowid",
            (step_id,),
        )
        return tuple(
            StepTouch(
                step_id=step_id,
                entity_key=str(row[0]),
                mode=cast("TouchMode", row[1]),
                resolution=cast("TouchResolution", row[2]),
            )
            for row in rows
        )

    def derive_consumes(self, step: EpisodicStep) -> StepConsumes | None:
        """Write the `consumed` edge *step* earns by adjacency, or count it unlinked (R8).

        No harness attribute joins a tool result to the model call that asked for
        it, so the edge is the latest inference placed at or before *step* among
        those filed under *step*'s own sequence key — the prompt and, for sub-agent
        work, the actor inside it — and the row says `adjacent` so that a derived
        edge is never read as an observed one (FR-003). Narrowing to `step`'s own
        `SequenceKey.agent_id` rather than every agent of the prompt relies on
        `agent_from_record`'s invariant: a sub-agent's inferences are filed under
        that sub-agent's own key, the same one its steps are, not the parent's.

        ``None`` when that turn holds no earlier inference: the step is counted in
        `inference_unlinked` and no edge is written, rather than attributed to a
        call that cannot have produced it.
        """
        preceding = _preceding_inference(self.inferences_for(step.sequence_key), step.occurred_at)
        if preceding is None:
            self.bump("inference_unlinked")
            return None
        consumes = StepConsumes(
            step_id=step.step_id, inference_id=preceding.inference_id, link="adjacent"
        )
        self._write_consumes(consumes)
        return consumes

    def _write_consumes(self, consumes: StepConsumes) -> None:
        """Insert *consumes*, counting the edge when it was derived rather than observed.

        `inference_adjacent` bumps on `link` being `adjacent` and on nothing else,
        so the count says how much of the consumed history is derivation
        (`contracts/counters.md`).
        """
        with self._connection:
            self._connection.execute(
                "INSERT INTO step_consumes (step_id, inference_id, link) VALUES (?, ?, ?)",
                (consumes.step_id, consumes.inference_id, consumes.link),
            )
        if consumes.link == "adjacent":
            self.bump("inference_adjacent")

    def consumes_for(self, step_id: int) -> tuple[StepConsumes, ...]:
        """Every model call the step *step_id* names consumed, oldest edge first.

        The stored `link` is trusted as the vocabulary it was written in, for the
        reason `touches_for` trusts a mode: `_write_consumes` is its only writer,
        and it takes a `StepConsumes` whose field is a `ConsumesLink` already.
        """
        rows = self._connection.execute(
            "SELECT inference_id, link FROM step_consumes WHERE step_id = ? ORDER BY rowid",
            (step_id,),
        )
        return tuple(
            StepConsumes(
                step_id=step_id,
                inference_id=str(row[0]),
                link=cast("ConsumesLink", row[1]),
            )
            for row in rows
        )

    def write_annotation(self, project_key: str, annotation: Annotation) -> None:
        """Store *annotation* against *project_key*, in the annotations table of its own (FR-038).

        The table is keyed by `Annotation.edge_key` rather than the edge
        itself, which does not exist here: an edge is re-derived from the
        episodic rows at every rebuild, and a note kept on it would be
        re-derived away.
        """
        with self._connection:
            self._connection.execute(
                "INSERT INTO annotations (annotation_id, edge_key, project_key, text,"
                " author, written_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    annotation.edge_key,
                    project_key,
                    annotation.text,
                    annotation.author,
                    annotation.written_at.isoformat(),
                ),
            )

    def annotations_for(self, project_key: str | None) -> tuple[Annotation, ...]:
        """Every annotation *project_key* names, or every one stored when it is ``None``."""
        if project_key is None:
            rows = self._connection.execute(
                "SELECT edge_key, text, author, written_at FROM annotations ORDER BY written_at"
            )
        else:
            rows = self._connection.execute(
                "SELECT edge_key, text, author, written_at FROM annotations"
                " WHERE project_key = ? ORDER BY written_at",
                (project_key,),
            )
        return tuple(
            Annotation(
                edge_key=str(row[0]),
                text=str(row[1]),
                author=str(row[2]),
                written_at=datetime.fromisoformat(str(row[3])),
            )
            for row in rows
        )

    def _log_fallback(self, counter: str, exc: sqlite3.Error) -> None:
        """Append one JSON line recording the counter the store could not keep.

        The last resort of a hook that must exit 0 regardless (FR-014): if even
        this fails there is nowhere left to say so, and losing the line is
        preferable to raising into the agent's own action. Rotated aside at
        5 MB (R16) so an unreachable store cannot grow this file forever.
        """
        log_fallback(self._log_path, counter, exc)


def log_fallback(log_path: Path, counter: str, exc: sqlite3.Error) -> None:
    """Append one JSON line at *log_path* recording *counter*, which could not be kept.

    The last resort of a hook that must exit 0 regardless (FR-014) — also the
    path taken when the store itself could not be opened, before any
    :class:`SQLiteEpisodicStore` exists to ask. If even this fails there is
    nowhere left to say so, and losing the line is preferable to raising into
    the agent's own action. Rotated aside at 5 MB (R16) so an unreachable store
    cannot grow this file forever.
    """
    line = json.dumps(
        {
            "at": datetime.now(UTC).isoformat(),
            "counter": counter,
            "error": str(exc),
        }
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size >= _LOG_ROTATE_BYTES:
            log_path.replace(log_path.with_suffix(".jsonl.1"))
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"{line}\n")
    except OSError:
        _logger.warning("could not record counter %s: %s", counter, exc)
