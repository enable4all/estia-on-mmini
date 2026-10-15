import sqlite3
import stat
from pathlib import Path

import pytest

from local_assistant.store import SCHEMA_V1, SCHEMA_VERSION, Store
from tests.conftest import ME, T0


def test_open_creates_private_files(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    db = Store.open(path)
    db.close()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reopen_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    db = Store.open(path)
    db.set_state("k", "v")
    db.close()
    again = Store.open(path)
    assert again.get_state("k") == "v"
    again.close()


def test_current_conversation_is_created_once(store: Store) -> None:
    first = store.current_conversation()
    assert store.current_conversation().id == first.id
    second = store.start_new_conversation()
    assert second.id != first.id
    assert store.current_conversation().id == second.id


def test_duplicate_delivery_is_ignored(store: Store) -> None:
    conv = store.current_conversation()
    assert store.add_incoming(conv.id, ME, T0, "text", "hello") is not None
    assert store.add_incoming(conv.id, ME, T0, "text", "hello") is None
    assert store.count_messages() == 1


def test_window_has_text_messages_after_the_summary_point(store: Store) -> None:
    conv = store.current_conversation()
    first = store.add_incoming(conv.id, ME, T0, "text", "first")
    assert first is not None
    reply = store.add_outgoing(conv.id, ME, "text", "reply", first)
    store.mark_sent(reply, T0 + 1)
    store.add_incoming(conv.id, ME, T0 + 2, "command", "/profile")
    note = store.add_outgoing(conv.id, ME, "note", "busy", first)
    store.mark_sent(note, T0 + 3)
    failed = store.add_outgoing(conv.id, ME, "text", "never arrived", first)
    store.set_status(failed, "failed")
    second = store.add_incoming(conv.id, ME, T0 + 4, "text", "second")
    assert [m.text for m in store.window_messages(conv.id, 0)] == ["first", "reply", "second"]
    reply_seq = store.message(reply).seq
    assert reply_seq is not None
    store.set_summary(conv.id, "summary", reply_seq)
    conversation = store.conversation(conv.id)
    assert (conversation.summary, conversation.summary_upto_seq) == ("summary", reply_seq)
    assert [m.id for m in store.window_messages(conv.id, conversation.summary_upto_seq)] == [second]


def test_a_message_joins_the_conversation_when_it_is_answered(store: Store) -> None:
    conv = store.current_conversation()
    first = store.add_incoming(conv.id, ME, T0, "text", "first question", defer_seq=True)
    second = store.add_incoming(conv.id, ME, T0 + 1, "text", "second question", defer_seq=True)
    assert first is not None and second is not None
    assert store.window_messages(conv.id, 0) == []  # waiting messages are not part of the conversation yet
    store.assign_seq(first)
    answer = store.add_outgoing(conv.id, ME, "text", "first answer", first)
    store.mark_sent(answer, T0 + 2)
    store.assign_seq(second)
    texts = [m.text for m in store.window_messages(conv.id, 0)]
    assert texts == ["first question", "first answer", "second question"]


def test_recovery_queries(store: Store) -> None:
    conv = store.current_conversation()
    waiting = store.add_incoming(conv.id, ME, T0, "text", "waiting")
    answered = store.add_incoming(conv.id, ME, T0 + 1, "text", "done")
    assert waiting is not None and answered is not None
    store.set_status(answered, "answered")
    pending = store.add_outgoing(conv.id, ME, "text", "pending", answered)
    assert [m.id for m in store.unanswered_incoming()] == [waiting]
    assert [m.id for m in store.unsent_outgoing()] == [pending]
    assert [m.text for m in store.user_messages_after(0, 10)] == ["waiting", "done"]
    assert [m.text for m in store.user_messages_after(waiting, 10)] == ["done"]


def test_search_returns_hit_with_neighbours(store: Store) -> None:
    conv = store.current_conversation()
    ids = [store.add_incoming(conv.id, ME, T0 + i, "text", f"message {i}") for i in range(5)]
    store.add_incoming(conv.id, ME, T0 + 10, "text", "We ate at restaurant De Kas in June")
    later = store.add_incoming(conv.id, ME, T0 + 11, "text", "and then went home")
    hits = store.search("which restaurant?", limit=3)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.kind == "message"
    assert [m.text for m in hit.context][-2:] == ["We ate at restaurant De Kas in June", "and then went home"]
    assert hit.context[0].id == ids[3]
    assert later is not None
    assert store.search("restaurant", exclude_message_id=hit.context[-2].id) == []


def test_search_ignores_diacritics_and_finds_documents(store: Store) -> None:
    store.add_document("https://example.org/cv", "CV of Alex", "Product manager at Acme in Zürich", None)
    hits = store.search("zurich")
    assert [h.kind for h in hits] == ["document"]
    assert hits[0].document is not None and hits[0].document.title == "CV of Alex"


def test_search_with_only_punctuation_returns_nothing(store: Store) -> None:
    assert store.search("?!") == []


def test_profile_facts_and_change_log(store: Store) -> None:
    fact = store.insert_fact("The user likes tea.", "remember", None, None)
    store.log_change(fact, "add", None, "The user likes tea.", None, "asked")
    store.update_fact(fact, "The user likes green tea.")
    store.log_change(fact, "replace", "The user likes tea.", "The user likes green tea.", None, "update")
    assert [f.text for f in store.facts()] == ["The user likes green tea."]
    store.blank_changes(fact)
    assert all(c.old_text is None and c.new_text is None for c in store.changes(fact))
    store.delete_fact(fact)
    assert store.facts() == []


def test_tool_calls_are_logged(store: Store) -> None:
    conv = store.current_conversation()
    msg = store.add_incoming(conv.id, ME, T0, "text", "hi")
    assert msg is not None
    store.log_tool_call(msg, "remember", '{"fact": "x"}', "Saved as fact #1.", True, 12)
    [record] = store.tool_calls(msg)
    assert (record.name, record.ok, record.duration_ms) == ("remember", True, 12)


def test_state_overwrite(store: Store) -> None:
    assert store.get_state("missing") is None
    store.set_state("k", "1")
    store.set_state("k", "2")
    assert store.get_state("k") == "2"


def test_transaction_rolls_back_on_error(store: Store) -> None:
    with pytest.raises(RuntimeError), store.tx():
        store.insert_fact("The user likes tea.", "chat", None, None)
        raise RuntimeError("boom")
    assert store.facts() == []


def test_nested_transaction_joins_the_outer_one(store: Store) -> None:
    with pytest.raises(RuntimeError), store.tx():
        with store.tx():
            store.insert_fact("inner", "chat", None, None)
        raise RuntimeError("outer fails")
    assert store.facts() == []


def test_db_size_is_reported(store: Store) -> None:
    assert store.db_size_bytes() > 0


def test_a_waiting_message_joins_the_conversation_that_is_open_when_it_is_answered(store: Store) -> None:
    old = store.current_conversation()
    waiting = store.add_incoming(old.id, ME, T0, "text", "let's talk about cooking", defer_seq=True)
    assert waiting is not None
    new = store.start_new_conversation()  # the user sent /new before this message was answered
    store.assign_seq(waiting, new.id)
    assert store.message(waiting).conversation_id == new.id
    assert [m.id for m in store.window_messages(new.id, 0)] == [waiting]


def test_fact_numbers_are_never_reused(store: Store) -> None:
    store.insert_fact("a", "chat", None, None)
    second = store.insert_fact("b", "chat", None, None)
    store.delete_fact(second)
    assert store.insert_fact("c", "chat", None, None) == second + 1  # "/forget N" must never hit a newer fact


def test_upgrade_from_version_1_keeps_facts_and_skips_used_numbers(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN;\n{SCHEMA_V1}\nPRAGMA user_version = 1;\nCOMMIT;")
    now = "2026-10-08T10:00:00+00:00"
    conn.execute(
        "INSERT INTO profile_facts (id, text, source_kind, created_at, updated_at) VALUES (1, 'kept', 'chat', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO profile_changes (fact_id, action, created_at, updated_at) VALUES (5, 'remove', ?, ?)", (now, now)
    )
    conn.commit()
    conn.close()
    store = Store.open(path)
    assert [f.text for f in store.facts()] == ["kept"]
    assert store.insert_fact("new", "chat", None, None) == 6  # numbers 2-5 were used before (change log)
    store.close()


def test_attachments_are_tracked_until_deleted(store: Store) -> None:
    conv = store.current_conversation()
    message = store.add_incoming(conv.id, ME, T0, "text", "[photo] my cat")
    assert message is not None
    first = store.add_attachment(message, "abc123.jpg", "image/jpeg", 2_048)
    store.add_attachment(message, "def456.png", "image/png", 4_096)
    assert [(a.signal_id, a.content_type, a.size) for a in store.attachments(message)] == [
        ("abc123.jpg", "image/jpeg", 2_048),
        ("def456.png", "image/png", 4_096),
    ]
    store.mark_attachment_deleted(first)
    assert [a.signal_id for a in store.attachments(message)] == ["def456.png"]  # deleted ones are hidden
    assert store.open_attachment_ids() == {"def456.png"}  # what the attachments sweep must keep


def test_a_message_text_can_be_replaced_and_search_follows(store: Store) -> None:
    conv = store.current_conversation()
    message_id = store.add_incoming(conv.id, ME, T0, "text", "[voice]")
    assert message_id is not None
    store.set_message_text(message_id, "[voice] Turn off the living room lamp")
    assert store.message(message_id).text == "[voice] Turn off the living room lamp"
    assert [hit.context[-1].id for hit in store.search("lamp")] == [message_id]


def test_call_turns_join_the_conversation_as_answered_and_sent(store: Store) -> None:
    conv = store.current_conversation()
    said = store.add_call_turn(conv.id, ME, "[call] what's on tomorrow?")
    reply = store.add_call_reply(conv.id, ME, "Two meetings.", said)
    window = store.window_messages(conv.id, 0)
    assert [(m.direction, m.kind, m.text) for m in window] == [
        ("in", "text", "[call] what's on tomorrow?"),
        ("out", "text", "Two meetings."),
    ]
    assert (store.message(said).status, store.message(reply).status) == ("answered", "sent")
    assert store.message(reply).reply_to_id == said
    # a restart must never answer them again by text or send the spoken reply as a text
    assert store.unanswered_incoming() == [] and store.unsent_outgoing() == []


def test_call_turns_get_increasing_made_up_timestamps(store: Store) -> None:
    conv = store.current_conversation()
    ids = [store.add_call_turn(conv.id, ME, f"[call] {n}") for n in range(5)]
    stamps = [ts for i in ids if (ts := store.message(i).signal_ts) is not None]
    assert len(stamps) == 5
    assert stamps == sorted(set(stamps))  # unique and increasing within the same millisecond
    # a text that came with a later Signal timestamp still stores fine
    assert store.add_incoming(conv.id, ME, max(stamps) + 10_000, "text", "hi") is not None


def test_audio_files_are_never_learned_from(store: Store) -> None:
    conv = store.current_conversation()
    store.add_incoming(conv.id, ME, T0, "text", "[voice] I play piano")
    store.add_incoming(conv.id, ME, T0 + 1, "text", "[audio file] I am the king of Spain")
    store.add_incoming(conv.id, ME, T0 + 2, "text", "thanks")
    assert [m.text for m in store.user_messages_after(0, 10)] == ["[voice] I play piano", "thanks"]


# V7: reminders, follow-ups and the initiative log (one migration for the whole design), and messages.footer

AT = "2026-10-12T12:00:00+00:00"  # the reminder queries take their time from the caller (an injected clock)


def remind(store: Store, text: str = "call mum", due_at: float = 1_000.0, **fields: object) -> int:
    options: dict[str, object] = {"repeat": "none", "call": False, "source": "chat", "source_message_id": None}
    options.update(fields)
    return store.add_reminder(
        ME,
        text,
        str(options.pop("local_time", "2026-10-13 07:00")),
        "Europe/Amsterdam",
        due_at,
        at=str(options.pop("at", AT)),
        **options,  # type: ignore[arg-type]
    )


def test_upgrade_from_version_6_keeps_messages_and_adds_the_new_tables(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    old = Store.open(path)
    old._conn.executescript("PRAGMA user_version = 6;")  # as an install before this design left it
    conv = old.current_conversation()
    kept = old.add_incoming(conv.id, ME, T0, "text", "hello")
    old.close()
    conn = sqlite3.connect(path)
    for table in ("reminders", "followups", "initiative_log", "known_devices", "doc_pages_fts", "doc_pages"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")  # back to a real V6 database
    conn.execute("DROP TABLE IF EXISTS doc_files")
    conn.execute("ALTER TABLE attachments DROP COLUMN filename")  # added by v10
    columns = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
    for column in ("footer", "source_device"):  # added by v7 and v9
        if column in columns:
            conn.execute(f"ALTER TABLE messages DROP COLUMN {column}")
    if "kind" in [row[1] for row in conn.execute("PRAGMA table_info(conversations)")]:
        conn.execute("ALTER TABLE conversations DROP COLUMN kind")  # added by v8
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    store = Store.open(path)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert kept is not None and store.message(kept).text == "hello"
    assert store._conn.execute("SELECT footer FROM messages WHERE id = ?", (kept,)).fetchone()[0] is None
    tables = {row[0] for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"reminders", "followups", "initiative_log"} <= tables
    store.close()


def test_a_reminder_is_stored_and_found_when_due(store: Store) -> None:
    first = remind(store, "call mum", due_at=2_000.0, call=True)
    second = remind(store, "take the pills", due_at=1_000.0, repeat="weekdays", source="command")
    reminder = store.reminder(first)
    assert reminder is not None
    assert (reminder.text, reminder.local_time, reminder.tz, reminder.due_at) == (
        "call mum",
        "2026-10-13 07:00",
        "Europe/Amsterdam",
        2_000.0,
    )
    assert (reminder.call, reminder.repeat, reminder.status, reminder.fired, reminder.created_at) == (
        True,
        "none",
        "open",
        0,
        AT,
    )
    assert [r.id for r in store.open_reminders(ME)] == [second, first]  # by time
    assert [r.id for r in store.due_reminders(1_500.0)] == [second]
    assert store.next_reminder_due() == 1_000.0
    assert store.reminder(999) is None


def test_firing_fences_the_note_then_a_repeat_goes_on(store: Store) -> None:
    rid = remind(store, due_at=1_000.0, repeat="daily")
    conv = store.current_conversation()
    note = store.add_outgoing(conv.id, ME, "note", "Reminder: call mum", None)
    store.fire_reminder(rid, note, at=AT)
    firing = store.reminder(rid)
    assert firing is not None and (firing.status, firing.message_id) == ("firing", note)
    assert store.due_reminders(5_000.0) == []  # a firing reminder is never picked up twice
    assert [r.id for r in store.firing_reminders()] == [rid]
    store.reschedule_reminder(rid, "2026-10-14 07:00", 87_400.0, outcome="sent", at=AT)
    again = store.reminder(rid)
    assert again is not None
    assert (again.status, again.message_id, again.fired, again.local_time, again.due_at, again.outcome) == (
        "open",
        None,
        1,
        "2026-10-14 07:00",
        87_400.0,
        "sent",
    )


def test_finishing_and_cancelling(store: Store) -> None:
    done = remind(store, due_at=1_000.0)
    store.finish_reminder(done, "done", "sent", at=AT)
    failed = remind(store, due_at=1_100.0)
    store.finish_reminder(failed, "failed", "send_failed", at=AT)
    open_one = remind(store, due_at=1_200.0)
    other = store.add_reminder(
        "11111111-2222-4333-8444-555555555555", "theirs", "2026-10-13 07:00", "UTC", 1_300.0, at=AT
    )
    finished = store.reminder(done)
    assert finished is not None and (finished.status, finished.fired, finished.outcome) == ("done", 1, "sent")
    assert [r.id for r in store.open_reminders(ME)] == [open_one]
    assert [r.id for r in store.reminders_with_outcome(ME, ("send_failed",), since=AT)] == [failed]
    assert store.reminders_with_outcome(ME, ("send_failed",), since="2026-10-13T00:00:00+00:00") == []
    assert [r.id for r in store.cancel_reminders(ME, open_one, at=AT)] == [open_one]
    assert store.cancel_reminders(ME, open_one, at=AT) == []  # already cancelled
    assert store.cancel_reminders(ME, other, at=AT) == []  # someone else's reminder: never touched
    assert [r.id for r in store.open_reminders("11111111-2222-4333-8444-555555555555")] == [other]
    again = remind(store, due_at=1_400.0)
    assert [r.id for r in store.cancel_reminders(ME, None, at=AT)] == [again]  # all of mine


def test_counting_new_reminders_and_pruning_old_ones(store: Store) -> None:
    remind(store, at="2026-10-11T23:00:00+00:00")
    remind(store, at="2026-10-12T08:00:00+00:00")
    remind(store, at="2026-10-12T09:00:00+00:00")
    assert store.count_reminders_created(ME, since="2026-10-11T22:00:00+00:00") == 3
    assert store.count_reminders_created(ME, since="2026-10-12T00:00:00+00:00") == 2
    old = remind(store, at="2026-09-01T09:00:00+00:00")
    store.finish_reminder(old, "done", "sent", at="2026-09-01T09:00:00+00:00")
    cancelled = remind(store, at="2026-09-01T09:00:00+00:00")
    store.cancel_reminders(ME, cancelled, at="2026-09-01T09:00:00+00:00")
    assert store.prune_reminders(before="2026-09-12T00:00:00+00:00") == 2
    assert store.reminder(old) is None and len(store.open_reminders(ME)) == 3


def test_open_follow_ups_are_listed_and_cancelled_per_user(store: Store) -> None:
    conv = store.current_conversation()
    said = store.add_incoming(conv.id, ME, T0, "text", "I'll call the dentist on Thursday")
    assert said is not None
    first = store._conn.execute(
        "INSERT INTO followups (peer_aci, what, source_message_id, due_at, expires_at, status, created_at,"
        " updated_at) VALUES (?, 'call the dentist', ?, 2000, 9000, 'open', ?, ?)",
        (ME, said, AT, AT),
    ).lastrowid
    assert first is not None
    assert [(f.id, f.what, f.due_at) for f in store.open_followups(ME)] == [(first, "call the dentist", 2000.0)]
    assert store.open_followups("11111111-2222-4333-8444-555555555555") == []
    assert [f.id for f in store.cancel_followups(ME, first, at=AT)] == [first]
    assert store.open_followups(ME) == []


def test_whether_the_user_wrote_after_a_message(store: Store) -> None:
    conv = store.current_conversation()
    note = store.add_outgoing(conv.id, ME, "note", "Wake-up call: 07:00", None)
    assert not store.wrote_after(ME, note)
    store.add_incoming(conv.id, "11111111-2222-4333-8444-555555555555", T0, "text", "not you")
    assert not store.wrote_after(ME, note)
    store.add_incoming(conv.id, ME, T0 + 1, "text", "I'm up")
    assert store.wrote_after(ME, note)


# V8: practice conversations (/babble): their own conversation kind, never the current chat


def _v7_database(path: Path) -> None:
    """A database as an install before practice left it: the v1-v7 scripts, user_version 7."""
    from local_assistant import store as store_module

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    for script in (
        store_module.SCHEMA_V1,
        store_module.MIGRATION_V2,
        store_module.MIGRATION_V3,
        store_module.MIGRATION_V4,
        store_module.MIGRATION_V5,
        store_module.MIGRATION_V6,
        store_module.MIGRATION_V7,
    ):
        conn.executescript(script)
    now = "2026-10-12T10:00:00+00:00"
    conn.execute(
        "INSERT INTO conversations (id, started_at, ended_at, created_at, updated_at) VALUES (1, ?, ?, ?, ?)",
        (now, now, now, now),
    )
    conn.execute(
        "INSERT INTO conversations (id, started_at, created_at, updated_at) VALUES (2, ?, ?, ?)", (now, now, now)
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, direction, peer_aci, signal_ts, kind, text, status, seq, created_at,"
        " updated_at) VALUES (2, 'in', ?, ?, 'text', 'hello', 'answered', 1, ?, ?)",
        (ME, T0, now, now),
    )
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()


def test_upgrade_from_version_7_marks_every_conversation_a_chat(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    _v7_database(path)
    store = Store.open(path)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION  # v8, then v9 (the devices)
    assert store.devices(ME) == [] and store.message(1).source_device is None
    kinds = store._conn.execute("SELECT id, kind FROM conversations ORDER BY id").fetchall()
    assert [(row["id"], row["kind"]) for row in kinds] == [(1, "chat"), (2, "chat")]
    current = store.current_conversation()
    assert (current.id, current.kind) == (2, "chat")
    assert [m.text for m in store.window_messages(2, 0)] == ["hello"]
    with pytest.raises(sqlite3.IntegrityError):  # only the two kinds
        store._conn.execute("UPDATE conversations SET kind = 'other' WHERE id = 1")
    store.close()


def test_a_practice_conversation_never_becomes_the_current_one(store: Store) -> None:
    chat = store.current_conversation()
    practice = store.start_practice_conversation()
    assert practice.kind == "practice" and practice.id != chat.id
    assert store.conversation(practice.id).kind == "practice"
    assert store.current_conversation().id == chat.id  # the chat stays open and current
    assert store.current_conversation().kind == "chat"


def test_new_leaves_a_running_practice_open(store: Store) -> None:
    store.current_conversation()
    practice = store.start_practice_conversation()
    new = store.start_new_conversation()
    assert new.kind == "chat" and store.current_conversation().id == new.id
    ended = store._conn.execute("SELECT ended_at FROM conversations WHERE id = ?", (practice.id,)).fetchone()[0]
    assert ended is None  # /new never touches a running practice


def test_ending_a_conversation_sets_its_end(store: Store) -> None:
    practice = store.start_practice_conversation()
    store.end_conversation(practice.id)
    ended = store._conn.execute("SELECT ended_at FROM conversations WHERE id = ?", (practice.id,)).fetchone()[0]
    assert ended is not None


def test_the_users_practice_words_and_the_idle_clock(store: Store) -> None:
    chat = store.current_conversation()
    practice = store.start_practice_conversation()
    assert store.user_texts(practice.id) == [] and store.last_user_ts(practice.id) is None
    store.add_incoming(chat.id, ME, T0, "text", "a chat line")
    first = store.add_incoming(practice.id, ME, T0 + 1, "text", "Ik ben gisteren naar huis gegaan.", defer_seq=True)
    assert first is not None
    store.assign_seq(first, practice.id)
    reply = store.add_outgoing(practice.id, ME, "text", "Wat leuk!", first)
    store.mark_sent(reply, T0 + 2)
    store.add_outgoing(practice.id, ME, "note", "a code note", first)
    store.add_incoming(practice.id, ME, T0 + 3, "command", "/babble")
    ignored = store.add_incoming(practice.id, ME, T0 + 4, "text", "never answered")
    assert ignored is not None
    store.set_status(ignored, "ignored")
    store.add_call_turn(practice.id, ME, "[call] Ik woon in Utrecht.")
    assert [m.text for m in store.user_texts(practice.id)] == [
        "Ik ben gisteren naar huis gegaan.",
        "[call] Ik woon in Utrecht.",
    ]
    newest = store.user_texts(practice.id)[-1].signal_ts
    assert store.last_user_ts(practice.id) == newest


def practice_turn(store: Store, words: str, ts: int) -> int:
    """A message the user sent during a practice session: stored in the chat, then moved into the practice one."""
    practice = store.start_practice_conversation()
    message_id = store.add_incoming(store.current_conversation().id, ME, ts, "text", words, defer_seq=True)
    assert message_id is not None
    store.assign_seq(message_id, practice.id)
    return message_id


def test_learning_and_check_ins_never_read_a_practice_turn(store: Store) -> None:
    practice_turn(store, "Ik heb een hond gekoopt.", T0)
    assert store.user_messages_after(0, 10) == []
    chat = store.add_incoming(store.current_conversation().id, ME, T0 + 1, "text", "I bought a dog.")
    assert [m.id for m in store.user_messages_after(0, 10)] == [chat]


def test_the_look_back_never_reads_a_practice_turn(store: Store) -> None:
    practice_turn(store, "Ik heb een hond gekoopt.", T0)
    chat = store.add_incoming(store.current_conversation().id, ME, T0 + 1, "text", "I bought a dog.")
    assert [m.id for m in store.user_messages_since(ME, T0 - 1)] == [chat]


def test_the_history_search_never_finds_a_practice_turn(store: Store) -> None:
    practice_turn(store, "Mijn hond heet Bello.", T0)
    assert store.search("Bello") == []
    chat = store.add_incoming(store.current_conversation().id, ME, T0 + 1, "text", "My dog Bello is ill.")
    hits = store.search("Bello")
    assert [m.id for hit in hits for m in hit.context] == [chat]


def test_a_practice_message_still_counts_as_the_user_writing(store: Store) -> None:
    turn = practice_turn(store, "Hallo!", T0)
    assert store.last_incoming_ts() == T0  # busy gates
    assert store.count_user_texts(ME) == 1 and store.first_user_text_ts(ME) == T0
    note = store.add_outgoing(store.current_conversation().id, ME, "note", "Reminder: call mum", None)
    assert not store.wrote_after(ME, note)
    assert turn < note
