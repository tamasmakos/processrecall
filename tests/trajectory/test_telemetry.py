"""The collector file's persisted read offset: resumption, rotation, mid-write lines."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from processrecall.trajectory.offset import OFFSET_NAME, OffsetFile


class FakeCounters:
    """A counter sink that keeps what it was bumped with, so a test reads it back."""

    def __init__(self) -> None:
        self.counted: Counter[str] = Counter()

    def bump(self, counter: str) -> None:
        self.counted[counter] += 1


@pytest.fixture
def counters() -> FakeCounters:
    return FakeCounters()


def test_rotated_file_resets_offset_and_does_not_duplicate(
    tmp_path: Path, counters: FakeCounters
) -> None:
    collector = tmp_path / "telemetry.jsonl"
    collector.write_bytes(b'{"n": 1}\n{"n": 2}\n')
    offsets = OffsetFile(tmp_path / OFFSET_NAME, counters)

    first = list(offsets.appended_lines(collector))
    assert first == ['{"n": 1}', '{"n": 2}']
    # A pass over an unchanged file has nothing to give: the offset is honoured.
    assert list(offsets.appended_lines(collector)) == []

    # Rotated: same name, a shorter file with a different head, its last line
    # still being written.
    collector.write_bytes(b'{"n": 3}\n{"n": 4')
    rotated = list(offsets.appended_lines(collector))

    assert rotated == ['{"n": 3}']
    assert counters.counted["telemetry_offset_reset"] == 1
    recorded = offsets.read()
    assert recorded is not None
    assert recorded.offset == len('{"n": 3}\n')
    # A further pass over the unchanged (rotated) file has nothing new to give.
    assert list(offsets.appended_lines(collector)) == []

    # The mid-write line arrives whole on the next pass.
    collector.write_bytes(b'{"n": 3}\n{"n": 4}\n')
    completed = list(offsets.appended_lines(collector))

    assert completed == ['{"n": 4}']
