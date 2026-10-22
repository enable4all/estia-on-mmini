"""The store's hardening: deleted rows overwritten, the sender's devices, history retention (prune) and compaction."""

import sqlite3
import time
from pathlib import Path

import pytest

from local_assistant.reminders import iso
from local_assistant.store import SCHEMA_VERSION, Store
from tests.conftest import ME, STRANGER, T0

DAY = 86_400.0
NOW = time.time()  # rows written without a time of their own (tool calls) are from now
OLD = iso(NOW - 40 * DAY)  # older than keep_days = 30
NEW = iso(NOW - 2 * DAY)


def test_deleted_rows_are_overwritten_in_the_file(store: Store) -> None:
    assert store._conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_the_schema_has_the_device_table_and_the_messages_device(store: Store) -> None:
    # v10: the documents index, v11: the evening note in the log (tests/test_evening.py), v12: each document's reader
    assert SCHEMA_VERSION == 14  # (tests/test_store_documents.py; v13: profile_facts.in_notes; v14: messages.history)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(known_devices)")}
    assert {"aci", "device", "first_seen", "last_seen", "source", "created_at", "updated_at"} <= columns
    assert "source_device" in {row[1] for row in store._conn.execute("PRAGMA table_info(messages)")}
    assert "kind" in {row[1] for row in store._conn.execute("PRAGMA table_info(conversations)")}  # v8 (/babble)


def _back_to(path: Path, version: int) -> None:
    """The database as that version left it: v10 (the documents index) and v9 (the devices) gone, and for 7 also v8
    (/babble's conversation kind)."""
    conn = sqlite3.connect(path)
    for table in ("doc_pages_fts", "doc_pages", "doc_files"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("ALTER TABLE attachments DROP COLUMN filename")
    conn.execute("DROP TABLE known_devices")
    conn.execute("ALTER TABLE messages DROP COLUMN source_device")
    if version < 8:
        conn.execute("ALTER TABLE conversations DROP COLUMN kind")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


def test_upgrade_from_version_7_keeps_messages(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    old = Store.open(path)
    conv = old.current_conversation()
    kept = old.add_incoming(conv.id, ME, T0, "text", "hello")
    old.close()
    _back_to(path, 7)
    store = Store.open(path)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert kept is not None and store.message(kept).text == "hello" and store.message(kept).source_device is None
    assert store.current_conversation().kind == "chat"  # v8 gave the old conversation its kind
    assert store.devices(ME) == []
    store.set_device(ME, 1, "primary")
    assert store.device_source(ME, 1) == "primary"
    store.close()


def test_upgrade_from_version_8_keeps_practice_and_adds_the_devices(tmp_path: Path) -> None:
    """/babble's v8 is live on installs already: v9 (the devices) goes on top of it, practice conversations kept."""
    path = tmp_path / "data" / "assistant.db"
    old = Store.open(path)
    chat = old.current_conversation()
    practice = old.start_practice_conversation()
    said = old.add_incoming(practice.id, ME, T0, "text", "Ik ben naar het strand gegaan.")
    old.close()
    _back_to(path, 8)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'known_devices'").fetchone() is None
    conn.close()
    store = Store.open(path)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.conversation(practice.id).kind == "practice" and store.current_conversation().id == chat.id
    assert said is not None and store.message(said).source_device is None
    assert store.devices(ME) == []
    later = store.add_incoming(chat.id, ME, T0 + 1, "text", "hello", device=2)
    assert later is not None and store.message(later).source_device == 2
    store.close()


def test_an_incoming_message_keeps_its_device(store: Store) -> None:
    conv = store.current_conversation()
    first = store.add_incoming(conv.id, ME, T0, "text", "from the phone", device=1)
    second = store.add_incoming(conv.id, ME, T0 + 1, "command", "/lock", device=3)
    third = store.add_incoming(conv.id, ME, T0 + 2, "text", "unnamed")
    assert first is not None and second is not None and third is not None
    assert [store.message(i).source_device for i in (first, second, third)] == [1, 3, None]


def test_known_devices_per_account(store: Store) -> None:
    assert store.devices(ME) == [] and store.device_source(ME, 1) is None
    store.set_device(ME, 1, "primary", at=OLD)
    store.set_device(ME, 3, "held", at=NEW)
    store.set_device(STRANGER, 2, "held", at=NEW)
    assert [(d.device, d.source, d.first_seen) for d in store.devices(ME)] == [(1, "primary", OLD), (3, "held", NEW)]
    store.set_device(ME, 3, "allowed", at=iso(NOW))  # allowed later: when it was first seen stays
    assert [(d.device, d.source, d.first_seen) for d in store.devices(ME)][1] == (3, "allowed", NEW)
    assert store.device_source(ME, 3) == "allowed" and store.device_source(STRANGER, 3) is None
    with pytest.raises(sqlite3.IntegrityError):
        store.set_device(ME, 4, "trusted", at=NEW)  # type: ignore[arg-type]


def test_a_state_key_can_be_removed(store: Store) -> None:
    store.set_state("lock:on", "1")
    store.delete_state("lock:on")
    store.delete_state("never-set")
    assert store.get_state("lock:on") is None


# retention


def _age(store: Store, table: str, row_id: int, at: str) -> None:
    store._conn.execute(f"UPDATE {table} SET created_at = ?, updated_at = ? WHERE id = ?", (at, at, row_id))


def _incoming(store: Store, text: str, at: str, ts: int) -> int:
    conv = store.current_conversation()
    message_id = store.add_incoming(conv.id, ME, ts, "text", text)
    assert message_id is not None
    _age(store, "messages", message_id, at)
    return message_id


def _reply(store: Store, text: str, at: str, reply_to: int | None) -> int:
    message_id = store.add_outgoing(store.current_conversation().id, ME, "text", text, reply_to)
    _age(store, "messages", message_id, at)
    return message_id


def _search(store: Store, word: str) -> list[int]:
    return [row[0] for row in store._conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?", (word,))]


def test_prune_deletes_old_messages_pages_and_tool_calls_with_their_search_rows(store: Store) -> None:
    old_in = _incoming(store, "my secret plan pumpernickel", OLD, T0)
    old_out = _reply(store, "about pumpernickel then", OLD, old_in)
    store.log_tool_call(old_in, "read_link", "{}", "page text pumpernickel", True, 5)
    page = store.add_document("https://example.com/a", "Old page", "pumpernickel recipe", old_in)
    _age(store, "documents", page, OLD)
    store.add_attachment(old_in, "photo.jpg", "image/jpeg", 10)
    new_in = _incoming(store, "a new pumpernickel note", NEW, T0 + 1)
    new_out = _reply(store, "fresh", NEW, new_in)
    store.log_tool_call(new_in, "search_history", "{}", "found", True, 5)
    assert sorted(_search(store, "pumpernickel")) == sorted([old_in, old_out, new_in])

    pruned = store.prune(30, now=NOW)

    assert (pruned.messages, pruned.documents, pruned.tool_calls) == (2, 1, 1)
    remaining = [row[0] for row in store._conn.execute("SELECT id FROM messages ORDER BY id")]
    assert remaining == [new_in, new_out]
    assert _search(store, "pumpernickel") == [new_in]  # the search rows went with them
    assert (
        store._conn.execute("SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH 'recipe'").fetchone()[0] == 0
    )
    assert [call.name for call in store.tool_calls()] == ["search_history"]
    assert store.attachments(old_in) == []
    assert [hit.kind for hit in store.search("pumpernickel")] == ["message"]


def test_prune_never_touches_open_reminders_or_the_initiative_log(store: Store) -> None:
    asked = _incoming(store, "remind me at 7 to stretch", OLD, T0)
    note = _reply(store, "Reminder: stretch", OLD, None)
    open_id = store.add_reminder(
        ME, "stretch", "2026-10-20 07:00", "Europe/Amsterdam", NOW + DAY, at=OLD, source_message_id=asked
    )
    store._conn.execute("UPDATE reminders SET message_id = ? WHERE id = ?", (note, open_id))
    done_from = _incoming(store, "remind me to water the plants", OLD, T0 + 1)
    done_id = store.add_reminder(
        ME, "water", "2026-09-01 07:00", "Europe/Amsterdam", NOW - 41 * DAY, at=OLD, source_message_id=done_from
    )
    store.finish_reminder(done_id, "done", "sent", at=OLD)
    started = _reply(store, "How did the run go?", OLD, None)
    store.log_initiative("followup", "2026-09-01", "sent", message_id=started, at=OLD)
    plan = _incoming(store, "I will run tomorrow", OLD, T0 + 2)
    store.add_followup(ME, "the run", plan, NOW + DAY, NOW + 2 * DAY, at=OLD)
    gone = _incoming(store, "just chat", OLD, T0 + 3)

    store.prune(30, now=NOW)

    left = {row[0] for row in store._conn.execute("SELECT id FROM messages")}
    assert {asked, note, started, plan} <= left  # an open reminder, the log's message and an open check-in's source
    assert done_from not in left and gone not in left
    reminder = store.reminder(open_id)
    assert reminder is not None and (reminder.source_message_id, reminder.message_id) == (asked, note)
    done = store.reminder(done_id)
    assert done is not None and done.source_message_id is None  # a finished one only loses its link
    entries = store.initiative_entries(since=iso(NOW - 400 * DAY))
    assert [entry.message_id for entry in entries] == [started]


def test_prune_keeps_the_profile_and_unlinks_its_sources(store: Store) -> None:
    told = _incoming(store, "I live in Lisbon", OLD, T0)
    fact = store.insert_fact("Lives in Lisbon", "chat", told, None)
    page = store.add_document("https://example.com/me", "About", "text", told)
    _age(store, "documents", page, OLD)
    linked = store.insert_fact("Works as a nurse", "link", None, page)
    store.log_change(fact, "add", None, "Lives in Lisbon", told, "")
    reply = _reply(store, "Noted", NEW, told)  # a newer reply to the old message keeps no dangling link

    store.prune(30, now=NOW)

    facts = {f.id: f for f in store.facts()}
    assert facts[fact].text == "Lives in Lisbon" and facts[fact].source_message_id is None
    assert facts[linked].source_document_id is None
    assert store.changes(fact)[0].source_message_id is None
    assert store.message(reply).reply_to_id is None


def test_prune_drops_an_ended_conversation_once_it_is_empty(store: Store) -> None:
    first = store.current_conversation()
    store._conn.execute(
        "UPDATE conversations SET summary = 'old summary', summary_upto_seq = 1 WHERE id = ?", (first.id,)
    )
    _incoming(store, "old", OLD, T0)
    second = store.start_new_conversation()
    _incoming(store, "new", NEW, T0 + 1)

    store.prune(30, now=NOW)

    ids = [row[0] for row in store._conn.execute("SELECT id FROM conversations")]
    assert ids == [second.id]
    assert store.current_conversation().id == second.id


def test_prune_with_nothing_old_changes_nothing(store: Store) -> None:
    kept = _incoming(store, "recent", NEW, T0)
    pruned = store.prune(30, now=NOW)
    assert (pruned.messages, pruned.documents, pruned.tool_calls) == (0, 0, 0)
    assert store.message(kept).text == "recent"


def test_compact_empties_the_write_ahead_log(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "data" / "assistant.db")
    for number in range(200):
        _incoming(store, "word " * 50, OLD, T0 + number)
    store.prune(30, now=NOW)
    wal = tmp_path / "data" / "assistant.db-wal"
    assert wal.exists() and wal.stat().st_size > 0
    started = time.monotonic()
    store.compact()
    assert wal.stat().st_size == 0
    assert time.monotonic() - started < 5
    assert store._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    store.close()
