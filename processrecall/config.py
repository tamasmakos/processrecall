"""The hot-path configuration: a frozen dataclass, the standard library only.

R17 puts this module at the bottom of the layering and forbids ``pydantic``
above it: importing a settings framework costs more than the whole hook's
latency budget (R9), so configuration here is ``dataclasses`` and ``json``.

Every field is a decision the spec records rather than a preference, and each
ships as configuration precisely so that the measurement which revises it
changes a default instead of code (R6).

Example:
    from processrecall.config import load_config

    config = load_config()
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

_logger = logging.getLogger("processrecall")

#: Every field is settable as ``PROCESSRECALL_`` plus its name, upper-cased.
ENV_PREFIX = "PROCESSRECALL_"

#: The directory name every processrecall store lives under, home or project.
STORE_DIR = ".processrecall"

def home_dir() -> Path:
    """The one store root every processrecall file lives under (FR-052).

    Named here, at the bottom of the layering, so every other module builds
    its filename from this rather than re-spelling
    ``Path.home() / ".processrecall"``. A function, not a constant: ``HOME``
    can change within a process (tests monkeypatch it), and ``Path.home()``
    already re-reads it on every call — freezing the result at import time
    would throw that away.
    """
    return Path.home() / STORE_DIR

#: The three levels of generality every node materialises (FR-023), coarsest
#: first. ``Config.level`` picks which of them guidance is served at; the other
#: two stay on the node either way.
LEVELS = ("class", "class/program", "class/program/ext")


@dataclass(frozen=True, slots=True)
class Config:
    """What the procedural memory is tuned by, resolved once per process.

    Attributes:
        level: The generality at which guidance is served (FR-023). All three
            levels stay materialised on every node; this picks which one the
            renderer reads.
        k: Repetitions of a node before the repetition trigger fires (R7).
        h: Radius, in hops, of the neighbourhood extracted around the located
            node (R7).
        min_support: Supporting episodic steps an edge needs before guidance
            may be served from it. Below it the edge still exists, silently.
        backoff_order: How many preceding procedures the variable-order
            back-off starts from (FR-028, R6).
        clean_prompt_weight: How much a transition observed inside a
            cleanly-ended prompt outweighs one that was not (FR-027, R6).
        same_file_conditioning: Whether the back-off additionally conditions
            on the candidate touching the same file as the previous step
            (FR-028, R6). R6 measured this costing 2 points top-1 at the
            shipped serving level while buying 2 points top-3 — configuration
            rather than a constant so that verdict can be revised without a
            code change.
    """

    level: str = "class/program"
    k: int = 3
    h: int = 1
    min_support: int = 2
    backoff_order: int = 3
    clean_prompt_weight: float = 4.0
    same_file_conditioning: bool = True

    def __post_init__(self) -> None:
        """Refuse a serving level that names none of the materialised ones.

        A level nobody materialises is not a quieter setting, it is a renderer
        that finds no node and a hook that silently never speaks again.
        """
        if self.level not in LEVELS:
            raise ValueError(f"level={self.level!r} is not one of {list(LEVELS)}")


#: Every field name against its shipped default value, computed once: both
#: override readers below consult this rather than each constructing and
#: re-validating a fresh ``Config`` on a path the module's own docstring calls
#: latency-budgeted.
_DEFAULTS: dict[str, Any] = {field.name: field.default for field in fields(Config)}


def _file_overrides() -> dict[str, Any]:
    """The recognised entries of ``~/.processrecall/config.json``, or nothing.

    An absent file is the ordinary case — the defaults are the shipped tuning —
    so it yields no overrides rather than an error. Keys that name no field are
    dropped: a stale one should not stop a session from starting.
    """
    path = home_dir() / "config.json"
    if not path.is_file():
        return {}
    stored = json.loads(path.read_text(encoding="utf-8"))
    unrecognised = sorted(set(stored) - set(_DEFAULTS))
    if unrecognised:
        # A typo and a stale key look identical here; the drop stays (a stale
        # key must not stop a session from starting), but a silent drop leaves
        # an operator's tuning silently never applied, so it is at least logged.
        _logger.warning("%s: ignoring unrecognised key(s): %s", path, ", ".join(unrecognised))
    return {name: value for name, value in stored.items() if name in _DEFAULTS}


#: Case-insensitive spellings ``bool()`` would otherwise get wrong: every
#: non-empty string is truthy to the constructor, so ``PROCESSRECALL_..._=0``
#: or ``=false`` would silently turn a switch back on.
_BOOL_STRINGS = {"1": True, "true": True, "0": False, "false": False}


def _environment_overrides() -> dict[str, Any]:
    """The fields a ``PROCESSRECALL_`` variable names, parsed to the field's type.

    The environment carries strings; the type of each shipped default says what
    to turn one into, so a field added above becomes settable without a second
    table to keep in step. Booleans are special-cased: ``bool("0")`` is ``True``,
    so the field's own type cannot be handed the raw string.
    """
    overrides: dict[str, Any] = {}
    for name, default in _DEFAULTS.items():
        variable = f"{ENV_PREFIX}{name.upper()}"
        raw = os.environ.get(variable)
        if raw is None:
            continue
        wanted = type(default)
        try:
            if wanted is bool:
                overrides[name] = _BOOL_STRINGS[raw.strip().lower()]
            else:
                overrides[name] = wanted(raw)
        except (ValueError, KeyError) as exc:
            # The stdlib message names the value and not the variable, which
            # leaves an operator with a shell full of candidates to bisect.
            raise ValueError(f"{variable}={raw!r} is not a valid {wanted.__name__}") from exc
    return overrides


def load_config() -> Config:
    """Resolve the configuration, environment over file over shipped defaults."""
    return Config(**{**_file_overrides(), **_environment_overrides()})
