"""The end-of-unit-of-work pass' pre-fold steps, drained from the collector (FR-004).

Telemetry reaches the memory as a file the developer's own collector writes, and
this is the one place that file is read: inside the detached job `SessionEnd`
spawns, the same pass the snapshots are folded in. Never on a hook path, which
has a timeout the file has no bound to respect, and never from a process that
stays up to watch it — that would be the listener the standing no-listening-port
decision rules out.

The drain is meant to run before the fold, because the records it takes are
episodic rows like any other and the snapshots this pass writes should be
folded from them too. That wiring waits on the OTLP reader and the
record-to-step ingest of `trajectory/telemetry.py` (T013, T014, T016); until
those land, this module only advances the persisted offset and reports the
lines it passed over.

Resuming from the persisted offset is what keeps the pass proportional to what
the last unit of work appended rather than to everything the session ever
emitted (`processrecall.trajectory.offset`).

Off the hot path, but on the store's layer, so the standard library only.

Example:
    from processrecall.cli.derive import drain
    from processrecall.config import load_config

    print(drain(load_config().telemetry_path, store))
"""

from __future__ import annotations

from pathlib import Path

from processrecall.config import Counters, home_dir
from processrecall.trajectory.offset import OFFSET_NAME, OffsetFile


def drain(telemetry_path: str, store: Counters) -> str | None:
    """Take what the collector at *telemetry_path* appended since the last pass.

    ``None`` when no collector file is configured: that is the shipped state
    (`processrecall.config.Config.telemetry_path`), and a pass with no
    telemetry source has nothing to say about one rather than a count of zero.

    A configured file that is not there yet is a cold start — the developer
    named it before their collector wrote it — so it is counted and reported,
    not raised: this pass also folds the snapshots, and none of that work may
    be lost to a telemetry source being late. Ageing a file that has gone
    stale is T061's, not this guard's.
    """
    if not telemetry_path:
        return None
    target = Path(telemetry_path)
    if not target.is_file():
        store.bump("telemetry_absent")
        return f"{target}  absent"
    offsets = OffsetFile(home_dir() / OFFSET_NAME, store)
    return f"{target}  lines={sum(1 for _ in offsets.appended_lines(target))}"
