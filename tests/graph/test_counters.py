"""The counter vocabulary: every degradation this feature can produce has a name.

`contracts/counters.md` is the contract; `COUNTERS` is what gives a name a
reader, since both readers — `processrecall show counters` and the `inspect`
tool — print the whole tuple, zeros included. Two checks hold that contract
open. The first spells the names out again rather than reading them off
`COUNTERS`, because a test that derived them from the constant it checks would
pass however short that constant was (FR-003, SC-012). The second walks every
`bump` call under `processrecall/` and asserts the names found there are a
subset of `COUNTERS` (`assert bumped`), so a name added at a call site with no
matching entry fails on its own rather than waiting to be added above.
"""

from __future__ import annotations

import ast
from pathlib import Path

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


#: The package whose `bump` calls have to be spelled in `COUNTERS` to be read.
PACKAGE = Path(__file__).resolve().parents[2] / "processrecall"


def _is_bump(function: ast.expr) -> bool:
    """Whether *function* is the `bump` being called, however it was reached."""
    if isinstance(function, ast.Attribute):
        return function.attr == "bump"
    return isinstance(function, ast.Name) and function.id == "bump"


def _module_dict_values(tree: ast.Module, name: str) -> set[str]:
    """The string values of the module-level dict or tuple bound to *name*.

    This is how `annotations.py` reaches a counter: `_COUNTERS[reason]` names
    nothing at the call site, so the argument names have to be read off the
    table the subscript indexes instead.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        values = node.value.values if isinstance(node.value, ast.Dict) else node.value.elts
        return {
            value.value
            for value in values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        }
    return set()


def _names_in(argument: ast.expr, tree: ast.Module) -> set[str]:
    """The counter name(s) *argument* spells out, however it was written."""
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return {argument.value}
    if isinstance(argument, ast.IfExp):
        return _names_in(argument.body, tree) | _names_in(argument.orelse, tree)
    if isinstance(argument, ast.Subscript) and isinstance(argument.value, ast.Name):
        return _module_dict_values(tree, argument.value.id)
    return set()


def _bumped_names(module: Path) -> set[str]:
    """Every counter name spelled out at a `bump` call in *module*.

    `bump` takes exactly one argument, so only that argument is read; a
    dynamically built name (an f-string, say) yields nothing rather than a
    guess.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    return {
        name
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and _is_bump(call.func) and call.args
        for name in _names_in(call.args[0], tree)
    }


def test_every_bumped_name_has_a_reader() -> None:
    """SC-012: a name `COUNTERS` omits is one neither surface ever prints."""
    bumped = {name for module in sorted(PACKAGE.rglob("*.py")) for name in _bumped_names(module)}

    assert bumped, f"no bump call found under {PACKAGE} — the scan reads nothing"
    undeclared = sorted(bumped - set(COUNTERS))
    assert not undeclared, f"counters with no reader: {undeclared}"
