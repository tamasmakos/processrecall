"""The hot-path configuration seam: defaults, the JSON file, env overrides.

Every value here is a decision recorded in the spec, not a preference: the
serving level is FR-023, `k`/`h` are R7, the back-off order and the clean-prompt
weight are R6. The defaults are asserted as literals so that changing one is a
visible change to this file rather than a silent drift.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from processrecall.config import ENV_PREFIX, load_config


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home directory with no ``PROCESSRECALL_`` variables set.

    Without clearing the environment too, a variable already exported in the
    shell running the tests would make the defaults assertions below flaky.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in list(os.environ):
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_config(home: Path, values: dict[str, object]) -> None:
    """Put *values* where ``load_config`` looks: ``~/.processrecall/config.json``."""
    directory = home / ".processrecall"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(json.dumps(values), encoding="utf-8")


def test_defaults_when_no_config_file_exists(home: Path) -> None:
    config = load_config()

    assert config.level == "class/program"
    assert config.k == 3
    assert config.h == 1
    assert config.min_support == 2
    assert config.backoff_order == 3
    assert config.clean_prompt_weight == 4.0


def test_config_file_overrides_only_the_fields_it_names(home: Path) -> None:
    _write_config(home, {"level": "class", "backoff_order": 2})

    config = load_config()

    assert config.level == "class"
    assert config.backoff_order == 2
    assert config.k == 3
    assert config.clean_prompt_weight == 4.0


def test_environment_overrides_the_config_file(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(home, {"level": "class", "k": 5})
    monkeypatch.setenv("PROCESSRECALL_LEVEL", "class/program/ext")
    monkeypatch.setenv("PROCESSRECALL_MIN_SUPPORT", "7")
    monkeypatch.setenv("PROCESSRECALL_CLEAN_PROMPT_WEIGHT", "1.5")

    config = load_config()

    assert config.level == "class/program/ext"
    assert config.min_support == 7
    assert config.clean_prompt_weight == 1.5
    assert config.k == 5


def test_unparseable_environment_value_names_the_variable(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROCESSRECALL_BACKOFF_ORDER", "three")

    with pytest.raises(ValueError, match="PROCESSRECALL_BACKOFF_ORDER"):
        load_config()


def test_serving_level_outside_the_three_materialised_levels_is_refused(home: Path) -> None:
    _write_config(home, {"level": "program"})

    with pytest.raises(ValueError, match="class/program"):
        load_config()


def test_telemetry_path_is_unset_until_a_collector_file_is_named(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-004: the developer points the memory at a file, so there is no default one."""
    assert load_config().telemetry_path == ""

    _write_config(home, {"telemetry_path": str(home / "collector.jsonl")})

    assert load_config().telemetry_path == str(home / "collector.jsonl")

    monkeypatch.setenv("PROCESSRECALL_TELEMETRY_PATH", str(home / "otel.jsonl"))

    assert load_config().telemetry_path == str(home / "otel.jsonl")


def test_unmeasured_traversals_stay_off_until_asked_for(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-036: a traversal that has not beaten the baseline is reachable, not served."""
    assert load_config().unmeasured_traversals is False

    monkeypatch.setenv("PROCESSRECALL_UNMEASURED_TRAVERSALS", "true")

    assert load_config().unmeasured_traversals is True
