"""The identity seam: one sub-activity -> its procedure identity at three levels.

FR-018 and FR-023: a sub-activity names one node key at the finest level, and
that node carries the coarser levels as is-a ancestors — all three materialised
whatever level guidance happens to be served at. The tests read the keys the way
the recorder and the renderer will, and never reach into the spelling underneath.
"""

from __future__ import annotations

from processrecall.config import LEVELS
from processrecall.procedures.step import SubActivity
from processrecall.procedures.taxonomy import identify_procedure
from processrecall.symbolic.packs import ActivityClass


def test_an_action_on_a_file_is_identified_down_to_its_file_type() -> None:
    """The finest level is class/program/ext, and the coarser two are its ancestors."""
    activity = SubActivity(
        0, ActivityClass.INSPECTION, "Read", ("Read", "file_path"), ("processrecall/config.py",)
    )
    identity = identify_procedure(activity)
    assert identity.key == "Inspection/Read/py"
    assert identity.is_a == ("Inspection/Read", "Inspection")


def test_an_action_on_no_file_still_names_a_key_at_the_finest_level() -> None:
    """``git status`` reads no file, and its key says so rather than trailing off."""
    activity = SubActivity(0, ActivityClass.INSPECTION, "git status", ("git", "status"), ())
    identity = identify_procedure(activity)
    assert identity.key == "Inspection/git status/--"
    assert identity.is_a == ("Inspection/git status", "Inspection")


def test_every_level_stays_readable_whatever_the_serving_level() -> None:
    """FR-023: the serving level picks which key is read, not which keys exist."""
    activity = SubActivity(
        0,
        ActivityClass.SEARCH,
        "rg",
        ("rg", "identify", "processrecall/procedures/taxonomy.py"),
        ("processrecall/procedures/taxonomy.py",),
    )
    identity = identify_procedure(activity)
    assert [identity.key_at(level) for level in LEVELS] == [
        "Search",
        "Search/rg",
        "Search/rg/py",
    ]


def test_the_file_type_comes_from_the_first_file_and_is_case_folded() -> None:
    """``cp src/a.PY build/a.py`` is a Python move: one key, from what it acted on."""
    activity = SubActivity(
        0,
        ActivityClass.SCRIPT_EXECUTION,
        "cp",
        ("cp", "src/a.PY", "build/a.py"),
        ("src/a.PY", "build/a.py"),
    )
    assert identify_procedure(activity).key == "ScriptExecution/cp/py"
