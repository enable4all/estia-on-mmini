import sqlite3
import stat
from pathlib import Path

import pytest

from local_assistant.store import SCHEMA_V1, Store
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
