"""The procedure identity of a sub-activity, at three levels of generality.

``Read`` on a ``.py`` file is an inspection, an inspection *by ``Read``*, and an
inspection by ``Read`` *of Python* — three true statements of decreasing breadth.
FR-018 keeps all three, but as one node: the finest key is the identity, the two
coarser ones are its is-a ancestors, so a broad query reaches a specific node
without the graph carrying it three times.

Lifted from prototype v3/v4 (R18). On the hot path, so the standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from processrecall.config import LEVELS
from processrecall.procedures.step import SubActivity
from processrecall.symbolic.packs import ActivityClass

#: The segment a key spells where it has no file type to name. A key is
#: ``/``-joined and positional, so the finest level needs a segment even when the
#: action touched no file: ``"Inspection/git status/--"``, never
#: ``"Inspection/git status/"``, which would collide with the coarser level.
NO_FILE_TYPE = "--"


@dataclass(frozen=True, slots=True)
class ProcedureIdentity:
    """What one sub-activity *is*, named at every level of generality.

    Derived, never stored: `identify_procedure` recomputes it from the
    sub-activity, so a change to the levels renames old steps on the next
    rebuild.

    Attributes:
        activity_class: The broadest level — the engineering activity (FR-019).
        program: What performed it.
        file_ext: The file type it acted on, lowercase and without the dot.
            ``""`` when it acted on no file, or on one with no suffix.
    """

    activity_class: ActivityClass
    program: str
    file_ext: str
    _keys: tuple[str, str, str]

    @property
    def key(self) -> str:
        """The node key: the identity at the finest level, ``class/program/ext``."""
        return self._keys[-1]

    @property
    def is_a(self) -> tuple[str, ...]:
        """The ancestor keys the node generalises to, coarsest last (FR-018)."""
        return self._keys[-2::-1]

    def key_at(self, level: str) -> str:
        """This identity's key at one of the materialised levels of `LEVELS`.

        Every level is spelled whatever level guidance is served at (FR-023):
        the serving level chooses which key the renderer reads, never which
        keys the node has.
        """
        return self._keys[LEVELS.index(level)]


def identify_procedure(activity: SubActivity) -> ProcedureIdentity:
    """The procedure identity of one sub-activity.

    An action may touch several files; the first is the one it is *about* —
    ``cp src/a.py build/a.py`` is a Python move, and the grammar already lists
    the files in the order the command spelled them.
    """
    first_file = activity.files[0] if activity.files else ""
    file_ext = PurePosixPath(first_file).suffix.lstrip(".").lower()
    program = activity.program
    activity_class = activity.activity_class.value
    by_program = f"{activity_class}/{program}"
    keys = (
        activity_class,
        by_program,
        f"{by_program}/{file_ext or NO_FILE_TYPE}",
    )
    return ProcedureIdentity(
        activity_class=activity.activity_class,
        program=program,
        file_ext=file_ext,
        _keys=keys,
    )
