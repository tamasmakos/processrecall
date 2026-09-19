"""The counter vocabulary: every degradation this feature can produce has a name.

`contracts/counters.md` is the contract; `COUNTERS` is what gives a name a
reader, since both readers — `processrecall show counters` and the `inspect`
tool — print the whole tuple, zeros included. The names are spelled out again
here rather than read off `COUNTERS`, because a test that derived them from the
constant it checks would pass however short that constant was (FR-003, SC-012).
"""

from __future__ import annotations

from processrecall.graph.store import COUNTERS

#: Reading the telemetry file and attributing what it holds.
TRANSPORT_COUNTERS = (
    "telemetry_absent",
    "telemetry_stale",
    "telemetry_records_read",
    "telemetry_record_unknown",
    "telemetry_record_partial",
    "telemetry_offset_reset",
    "telemetry_session_unbound",
    "telemetry_hook_disagreement",
    "telemetry_content_stripped",
    "telemetry_identity_stripped",
)

#: Writing the episodic plane, and moving a v1 store onto v2.
STORAGE_COUNTERS = (
    "steps_from_telemetry",
    "steps_from_hook",
    "inferences_recorded",
    "inference_adjacent",
    "inference_unlinked",
    "touched_file_only",
    "touched_symbol_resolved",
    "migration_v1_v2",
)

#: Deriving the semantic plane from the files a step touched.
SEMANTIC_COUNTERS = (
    "semantic_files_parsed",
    "semantic_files_skipped",
    "semantic_parse_failed",
    "semantic_unresolved_call",
    "process_type_derived",
    "process_type_unknown",
)

#: Why a consumed field is not there, one name per cause because the fixes
#: differ: turn spans on, turn the tool-details gate on, or upgrade the
#: harness (R9). The set is closed.
GAP_COUNTERS = (
    "gap_ttft",
    "gap_permission_wait",
    "gap_agent_nesting",
    "gap_stop_reason",
    "gap_error_class",
    "gap_tool_details",
    "gap_version_floor",
)

#: The traversals, one stem each.
RETRIEVAL_PATHS = (
    "usual_next",
    "generalised",
    "after_change",
    "on_entity",
    "prompt_start",
    "after_callers",
    "usually_refused",
    "ppr_neighbourhood",
    "frequent_episode",
)

#: Two per traversal: that the path ran, and that it contributed a candidate to
#: what was rendered. The gap between them measures whether the path earns its
#: place (FR-036).
RETRIEVAL_COUNTERS = tuple(
    f"path_{path}{suffix}" for path in RETRIEVAL_PATHS for suffix in ("", "_used")
)

#: Fusion's own doing rather than any single path's: the order the fused list is
#: served in, and the slot reserved against it.
FUSION_COUNTERS = (
    "guidance_exploration_slot",
    "guidance_diversity_dropped",
)

#: Every name `contracts/counters.md` adds.
FEATURE_COUNTERS = (
    *TRANSPORT_COUNTERS,
    *STORAGE_COUNTERS,
    *SEMANTIC_COUNTERS,
    *GAP_COUNTERS,
    *RETRIEVAL_COUNTERS,
    *FUSION_COUNTERS,
)


def test_feature_counter_names_are_declared() -> None:
    """A name missing from `COUNTERS` is invisible on both surfaces (SC-012)."""
    undeclared = sorted(set(FEATURE_COUNTERS) - set(COUNTERS))
    assert not undeclared, f"counters with no reader: {undeclared}"
