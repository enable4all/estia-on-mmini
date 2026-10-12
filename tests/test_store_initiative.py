"""The store's part of the messages she starts: follow-ups, the initiative log (metadata only) and message footers."""

from pathlib import Path

import pytest

from local_assistant.store import Store
from tests.conftest import ME, STRANGER, T0

AT = "2026-10-12T12:00:00+00:00"


def said(store: Store, text: str, ts: int, peer: str = ME, kind: str = "text") -> int:
    message_id = store.add_incoming(store.current_conversation().id, peer, ts, kind, text)  # type: ignore[arg-type]
    assert message_id is not None
    return message_id


# message footers


def test_a_footer_is_kept_apart_from_the_text_and_never_searched(store: Store) -> None:
    conversation = store.current_conversation()
    out = store.add_outgoing(conversation.id, ME, "text", "Did you fix the bike?", None, footer="(pause with /quiet)")
    store.mark_sent(out, T0)
    message = store.message(out)
    assert (message.text, message.footer) == ("Did you fix the bike?", "(pause with /quiet)")
    assert store.search("pause quiet") == []  # the footer is not in the history search
    assert [m.text for m in store.window_messages(conversation.id, 0)] == ["Did you fix the bike?"]
    plain = store.add_outgoing(conversation.id, ME, "text", "Hi", None)
    assert store.message(plain).footer is None


# follow-ups


def test_follow_ups_are_added_found_when_due_and_closed(store: Store) -> None:
    source = said(store, "I'll call the dentist tomorrow", T0)
    first = store.add_followup(ME, "call the dentist", source, due_at=100.0, expires_at=300.0, at=AT)
    later = store.add_followup(ME, "fix the bike", source, due_at=200.0, expires_at=400.0, at=AT)
    assert [f.id for f in store.due_followups(150.0)] == [first]
    assert [f.id for f in store.due_followups(250.0)] == [first, later]
    store.set_followup(first, status="sent", at=AT)
    store.set_followup(later, due_at=500.0, at=AT)
    assert store.due_followups(250.0) == []
    assert store.followup(later) is not None and store.followup(later).due_at == 500.0  # type: ignore[union-attr]
    assert [f.id for f in store.open_followups(ME)] == [later]
    assert store.followup_sources() == {source}


def test_only_the_open_follow_ups_of_everyone_are_listed_for_the_extraction(store: Store) -> None:
    mine = said(store, "I'll paint the door", T0)
    theirs = said(store, "I'll bake bread", T0, peer=STRANGER)
    store.add_followup(ME, "paint the door", mine, due_at=1.0, expires_at=2.0, at=AT)
    closed = store.add_followup(STRANGER, "bake bread", theirs, due_at=1.0, expires_at=2.0, at=AT)
    store.set_followup(closed, status="done", at=AT)
    assert [f.what for f in store.all_open_followups()] == ["paint the door"]


def test_old_closed_follow_ups_are_deleted(store: Store) -> None:
    source = said(store, "I'll call", T0)
    old = store.add_followup(ME, "call", source, due_at=1.0, expires_at=2.0, at="2026-09-01T00:00:00+00:00")
    store.set_followup(old, status="expired", at="2026-09-01T00:00:00+00:00")
    kept = store.add_followup(ME, "call", source, due_at=1.0, expires_at=2.0, at="2026-09-01T00:00:00+00:00")
    assert store.prune_followups(before="2026-10-01T00:00:00+00:00") == 1
    assert store.followup(old) is None and store.followup(kept) is not None  # an open one stays


def test_the_messages_that_set_a_reminder(store: Store) -> None:
    asked = said(store, "remind me at 5 to call mum", T0)
    other = said(store, "I'll call mum later", T0 + 1)
    store.add_reminder(ME, "call mum", "2026-10-12 17:00", "Europe/Amsterdam", 1.0, source_message_id=asked)
    assert store.reminder_sources([asked, other]) == {asked}
    assert store.reminder_sources([]) == set()


# the initiative log


def test_the_log_counts_by_day_kind_and_outcome(store: Store) -> None:
    sent = store.log_initiative("improvise", "2026-10-12", "sending", at=AT)
    store.set_initiative_outcome(sent, "sent", at=AT)
    store.log_initiative("followup", "2026-10-12", "skipped", reason="day_cap", at=AT)
    store.log_initiative("calendar", "2026-10-13", "unknown", at=AT)
    assert store.count_initiative("2026-10-12", "2026-10-12") == 1  # skipped rows never count
    assert store.count_initiative("2026-10-12", "2026-10-18") == 2
    assert store.count_initiative("2026-10-12", "2026-10-18", kind="improvise") == 1
    assert store.count_initiative("2026-10-12", "2026-10-18", kind="followup") == 0
    entry = store.initiative_entry(sent)
    assert entry is not None and (entry.kind, entry.outcome, entry.reason) == ("improvise", "sent", None)


def test_a_row_still_sending_at_a_start_is_found(store: Store) -> None:
    out = store.add_outgoing(store.current_conversation().id, ME, "text", "Hi", None)
    sending = store.log_initiative("improvise", "2026-10-12", "sending", message_id=out, at=AT)
    store.log_initiative("followup", "2026-10-12", "sent", at=AT)
    assert [e.id for e in store.initiative_entries(outcomes=("sending",))] == [sending]


def test_replies_and_quiet_are_recorded_on_the_log(store: Store) -> None:
    out = store.add_outgoing(store.current_conversation().id, ME, "text", "A thought", None)
    entry = store.log_initiative("improvise", "2026-10-12", "sent", message_id=out, at=AT)
    store.set_initiative_replied(entry, 1_000.0, at=AT)
    store.set_initiative_quieted([entry], 2_000.0, at=AT)
    logged = store.initiative_entry(entry)
    assert logged is not None and (logged.replied_at, logged.quieted_at) == (1_000.0, 2_000.0)


def test_the_log_is_kept_for_a_year(store: Store) -> None:
    store.log_initiative("improvise", "2025-01-01", "sent", at="2025-01-01T12:00:00+00:00")
    store.log_initiative("improvise", "2026-10-12", "sent", at=AT)
    assert store.prune_initiative(before="2025-10-12T00:00:00+00:00") == 1
    assert len(store.initiative_entries()) == 1


def test_the_log_refuses_an_unknown_kind_or_outcome(store: Store) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.log_initiative("weather", "2026-10-12", "sent", at=AT)  # type: ignore[arg-type]


# what the gate and the inputs read from the messages


def test_the_newest_user_message_time_and_the_first_reply_after_a_message(store: Store) -> None:
    assert store.last_incoming_ts() is None
    said(store, "hello", T0)
    out = store.add_outgoing(store.current_conversation().id, ME, "text", "A thought", None)
    said(store, "/status", T0 + 1_000, kind="command")  # a command is no reply
    reply = said(store, "nice one", T0 + 2_000)
    said(store, "and another", T0 + 3_000)
    assert store.last_incoming_ts() == T0 + 3_000
    first = store.first_reply_after(ME, out)
    assert first is not None and first.id == reply
    assert store.first_reply_after(STRANGER, out) is None


def test_the_users_messages_since_a_moment_and_their_count(store: Store) -> None:
    said(store, "old", T0 - 10_000)
    said(store, "[audio file] someone else's voice", T0 + 1)  # never the user's words
    said(store, "/help", T0 + 2, kind="command")
    keep = said(store, "fixed the bike", T0 + 3)
    said(store, "theirs", T0 + 4, peer=STRANGER)
    assert [m.id for m in store.user_messages_since(ME, T0)] == [keep]
    assert store.count_user_texts(ME) == 2  # the old one and the bike; never audio files or commands
    assert store.first_user_text_ts(ME) == T0 - 10_000
    assert store.first_user_text_ts(STRANGER) == T0 + 4


def test_commands_are_counted_by_word_since_a_moment(store: Store) -> None:
    said(store, "/quiet", T0, kind="command")
    said(store, "/quiet off", T0 + 1, kind="command")
    said(store, "/QUIET", T0 + 2, kind="command")
    said(store, "/quietly", T0 + 3, kind="command")
    said(store, "/quiet", T0 - 10, kind="command")
    assert store.count_quiet_commands(since_ms=T0) == 2


def test_a_read_only_store_reads_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "data" / "assistant.db"
    writer = Store.open(path)
    said(writer, "hello", T0)
    writer.close()
    reader = Store.open_read_only(path)
    try:
        assert reader.count_messages() == 1
        import sqlite3

        with pytest.raises(sqlite3.OperationalError):
            reader.set_state("x", "y")
    finally:
        reader.close()
