"""`processrecall doctor`: the readiness check on the collector's file (FR-004).

Driven through the command line, which is the whole of this verb's surface: an
operator asking whether their collector is feeding the memory reads a terminal.
"""

from __future__ import annotations

import json
from pathlib import Path

from processrecall.cli.__main__ import COMMANDS
from tests.cli.conftest import run_cli


def _name_telemetry_file(home: Path, telemetry_path: Path) -> None:
    """Point the configuration under *home* at *telemetry_path*."""
    directory = home / ".processrecall"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(
        json.dumps({"telemetry_path": str(telemetry_path)}), encoding="utf-8"
    )


def test_doctor_is_one_of_the_commands_the_cli_registers() -> None:
    """The verb is registered, so `--help` and the docs name it (FR-004)."""
    assert "doctor" in COMMANDS


def test_doctor_reports_the_configured_collector_file_or_no_source(tmp_path: Path) -> None:
    """A named file is reported on; the shipped state reads as no source, not a failure."""
    unconfigured = run_cli(tmp_path / "unconfigured", "doctor")

    assert unconfigured.returncode == 0, unconfigured.stderr
    assert "no telemetry source" in unconfigured.stdout

    home, telemetry = tmp_path / "home", tmp_path / "otel.jsonl"
    telemetry.write_text('{"resourceLogs": []}\n', encoding="utf-8")
    _name_telemetry_file(home, telemetry)

    configured = run_cli(home, "doctor")

    assert configured.returncode == 0, configured.stderr
    assert str(telemetry) in configured.stdout
    assert "exists=True" in configured.stdout
