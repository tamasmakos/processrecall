"""The harness vocabulary: one pack per harness, tool name to procedure class.

FR-003 keeps this mapping out of the code. A harness calls its tools whatever it
likes, and the classes the graph reasons over are the closed set
:class:`~processrecall.config.ActivityClass` — so the join between the two lives in a
hand-edited JSON pack that a second harness supplies for itself, with no edit
here (FR-004). Read the way ``symbolic/packs.py`` reads its own: plain
``json``, every defect surfacing as a :class:`~processrecall.exceptions.PackError`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from processrecall.config import ActivityClass
from processrecall.exceptions import PackError

#: The one harness pack this phase ships (FR-005), beside the code that reads it.
CLAUDE_CODE = Path(__file__).parent / "vocab" / "claude_code.json"


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """One tabled tool: the class it performs, or the grammar it defers to.

    ``activity_class`` is ``None`` exactly when ``decompose`` names a grammar:
    one ``Bash`` call is several activities, so the table cannot name its class
    and the command grammar of :mod:`processrecall.procedures.shell` does it per
    simple command instead (FR-017).
    """

    activity_class: ActivityClass | None
    #: Carried for pack fidelity; no reader takes it today (`steps_from` uses
    #: `event.tool_name` instead — see its docstring).
    program: str | None
    decompose: str | None = None


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """A loaded harness pack: the tools it tables, keyed by the harness's name."""

    version: int
    harness: str
    tools: Mapping[str, ToolEntry]

    def activity_for(self, tool_name: str, bump: Callable[[str], None]) -> ActivityClass | None:
        """The class *tool_name* performs, for `procedures.step.steps_from`.

        ``None`` means the entry defers to the command grammar. A name the pack
        does not table is ``Unknown`` — a member of the vocabulary, not a
        failure — and *bump*, the store's counter (R16), is told so the residue
        stays visible (FR-022).
        """
        if (entry := self.tools.get(tool_name)) is None:
            bump("class_unknown")
            return ActivityClass.UNKNOWN
        return entry.activity_class


def load_vocabulary(path: Path = CLAUDE_CODE) -> Vocabulary:
    """Read a harness vocabulary pack from *path*."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Vocabulary(
            version=raw["version"],
            harness=raw["harness"],
            tools={name: _entry_from(name, entry) for name, entry in raw["tools"].items()},
        )
    except (OSError, KeyError, TypeError, ValueError) as defect:
        raise PackError(str(path), str(defect)) from defect


def _entry_from(tool_name: str, entry: Mapping[str, Any]) -> ToolEntry:
    """One table row as the dataclass, refusing a row that routes nowhere.

    A class outside the closed set, or a null class that names no grammar to
    defer to, is a hand-edit that left the tool unreachable — and a tool the
    table mentions but cannot route is worse than one it never mentioned.
    """
    named, decompose = entry["class"], entry.get("decompose")
    if named is None and decompose != "shell":
        raise ValueError(f"{tool_name} names neither a class nor the shell decomposition")
    return ToolEntry(
        activity_class=None if named is None else ActivityClass(named),
        program=entry["program"],
        decompose=decompose,
    )
