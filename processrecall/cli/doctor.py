"""``processrecall doctor`` — the readiness check on the collector's file (FR-004).

The memory never installs, starts or supervises a collector: the file it reads is
written by one the developer runs. So the only help this verb can offer is a
report on the file they named, and a path that is not there yet reads as "no
telemetry source" rather than as a failure — naming the file before the
collector has written it is the ordinary cold start
(`contracts/collector-transport.md`).

A read and nothing else: no counter is bumped here. `telemetry_absent` counts
the drain passes that found nothing to take (`processrecall.cli.derive.drain`),
and a human asking whether their collector works is not one of those passes.

The collector the developer runs is theirs to configure, so the other thing this
verb offers is the example configuration, shipped as package data and printed on
request: an installed wheel has no checkout to read it from.

Example:
    from processrecall.cli.doctor import collector_config, readiness
    from processrecall.config import load_config

    print(readiness(load_config().telemetry_path))
    print(collector_config())
"""

from __future__ import annotations

from pathlib import Path

#: What the report says in place of a path when no collector file is configured,
#: which is the shipped state (`processrecall.config.Config.telemetry_path`).
NO_SOURCE = "no telemetry source"

#: The example collector configuration, beside this module so that it ships with
#: it (`tests/test_packaging.py`) and prints from an install (FR-004).
COLLECTOR_CONFIG = Path(__file__).parent / "data" / "otel-collector.yaml"


def collector_config() -> str:
    """The shipped example collector configuration, verbatim.

    Printed for the developer to review and place themselves: nothing here writes
    it into a collector directory, and no collector is started.
    """
    return COLLECTOR_CONFIG.read_text(encoding="utf-8")


def readiness(telemetry_path: str) -> str:
    """What `doctor` reports about the collector file at *telemetry_path*.

    The path as configured, so an operator can see which file the memory is
    looking at rather than only whether *a* file was found: the commonest
    reading of an empty report is a collector writing somewhere else.
    """
    if not telemetry_path:
        return NO_SOURCE
    return f"{telemetry_path}  exists={Path(telemetry_path).is_file()}"
