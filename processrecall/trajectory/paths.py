"""Whatever the harness spelled for a file -> a path safe to write down.

FR-013 anchors every recorded path on the project directory, so a repository
that moves or is checked out elsewhere still matches the graph it built.

On the hot path, so the standard library only.
"""

from __future__ import annotations

import posixpath
import re
from hashlib import sha256
from pathlib import Path, PurePosixPath

#: What a path under the user's home is spelled relative to. A home-relative
#: path cannot contain the username, so the shape stays legible and the identity
#: does not travel with it (R4).
HOME_ROOT = "~"

#: What a path neither the project nor the home contains is spelled under, with
#: every directory component discarded. Deliberately lossy: a system binary or a
#: sibling checkout stays typeable as an action without its layout travelling
#: into a committable snapshot (FR-054, R4).
EXTERNAL_ROOT = "<external>"

#: A leading Windows drive, rewritten to a first path segment rather than
#: dropped: two checkouts on two drives must not normalise to one path, and the
#: letter cannot reach the output because every case returns a relative form.
#: Uppercased in the substitution so `c:\...` and `C:\...` name the same
#: segment: Windows drive letters are case-insensitive, and two spellings of
#: one project directory must not become two nodes.
_DRIVE = re.compile(r"^([A-Za-z]):")


def normalise_path(raw: str, project_dir: str) -> str:
    """*raw* as a POSIX path safe to record, anchored on *project_dir*.

    Three cases, tried in that order (R4): a path the project contains becomes
    project-relative; a path the user's home contains becomes ``~/``-relative;
    anything else keeps its name alone. So the result never carries a drive
    letter, a leading separator, a ``..`` or an absolute user path, whichever
    way the harness happened to spell the path. *raw* naming the project
    directory itself (or an empty *raw*, which resolves to it) returns ``"."``,
    the project-relative spelling of "no sub-path".
    """
    project = lexical_path(project_dir)
    path = lexical_path(raw)
    if not path.is_absolute():
        path = lexical_path(f"{project}/{path}")
    if path.is_relative_to(project):
        return str(path.relative_to(project))
    home = lexical_path(str(Path.home()))
    if path.is_relative_to(home):
        return f"{HOME_ROOT}/{path.relative_to(home)}"
    return f"{EXTERNAL_ROOT}/{path.name}"


def lexical_path(raw: str) -> PurePosixPath:
    """*raw* with POSIX separators, a drive letter as a segment, and no ``..``.

    Lexical on purpose: a path is normalised where it is recorded, which is a
    hot path and a machine that may no longer hold the file.
    """
    posix = _DRIVE.sub(lambda m: f"/{m.group(1).upper()}", raw.replace("\\", "/"))
    return PurePosixPath(posixpath.normpath(posix))


def project_key(project_dir: str) -> str:
    """The stable, path-free key a sequence is filed under (`contracts/storage.md`).

    A hash rather than the directory itself: the key is what a project's
    guidance is later looked up by, and an absolute user path is precisely what
    FR-051 keeps out of anything a snapshot can carry.
    """
    return sha256(str(lexical_path(project_dir)).encode()).hexdigest()[:16]
