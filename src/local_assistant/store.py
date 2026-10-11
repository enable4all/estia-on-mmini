"""SQLite storage: conversations, messages, documents, profile, tool calls and state.

One process (the bridge) owns the database; every write goes through this module.
Spec section 4. Full-text search uses FTS5 (external content tables kept in sync by triggers).
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from local_assistant.timefmt import utc_now

Direction = Literal["in", "out"]
MessageKind = Literal["text", "command", "unsupported", "note"]
MessageStatus = Literal["received", "answered", "failed", "ignored", "pending", "sent"]
SourceKind = Literal["chat", "remember", "link"]
ChangeAction = Literal["add", "replace", "remove"]

SCHEMA_VERSION = 6

SCHEMA_V1 = """
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    summary_upto_seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id),
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    peer_aci TEXT NOT NULL,
    signal_ts INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'command', 'unsupported', 'note')),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('received', 'answered', 'failed', 'ignored', 'pending', 'sent')),
    reply_to_id INTEGER REFERENCES messages (id),
    seq INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX messages_conversation ON messages (conversation_id, id);
CREATE INDEX messages_conversation_seq ON messages (conversation_id, seq);
CREATE INDEX messages_direction_status ON messages (direction, status);
CREATE INDEX messages_reply_to ON messages (reply_to_id);
CREATE UNIQUE INDEX messages_incoming_once ON messages (peer_aci, signal_ts) WHERE direction = 'in';

CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    source_message_id INTEGER REFERENCES messages (id),
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX documents_source_message ON documents (source_message_id);

CREATE TABLE profile_facts (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('chat', 'remember', 'link')),
    source_message_id INTEGER REFERENCES messages (id),
    source_document_id INTEGER REFERENCES documents (id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX profile_facts_source_message ON profile_facts (source_message_id);
CREATE INDEX profile_facts_source_document ON profile_facts (source_document_id);

CREATE TABLE profile_changes (
    id INTEGER PRIMARY KEY,
    fact_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('add', 'replace', 'remove')),
    old_text TEXT,
    new_text TEXT,
    source_message_id INTEGER REFERENCES messages (id),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX profile_changes_fact ON profile_changes (fact_id);
CREATE INDEX profile_changes_source_message ON profile_changes (source_message_id);

CREATE TABLE tool_calls (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages (id),
    name TEXT NOT NULL,
    arguments TEXT NOT NULL,
    result TEXT NOT NULL,
    ok INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX tool_calls_message ON tool_calls (message_id);

CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE messages_fts USING fts5 (
    text, content = 'messages', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER messages_fts_update AFTER UPDATE OF text ON messages BEGIN
    INSERT INTO messages_fts (messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO messages_fts (rowid, text) VALUES (new.id, new.text);
END;

CREATE VIRTUAL TABLE documents_fts USING fts5 (
    title, text, content = 'documents', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER documents_fts_insert AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts (rowid, title, text) VALUES (new.id, new.title, new.text);
END;
CREATE TRIGGER documents_fts_delete AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts (documents_fts, rowid, title, text) VALUES ('delete', old.id, old.title, old.text);
END;
CREATE TRIGGER documents_fts_update AFTER UPDATE OF title, text ON documents BEGIN
    INSERT INTO documents_fts (documents_fts, rowid, title, text) VALUES ('delete', old.id, old.title, old.text);
    INSERT INTO documents_fts (rowid, title, text) VALUES (new.id, new.title, new.text);
END;
"""

# v2: fact numbers are never reused (AUTOINCREMENT), so "/forget N" can never hit a newer fact and the
# change log of a removed fact never mixes with a new one. Numbers already in the change log are skipped.
MIGRATION_V2 = """
CREATE TABLE profile_facts_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('chat', 'remember', 'link')),
    source_message_id INTEGER REFERENCES messages (id),
    source_document_id INTEGER REFERENCES documents (id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO profile_facts_v2 (id, text, source_kind, source_message_id, source_document_id, created_at, updated_at)
    SELECT id, text, source_kind, source_message_id, source_document_id, created_at, updated_at FROM profile_facts;
DROP TABLE profile_facts;
ALTER TABLE profile_facts_v2 RENAME TO profile_facts;
CREATE INDEX profile_facts_source_message ON profile_facts (source_message_id);
CREATE INDEX profile_facts_source_document ON profile_facts (source_document_id);
DELETE FROM sqlite_sequence WHERE name = 'profile_facts';
INSERT INTO sqlite_sequence (name, seq) VALUES ('profile_facts', MAX(
    (SELECT COALESCE(MAX(id), 0) FROM profile_facts), (SELECT COALESCE(MAX(fact_id), 0) FROM profile_changes)
));
"""

# v3: photos sent over Signal. The file itself lives in signal-cli's attachment folder only until
# the answer is sent; deleted_at marks that it is gone.
MIGRATION_V3 = """
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages (id),
    signal_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX attachments_message ON attachments (message_id);
"""

# v4: mail headers of the Exchange mailboxes (no bodies), Gemma's label and one-line summary, and the
# Graph delta links per mailbox and folder.
# v5: how each mail was sorted (headers, Jev or Gemma) and Jev's answer beside Gemma's, to measure Jev
# (no mail text: ids, numbers and labels only).
MIGRATION_V5 = """
CREATE TABLE mail_triage (
    mail_id INTEGER PRIMARY KEY REFERENCES mail_messages (id) ON DELETE CASCADE,
    tier TEXT NOT NULL CHECK (tier IN ('headers', 'jev', 'gemma')),
    jev_p REAL,
    gemma_label TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX mail_triage_created ON mail_triage (created_at);
"""

# v6: sorting records kept by date, no longer tied to the mail row (a deleted mail dropped its numbers).
# Correspondents: every address the user sent mail to (To and Cc), kept beyond the 30 days of headers, so their
# mail always reaches Gemma. The Sent folder is read again once to pick up Cc.
MIGRATION_V6 = """
CREATE TABLE mail_triage_v6 (
    mail_id INTEGER PRIMARY KEY,
    tier TEXT NOT NULL CHECK (tier IN ('headers', 'jev', 'gemma')),
    jev_p REAL,
    gemma_label TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO mail_triage_v6 SELECT mail_id, tier, jev_p, gemma_label, created_at, updated_at FROM mail_triage;
DROP TABLE mail_triage;
ALTER TABLE mail_triage_v6 RENAME TO mail_triage;
CREATE INDEX mail_triage_created ON mail_triage (created_at);
CREATE TABLE mail_correspondents (
    address TEXT PRIMARY KEY,
    last_sent_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
DELETE FROM mail_sync WHERE folder = 'sent';
"""

MIGRATION_V4 = """
CREATE TABLE mail_messages (
    id INTEGER PRIMARY KEY,
    account TEXT NOT NULL,
    folder TEXT NOT NULL CHECK (folder IN ('inbox', 'sent')),
    graph_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    internet_message_id TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    sender_address TEXT NOT NULL,
    recipients TEXT NOT NULL,
    subject TEXT NOT NULL,
    received_at TEXT NOT NULL,
    is_read INTEGER NOT NULL,
    has_attachments INTEGER NOT NULL,
    importance TEXT NOT NULL,
    focused INTEGER NOT NULL,
    label TEXT,
    summary TEXT NOT NULL DEFAULT '',
    label_attempts INTEGER NOT NULL DEFAULT 0,
    labelled_at TEXT,
    answered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account, folder, graph_id)
);
CREATE INDEX mail_messages_received ON mail_messages (account, folder, received_at);
CREATE INDEX mail_messages_conversation ON mail_messages (conversation_id);
CREATE VIRTUAL TABLE mail_fts USING fts5 (
    subject, sender_name, sender_address, summary, content = 'mail_messages', content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER mail_fts_insert AFTER INSERT ON mail_messages BEGIN
    INSERT INTO mail_fts (rowid, subject, sender_name, sender_address, summary)
    VALUES (new.id, new.subject, new.sender_name, new.sender_address, new.summary);
END;
CREATE TRIGGER mail_fts_delete AFTER DELETE ON mail_messages BEGIN
    INSERT INTO mail_fts (mail_fts, rowid, subject, sender_name, sender_address, summary)
    VALUES ('delete', old.id, old.subject, old.sender_name, old.sender_address, old.summary);
END;
CREATE TRIGGER mail_fts_update AFTER UPDATE OF subject, sender_name, sender_address, summary ON mail_messages BEGIN
    INSERT INTO mail_fts (mail_fts, rowid, subject, sender_name, sender_address, summary)
    VALUES ('delete', old.id, old.subject, old.sender_name, old.sender_address, old.summary);
    INSERT INTO mail_fts (rowid, subject, sender_name, sender_address, summary)
    VALUES (new.id, new.subject, new.sender_name, new.sender_address, new.summary);
END;
CREATE TABLE mail_sync (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    delta_link TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account, folder)
);
"""


@dataclass(frozen=True)
class Conversation:
    id: int
    started_at: str
    summary: str
    summary_upto_seq: int


@dataclass(frozen=True)
class Message:
    id: int
    conversation_id: int
    direction: Direction
    peer_aci: str
    signal_ts: int | None
    kind: MessageKind
    text: str
    status: MessageStatus
    reply_to_id: int | None
    seq: int | None
    created_at: str


@dataclass(frozen=True)
class Document:
    id: int
    url: str
    title: str
    text: str
    source_message_id: int | None
    fetched_at: str


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    source_kind: SourceKind
    source_message_id: int | None
    source_document_id: int | None
    created_at: str


@dataclass(frozen=True)
class Change:
    id: int
    fact_id: int
    action: ChangeAction
    old_text: str | None
    new_text: str | None
    source_message_id: int | None
    reason: str


@dataclass(frozen=True)
class Attachment:
    id: int
    message_id: int
    signal_id: str
    content_type: str
    size: int


@dataclass(frozen=True)
class ToolCallRecord:
    id: int
    message_id: int
    name: str
    arguments: str
    result: str
    ok: bool
    duration_ms: int


@dataclass(frozen=True)
class Hit:
    """A search result: a message with the messages around it, or a learned page."""

    kind: Literal["message", "document"]
    created_at: str
    conversation_id: int | None = None
    context: tuple[Message, ...] = ()
    document: Document | None = None


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        started_at=row["started_at"],
        summary=row["summary"],
        summary_upto_seq=row["summary_upto_seq"],
    )


MailFolder = Literal["inbox", "sent"]
_REPLY_LABELS = ("urgent_reply", "reply")


@dataclass(frozen=True)
class MailFields:
    """What the sync stores per mail: headers only, never the body."""

    graph_id: str
    conversation_id: str
    internet_message_id: str
    sender_name: str
    sender_address: str
    recipients: str  # comma-separated addresses
    subject: str
    received_at: str  # UTC ISO; the sent time for Sent Items
    is_read: bool
    has_attachments: bool
    importance: str
    focused: bool


@dataclass(frozen=True)
class MailHeader:
    id: int
    account: str
    folder: str
    graph_id: str
    conversation_id: str
    sender_name: str
    sender_address: str
    recipients: str
    subject: str
    received_at: str
    is_read: bool
    has_attachments: bool
    importance: str
    focused: bool
    label: str | None
    summary: str
    label_attempts: int
    answered_at: str | None


def _mail(row: sqlite3.Row) -> MailHeader:
    return MailHeader(
        id=row["id"],
        account=row["account"],
        folder=row["folder"],
        graph_id=row["graph_id"],
        conversation_id=row["conversation_id"],
        sender_name=row["sender_name"],
        sender_address=row["sender_address"],
        recipients=row["recipients"],
        subject=row["subject"],
        received_at=row["received_at"],
        is_read=bool(row["is_read"]),
        has_attachments=bool(row["has_attachments"]),
        importance=row["importance"],
        focused=bool(row["focused"]),
        label=row["label"],
        summary=row["summary"],
        label_attempts=row["label_attempts"],
        answered_at=row["answered_at"],
    )


def _message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        direction=row["direction"],
        peer_aci=row["peer_aci"],
        signal_ts=row["signal_ts"],
        kind=row["kind"],
        text=row["text"],
        status=row["status"],
        reply_to_id=row["reply_to_id"],
        seq=row["seq"],
        created_at=row["created_at"],
    )


def _document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        text=row["text"],
        source_message_id=row["source_message_id"],
        fetched_at=row["fetched_at"],
    )


def _fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        text=row["text"],
        source_kind=row["source_kind"],
        source_message_id=row["source_message_id"],
        source_document_id=row["source_document_id"],
        created_at=row["created_at"],
    )


def _rowid(cursor: sqlite3.Cursor) -> int:
    rowid = cursor.lastrowid
    if rowid is None:
        raise RuntimeError("insert returned no row id")
    return rowid


class Store:
    """The assistant's database. Not thread-safe: use it from the event loop thread only."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._tx_depth = 0

    @classmethod
    def open(cls, path: Path) -> Store:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        conn = sqlite3.connect(path, isolation_level=None)
        path.chmod(0o600)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        store = cls(conn)
        store._migrate()
        return store

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._conn.executescript(f"BEGIN;\n{SCHEMA_V1}\nPRAGMA user_version = 1;\nCOMMIT;")
        if version < 2:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V2}\nPRAGMA user_version = 2;\nCOMMIT;")
        if version < 3:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V3}\nPRAGMA user_version = 3;\nCOMMIT;")
        if version < 4:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V4}\nPRAGMA user_version = 4;\nCOMMIT;")
        if version < 5:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V5}\nPRAGMA user_version = 5;\nCOMMIT;")
        if version < 6:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V6}")  # one transaction with the backfill
            try:
                for row in self._conn.execute(
                    "SELECT recipients, received_at FROM mail_messages WHERE folder = 'sent'"
                ).fetchall():
                    self._add_correspondents(str(row["recipients"]), str(row["received_at"]))
                self._conn.execute("PRAGMA user_version = 6")
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[None]:
        """A write transaction. Nested calls join the outer transaction."""
        if self._tx_depth:
            self._tx_depth += 1
            try:
                yield
            finally:
                self._tx_depth -= 1
            return
        self._conn.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        try:
            yield
        except BaseException:
            self._tx_depth = 0
            self._conn.execute("ROLLBACK")
            raise
        self._tx_depth = 0
        self._conn.execute("COMMIT")

    # conversations

    def current_conversation(self) -> Conversation:
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _conversation(row) if row else self.start_new_conversation()

    def start_new_conversation(self) -> Conversation:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "UPDATE conversations SET ended_at = ?, updated_at = ? WHERE ended_at IS NULL", (now, now)
            )
            cursor = self._conn.execute(
                "INSERT INTO conversations (started_at, created_at, updated_at) VALUES (?, ?, ?)", (now, now, now)
            )
        return Conversation(id=_rowid(cursor), started_at=now, summary="", summary_upto_seq=0)

    def conversation(self, conversation_id: int) -> Conversation:
        row = self._conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            raise KeyError(f"no conversation {conversation_id}")
        return _conversation(row)

    def set_summary(self, conversation_id: int, summary: str, upto_seq: int) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE conversations SET summary = ?, summary_upto_seq = ?, updated_at = ? WHERE id = ?",
                (summary, upto_seq, utc_now(), conversation_id),
            )

    def move_window_start(self, conversation_id: int, upto_seq: int) -> None:
        """Emergency trim: drop messages up to this position from the window; the summary stays as it is."""
        with self.tx():
            self._conn.execute(
                "UPDATE conversations SET summary_upto_seq = ?, updated_at = ? WHERE id = ?",
                (upto_seq, utc_now(), conversation_id),
            )

    # messages

    def add_incoming(
        self,
        conversation_id: int,
        peer_aci: str,
        signal_ts: int,
        kind: MessageKind,
        text: str,
        *,
        defer_seq: bool = False,
    ) -> int | None:
        """Store an incoming message; None if this delivery was already stored (same sender and timestamp).

        defer_seq: the message joins the conversation (gets its seq) only when assign_seq is called, i.e.
        when the bridge starts answering it. Messages that arrive during an answer then come after it.
        """
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO messages (conversation_id, direction, peer_aci, signal_ts, kind, text, status, seq,"
                " created_at, updated_at) VALUES (?, 'in', ?, ?, ?, ?, 'received',"
                " CASE WHEN ? THEN NULL ELSE (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages) END, ?, ?)"
                " ON CONFLICT (peer_aci, signal_ts) WHERE direction = 'in' DO NOTHING",
                (conversation_id, peer_aci, signal_ts, kind, text, defer_seq, now, now),
            )
        return _rowid(cursor) if cursor.rowcount == 1 else None

    def assign_seq(self, message_id: int, conversation_id: int | None = None) -> None:
        """Place a message at the end of the conversation (called when the bridge starts answering it).

        conversation_id: move it into the conversation that is open now (after a /new that was sent
        while this message waited, it belongs to the new conversation).
        """
        with self.tx():
            self._conn.execute(
                "UPDATE messages SET seq = (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages),"
                " conversation_id = COALESCE(?, conversation_id), updated_at = ? WHERE id = ?",
                (conversation_id, utc_now(), message_id),
            )

    def add_outgoing(
        self, conversation_id: int, peer_aci: str, kind: MessageKind, text: str, reply_to_id: int | None
    ) -> int:
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO messages (conversation_id, direction, peer_aci, kind, text, status, reply_to_id, seq,"
                " created_at, updated_at) VALUES (?, 'out', ?, ?, ?, 'pending', ?,"
                " (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages), ?, ?)",
                (conversation_id, peer_aci, kind, text, reply_to_id, now, now),
            )
        return _rowid(cursor)

    def add_call_turn(self, conversation_id: int, peer_aci: str, text: str) -> int:
        """What the user said on a call, at the end of the conversation and already answered: recovery must never
        answer it again by text. Its made-up Signal timestamp (now, or 1 ms after the sender's latest) keeps the
        (sender, timestamp) key unique and is the time Gemma sees."""
        now = utc_now()
        with self.tx():
            latest = self._conn.execute(
                "SELECT MAX(signal_ts) FROM messages WHERE direction = 'in' AND peer_aci = ?", (peer_aci,)
            ).fetchone()[0]
            signal_ts = max(int(time.time() * 1000), (latest or 0) + 1)
            cursor = self._conn.execute(
                "INSERT INTO messages (conversation_id, direction, peer_aci, signal_ts, kind, text, status, seq,"
                " created_at, updated_at) VALUES (?, 'in', ?, ?, 'text', ?, 'answered',"
                " (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages), ?, ?)",
                (conversation_id, peer_aci, signal_ts, text, now, now),
            )
        return _rowid(cursor)

    def add_call_reply(self, conversation_id: int, peer_aci: str, text: str, reply_to_id: int) -> int:
        """What she said on a call: stored as sent (recovery must never send it as a text)."""
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO messages (conversation_id, direction, peer_aci, signal_ts, kind, text, status,"
                " reply_to_id, seq, created_at, updated_at) VALUES (?, 'out', ?, ?, 'text', ?, 'sent', ?,"
                " (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages), ?, ?)",
                (conversation_id, peer_aci, int(time.time() * 1000), text, reply_to_id, now, now),
            )
        return _rowid(cursor)

    def mark_sent(self, message_id: int, signal_ts: int) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE messages SET status = 'sent', signal_ts = ?, updated_at = ? WHERE id = ?",
                (signal_ts, utc_now(), message_id),
            )

    def set_message_text(self, message_id: int, text: str) -> None:
        """Replace a message's text (a voice note's transcript); the search index follows (trigger)."""
        with self.tx():
            self._conn.execute(
                "UPDATE messages SET text = ?, updated_at = ? WHERE id = ?", (text, utc_now(), message_id)
            )

    def set_status(self, message_id: int, status: MessageStatus) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE messages SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), message_id)
            )

    def message(self, message_id: int) -> Message:
        row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise KeyError(f"no message {message_id}")
        return _message(row)

    def window_messages(self, conversation_id: int, after_seq: int) -> list[Message]:
        """The conversation Gemma sees, in conversation order: text messages after the summary point."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND seq IS NOT NULL AND seq > ? AND kind = 'text'"
            " AND ((direction = 'in' AND status != 'ignored') OR (direction = 'out' AND status = 'sent'))"
            " ORDER BY seq",
            (conversation_id, after_seq),
        ).fetchall()
        return [_message(row) for row in rows]

    def unanswered_incoming(self) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE direction = 'in' AND status = 'received' ORDER BY id"
        ).fetchall()
        return [_message(row) for row in rows]

    def unsent_outgoing(self) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE direction = 'out' AND status IN ('pending', 'failed') ORDER BY id"
        ).fetchall()
        return [_message(row) for row in rows]

    def user_messages_after(self, after_id: int, limit: int) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE direction = 'in' AND kind = 'text' AND id > ?"
            " AND text NOT LIKE '[audio file]%' ORDER BY id LIMIT ?",  # someone else's voice: never learned
            (after_id, limit),
        ).fetchall()
        return [_message(row) for row in rows]

    def count_messages(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def _neighbours(self, message: Message, before: int, after: int) -> list[Message]:
        earlier = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND id < ? AND kind = 'text' ORDER BY id DESC LIMIT ?",
            (message.conversation_id, message.id, before),
        ).fetchall()
        later = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND id > ? AND kind = 'text' ORDER BY id LIMIT ?",
            (message.conversation_id, message.id, after),
        ).fetchall()
        return [*(_message(row) for row in reversed(earlier)), message, *(_message(row) for row in later)]

    def search(self, query: str, limit: int = 5, exclude_message_id: int | None = None) -> list[Hit]:
        """Keyword search (any word, best matches first) over messages and learned pages."""
        terms = [term for term in re.findall(r"\w+", query.casefold()) if len(term) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        rows = self._conn.execute(
            "SELECT m.* FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid"
            " WHERE messages_fts MATCH ? AND m.kind = 'text' AND m.id != ?"
            " ORDER BY bm25(messages_fts) LIMIT ?",
            (match, exclude_message_id or 0, limit * 3),
        ).fetchall()
        hits: list[Hit] = []
        covered: set[int] = set()
        for row in rows:
            message = _message(row)
            if message.id in covered:
                continue
            context = tuple(self._neighbours(message, before=2, after=2))
            covered.update(m.id for m in context)
            hits.append(
                Hit(
                    kind="message",
                    created_at=message.created_at,
                    conversation_id=message.conversation_id,
                    context=context,
                )
            )
            if len(hits) >= limit:
                break
        doc_rows = self._conn.execute(
            "SELECT d.* FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid"
            " WHERE documents_fts MATCH ? ORDER BY bm25(documents_fts) LIMIT 2",
            (match,),
        ).fetchall()
        hits.extend(Hit(kind="document", created_at=row["created_at"], document=_document(row)) for row in doc_rows)
        return hits

    # documents

    def add_document(self, url: str, title: str, text: str, source_message_id: int | None) -> int:
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO documents (url, title, text, source_message_id, fetched_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (url, title, text, source_message_id, now, now, now),
            )
        return _rowid(cursor)

    # profile

    def facts(self) -> list[Fact]:
        return [_fact(row) for row in self._conn.execute("SELECT * FROM profile_facts ORDER BY id").fetchall()]

    def insert_fact(
        self, text: str, source_kind: SourceKind, source_message_id: int | None, source_document_id: int | None
    ) -> int:
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO profile_facts (text, source_kind, source_message_id, source_document_id,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (text, source_kind, source_message_id, source_document_id, now, now),
            )
        return _rowid(cursor)

    def update_fact(self, fact_id: int, text: str) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE profile_facts SET text = ?, updated_at = ? WHERE id = ?", (text, utc_now(), fact_id)
            )

    def own_fact(self, fact_id: int, text: str, source_message_id: int | None) -> None:
        """/fix: the fact in the user's own words, from now on theirs ("remember")."""
        with self.tx():
            self._conn.execute(
                "UPDATE profile_facts SET text = ?, source_kind = 'remember', source_message_id = ?,"
                " source_document_id = NULL, updated_at = ? WHERE id = ?",
                (text, source_message_id, utc_now(), fact_id),
            )

    def delete_fact(self, fact_id: int) -> None:
        with self.tx():
            self._conn.execute("DELETE FROM profile_facts WHERE id = ?", (fact_id,))

    def log_change(
        self,
        fact_id: int,
        action: ChangeAction,
        old_text: str | None,
        new_text: str | None,
        source_message_id: int | None,
        reason: str,
    ) -> None:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "INSERT INTO profile_changes (fact_id, action, old_text, new_text, source_message_id, reason,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (fact_id, action, old_text, new_text, source_message_id, reason, now, now),
            )

    def blank_changes(self, fact_id: int) -> None:
        """Forget: remove a fact's text from the change log (the log keeps that something changed)."""
        with self.tx():
            self._conn.execute(
                "UPDATE profile_changes SET old_text = NULL, new_text = NULL, reason = '(forgotten)',"
                " updated_at = ? WHERE fact_id = ?",
                (utc_now(), fact_id),
            )

    def changes(self, fact_id: int) -> list[Change]:
        rows = self._conn.execute("SELECT * FROM profile_changes WHERE fact_id = ? ORDER BY id", (fact_id,)).fetchall()
        return [
            Change(
                id=row["id"],
                fact_id=row["fact_id"],
                action=row["action"],
                old_text=row["old_text"],
                new_text=row["new_text"],
                source_message_id=row["source_message_id"],
                reason=row["reason"],
            )
            for row in rows
        ]

    # tool calls

    def log_tool_call(
        self, message_id: int, name: str, arguments: str, result: str, ok: bool, duration_ms: int
    ) -> None:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "INSERT INTO tool_calls (message_id, name, arguments, result, ok, duration_ms, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, name, arguments, result, int(ok), duration_ms, now, now),
            )

    def tool_calls(self, message_id: int | None = None) -> list[ToolCallRecord]:
        if message_id is None:
            rows = self._conn.execute("SELECT * FROM tool_calls ORDER BY id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tool_calls WHERE message_id = ? ORDER BY id", (message_id,)
            ).fetchall()
        return [
            ToolCallRecord(
                id=row["id"],
                message_id=row["message_id"],
                name=row["name"],
                arguments=row["arguments"],
                result=row["result"],
                ok=bool(row["ok"]),
                duration_ms=row["duration_ms"],
            )
            for row in rows
        ]

    # attachments

    def add_attachment(self, message_id: int, signal_id: str, content_type: str, size: int) -> int:
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO attachments (message_id, signal_id, content_type, size, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, signal_id, content_type, size, now, now),
            )
        return _rowid(cursor)

    def attachments(self, message_id: int) -> list[Attachment]:
        """The message's attachments that still exist."""
        rows = self._conn.execute(
            "SELECT * FROM attachments WHERE message_id = ? AND deleted_at IS NULL ORDER BY id", (message_id,)
        ).fetchall()
        return [
            Attachment(
                id=row["id"],
                message_id=row["message_id"],
                signal_id=row["signal_id"],
                content_type=row["content_type"],
                size=row["size"],
            )
            for row in rows
        ]

    # mail (headers, labels, delta links)

    def upsert_mail(self, account: str, folder: MailFolder, mail: MailFields) -> int:
        """Insert or refresh a mail's headers; Gemma's label and summary are kept."""
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "INSERT INTO mail_messages (account, folder, graph_id, conversation_id, internet_message_id,"
                " sender_name, sender_address, recipients, subject, received_at, is_read, has_attachments,"
                " importance, focused, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (account, folder, graph_id) DO UPDATE SET conversation_id = excluded.conversation_id,"
                " internet_message_id = excluded.internet_message_id, sender_name = excluded.sender_name,"
                " sender_address = excluded.sender_address, recipients = excluded.recipients,"
                " subject = excluded.subject, received_at = excluded.received_at, is_read = excluded.is_read,"
                " has_attachments = excluded.has_attachments, importance = excluded.importance,"
                " focused = excluded.focused, updated_at = excluded.updated_at",
                (
                    account,
                    folder,
                    mail.graph_id,
                    mail.conversation_id,
                    mail.internet_message_id,
                    mail.sender_name,
                    mail.sender_address.lower(),
                    mail.recipients.lower(),
                    mail.subject,
                    mail.received_at,
                    int(mail.is_read),
                    int(mail.has_attachments),
                    mail.importance,
                    int(mail.focused),
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT id FROM mail_messages WHERE account = ? AND folder = ? AND graph_id = ?",
                (account, folder, mail.graph_id),
            ).fetchone()
            if folder == "sent":  # everyone the user mails (To and Cc) stays a correspondent
                self._add_correspondents(mail.recipients, mail.received_at)
        return int(row["id"])

    def remove_mail(self, account: str, folder: MailFolder, graph_id: str) -> bool:
        with self.tx():
            cursor = self._conn.execute(
                "DELETE FROM mail_messages WHERE account = ? AND folder = ? AND graph_id = ?",
                (account, folder, graph_id),
            )
        return cursor.rowcount > 0

    def mail(self, mail_id: int) -> MailHeader:
        row = self._conn.execute("SELECT * FROM mail_messages WHERE id = ?", (mail_id,)).fetchone()
        if row is None:
            raise KeyError(mail_id)
        return _mail(row)

    def mail_by_graph_id(self, account: str, graph_id: str) -> MailHeader | None:
        row = self._conn.execute(
            "SELECT * FROM mail_messages WHERE account = ? AND graph_id = ? ORDER BY folder LIMIT 1",
            (account, graph_id),
        ).fetchone()
        return _mail(row) if row else None

    def count_mail(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0])

    def update_answered(self, account: str) -> int:
        """Mark Inbox mail answered by a later Sent Items message in the same conversation; returns the
        number of mails whose state changed."""
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "UPDATE mail_messages SET answered_at = (SELECT MIN(s.received_at) FROM mail_messages AS s"
                " WHERE s.account = mail_messages.account AND s.folder = 'sent' AND s.conversation_id != ''"
                " AND s.conversation_id = mail_messages.conversation_id AND s.received_at > mail_messages.received_at),"
                " updated_at = ? WHERE account = ? AND folder = 'inbox' AND answered_at IS NOT (SELECT MIN(s.received_at)"
                " FROM mail_messages AS s WHERE s.account = mail_messages.account AND s.folder = 'sent'"
                " AND s.conversation_id != '' AND s.conversation_id = mail_messages.conversation_id"
                " AND s.received_at > mail_messages.received_at)",
                (now, account),
            )
        return cursor.rowcount

    def prune_mail(self, before: str) -> int:
        with self.tx():
            cursor = self._conn.execute("DELETE FROM mail_messages WHERE received_at < ?", (before,))
        return cursor.rowcount

    def mail_delta_link(self, account: str, folder: MailFolder) -> str | None:
        row = self._conn.execute(
            "SELECT delta_link FROM mail_sync WHERE account = ? AND folder = ?", (account, folder)
        ).fetchone()
        return str(row["delta_link"]) if row else None

    def set_mail_delta_link(self, account: str, folder: MailFolder, link: str) -> None:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "INSERT INTO mail_sync (account, folder, delta_link, synced_at, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT (account, folder) DO UPDATE SET delta_link = excluded.delta_link,"
                " synced_at = excluded.synced_at, updated_at = excluded.updated_at",
                (account, folder, link, now, now, now),
            )

    def mail_synced_at(self, account: str) -> str | None:
        row = self._conn.execute("SELECT MIN(synced_at) AS at FROM mail_sync WHERE account = ?", (account,)).fetchone()
        return str(row["at"]) if row and row["at"] else None

    def clear_mail_sync(self, account: str) -> None:
        """Forget the delta links (a full re-sync follows), e.g. after Graph answers 410 Gone."""
        with self.tx():
            self._conn.execute("DELETE FROM mail_sync WHERE account = ?", (account,))

    def unlabelled_mail(self, limit: int, account: str | None = None) -> list[MailHeader]:
        """Unlabelled Inbox mail, newest first (of one mailbox when `account` is given)."""
        rows = self._conn.execute(
            "SELECT * FROM mail_messages WHERE folder = 'inbox' AND label IS NULL AND (? IS NULL OR account = ?)"
            " ORDER BY received_at DESC LIMIT ?",
            (account, account, limit),
        ).fetchall()
        return [_mail(row) for row in rows]

    def set_mail_label(self, mail_id: int, label: str, summary: str, graph_id: str | None = None) -> bool:
        """Label a mail; with `graph_id` only if the row is still that mail (the sync may have removed it meanwhile and
        SQLite may have given its number to a newer mail). False when nothing was labelled."""
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "UPDATE mail_messages SET label = ?, summary = ?, labelled_at = ?, updated_at = ?"
                " WHERE id = ? AND (? IS NULL OR graph_id = ?)",
                (label, summary, now, now, mail_id, graph_id, graph_id),
            )
        return cursor.rowcount == 1

    def record_triage(self, mail_id: int, tier: str, jev_p: float | None, gemma_label: str | None) -> None:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "INSERT INTO mail_triage (mail_id, tier, jev_p, gemma_label, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (mail_id) DO UPDATE SET tier = excluded.tier,"
                " jev_p = excluded.jev_p, gemma_label = excluded.gemma_label, created_at = excluded.created_at,"
                " updated_at = excluded.updated_at",
                (mail_id, tier, jev_p, gemma_label, now, now),
            )

    def mail_triage(self, mail_id: int) -> tuple[str, float | None, str | None] | None:
        row = self._conn.execute(
            "SELECT tier, jev_p, gemma_label FROM mail_triage WHERE mail_id = ?", (mail_id,)
        ).fetchone()
        return None if row is None else (str(row["tier"]), row["jev_p"], row["gemma_label"])

    def triage_since(self, since: str) -> list[tuple[str, float | None, str | None]]:
        rows = self._conn.execute(
            "SELECT tier, jev_p, gemma_label FROM mail_triage WHERE created_at >= ?", (since,)
        ).fetchall()
        return [(str(row["tier"]), row["jev_p"], row["gemma_label"]) for row in rows]

    def has_written_to(self, address: str) -> bool:
        """Whether the user ever sent mail to this address (To or Cc), also after the headers were pruned."""
        wanted = address.strip().lower()
        if not wanted:
            return False
        row = self._conn.execute("SELECT 1 FROM mail_correspondents WHERE address = ?", (wanted,)).fetchone()
        return row is not None

    def in_users_thread(self, conversation_id: str) -> bool:
        """Whether the user sent mail in this conversation (a reply in it may come from someone new)."""
        if not conversation_id:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM mail_messages WHERE folder = 'sent' AND conversation_id = ? LIMIT 1", (conversation_id,)
        ).fetchone()
        return row is not None

    def prune_triage(self, before: str) -> int:
        with self.tx():
            cursor = self._conn.execute("DELETE FROM mail_triage WHERE created_at < ?", (before,))
        return cursor.rowcount

    def _add_correspondents(self, recipients: str, at: str) -> None:
        now = utc_now()
        for address in {part.strip().lower() for part in recipients.split(",") if "@" in part}:
            self._conn.execute(
                "INSERT INTO mail_correspondents (address, last_sent_at, created_at, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (address) DO UPDATE SET last_sent_at = max(last_sent_at, excluded.last_sent_at),"
                " updated_at = excluded.updated_at",
                (address, at, now, now),
            )

    def bump_mail_attempts(self, mail_id: int, graph_id: str | None = None) -> int:
        """One more failed try; 0 when the mail is gone (with `graph_id`: or its number now belongs to another)."""
        with self.tx():
            self._conn.execute(
                "UPDATE mail_messages SET label_attempts = label_attempts + 1, updated_at = ?"
                " WHERE id = ? AND (? IS NULL OR graph_id = ?)",
                (utc_now(), mail_id, graph_id, graph_id),
            )
            row = self._conn.execute(
                "SELECT label_attempts FROM mail_messages WHERE id = ? AND (? IS NULL OR graph_id = ?)",
                (mail_id, graph_id, graph_id),
            ).fetchone()
        return 0 if row is None else int(row["label_attempts"])

    def mail_needing_reply(self, since: str) -> list[MailHeader]:
        """Unanswered Inbox mail labelled urgent_reply or reply since `since`: urgent first, then oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM mail_messages WHERE folder = 'inbox' AND label IN (?, ?) AND answered_at IS NULL"
            " AND received_at >= ? ORDER BY label = 'urgent_reply' DESC, received_at",
            (*_REPLY_LABELS, since),
        ).fetchall()
        return [_mail(row) for row in rows]

    def mail_actions(self, since: str, floor: str) -> list[MailHeader]:
        """Inbox mail labelled "action" after `since` (sorting may come after the digest), received after `floor`."""
        rows = self._conn.execute(
            "SELECT * FROM mail_messages WHERE folder = 'inbox' AND label = 'action' AND labelled_at > ?"
            " AND received_at >= ? ORDER BY received_at",
            (since, floor),
        ).fetchall()
        return [_mail(row) for row in rows]

    def unlabelled_from_known(self, since: str) -> list[MailHeader]:
        """Unlabelled, unanswered Inbox mail from people the user has written to (shown with its subject)."""
        rows = self._conn.execute(
            "SELECT * FROM mail_messages AS m WHERE m.folder = 'inbox' AND m.label IS NULL AND m.answered_at IS NULL"
            " AND m.received_at >= ? AND EXISTS (SELECT 1 FROM mail_messages AS s WHERE s.account = m.account"
            " AND s.folder = 'sent' AND instr(',' || s.recipients || ',', ',' || m.sender_address || ',') > 0)"
            " ORDER BY m.received_at",
            (since,),
        ).fetchall()
        return [_mail(row) for row in rows]

    def search_mail(
        self, words: str, *, account: str | None = None, since: str = "", limit: int = 10
    ) -> list[MailHeader]:
        """Any of the words in sender, subject or summary (Inbox and Sent), best matches first."""
        terms = [term for term in re.findall(r"\w+", words.casefold()) if len(term) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        rows = self._conn.execute(
            "SELECT m.* FROM mail_fts JOIN mail_messages AS m ON m.id = mail_fts.rowid WHERE mail_fts MATCH ?"
            " AND (? IS NULL OR m.account = ?) AND m.received_at >= ? ORDER BY bm25(mail_fts) LIMIT ?",
            (match, account, account, since, limit),
        ).fetchall()
        return [_mail(row) for row in rows]

    def open_attachment_ids(self) -> set[str]:
        """signal-cli file ids of photos not yet deleted (their messages still wait for an answer)."""
        rows = self._conn.execute("SELECT signal_id FROM attachments WHERE deleted_at IS NULL").fetchall()
        return {str(row["signal_id"]) for row in rows}

    def mark_attachment_deleted(self, attachment_id: int) -> None:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "UPDATE attachments SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, attachment_id)
            )

    # state

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_state(self, key: str, value: str) -> None:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "INSERT INTO state (key, value, created_at, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, now, now),
            )

    def db_size_bytes(self) -> int:
        pages = self._conn.execute("PRAGMA page_count").fetchone()[0]
        size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        return int(pages) * int(size)
