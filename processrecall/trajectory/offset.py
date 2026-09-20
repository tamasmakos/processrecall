"""Where the last pass over the collector file stopped (`contracts/collector-transport.md`).

The collector writes a file that grows; the end-of-unit-of-work pass drains it and
must not start from the top every time. What makes that safe is not the byte
offset alone — a stale offset into a rotated file of the same name yields
well-formed JSON from the wrong place, the one failure mode here that would be
silent. So the offset travels with a fingerprint of the file it was taken from,
and is trusted only while that fingerprint still holds.

Re-reading is cheap and duplicates nothing: ingest is idempotent on the tool-use
identity, which is what lets the offset be a hint rather than a contract.

On the hot path's layer, so the standard library only.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from processrecall.config import Counters

#: The offset file's name, beside the store under ``~/.processrecall``.
OFFSET_NAME = "collector-offset.json"

#: How much of the file's head identifies it. Bounded so the check stays a read
#: of a few pages rather than of a collector file that has grown all session.
HEAD_BYTES = 4096


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What says a file is still the file an offset was taken from.

    Attributes:
        digest: Hash of the head — the first `size` bytes, capped at
            :data:`HEAD_BYTES`. The recorded size is part of the fingerprint
            because it says *which* region was hashed: a file below the cap that
            merely gained a line must re-hash the shorter region it had then, or
            every append would read as a rotation.
        size: The total size at last read.
    """

    digest: str
    size: int


@dataclass(frozen=True, slots=True)
class ReadOffset:
    """The persisted triple: which file, how far into it, and that it is that file."""

    path: str
    offset: int
    fingerprint: Fingerprint


class OffsetFile:
    """One persisted read offset on disk, counted into *counters*.

    The path is state rather than an argument repeated at every call, so a
    caller draining a collector file holds the offset that belongs to it.
    """

    def __init__(self, path: Path, counters: Counters) -> None:
        self._path = path
        self._counters = counters

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._path!s})"

    def read(self) -> ReadOffset | None:
        """The recorded offset, or ``None`` when there is none to trust.

        A missing or half-written file is not an error: the answer it would have
        given is a hint, and its absence means the next pass reads from the top.
        """
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            return ReadOffset(
                path=str(document["path"]),
                offset=int(document["offset"]),
                fingerprint=Fingerprint(str(document["digest"]), int(document["size"])),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def write(self, offset: ReadOffset) -> None:
        """Land *offset* at this path, replacing whatever was recorded before.

        Written in place rather than through a temporary file: a torn write is
        read back as no offset at all, which costs a re-read and nothing else.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "path": offset.path,
            "offset": offset.offset,
            "digest": offset.fingerprint.digest,
            "size": offset.fingerprint.size,
        }
        self._path.write_text(json.dumps(document), encoding="utf-8")

    def appended_lines(self, target: Path) -> Iterator[str]:
        """Every complete line *target* has gained since the last pass, in order.

        A trailing line with no newline is mid-write: it is not consumed, and the
        offset stops before it, so the next pass reads it whole. The offset is
        recorded once the caller has taken every line — an abandoned iteration
        leaves the previous offset standing and re-reads.
        """
        with target.open("rb") as handle:
            consumed = handle.seek(self._start(target, handle))
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                consumed += len(raw)
                # Undecodable bytes reach the reader as a line that will not
                # parse, which is counted there, rather than as a lost pass.
                yield raw.decode("utf-8", "replace").removesuffix("\n")
            self.write(ReadOffset(str(target), consumed, _fingerprint(handle)))

    def _start(self, target: Path, handle: BinaryIO) -> int:
        """Where reading *target* may resume: the recorded offset, or zero.

        Zero is reached two ways, and only one of them is news. A file this
        offset was never taken from is simply a new file; a file whose
        fingerprint no longer holds was rotated, truncated or replaced under a
        name we were mid-way through, and that is the counted case.
        """
        recorded = self.read()
        if recorded is None or recorded.path != str(target):
            return 0
        if _still_the_same_file(handle, recorded.fingerprint):
            return recorded.offset
        self._counters.bump("telemetry_offset_reset")
        return 0


def _still_the_same_file(handle: BinaryIO, recorded: Fingerprint) -> bool:
    """Whether the file open as *handle* is the one *recorded* fingerprinted.

    A file shorter than it was has lost bytes an offset was counted against, so
    it is a different file however its head reads.
    """
    if os.fstat(handle.fileno()).st_size < recorded.size:
        return False
    return _head_digest(handle, min(HEAD_BYTES, recorded.size)) == recorded.digest


def _fingerprint(handle: BinaryIO) -> Fingerprint:
    """The fingerprint of the file open as *handle*, as it stands now."""
    size = os.fstat(handle.fileno()).st_size
    return Fingerprint(digest=_head_digest(handle, min(HEAD_BYTES, size)), size=size)


def _head_digest(handle: BinaryIO, length: int) -> str:
    """Hash the first *length* bytes of the file open as *handle*."""
    handle.seek(0)
    return sha256(handle.read(length)).hexdigest()
