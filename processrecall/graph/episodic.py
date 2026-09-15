"""The episodic index: one private SQLite file, opened per hook invocation.

The Tensor Brain's episodic index, on disk. Every observed step earns a row
here — a time instance with its own identity rather than a timestamp column on
something else — and the abstract graph is an aggregation *of* these rows, never
a second copy of them.

R10 fixes how it is opened: WAL so two projects' sessions writing at once is a
non-event, `synchronous = NORMAL` because the rows are reconstructible by
backfill, and a 2 s busy timeout well inside the hook's 5 s fence. On the hot
path, so the standard library only. `SequenceIdentity.begin` uses `RETURNING`,
which needs SQLite 3.35+; every platform this ships on bundles that or newer.

`SequenceIdentity` holds the epoch that makes clear and fork start a new
sequence (R2) while resume and compact continue the one already running.

Example:
    from processrecall.graph.episodic import open_index

    connection = open_index()
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from processrecall.config import home_dir
from processrecall.graph.store import SequenceKey

#: The one private store (FR-052). Private: it holds snippets and prompts, and
#: nothing crosses from here into a snapshot except through aggregation.
DEFAULT_DATABASE_PATH = home_dir() / "episodes.db"

#: The `SessionStart` sources that begin a fresh chain (R2). Every other
#: source — `startup`, `resume`, `compact`, and any a later harness adds —
#: continues the chain already running, because losing a turn's history to an
#: unrecognised word is the worse of the two wrong answers (FR-012).
NEW_SEQUENCE_SOURCES = frozenset({"clear", "fork"})

#: The shape below, stamped into ``meta`` when the store is created and checked
#: on every open. It changes when a column does, and a store stamped with
#: anything else is refused rather than written to.
SCHEMA_VERSION = "1"

#: What R10 sets on every connection. `journal_mode` is a property of the file
#: and survives; the other three are per-connection and so are re-applied on
#: every open.
_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous  = NORMAL",
    "PRAGMA busy_timeout = 2000",
    "PRAGMA foreign_keys = ON",
)


#: The shape of `contracts/storage.md`, verbatim. `IF NOT EXISTS` throughout so
#: that opening is idempotent: every hook invocation opens the same store, and
#: only the first one finds it empty.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sequences (
    conversation_id  TEXT    NOT NULL,
    session_epoch    INTEGER NOT NULL,
    prompt_id        TEXT    NOT NULL,
    agent_id         TEXT    NOT NULL DEFAULT '',
    project_dir_key  TEXT    NOT NULL,
    process_type     TEXT    NOT NULL DEFAULT 'Unknown',
    status           TEXT    NOT NULL DEFAULT 'open',
    derived_outcome  TEXT    NOT NULL DEFAULT 'neutral',
    declared_outcome TEXT,
    started_at       TEXT    NOT NULL,
    ended_at         TEXT,
    PRIMARY KEY (conversation_id, session_epoch, prompt_id, agent_id)
);

CREATE TABLE IF NOT EXISTS steps (
    step_id         INTEGER PRIMARY KEY,
    dedup_key       TEXT    NOT NULL UNIQUE,
    conversation_id TEXT    NOT NULL,
    session_epoch   INTEGER NOT NULL,
    prompt_id       TEXT    NOT NULL,
    agent_id        TEXT    NOT NULL DEFAULT '',
    position        INTEGER NOT NULL,
    node_key        TEXT    NOT NULL,
    activity_class  TEXT    NOT NULL,
    program         TEXT    NOT NULL DEFAULT '',
    template        TEXT    NOT NULL,
    files           TEXT    NOT NULL DEFAULT '[]',
    result_snippet  TEXT    NOT NULL DEFAULT '',
    outcome         TEXT    NOT NULL DEFAULT 'neutral',
    record_ref      TEXT    NOT NULL DEFAULT '',
    occurred_at     TEXT    NOT NULL,
    rationale_label TEXT,
    symbol_ref      TEXT,
    valid_from      TEXT,
    invalidated_at  TEXT,
    FOREIGN KEY (conversation_id, session_epoch, prompt_id, agent_id)
        REFERENCES sequences (conversation_id, session_epoch, prompt_id, agent_id)
);
CREATE INDEX IF NOT EXISTS steps_by_sequence
    ON steps (conversation_id, session_epoch, prompt_id, agent_id, position);
CREATE INDEX IF NOT EXISTS steps_by_node ON steps (node_key);

CREATE TABLE IF NOT EXISTS annotations (
    annotation_id TEXT PRIMARY KEY,
    edge_key      TEXT NOT NULL,
    project_key   TEXT NOT NULL,
    text          TEXT NOT NULL,
    author        TEXT NOT NULL,
    written_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS annotations_by_edge ON annotations (project_key, edge_key);

CREATE TABLE IF NOT EXISTS epochs (
    conversation_id TEXT PRIMARY KEY,
    session_epoch   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _stored_schema_version(connection: sqlite3.Connection) -> str | None:
    """The version stamped in ``meta``, or ``None`` when the store is new.

    A `meta` table that exists but carries no `schema_version` row is not a
    new store — it is one this build does not recognise — so it is reported
    as an unknown version rather than treated as unstamped and adopted.
    """
    meta = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if meta is None:
        return None
    stamp = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return "<unstamped>" if stamp is None else str(stamp[0])


def open_index(path: Path = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    """Open the episodic index at ``path``, creating it on first use.

    Applies the settings R10 fixes and leaves the store at the shape
    `contracts/storage.md` specifies.

    Raises:
        sqlite3.DatabaseError: The store was written at a schema version this
            build does not understand. Refusing is the point: running the
            current statements against an older or newer shape is how a store
            gets half-migrated by a session that never knew it was migrating.
            Raised as the stdlib type, not a name from ``exceptions.py``: that
            module is reserved for the three failures a caller is expected to
            catch and handle individually (bootstrap, packs, annotations); a
            version refusal is meant to surface and stop the process, the same
            as the corruption ``sqlite3.DatabaseError`` already reports.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    for pragma in _PRAGMAS:
        connection.execute(pragma)
    if (stored := _stored_schema_version(connection)) not in (None, SCHEMA_VERSION):
        connection.close()
        raise sqlite3.DatabaseError(
            f"{path} was written at schema version {stored}; this processrecall understands"
            f" version {SCHEMA_VERSION}. Nothing was read and nothing was written."
        )
    connection.executescript(_SCHEMA)
    with connection:
        connection.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
            " ON CONFLICT (key) DO NOTHING",
            (SCHEMA_VERSION,),
        )
    return connection


class SequenceIdentity:
    """One conversation's epoch, and the keys it stamps on that turn's steps (R2).

    Bound to a conversation because a hook invocation only ever handles one:
    the caller names it once, then asks for keys without carrying the epoch
    around and without spelling the `epochs` table.
    """

    def __init__(self, connection: sqlite3.Connection, conversation_id: str) -> None:
        self._connection = connection
        self._conversation_id = conversation_id
        self._epoch: int | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(conversation_id={self._conversation_id!r})"

    @property
    def epoch(self) -> int:
        """The conversation's current epoch; ``0`` until something rotates it.

        Read once per instance and cached: nothing but `begin` can change it,
        and `begin` updates the cache itself, so a hook stamping many steps in
        one invocation pays one query instead of one per step.
        """
        if self._epoch is None:
            row = self._connection.execute(
                "SELECT session_epoch FROM epochs WHERE conversation_id = ?",
                (self._conversation_id,),
            ).fetchone()
            self._epoch = 0 if row is None else int(row[0])
        return self._epoch

    def begin(self, source: str) -> int:
        """Apply what a `SessionStart` of *source* does to the epoch (FR-012).

        Returns the epoch every subsequent key is stamped with, so a caller
        that wants both the rotation and the identity asks once.
        """
        if source not in NEW_SEQUENCE_SOURCES:
            return self.epoch
        with self._connection:
            row = self._connection.execute(
                "INSERT INTO epochs (conversation_id, session_epoch) VALUES (?, 1)"
                " ON CONFLICT (conversation_id) DO UPDATE SET session_epoch = session_epoch + 1"
                " RETURNING session_epoch",
                (self._conversation_id,),
            ).fetchone()
        self._epoch = int(row[0])
        return self._epoch

    def key(self, prompt_id: str, agent_id: str = "") -> SequenceKey:
        """The identity of the turn *prompt_id* names, at the current epoch.

        ``agent_id`` defaults to the main agent's empty string; passing a
        sub-agent's gives that sub-agent its own chain inside the same turn
        (FR-011).
        """
        return SequenceKey(self._conversation_id, self.epoch, prompt_id, agent_id)
