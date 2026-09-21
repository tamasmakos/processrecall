"""`processrecall doctor`: the readiness check on the collector's file (FR-004).

Driven through the command line, which is the whole of this verb's surface: an
operator asking whether their collector is feeding the memory reads a terminal.
"""

from __future__ import annotations

import json
from pathlib import Path

from processrecall.cli.__main__ import COMMANDS
from processrecall.graph.store import COUNTERS
from processrecall.trajectory.offset import OFFSET_NAME
from tests.cli.conftest import run_cli

#: The synthetic OTLP corpus, shared with `tests/trajectory/test_telemetry.py`:
#: `gate_off.jsonl` is the run whose tool records carry no detail attributes.
_TELEMETRY_FIXTURES = Path(__file__).parents[1] / "fixtures" / "telemetry"


def _persist_offset(home: Path, telemetry_path: Path, offset: int) -> None:
    """Record *offset* bytes of *telemetry_path* as already drained under *home*."""
    directory = home / ".processrecall"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / OFFSET_NAME).write_text(
        json.dumps({"path": str(telemetry_path), "offset": offset, "digest": "", "size": offset}),
        encoding="utf-8",
    )


def _name_telemetry_file(home: Path, telemetry_path: Path) -> None:
    """Point the configuration under *home* at *telemetry_path*."""
    directory = home / ".processrecall"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(
        json.dumps({"telemetry_path": str(telemetry_path)}), encoding="utf-8"
    )


def _directives(config: str) -> list[str]:
    """The configuration lines of *config*: what the collector reads, comments aside."""
    return [line.strip() for line in config.splitlines() if line.strip()[:1] not in ("", "#")]


def _comments(config: str) -> list[str]:
    """The comment lines of *config*, which is where its reasons live."""
    return [line.strip() for line in config.splitlines() if line.strip().startswith("#")]


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


def test_doctor_prints_the_shipped_example_collector_config(tmp_path: Path) -> None:
    """`--collector-config` prints the example the developer runs their collector on.

    The memory starts no collector (FR-004), so this text is the whole of the help
    it offers, and what it declares is asserted here: a loopback receiver, a batch
    processor, `format: json` with no rotation, the two metrics settings the memory
    needs, and the tool-details gate with its privacy consequence spelled out.
    """
    printed = run_cli(tmp_path / "unconfigured", "doctor", "--collector-config")

    assert printed.returncode == 0, printed.stderr
    directives = _directives(printed.stdout)
    assert "endpoint: 127.0.0.1:4317" in directives
    assert not [line for line in directives if "0.0.0.0" in line], (
        "a collector reachable from the network is a telemetry source for whoever finds it"
    )
    assert "batch:" in directives
    assert "format: json" in directives
    assert not [line for line in directives if line.startswith("rotation:")], (
        "with rotation on, a byte offset into the file stops meaning the same thing"
    )
    assert "OTEL_METRICS_INCLUDE_VERSION" in printed.stdout
    assert "OTEL_METRICS_INCLUDE_SESSION_ID" in printed.stdout
    assert "OTEL_LOG_TOOL_DETAILS" in printed.stdout
    assert any("Privacy" in line for line in _comments(printed.stdout)), (
        "the tool-details gate ships without its privacy consequence in a comment"
    )


def test_doctor_reports_source_then_gates_then_counters(tmp_path: Path) -> None:
    """The readiness report holds the order `contracts/collector-transport.md` fixes.

    An operator reads it top to bottom: which file, when it was last written and
    how much of it no pass has taken, then the two gated attributes and the gates
    the records themselves imply, then every telemetry counter — including the
    names still at zero, since a drain that never ran reads like one that found
    nothing unless the name is printed anyway.
    """
    home, telemetry = tmp_path / "home", tmp_path / "otel.jsonl"
    telemetry.write_bytes((_TELEMETRY_FIXTURES / "gate_off.jsonl").read_bytes())
    _name_telemetry_file(home, telemetry)
    _persist_offset(home, telemetry, 100)

    report = run_cli(home, "doctor")

    assert report.returncode == 0, report.stderr
    expected = [
        f"{telemetry}  exists=True",
        "last modified",
        f"unread bytes  {telemetry.stat().st_size - 100}",
        "app.version  observed",
        "session.id  observed",
        "content gate  off",
        "tool details gate  off",
        *(name for name in COUNTERS if name.startswith("telemetry_")),
    ]
    found = [report.stdout.find(text) for text in expected]
    assert -1 not in found, [text for text, at in zip(expected, found, strict=True) if at == -1]
    assert found == sorted(found), f"out of the contract's order:\n{report.stdout}"
