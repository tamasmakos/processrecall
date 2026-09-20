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

Example:
    from processrecall.cli.doctor import readiness
    from processrecall.config import load_config

    print(readiness(load_config().telemetry_path))
"""

from __future__ import annotations

from pathlib import Path

#: What the report says in place of a path when no collector file is configured,
#: which is the shipped state (`processrecall.config.Config.telemetry_path`).
NO_SOURCE = "no telemetry source"


def readiness(telemetry_path: str) -> str:
    """What `doctor` reports about the collector file at *telemetry_path*.

    The path as configured, so an operator can see which file the memory is
    looking at rather than only whether *a* file was found: the commonest
    reading of an empty report is a collector writing somewhere else.
    """
    if not telemetry_path:
        return NO_SOURCE
    return f"{telemetry_path}  exists={Path(telemetry_path).is_file()}"
