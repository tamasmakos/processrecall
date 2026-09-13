"""One recorded action -> the ordered sub-activities it actually performed.

A step is not an activity (FR-017). ``uv run pytest -q | tail -5`` is one tool
call and two activities, and a node named after the whole call would be a node no
second step ever matches. So a shell action decomposes through the command
grammar of :mod:`processrecall.procedures.shell`, and every other tool yields the
single sub-activity its vocabulary entry names.

Lifted from prototype v3/v4 (R18). On the hot path, so the standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass

from processrecall.procedures.shell import artifacts_in, classify_program, subcommands
from processrecall.symbolic.packs import ActivityClass
from processrecall.trajectory.event import TrajectoryEvent


@dataclass(frozen=True, slots=True)
class SubActivity:
    """One sub-activity of one action: what it did, with what, to which files.

    Derived, never stored: `steps_from` recomputes it from the event, so a
    change to the grammar reclassifies old actions on the next rebuild rather
    than needing a migration.

    Attributes:
        ordinal: 0-based position within the action that decomposed into it.
        activity_class: The engineering activity it belongs to (FR-019).
        program: What performed it — ``"git commit"``, ``"Read"``.
        tokens: The invocation, for the action template to abstract.
        files: The paths it touched, as the action spelled them; normalisation
            is the recorder's job, not the grammar's.
    """

    ordinal: int
    activity_class: ActivityClass
    program: str
    tokens: tuple[str, ...]
    files: tuple[str, ...]


def steps_from(
    event: TrajectoryEvent, tool_activity: ActivityClass | None
) -> tuple[SubActivity, ...]:
    """The ordered sub-activities of one action.

    *tool_activity* is the class the harness vocabulary assigns the tool, or
    ``None`` when the pack routes the tool through the command grammar instead
    (``"class": null, "decompose": "shell"`` in ``contracts/packs.md``) — the
    grammar names a class per simple command, so the table cannot.

    A tabled tool's ``program`` is deliberately ``event.tool_name`` rather than
    the pack entry's own ``"program"`` field: today the two always agree
    (``"Read": {"program": "Read"}``), and taking the pack entry itself would
    mean threading it through this call instead of just the class it resolved
    to. Revisit if a future pack ever wants the two to diverge.
    """
    if tool_activity is None:
        command = str(event.tool_call_arguments.get("command", ""))
        shell_steps = _shell_steps(command)
        # A line of pure plumbing named no activity, but the action still
        # happened: it stays on the chain as unknown rather than vanishing.
        return shell_steps or (_unknown_shell_step(command),)
    return (_tool_step(event, tool_activity),)


#: Argument names a harness gives the file its tool acts on, in preference order.
_PATH_ARGUMENTS = ("file_path", "notebook_path", "path")


def _tool_step(event: TrajectoryEvent, activity: ActivityClass) -> SubActivity:
    """The single sub-activity a non-shell tool performs.

    The tokens are the tool and its argument *names*, never their values: a
    tabled tool has no command line to abstract, and the values are the payload
    an action template must not be able to carry.
    """
    arguments = event.tool_call_arguments
    path = next(
        (
            value
            for name in _PATH_ARGUMENTS
            if isinstance(value := arguments.get(name), str) and value
        ),
        "",
    )
    return SubActivity(
        ordinal=0,
        activity_class=activity,
        program=event.tool_name,
        tokens=(event.tool_name, *sorted(arguments)),
        files=(path,) if path else (),
    )


def _unknown_shell_step(command: str) -> SubActivity:
    """The one sub-activity for a command line whose simple commands are all plumbing.

    FR-022: the tokens carry the command line itself, not the argument name, so
    the step is reclassifiable later without re-ingesting the transcript.
    """
    return SubActivity(0, ActivityClass.UNKNOWN, "Bash", tuple(command.split()), ())


def _shell_steps(command: str) -> tuple[SubActivity, ...]:
    """The simple commands of a command line that count as activities.

    A builtin or a control word classifies as no activity at all and is dropped:
    keeping ``cd`` would bury the pipeline's real steps under plumbing.
    """
    steps: list[SubActivity] = []
    for tokens in subcommands(command):
        activity, program = classify_program(tokens)
        if activity is None:
            continue
        files = tuple(path for _, path in artifacts_in(tokens))
        steps.append(SubActivity(len(steps), activity, program, tuple(tokens), files))
    return tuple(steps)
