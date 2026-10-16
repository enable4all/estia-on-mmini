"""SQLite storage: conversations, messages, documents, profile, tool calls and state.

One process (the bridge) owns the database; every write goes through this module.
Spec section 4. Full-text search uses FTS5 (external content tables kept in sync by triggers).
"""

from __future__ import annotations

import itertools
import math
import re
import sqlite3
import time
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from local_assistant.timefmt import utc_now

Direction = Literal["in", "out"]
MessageKind = Literal["text", "command", "unsupported", "note"]
MessageStatus = Literal["received", "answered", "failed", "ignored", "pending", "sent"]
SourceKind = Literal["chat", "remember", "link"]
ChangeAction = Literal["add", "replace", "remove"]
ReminderRepeat = Literal["none", "daily", "weekdays", "weekly"]
ReminderStatus = Literal["open", "firing", "done", "cancelled", "failed"]
ReminderSource = Literal["chat", "command"]
FollowUpStatus = Literal["open", "sent", "done", "cancelled", "expired"]
InitiativeKind = Literal["followup", "calendar", "early_start", "lookback", "improvise", "announce", "evening"]
InitiativeOutcome = Literal["sending", "sent", "unknown", "skipped", "failed"]
ConversationKind = Literal["chat", "practice"]
DocSource = Literal["folder", "signal"]  # found in the documents folder, or saved there from a Signal message
COUNTED: tuple[InitiativeOutcome, ...] = ("sending", "sent", "unknown")  # what counts toward the caps

SCHEMA_VERSION = 13

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

# v7: reminders and wake-up calls the user set, the check-ins Estia may send (follow-ups) and the log of the messages
# she sends first (metadata only), in one migration so the initiative part needs no schema change of its own.
# messages.footer: a code line sent after a message she starts; never in Gemma's history or the history search.
MIGRATION_V7 = """
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY,
    peer_aci TEXT NOT NULL,
    text TEXT NOT NULL,
    local_time TEXT NOT NULL,
    tz TEXT NOT NULL,
    due_at REAL NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'none' CHECK (repeat IN ('none', 'daily', 'weekdays', 'weekly')),
    call INTEGER NOT NULL DEFAULT 0 CHECK (call IN (0, 1)),
    source TEXT NOT NULL CHECK (source IN ('chat', 'command')),
    source_message_id INTEGER REFERENCES messages (id),
    status TEXT NOT NULL CHECK (status IN ('open', 'firing', 'done', 'cancelled', 'failed')),
    message_id INTEGER REFERENCES messages (id),
    fired INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX reminders_due ON reminders (status, due_at);
CREATE INDEX reminders_source ON reminders (source_message_id);
CREATE INDEX reminders_message ON reminders (message_id);

CREATE TABLE followups (
    id INTEGER PRIMARY KEY,
    peer_aci TEXT NOT NULL,
    what TEXT NOT NULL,
    source_message_id INTEGER NOT NULL REFERENCES messages (id),
    due_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'sent', 'done', 'cancelled', 'expired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX followups_due ON followups (status, due_at);
CREATE INDEX followups_source ON followups (source_message_id);

CREATE TABLE initiative_log (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('followup', 'calendar', 'early_start', 'lookback', 'improvise', 'announce')),
    local_day TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('sending', 'sent', 'unknown', 'skipped', 'failed')),
    reason TEXT,
    message_id INTEGER REFERENCES messages (id),
    replied_at REAL,
    quieted_at REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX initiative_log_day ON initiative_log (local_day);
CREATE INDEX initiative_log_message ON initiative_log (message_id);

ALTER TABLE messages ADD COLUMN footer TEXT;
"""

# v8: language practice (/babble) runs in a conversation of its own kind: never the current chat, never summarised,
# skipped by learning, check-ins, the look back and the history search. Nothing else is stored for it.
MIGRATION_V8 = """
ALTER TABLE conversations ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat', 'practice'));
"""
# the practice conversations' ids, for the queries that leave practice turns out
_NOT_PRACTICE = "conversation_id NOT IN (SELECT id FROM conversations WHERE kind = 'practice')"

# v9: the user's devices (an ACI and a Signal device id: 1 is their phone, 2 and up are linked devices): known from the
# start (the phone), allowed with /devices ok N, or held (seen, not allowed: no answer); last_seen: its last message
# (an allowed device silent too long is held again). An incoming message keeps the device that sent it. The lock
# (/lock) lives in the state table (lock:on, lock:device, lock:since).
MIGRATION_V9 = """
CREATE TABLE known_devices (
    aci TEXT NOT NULL,
    device INTEGER NOT NULL CHECK (device >= 1),
    first_seen TEXT NOT NULL,
    last_seen TEXT,
    source TEXT NOT NULL CHECK (source IN ('primary', 'allowed', 'held')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (aci, device)
);
ALTER TABLE messages ADD COLUMN source_device INTEGER;
"""

# v10: documents ([documents]): the PDFs of the documents folder, keyed by their path in it, size and change time
# (an unchanged file is never read twice), with why one could not be read and the pages skipped; each page's text in a
# full-text index. AUTOINCREMENT: a number /docs shows is never reused. An attachment keeps the name it was sent with
# (a PDF the user sends is saved under it).
MIGRATION_V10 = """
CREATE TABLE doc_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    skipped TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('folder', 'signal')),
    announced INTEGER NOT NULL DEFAULT 0,
    found_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE doc_pages (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES doc_files (id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    ocr INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (file_id, page)
);
CREATE VIRTUAL TABLE doc_pages_fts USING fts5 (
    text, content = 'doc_pages', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER doc_pages_fts_insert AFTER INSERT ON doc_pages BEGIN
    INSERT INTO doc_pages_fts (rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER doc_pages_fts_delete AFTER DELETE ON doc_pages BEGIN
    INSERT INTO doc_pages_fts (doc_pages_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER doc_pages_fts_update AFTER UPDATE OF text ON doc_pages BEGIN
    INSERT INTO doc_pages_fts (doc_pages_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO doc_pages_fts (rowid, text) VALUES (new.id, new.text);
END;
ALTER TABLE attachments ADD COLUMN filename TEXT NOT NULL DEFAULT '';
"""

# v12: the reader that read each file (documents.INDEX_VERSION): a fix to the reader changes neither a file's size nor
# its change time, so a file an older reader did not read whole is read again. Rows from before, and rows an older
# release writes after a rollback (its insert names no reader), are reader 1: the reader before Foundation's home was
# set (15 Oct), under which scanned pages were skipped in her own user.
MIGRATION_V12 = """
ALTER TABLE doc_files ADD COLUMN reader INTEGER NOT NULL DEFAULT 1;
"""

# v13: the profile in two parts (owner, 16 Oct): a fact moved out of the user's part to make room is kept in her
# notes with its origin (source_kind) as it was, so "you asked" stays "you asked", learning still never changes it, and
# a fact from a page stays out of the messages she starts. 0 for every fact before: none had been moved.
MIGRATION_V13 = """
ALTER TABLE profile_facts ADD COLUMN in_notes INTEGER NOT NULL DEFAULT 0;
"""

# v11: the evening note (/evening) in the initiative log, as kind 'evening'. SQLite cannot change a CHECK, so the table
# is made again with it: every row and its id kept, its indexes too. Nothing refers to the log, so nothing else moves.
MIGRATION_V11 = """
CREATE TABLE initiative_log_v11 (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (
        kind IN ('followup', 'calendar', 'early_start', 'lookback', 'improvise', 'announce', 'evening')
    ),
    local_day TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('sending', 'sent', 'unknown', 'skipped', 'failed')),
    reason TEXT,
    message_id INTEGER REFERENCES messages (id),
    replied_at REAL,
    quieted_at REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO initiative_log_v11 (
    id, kind, local_day, outcome, reason, message_id, replied_at, quieted_at, created_at, updated_at
)
SELECT id, kind, local_day, outcome, reason, message_id, replied_at, quieted_at, created_at, updated_at
FROM initiative_log;
DROP TABLE initiative_log;
ALTER TABLE initiative_log_v11 RENAME TO initiative_log;
CREATE INDEX initiative_log_day ON initiative_log (local_day);
CREATE INDEX initiative_log_message ON initiative_log (message_id);
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
    kind: ConversationKind = "chat"  # "practice": a /babble session's own conversation


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
    footer: str | None = None  # a code line sent after the text (a message she starts); never in Gemma's history
    source_device: int | None = None  # incoming: the sender's device that sent it (1: their phone); None: not named


DeviceSource = Literal["primary", "allowed", "held"]


@dataclass(frozen=True)
class KnownDevice:
    """One of a user's Signal devices: known from the start (the phone), allowed with /devices ok N, or held."""

    device: int
    source: DeviceSource
    first_seen: str
    last_seen: str | None = None  # its last message (or when it was allowed)


@dataclass(frozen=True)
class Pruned:
    """What one retention pass deleted (counts only)."""

    messages: int
    documents: int
    tool_calls: int


# tool calls whose results are outside text (mail, calendar, web, maps, pages, project files, documents, the history)
OUTSIDE_TOOLS = (
    "read_link",
    "learn_link",
    "search_history",
    "web_search",
    "places_search",
    "route",
    "search_documents",
    "read_document",
)
OUTSIDE_TOOL_PREFIXES = ("mail_", "calendar_", "project_")


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
    in_notes: bool = False  # moved out of the user's part into her notes, its origin kept (schema v13)


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
    filename: str = ""  # the name it was sent with ("" when Signal named none)


@dataclass(frozen=True)
class DocPage:
    """One page of a document, as the documents helper read it (`ocr`: Vision read a page without text)."""

    n: int
    text: str
    ocr: bool


@dataclass(frozen=True)
class DocFile:
    """A PDF of the documents folder in her index: its path in the folder, size and change time (the key), the pages
    read, the pages skipped with why, why it could not be read at all ("" when it was), and the reader that read it
    (documents.INDEX_VERSION; 1 for rows from before v12)."""

    id: int
    path: str
    size: int
    mtime_ns: int
    pages: int
    skipped: tuple[tuple[int, str], ...]
    error: str
    source: DocSource
    announced: bool
    found_at: str
    reader: int = 1


@dataclass(frozen=True)
class DocHit:
    """A passage the documents search found: the document, the page and the words around the match."""

    file_id: int
    path: str
    page: int
    passage: str


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


@dataclass(frozen=True)
class Reminder:
    """A reminder the user set: the local time as meant and its zone, and the UTC instant of the next occurrence."""

    id: int
    peer_aci: str
    text: str  # the user's words
    local_time: str  # 'YYYY-MM-DD HH:MM' of the next occurrence, as meant
    tz: str  # IANA zone when it was set
    due_at: float  # UTC epoch seconds of the next occurrence
    repeat: ReminderRepeat
    call: bool
    source: ReminderSource
    source_message_id: int | None
    status: ReminderStatus
    message_id: int | None  # the note of the current firing (the delivery fence)
    fired: int
    outcome: str | None  # a fixed word (reminders.OUTCOMES)
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FollowUp:
    """A check-in she may send on something the user said they would do (written by the initiative part)."""

    id: int
    peer_aci: str
    what: str
    source_message_id: int
    due_at: float
    expires_at: float
    status: FollowUpStatus


@dataclass(frozen=True)
class InitiativeEntry:
    """One message she started, or one she skipped: metadata only, never text."""

    id: int
    kind: InitiativeKind
    local_day: str  # 'YYYY-MM-DD' in the user's zone
    outcome: InitiativeOutcome
    reason: str | None  # a fixed word (initiative.REASONS)
    message_id: int | None
    replied_at: float | None  # the user's first message within 24 h
    quieted_at: float | None  # a /quiet within 48 h
    created_at: str


def _entry(row: sqlite3.Row) -> InitiativeEntry:
    return InitiativeEntry(
        id=row["id"],
        kind=row["kind"],
        local_day=row["local_day"],
        outcome=row["outcome"],
        reason=row["reason"],
        message_id=row["message_id"],
        replied_at=row["replied_at"],
        quieted_at=row["quieted_at"],
        created_at=row["created_at"],
    )


def _reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        peer_aci=row["peer_aci"],
        text=row["text"],
        local_time=row["local_time"],
        tz=row["tz"],
        due_at=float(row["due_at"]),
        repeat=row["repeat"],
        call=bool(row["call"]),
        source=row["source"],
        source_message_id=row["source_message_id"],
        status=row["status"],
        message_id=row["message_id"],
        fired=row["fired"],
        outcome=row["outcome"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _followup(row: sqlite3.Row) -> FollowUp:
    return FollowUp(
        id=row["id"],
        peer_aci=row["peer_aci"],
        what=row["what"],
        source_message_id=row["source_message_id"],
        due_at=float(row["due_at"]),
        expires_at=float(row["expires_at"]),
        status=row["status"],
    )


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        started_at=row["started_at"],
        summary=row["summary"],
        summary_upto_seq=row["summary_upto_seq"],
        kind=row["kind"],
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
        footer=row["footer"],
        source_device=row["source_device"],
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


def _doc_file(row: sqlite3.Row) -> DocFile:
    skipped: list[tuple[int, str]] = []
    for item in str(row["skipped"]).split(","):
        number, _, why = item.partition(":")
        if number.isdecimal() and why:
            skipped.append((int(number), why))
    return DocFile(
        id=row["id"],
        path=row["path"],
        size=row["size"],
        mtime_ns=row["mtime_ns"],
        pages=row["pages"],
        skipped=tuple(skipped),
        error=row["error"],
        source=row["source"],
        announced=bool(row["announced"]),
        found_at=row["found_at"],
        reader=row["reader"],
    )


COMMON_MIN_PAGES = 10  # from this many pages on, a word on more than half of them says nothing in a documents search
QUERY_WORDS = 12  # a documents search's words at most (each is one index lookup)
RANKED_PAGES = 1_000  # bm25's order is read for this many pages at most (the last tie-break)


def fold(text: str) -> str:
    """Lower case without accents, for comparing words in Python (names, notes). Never for the index's MATCH: its
    unicode61 tokenizer removes accents from Latin letters only, so a folded Greek, Cyrillic й or kana word finds
    nothing there."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def name_words(path: str) -> set[str]:
    """A document's name as words (its file name without .pdf; folders not), folded; numbers left out (the date a
    sent PDF's name starts with, "2026-10-14 lease.pdf", names nothing)."""
    return {word for word in re.findall(r"[^\W_]+", fold(Path(path).stem)) if not word.isdecimal()}


def _rarity(holding: int, out_of: int) -> float:
    """BM25's weight for a word `holding` of `out_of` pages (or names) have: rare words weigh most, never below 0."""
    return math.log(1 + (out_of - holding + 0.5) / (holding + 0.5))


def _query_words(query: str) -> list[str]:
    """A documents search's words as typed (the index folds them as it folded the pages): two characters or more,
    or a number ("article 7"); each once (by its folded form), in order, at most `QUERY_WORDS`."""
    seen: dict[str, str] = {}
    for word in re.findall(r"[^\W_]+", query):
        if (len(word) > 1 or word.isdecimal()) and fold(word) not in seen:
            seen[fold(word)] = word
    return list(seen.values())[:QUERY_WORDS]


def _in(column: str, ids: Sequence[int]) -> str:
    """` AND column IN (?, …)` for that many ids (never an empty list: `IN ()` is no match)."""
    return f" AND {column} IN ({', '.join('?' * len(ids))})" if ids else " AND 0"


def _fts_terms(query: str) -> str:
    """Any of the query's words (two letters or more), each quoted: never FTS5's own syntax from the caller."""
    terms = [term for term in re.findall(r"\w+", query.casefold()) if len(term) > 1]
    return " OR ".join(f'"{term}"' for term in terms)


def _fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        text=row["text"],
        source_kind=row["source_kind"],
        source_message_id=row["source_message_id"],
        source_document_id=row["source_document_id"],
        created_at=row["created_at"],
        in_notes=bool(row["in_notes"]),
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
        conn.execute("PRAGMA secure_delete = ON")  # a deleted row's bytes are overwritten, not just unlinked
        store = cls(conn)
        store._migrate()
        return store

    @classmethod
    def open_read_only(cls, path: Path) -> Store:
        """The database, only read (the terminal draft, doctor): through the write-ahead log while the bridge runs,
        as an unchanging file while it is stopped (no log), so nothing is created next to it. Never migrated."""
        mode = "mode=ro" if path.with_name(path.name + "-wal").exists() else "mode=ro&immutable=1"
        conn = sqlite3.connect(f"{path.as_uri()}?{mode}", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return cls(conn)

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
        if version < 7:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V7}\nPRAGMA user_version = 7;\nCOMMIT;")
        if version < 8:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V8}\nPRAGMA user_version = 8;\nCOMMIT;")
        if version < 9:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V9}\nPRAGMA user_version = 9;\nCOMMIT;")
        if version < 10:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V10}\nPRAGMA user_version = 10;\nCOMMIT;")
        if version < 11:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V11}\nPRAGMA user_version = 11;\nCOMMIT;")
        if version < 12:
            self._conn.executescript(f"BEGIN;\n{MIGRATION_V12}\nPRAGMA user_version = 12;\nCOMMIT;")
        if version < 13:  # the column only where it is missing (a database copied back to an older version has it)
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(profile_facts)")}
            script = MIGRATION_V13 if "in_notes" not in columns else ""
            self._conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = 13;\nCOMMIT;")

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
        """The open chat conversation (a practice conversation is never the current one)."""
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE ended_at IS NULL AND kind = 'chat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _conversation(row) if row else self.start_new_conversation()

    def start_new_conversation(self) -> Conversation:
        """A fresh chat conversation (/new): the open chat ends; a running practice conversation stays open."""
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "UPDATE conversations SET ended_at = ?, updated_at = ? WHERE ended_at IS NULL AND kind = 'chat'",
                (now, now),
            )
            cursor = self._conn.execute(
                "INSERT INTO conversations (started_at, created_at, updated_at) VALUES (?, ?, ?)", (now, now, now)
            )
        return Conversation(id=_rowid(cursor), started_at=now, summary="", summary_upto_seq=0)

    def start_practice_conversation(self) -> Conversation:
        """A practice session's own conversation (/babble); the chat conversation stays open and current."""
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO conversations (started_at, kind, created_at, updated_at) VALUES (?, 'practice', ?, ?)",
                (now, now, now),
            )
        return Conversation(id=_rowid(cursor), started_at=now, summary="", summary_upto_seq=0, kind="practice")

    def end_conversation(self, conversation_id: int) -> None:
        now = utc_now()
        with self.tx():
            self._conn.execute(
                "UPDATE conversations SET ended_at = ?, updated_at = ? WHERE id = ? AND ended_at IS NULL",
                (now, now, conversation_id),
            )

    def user_texts(self, conversation_id: int) -> list[Message]:
        """Every text the user sent in this conversation (typed, spoken, on a call; not ignored ones), in conversation
        order, with no window cut: a practice session's recap reads them all."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND direction = 'in' AND kind = 'text'"
            " AND status != 'ignored' AND seq IS NOT NULL ORDER BY seq",
            (conversation_id,),
        ).fetchall()
        return [_message(row) for row in rows]

    def last_user_ts(self, conversation_id: int) -> int | None:
        """The Signal time (ms) of the user's newest text in this conversation (a practice session's idle clock)."""
        row = self._conn.execute(
            "SELECT MAX(signal_ts) FROM messages WHERE conversation_id = ? AND direction = 'in' AND kind = 'text'",
            (conversation_id,),
        ).fetchone()
        return None if row[0] is None else int(row[0])

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
        device: int | None = None,
    ) -> int | None:
        """Store an incoming message; None if this delivery was already stored (same sender and timestamp).

        defer_seq: the message joins the conversation (gets its seq) only when assign_seq is called, i.e.
        when the bridge starts answering it. Messages that arrive during an answer then come after it.
        device: the sender's device that sent it (a command such as /unlock is checked against it).
        """
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO messages (conversation_id, direction, peer_aci, signal_ts, kind, text, status, seq,"
                " source_device, created_at, updated_at) VALUES (?, 'in', ?, ?, ?, ?, 'received',"
                " CASE WHEN ? THEN NULL ELSE (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages) END, ?, ?, ?)"
                " ON CONFLICT (peer_aci, signal_ts) WHERE direction = 'in' DO NOTHING",
                (conversation_id, peer_aci, signal_ts, kind, text, defer_seq, device, now, now),
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
        self,
        conversation_id: int,
        peer_aci: str,
        kind: MessageKind,
        text: str,
        reply_to_id: int | None,
        *,
        footer: str | None = None,
    ) -> int:
        """An outgoing message, pending until sent. `footer`: a code line sent after the text (a message she starts),
        kept apart so it never reaches Gemma's history or the history search."""
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO messages (conversation_id, direction, peer_aci, kind, text, status, reply_to_id, seq,"
                " footer, created_at, updated_at) VALUES (?, 'out', ?, ?, ?, 'pending', ?,"
                " (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages), ?, ?, ?)",
                (conversation_id, peer_aci, kind, text, reply_to_id, footer, now, now),
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
        """The user's texts after `after_id` for idle learning and the check-ins: never an audio file (someone else's
        voice) or a practice turn (a roleplay in another language, never a fact or a plan)."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE direction = 'in' AND kind = 'text' AND id > ?"
            f" AND text NOT LIKE '[audio file]%' AND {_NOT_PRACTICE} ORDER BY id LIMIT ?",
            (after_id, limit),
        ).fetchall()
        return [_message(row) for row in rows]

    def newest_message_id(self) -> int:
        return int(self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0])

    def count_messages(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def last_incoming_ts(self) -> int | None:
        """The Signal time (ms) of the newest message from any user (text, command, call turn)."""
        row = self._conn.execute("SELECT MAX(signal_ts) FROM messages WHERE direction = 'in'").fetchone()
        return None if row[0] is None else int(row[0])

    def first_reply_after(self, peer_aci: str, message_id: int) -> Message | None:
        """This user's first text (typed, spoken or on a call) stored after the message `message_id`."""
        row = self._conn.execute(
            "SELECT * FROM messages WHERE direction = 'in' AND kind = 'text' AND peer_aci = ? AND id > ?"
            " ORDER BY id LIMIT 1",
            (peer_aci, message_id),
        ).fetchone()
        return _message(row) if row else None

    def user_messages_since(self, peer_aci: str, since_ms: int, limit: int = 500) -> list[Message]:
        """This user's own texts (typed, voice notes, call turns; never audio files or practice turns) sent at or after
        `since_ms`."""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE direction = 'in' AND kind = 'text' AND peer_aci = ? AND signal_ts >= ?"
            f" AND text NOT LIKE '[audio file]%' AND {_NOT_PRACTICE} ORDER BY signal_ts, id LIMIT ?",
            (peer_aci, since_ms, limit),
        ).fetchall()
        return [_message(row) for row in rows]

    def count_user_texts(self, peer_aci: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE direction = 'in' AND kind = 'text' AND peer_aci = ?"
                " AND text NOT LIKE '[audio file]%'",
                (peer_aci,),
            ).fetchone()[0]
        )

    def first_user_text_ts(self, peer_aci: str) -> int | None:
        row = self._conn.execute(
            "SELECT MIN(signal_ts) FROM messages WHERE direction = 'in' AND kind = 'text' AND peer_aci = ?",
            (peer_aci,),
        ).fetchone()
        return None if row[0] is None else int(row[0])

    def count_quiet_commands(self, *, since_ms: int) -> int:
        """How often /quiet (not /quiet off) was sent since `since_ms` (the initiative's "goes well" counts)."""
        rows = self._conn.execute(
            "SELECT text FROM messages WHERE direction = 'in' AND kind = 'command' AND signal_ts >= ?"
            " AND lower(text) LIKE '/quiet%'",
            (since_ms,),
        ).fetchall()
        return sum(1 for row in rows if str(row["text"]).strip().lower() == "/quiet")

    def wrote_after(self, peer_aci: str, message_id: int) -> bool:
        """Whether this user sent anything (text, command, call turn) after the message `message_id` was stored."""
        row = self._conn.execute(
            "SELECT 1 FROM messages WHERE direction = 'in' AND peer_aci = ? AND id > ? LIMIT 1", (peer_aci, message_id)
        ).fetchone()
        return row is not None

    def _neighbours(self, message: Message, before: int, after: int) -> list[Message]:
        earlier = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND id < ? AND kind = 'text'"
            f" AND {_NOT_PRACTICE} ORDER BY id DESC LIMIT ?",
            (message.conversation_id, message.id, before),
        ).fetchall()
        later = self._conn.execute(
            f"SELECT * FROM messages WHERE conversation_id = ? AND id > ? AND kind = 'text' AND {_NOT_PRACTICE}"
            " ORDER BY id LIMIT ?",
            (message.conversation_id, message.id, after),
        ).fetchall()
        return [*(_message(row) for row in reversed(earlier)), message, *(_message(row) for row in later)]

    def search(self, query: str, limit: int = 5, exclude_message_id: int | None = None) -> list[Hit]:
        """Keyword search (any word, best matches first) over messages and learned pages; never a practice turn (the
        roleplay of a /babble session never reaches her normal history)."""
        terms = [term for term in re.findall(r"\w+", query.casefold()) if len(term) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        rows = self._conn.execute(
            "SELECT m.* FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid"
            " WHERE messages_fts MATCH ? AND m.kind = 'text' AND m.id != ?"
            " AND m.conversation_id NOT IN (SELECT id FROM conversations WHERE kind = 'practice')"
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
                "UPDATE profile_facts SET text = ?, source_kind = 'remember', in_notes = 0, source_message_id = ?,"
                " source_document_id = NULL, updated_at = ? WHERE id = ?",
                (text, source_message_id, utc_now(), fact_id),
            )

    def demote_fact(self, fact_id: int) -> None:
        """A user's fact moved into her notes (their part of the profile was full): kept with its origin, no longer
        always sent."""
        with self.tx():
            self._conn.execute(
                "UPDATE profile_facts SET in_notes = 1, updated_at = ? WHERE id = ?", (utc_now(), fact_id)
            )

    def promote_fact(self, fact_id: int, source_message_id: int | None) -> None:
        """A note the user asked to remember: from now on theirs ("remember"), in their part."""
        with self.tx():
            self._conn.execute(
                "UPDATE profile_facts SET source_kind = 'remember', in_notes = 0, source_message_id = ?,"
                " source_document_id = NULL, updated_at = ? WHERE id = ?",
                (source_message_id, utc_now(), fact_id),
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

    def add_attachment(
        self, message_id: int, signal_id: str, content_type: str, size: int, *, filename: str = ""
    ) -> int:
        now = utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO attachments (message_id, signal_id, content_type, size, filename, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, signal_id, content_type, size, filename, now, now),
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
                filename=row["filename"],
            )
            for row in rows
        ]

    # documents (the folder's PDFs and their pages; [documents])

    def put_doc_file(
        self,
        path: str,
        size: int,
        mtime_ns: int,
        pages: list[DocPage],
        *,
        page_count: int,
        skipped: tuple[tuple[int, str], ...] = (),
        error: str = "",
        source: DocSource = "folder",
        announced: bool = False,
        found_at: str | None = None,
        reader: int = 1,
    ) -> int:
        """A file read (or not readable: `error`) and its pages, in one transaction; a file already indexed at this
        path keeps its number, source, when it was found and whether it was announced, and gets its new text.
        `reader`: the reader that read it (documents.INDEX_VERSION)."""
        now = utc_now()
        skipped_text = ",".join(f"{number}:{why}" for number, why in skipped)
        with self.tx():
            row = self._conn.execute("SELECT id FROM doc_files WHERE path = ?", (path,)).fetchone()
            if row is None:
                cursor = self._conn.execute(
                    "INSERT INTO doc_files (path, size, mtime_ns, pages, skipped, error, source, announced, found_at,"
                    " reader, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        path,
                        size,
                        mtime_ns,
                        page_count,
                        skipped_text,
                        error,
                        source,
                        int(announced),
                        found_at or now,
                        reader,
                        now,
                        now,
                    ),
                )
                file_id = _rowid(cursor)
            else:
                file_id = int(row["id"])
                self._conn.execute("DELETE FROM doc_pages WHERE file_id = ?", (file_id,))
                self._conn.execute(
                    "UPDATE doc_files SET size = ?, mtime_ns = ?, pages = ?, skipped = ?, error = ?, reader = ?,"
                    " updated_at = ? WHERE id = ?",
                    (size, mtime_ns, page_count, skipped_text, error, reader, now, file_id),
                )
            self._conn.executemany(
                "INSERT INTO doc_pages (file_id, page, ocr, text, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(file_id, page.n, int(page.ocr), page.text, now, now) for page in pages],
            )
            if row is not None:
                self._conn.execute("INSERT INTO doc_pages_fts (doc_pages_fts) VALUES ('optimize')")
        return file_id

    def doc_files(self) -> list[DocFile]:
        """Every file in the index, newest first (when she first found or saved it)."""
        rows = self._conn.execute("SELECT * FROM doc_files ORDER BY found_at DESC, id DESC").fetchall()
        return [_doc_file(row) for row in rows]

    def doc_file(self, file_id: int) -> DocFile | None:
        row = self._conn.execute("SELECT * FROM doc_files WHERE id = ?", (file_id,)).fetchone()
        return _doc_file(row) if row is not None else None

    def doc_file_by_path(self, path: str) -> DocFile | None:
        row = self._conn.execute("SELECT * FROM doc_files WHERE path = ?", (path,)).fetchone()
        return _doc_file(row) if row is not None else None

    def doc_page(self, file_id: int, page: int) -> DocPage | None:
        row = self._conn.execute(
            "SELECT page, text, ocr FROM doc_pages WHERE file_id = ? AND page = ?", (file_id, page)
        ).fetchone()
        return DocPage(row["page"], row["text"], bool(row["ocr"])) if row is not None else None

    def remove_doc_files(self, file_ids: list[int]) -> None:
        """Files gone from the folder: their rows and pages, then the index merged so no word of theirs stays."""
        if not file_ids:
            return
        with self.tx():
            for file_id in file_ids:
                self._conn.execute("DELETE FROM doc_pages WHERE file_id = ?", (file_id,))
                self._conn.execute("DELETE FROM doc_files WHERE id = ?", (file_id,))
            self._conn.execute("INSERT INTO doc_pages_fts (doc_pages_fts) VALUES ('optimize')")

    def clear_documents(self) -> int:
        """A fresh index (the share was revoked, or a new share began): every file and page goes; how many files."""
        with self.tx():
            count = int(self._conn.execute("SELECT count(*) FROM doc_files").fetchone()[0])
            self._conn.execute("DELETE FROM doc_pages")
            self._conn.execute("DELETE FROM doc_files")
            self._conn.execute("INSERT INTO doc_pages_fts (doc_pages_fts) VALUES ('optimize')")
        return count

    def search_doc_pages(self, query: str, limit: int = 6, *, file_ids: Sequence[int] | None = None) -> list[DocHit]:
        """Pages matching the query's words, best first, each with the words around its best match (owner, 16 Oct:
        "section B5 from the Practice Exam" found the syllabus first). Each word goes to the index as typed, so FTS5
        reads it as it read the pages (unicode61 keeps Greek accents, й and voiced kana: folding them here found
        nothing). In order:
        - a word on more than half the pages ("the", "from") says nothing, from `COMMON_MIN_PAGES` pages on (each
          word's pages are counted first; only the others are read);
        - the query words in some names and not all name the documents whose names hold the most of them, when
          those all hold the same ones ("practice exam"): their pages get the words' weight and the words are not
          looked for in the text; one word each of different names ("insurance", "deductible") names nothing;
        - each other word adds to the pages that hold it, the more the fewer pages do ("b5"), and two of them side
          by side on a page ("article 7") add their weight again;
        - ties: more query words in the document's name, then the name with the fewest other words (the exam
          before "… Exam - Answer Sheet"), then FTS5's bm25.
        `file_ids`: only these documents' pages (no naming then)."""
        terms = _query_words(query)
        if not terms:
            return []
        scope, args = ("", ()) if file_ids is None else (_in("p.file_id", file_ids), tuple(file_ids))
        join = "FROM doc_pages_fts JOIN doc_pages p ON p.id = doc_pages_fts.rowid WHERE doc_pages_fts MATCH ?"
        total = self._conn.execute(f"SELECT count(*) FROM doc_pages p WHERE 1 = 1{scope}", args).fetchone()[0]
        held = {t: self._conn.execute(f"SELECT count(*) {join}{scope}", (f'"{t}"', *args)).fetchone()[0] for t in terms}
        present = [t for t in terms if held[t]]
        if not present:
            return []
        common = {t for t in present if total >= COMMON_MIN_PAGES and held[t] * 2 > total}
        rare = [t for t in present if t not in common]
        names = {} if file_ids is not None else self._doc_name_words()
        # every query word in some names (also one no page holds: "insurance" of "Car insurance policy.pdf")
        name_words = {
            t for t in terms if t not in common and 0 < sum(fold(t) in words for words in names.values()) < len(names)
        }
        # the names holding the most of those words name the document, when they all hold the same ones ("Practice
        # Exam": the exam and its answer sheet; a stray "from" of "Lecture 2 - From telemetry" changes nothing); one
        # word each of different names ("insurance", "deductible") names nothing
        held_in = {fid: frozenset(t for t in name_words if fold(t) in words) for fid, words in names.items()}
        most = max((len(found) for found in held_in.values()), default=0)
        leaders = {found for found in held_in.values() if most and len(found) == most}
        naming = set(next(iter(leaders))) if len(leaders) == 1 else set()
        content = [t for t in rare if t not in naming] or rare or present  # only common words: they are what is left
        on: dict[str, dict[int, int]] = {}  # word -> {page row id: its file id}
        for term in content:
            rows = self._conn.execute(f"SELECT p.id, p.file_id {join}{scope}", (f'"{term}"', *args)).fetchall()
            on[term] = {row[0]: row[1] for row in rows}
        weight = {t: _rarity(held[t], total) for t in content}
        boost = {t: _rarity(sum(fold(t) in words for words in names.values()), len(names)) for t in naming}
        file_of: dict[int, int] = {}
        for term in content:
            file_of.update(on[term])
        together: dict[int, float] = {}  # two words side by side ("article 7"): their weight again
        for first, second in itertools.pairwise(content):
            phrase = self._conn.execute(f"SELECT p.id {join}{scope}", (f'"{first} {second}"', *args)).fetchall()
            for (page_id,) in phrase:
                together[page_id] = together.get(page_id, 0.0) + weight[first] + weight[second]
        match = " OR ".join(f'"{t}"' for t in content)
        ranked = self._conn.execute(
            f"SELECT p.id {join}{scope} ORDER BY bm25(doc_pages_fts) LIMIT {RANKED_PAGES}", (match, *args)
        )
        rank = {row[0]: n for n, row in enumerate(ranked)}
        asked = {fold(t) for t in terms}

        def order(page_id: int) -> tuple[float, int, int, int]:
            words = names.get(file_of[page_id], set())
            found = sum(weight[t] for t in content if page_id in on[t]) + together.get(page_id, 0.0)
            found += sum(boost[t] for t in naming if fold(t) in words)
            return (-round(found, 9), -len(asked & words), len(words - asked), rank.get(page_id, RANKED_PAGES))

        hits: list[DocHit] = []
        for page_id in sorted(file_of, key=order)[:limit]:
            # the passage around the page's rarest words ("B5"), not around a frequent one ("from") near its top
            mine = [t for t in content if page_id in on[t]]
            best = max(weight[t] for t in mine)
            here = " OR ".join(f'"{t}"' for t in mine if weight[t] * 2 >= best)
            row = self._conn.execute(
                "SELECT p.file_id, f.path, p.page, snippet(doc_pages_fts, 0, '', '', ' … ', 64) AS passage"
                " FROM doc_pages_fts JOIN doc_pages p ON p.id = doc_pages_fts.rowid JOIN doc_files f ON f.id = p.file_id"
                " WHERE doc_pages_fts MATCH ? AND doc_pages_fts.rowid = ?",
                (here, page_id),
            ).fetchone()
            if row is not None:
                hits.append(DocHit(row["file_id"], row["path"], row["page"], row["passage"]))
        return hits

    def _doc_name_words(self) -> dict[int, set[str]]:
        """Each read document's name words (file id -> words), as `name_words` gives them."""
        rows = self._conn.execute("SELECT id, path FROM doc_files WHERE error = ''").fetchall()
        return {row["id"]: name_words(row["path"]) for row in rows}

    def unannounced_doc_files(self) -> list[DocFile]:
        """Files found in the folder that no note named yet, oldest first."""
        rows = self._conn.execute("SELECT * FROM doc_files WHERE announced = 0 ORDER BY found_at, id").fetchall()
        return [_doc_file(row) for row in rows]

    def mark_doc_files_announced(self, file_ids: list[int]) -> None:
        now = utc_now()
        with self.tx():
            self._conn.executemany(
                "UPDATE doc_files SET announced = 1, updated_at = ? WHERE id = ?", [(now, i) for i in file_ids]
            )

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

    # reminders (the times come from the caller's clock: `at` is a UTC ISO string)

    def add_reminder(
        self,
        peer_aci: str,
        text: str,
        local_time: str,
        tz: str,
        due_at: float,
        *,
        repeat: ReminderRepeat = "none",
        call: bool = False,
        source: ReminderSource = "chat",
        source_message_id: int | None = None,
        at: str | None = None,
    ) -> int:
        now = at or utc_now()
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO reminders (peer_aci, text, local_time, tz, due_at, repeat, call, source, source_message_id,"
                " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (peer_aci, text, local_time, tz, due_at, repeat, int(call), source, source_message_id, now, now),
            )
        return _rowid(cursor)

    def reminder(self, reminder_id: int) -> Reminder | None:
        row = self._conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return _reminder(row) if row else None

    def open_reminders(self, peer_aci: str | None = None) -> list[Reminder]:
        """Open and firing reminders (of one user, or everyone's), soonest first."""
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE status IN ('open', 'firing') AND (? IS NULL OR peer_aci = ?)"
            " ORDER BY due_at, id",
            (peer_aci, peer_aci),
        ).fetchall()
        return [_reminder(row) for row in rows]

    def due_reminders(self, now: float) -> list[Reminder]:
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE status = 'open' AND due_at <= ? ORDER BY due_at, id", (now,)
        ).fetchall()
        return [_reminder(row) for row in rows]

    def firing_reminders(self) -> list[Reminder]:
        rows = self._conn.execute("SELECT * FROM reminders WHERE status = 'firing' ORDER BY due_at, id").fetchall()
        return [_reminder(row) for row in rows]

    def next_reminder_due(self) -> float | None:
        row = self._conn.execute("SELECT MIN(due_at) FROM reminders WHERE status = 'open'").fetchone()
        return None if row[0] is None else float(row[0])

    def count_reminders_created(self, peer_aci: str, *, since: str) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE peer_aci = ? AND created_at >= ?", (peer_aci, since)
            ).fetchone()[0]
        )

    def reminders_with_outcome(self, peer_aci: str | None, outcomes: tuple[str, ...], *, since: str) -> list[Reminder]:
        """Reminders whose last firing ended with one of these outcomes, at or after `since` (UTC ISO)."""
        marks = ", ".join("?" for _ in outcomes)
        rows = self._conn.execute(
            f"SELECT * FROM reminders WHERE outcome IN ({marks}) AND updated_at >= ?"
            " AND (? IS NULL OR peer_aci = ?) ORDER BY updated_at, id",
            (*outcomes, since, peer_aci, peer_aci),
        ).fetchall()
        return [_reminder(row) for row in rows]

    def start_firing(self, reminder_id: int, *, at: str | None = None) -> None:
        """A call reminder is being taken up (its note comes once the call's checks are done): never picked up twice."""
        with self.tx():
            self._conn.execute(
                "UPDATE reminders SET status = 'firing', message_id = NULL, updated_at = ? WHERE id = ?",
                (at or utc_now(), reminder_id),
            )

    def reopen_reminder(self, reminder_id: int, *, at: str | None = None) -> None:
        """Back to open: one that was taken up but sent nothing before the bridge stopped."""
        with self.tx():
            self._conn.execute(
                "UPDATE reminders SET status = 'open', message_id = NULL, updated_at = ? WHERE id = ?",
                (at or utc_now(), reminder_id),
            )

    def fire_reminder(self, reminder_id: int, message_id: int, *, at: str | None = None) -> None:
        """The note of this firing is stored: the reminder is firing until it is known whether the note went out."""
        with self.tx():
            self._conn.execute(
                "UPDATE reminders SET status = 'firing', message_id = ?, updated_at = ? WHERE id = ?",
                (message_id, at or utc_now(), reminder_id),
            )

    def reschedule_reminder(
        self, reminder_id: int, local_time: str, due_at: float, *, outcome: str, at: str | None = None
    ) -> None:
        """A repeating reminder fired: open again for its next occurrence."""
        with self.tx():
            self._conn.execute(
                "UPDATE reminders SET status = 'open', message_id = NULL, fired = fired + 1, local_time = ?,"
                " due_at = ?, outcome = ?, updated_at = ? WHERE id = ?",
                (local_time, due_at, outcome, at or utc_now(), reminder_id),
            )

    def finish_reminder(
        self, reminder_id: int, status: Literal["done", "failed"], outcome: str, *, at: str | None = None
    ) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE reminders SET status = ?, fired = fired + 1, outcome = ?, updated_at = ? WHERE id = ?",
                (status, outcome, at or utc_now(), reminder_id),
            )

    def cancel_reminders(self, peer_aci: str, reminder_id: int | None, *, at: str | None = None) -> list[Reminder]:
        """Cancel one of this user's open (or firing) reminders, or all of them (`reminder_id` None); the cancelled."""
        with self.tx():
            rows = self._conn.execute(
                "SELECT * FROM reminders WHERE peer_aci = ? AND status IN ('open', 'firing') AND (? IS NULL OR id = ?)"
                " ORDER BY due_at, id",
                (peer_aci, reminder_id, reminder_id),
            ).fetchall()
            self._conn.executemany(
                "UPDATE reminders SET status = 'cancelled', updated_at = ? WHERE id = ?",
                [(at or utc_now(), row["id"]) for row in rows],
            )
        return [_reminder(row) for row in rows]

    def prune_reminders(self, *, before: str) -> int:
        """Done and cancelled reminders are deleted after a while (the caller's `before`, UTC ISO)."""
        with self.tx():
            cursor = self._conn.execute(
                "DELETE FROM reminders WHERE status IN ('done', 'cancelled') AND updated_at < ?", (before,)
            )
        return cursor.rowcount

    def reminder_sources(self, message_ids: list[int]) -> set[int]:
        """Which of these messages set a reminder (also one cancelled since): they never become a follow-up."""
        if not message_ids:
            return set()
        marks = ", ".join("?" for _ in message_ids)
        rows = self._conn.execute(
            f"SELECT DISTINCT source_message_id FROM reminders WHERE source_message_id IN ({marks})", message_ids
        ).fetchall()
        return {int(row[0]) for row in rows}

    # follow-ups (written by the initiative part; listed and cancelled with /reminders)

    def add_followup(
        self, peer_aci: str, what: str, source_message_id: int, due_at: float, expires_at: float, *, at: str
    ) -> int:
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO followups (peer_aci, what, source_message_id, due_at, expires_at, status, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                (peer_aci, what, source_message_id, due_at, expires_at, at, at),
            )
        return _rowid(cursor)

    def followup(self, followup_id: int) -> FollowUp | None:
        row = self._conn.execute("SELECT * FROM followups WHERE id = ?", (followup_id,)).fetchone()
        return _followup(row) if row else None

    def due_followups(self, now: float) -> list[FollowUp]:
        rows = self._conn.execute(
            "SELECT * FROM followups WHERE status = 'open' AND due_at <= ? ORDER BY due_at, id", (now,)
        ).fetchall()
        return [_followup(row) for row in rows]

    def all_open_followups(self) -> list[FollowUp]:
        rows = self._conn.execute("SELECT * FROM followups WHERE status = 'open' ORDER BY due_at, id").fetchall()
        return [_followup(row) for row in rows]

    def followup_sources(self) -> set[int]:
        """The messages that already gave a follow-up (open or not): one message, one check-in at most."""
        rows = self._conn.execute("SELECT DISTINCT source_message_id FROM followups").fetchall()
        return {int(row[0]) for row in rows}

    def set_followup(
        self, followup_id: int, *, status: FollowUpStatus | None = None, due_at: float | None = None, at: str
    ) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE followups SET status = COALESCE(?, status), due_at = COALESCE(?, due_at), updated_at = ?"
                " WHERE id = ?",
                (status, due_at, at, followup_id),
            )

    def prune_followups(self, *, before: str) -> int:
        """Follow-ups no longer open are deleted after a while (the caller's `before`, UTC ISO)."""
        with self.tx():
            cursor = self._conn.execute("DELETE FROM followups WHERE status != 'open' AND updated_at < ?", (before,))
        return cursor.rowcount

    def open_followups(self, peer_aci: str) -> list[FollowUp]:
        rows = self._conn.execute(
            "SELECT * FROM followups WHERE peer_aci = ? AND status = 'open' ORDER BY due_at, id", (peer_aci,)
        ).fetchall()
        return [_followup(row) for row in rows]

    def cancel_followups(self, peer_aci: str, followup_id: int | None, *, at: str | None = None) -> list[FollowUp]:
        with self.tx():
            rows = self._conn.execute(
                "SELECT * FROM followups WHERE peer_aci = ? AND status = 'open' AND (? IS NULL OR id = ?)"
                " ORDER BY due_at, id",
                (peer_aci, followup_id, followup_id),
            ).fetchall()
            self._conn.executemany(
                "UPDATE followups SET status = 'cancelled', updated_at = ? WHERE id = ?",
                [(at or utc_now(), row["id"]) for row in rows],
            )
        return [_followup(row) for row in rows]

    # the initiative log: the messages she starts, metadata only (never text)

    def log_initiative(
        self,
        kind: InitiativeKind,
        local_day: str,
        outcome: InitiativeOutcome,
        *,
        reason: str | None = None,
        message_id: int | None = None,
        at: str,
    ) -> int:
        with self.tx():
            cursor = self._conn.execute(
                "INSERT INTO initiative_log (kind, local_day, outcome, reason, message_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, local_day, outcome, reason, message_id, at, at),
            )
        return _rowid(cursor)

    def set_initiative_outcome(
        self, entry_id: int, outcome: InitiativeOutcome, *, reason: str | None = None, at: str
    ) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE initiative_log SET outcome = ?, reason = COALESCE(?, reason), updated_at = ? WHERE id = ?",
                (outcome, reason, at, entry_id),
            )

    def initiative_entry(self, entry_id: int) -> InitiativeEntry | None:
        row = self._conn.execute("SELECT * FROM initiative_log WHERE id = ?", (entry_id,)).fetchone()
        return _entry(row) if row else None

    def initiative_entries(
        self,
        *,
        since: str = "",
        kinds: tuple[InitiativeKind, ...] | None = None,
        outcomes: tuple[InitiativeOutcome, ...] | None = None,
    ) -> list[InitiativeEntry]:
        """Log rows created at or after `since` (UTC ISO), oldest first."""
        sql = "SELECT * FROM initiative_log WHERE created_at >= ?"
        args: list[object] = [since]
        for column, values in (("kind", kinds), ("outcome", outcomes)):
            if values is not None:
                sql += f" AND {column} IN ({', '.join('?' for _ in values)})"
                args += values
        rows = self._conn.execute(sql + " ORDER BY id", args).fetchall()
        return [_entry(row) for row in rows]

    def count_initiative(
        self,
        first_day: str,
        last_day: str,
        *,
        kind: InitiativeKind | None = None,
        outcomes: tuple[InitiativeOutcome, ...] = COUNTED,
    ) -> int:
        """Messages she started (sent, being sent or maybe sent; or with `outcomes`, rows with those outcomes) on the
        local days first_day..last_day."""
        marks = ", ".join("?" for _ in outcomes)
        return int(
            self._conn.execute(
                f"SELECT COUNT(*) FROM initiative_log WHERE local_day BETWEEN ? AND ? AND outcome IN ({marks})"
                " AND (? IS NULL OR kind = ?)",
                (first_day, last_day, *outcomes, kind, kind),
            ).fetchone()[0]
        )

    def set_initiative_replied(self, entry_id: int, replied_at: float, *, at: str) -> None:
        with self.tx():
            self._conn.execute(
                "UPDATE initiative_log SET replied_at = ?, updated_at = ? WHERE id = ?", (replied_at, at, entry_id)
            )

    def set_initiative_quieted(self, entry_ids: list[int], quieted_at: float, *, at: str) -> None:
        with self.tx():
            self._conn.executemany(
                "UPDATE initiative_log SET quieted_at = ?, updated_at = ? WHERE id = ? AND quieted_at IS NULL",
                [(quieted_at, at, entry_id) for entry_id in entry_ids],
            )

    def prune_initiative(self, *, before: str) -> int:
        """The log is kept for a year (the caller's `before`, UTC ISO)."""
        with self.tx():
            cursor = self._conn.execute("DELETE FROM initiative_log WHERE created_at < ?", (before,))
        return cursor.rowcount

    def outside_seqs(self, conversation_id: int) -> list[tuple[int, int]]:
        """The incoming messages of a conversation (id, seq) whose answer read outside text (a tool in OUTSIDE_TOOLS or
        with an OUTSIDE_TOOL_PREFIXES name) or that are outside text themselves (an audio file, a photo)."""
        prefixes = " OR ".join("t.name LIKE ? ESCAPE '\\'" for _ in OUTSIDE_TOOL_PREFIXES)
        names = ", ".join("?" for _ in OUTSIDE_TOOLS)
        rows = self._conn.execute(
            "SELECT m.id, m.seq FROM messages m WHERE m.conversation_id = ? AND m.direction = 'in'"
            " AND m.seq IS NOT NULL AND (m.text LIKE '[audio file]%' OR m.text LIKE '[photo]%'"
            f" OR EXISTS (SELECT 1 FROM tool_calls t WHERE t.message_id = m.id AND (t.name IN ({names}) OR {prefixes})))"
            " ORDER BY m.seq",
            (conversation_id, *OUTSIDE_TOOLS, *(prefix.replace("_", "\\_") + "%" for prefix in OUTSIDE_TOOL_PREFIXES)),
        ).fetchall()
        return [(int(row["id"]), int(row["seq"])) for row in rows]

    # the user's devices

    def devices(self, aci: str) -> list[KnownDevice]:
        rows = self._conn.execute(
            "SELECT device, source, first_seen, last_seen FROM known_devices WHERE aci = ? ORDER BY device", (aci,)
        ).fetchall()
        return [
            KnownDevice(int(row["device"]), row["source"], str(row["first_seen"]), row["last_seen"]) for row in rows
        ]

    def device(self, aci: str, device: int) -> KnownDevice | None:
        return next((item for item in self.devices(aci) if item.device == device), None)

    def device_source(self, aci: str, device: int) -> DeviceSource | None:
        row = self._conn.execute(
            "SELECT source FROM known_devices WHERE aci = ? AND device = ?", (aci, device)
        ).fetchone()
        return None if row is None else row["source"]

    def set_device(self, aci: str, device: int, source: DeviceSource, *, at: str | None = None) -> None:
        """Add a device, or change how it is known; when it was first seen stays."""
        now = at or utc_now()
        with self.tx():
            self._conn.execute(
                "INSERT INTO known_devices (aci, device, first_seen, source, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (aci, device) DO UPDATE SET source = excluded.source,"
                " updated_at = excluded.updated_at",
                (aci, device, now, source, now, now),
            )

    def touch_device(self, aci: str, device: int, at: str) -> None:
        """When the device last sent a message (or was allowed)."""
        with self.tx():
            self._conn.execute(
                "UPDATE known_devices SET last_seen = ?, updated_at = ? WHERE aci = ? AND device = ?",
                (at, utc_now(), aci, device),
            )

    def forget_device(self, aci: str, device: int) -> bool:
        """Remove a device (/devices forget N): a message from that number is held again. Whether it was there."""
        with self.tx():
            gone = self._conn.execute("DELETE FROM known_devices WHERE aci = ? AND device = ?", (aci, device))
        return gone.rowcount > 0

    # retention ([privacy] keep_days)

    def prune(self, keep_days: int, *, now: float | None = None) -> Pruned:
        """Delete messages, pages and tool calls older than `keep_days`, with their search rows (the FTS triggers),
        then merge the search indexes so no deleted word stays in them. Kept: a message an open reminder, an open
        check-in or the initiative log points to (those rows are never touched); the profile stays, its link to a
        deleted message or page is cleared, and so is a finished reminder's. An ended conversation with no message
        left goes, with its summary. Compaction (`compact`) is separate: it blocks the database for a moment."""
        before = datetime.fromtimestamp((time.time() if now is None else now) - keep_days * 86_400, UTC).isoformat(
            timespec="seconds"
        )
        with self.tx():
            self._conn.execute("CREATE TEMP TABLE IF NOT EXISTS pruned_messages (id INTEGER PRIMARY KEY)")
            self._conn.execute("DELETE FROM pruned_messages")
            self._conn.execute(
                "INSERT INTO pruned_messages SELECT id FROM messages WHERE created_at < ?"
                " AND id NOT IN (SELECT source_message_id FROM reminders WHERE status IN ('open', 'firing')"
                "   AND source_message_id IS NOT NULL)"
                " AND id NOT IN (SELECT message_id FROM reminders WHERE status IN ('open', 'firing')"
                "   AND message_id IS NOT NULL)"
                " AND id NOT IN (SELECT source_message_id FROM followups WHERE status = 'open')"
                " AND id NOT IN (SELECT message_id FROM initiative_log WHERE message_id IS NOT NULL)",
                (before,),
            )
            gone = "IN (SELECT id FROM pruned_messages)"
            run = self._conn.execute
            run(f"UPDATE reminders SET source_message_id = NULL WHERE source_message_id {gone}")
            run(f"UPDATE reminders SET message_id = NULL WHERE message_id {gone}")
            run(f"DELETE FROM followups WHERE source_message_id {gone}")
            run(f"UPDATE profile_facts SET source_message_id = NULL WHERE source_message_id {gone}")
            run(f"UPDATE profile_changes SET source_message_id = NULL WHERE source_message_id {gone}")
            run(f"UPDATE messages SET reply_to_id = NULL WHERE reply_to_id {gone} AND id NOT {gone}")
            run(f"DELETE FROM attachments WHERE message_id {gone}")
            tool_calls = run(f"DELETE FROM tool_calls WHERE message_id {gone} OR created_at < ?", (before,)).rowcount
            run(f"UPDATE documents SET source_message_id = NULL WHERE source_message_id {gone}")
            old_documents = "SELECT id FROM documents WHERE created_at < ?"
            run(
                f"UPDATE profile_facts SET source_document_id = NULL WHERE source_document_id IN ({old_documents})",
                (before,),
            )
            documents = run("DELETE FROM documents WHERE created_at < ?", (before,)).rowcount
            messages = run(f"DELETE FROM messages WHERE id {gone}").rowcount
            run(
                "DELETE FROM conversations WHERE ended_at IS NOT NULL"
                " AND id NOT IN (SELECT DISTINCT conversation_id FROM messages)"
            )
            run("DELETE FROM pruned_messages")
            if messages:
                run("INSERT INTO messages_fts (messages_fts) VALUES ('optimize')")
            if documents:
                run("INSERT INTO documents_fts (documents_fts) VALUES ('optimize')")
        return Pruned(messages, documents, tool_calls)

    def compact(self) -> None:
        """After a prune, in a quiet moment: empty the write-ahead log (old pages in it go), rebuild the file without
        its free pages (VACUUM; it blocks the database for a moment), and empty the log again."""
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.execute("VACUUM")
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

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

    def delete_state(self, key: str) -> None:
        with self.tx():
            self._conn.execute("DELETE FROM state WHERE key = ?", (key,))

    def states_with_prefix(self, prefix: str) -> list[tuple[str, str]]:
        """(key, value) of every state row whose key starts with `prefix` (taken literally)."""
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._conn.execute(
            "SELECT key, value FROM state WHERE key LIKE ? ESCAPE '\\' ORDER BY key", (escaped + "%",)
        )
        return [(str(row["key"]), str(row["value"])) for row in rows]

    def rewrite_notes(self, olds: list[str], new: str) -> int:
        """Replace each of `olds` in code-written notes with `new` (an UPDATE, so the search index's trigger takes the
        old words out), then merge the index so no deleted words stay in it. How many notes changed."""
        changed = 0
        with self.tx():
            for old in olds:
                changed += self._conn.execute(
                    "UPDATE messages SET text = replace(text, ?, ?), updated_at = ?"
                    " WHERE kind = 'note' AND instr(text, ?) > 0",
                    (old, new, utc_now(), old),
                ).rowcount
            if changed:
                self._conn.execute("INSERT INTO messages_fts (messages_fts) VALUES ('optimize')")
        return changed

    def checkpoint(self) -> None:
        """Write the log back into the database file and empty it: a row just overwritten (secure_delete) leaves no
        older copy in the log."""
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def db_size_bytes(self) -> int:
        pages = self._conn.execute("PRAGMA page_count").fetchone()[0]
        size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        return int(pages) * int(size)
