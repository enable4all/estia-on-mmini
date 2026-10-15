from pathlib import Path

from local_assistant.store import MIGRATION_V5, SCHEMA_VERSION, MailFields, MailFolder, Store

INBOX: MailFolder = "inbox"
SENT: MailFolder = "sent"


def fields(
    graph_id: str,
    *,
    conversation: str = "c1",
    sender: str = "anna@example.com",
    subject: str = "Moving Friday's meeting",
    at: str = "2026-10-08T07:00:00+00:00",
    recipients: str = "info@example.com",
) -> MailFields:
    return MailFields(
        graph_id=graph_id,
        conversation_id=conversation,
        internet_message_id=f"<{graph_id}@example.com>",
        sender_name="Anna Smith",
        sender_address=sender,
        recipients=recipients,
        subject=subject,
        received_at=at,
        is_read=False,
        has_attachments=False,
        importance="normal",
        focused=True,
    )


def test_schema_v4_upsert_keeps_gemma_fields(store: Store) -> None:
    mail_id = store.upsert_mail("family", INBOX, fields("g1"))
    store.set_mail_label(mail_id, "reply", "Anna asks to move Friday's meeting.")
    again = store.upsert_mail("family", INBOX, fields("g1", subject="Re: Moving Friday's meeting"))
    assert again == mail_id
    mail = store.mail(mail_id)
    assert (mail.subject, mail.label, mail.summary) == (
        "Re: Moving Friday's meeting",
        "reply",
        "Anna asks to move Friday's meeting.",
    )
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_answered_when_sent_items_has_a_later_message_in_the_conversation(store: Store) -> None:
    asked = store.upsert_mail("family", INBOX, fields("g1", at="2026-10-08T07:00:00+00:00"))
    other = store.upsert_mail("family", INBOX, fields("g2", conversation="c2"))
    store.upsert_mail("family", SENT, fields("s0", at="2026-10-08T06:00:00+00:00"))  # before: not an answer
    assert store.update_answered("family") == 0
    store.upsert_mail("family", SENT, fields("s1", at="2026-10-08T08:30:00+00:00", sender="info@example.com"))
    assert store.update_answered("family") == 1
    assert store.mail(asked).answered_at == "2026-10-08T08:30:00+00:00"
    assert store.mail(other).answered_at is None


def test_removed_and_old_mail_is_deleted(store: Store) -> None:
    store.upsert_mail("family", INBOX, fields("g1", at="2026-08-01T07:00:00+00:00"))
    store.upsert_mail("family", INBOX, fields("g2", at="2026-10-08T07:00:00+00:00"))
    assert store.remove_mail("family", INBOX, "g2") is True
    assert store.remove_mail("family", INBOX, "missing") is False
    assert store.prune_mail("2026-09-08T00:00:00+00:00") == 1
    assert store.count_mail() == 0


def test_delta_links_per_account_and_folder(store: Store) -> None:
    assert store.mail_delta_link("family", INBOX) is None
    store.set_mail_delta_link("family", INBOX, "https://graph.microsoft.com/v1.0/delta?token=a")
    store.set_mail_delta_link("family", INBOX, "https://graph.microsoft.com/v1.0/delta?token=b")
    assert store.mail_delta_link("family", INBOX) == "https://graph.microsoft.com/v1.0/delta?token=b"
    assert store.mail_synced_at("family") is not None and store.mail_synced_at("work") is None
    store.clear_mail_sync("family")
    assert store.mail_delta_link("family", INBOX) is None


def test_unlabelled_newest_first_and_attempts(store: Store) -> None:
    old = store.upsert_mail("family", INBOX, fields("g1", at="2026-10-07T07:00:00+00:00"))
    new = store.upsert_mail("family", INBOX, fields("g2", at="2026-10-08T07:00:00+00:00"))
    store.upsert_mail("family", SENT, fields("s1"))
    assert [m.id for m in store.unlabelled_mail(10)] == [new, old]
    assert store.unlabelled_mail(10, account="work") == []
    assert store.bump_mail_attempts(new) == 1
    assert store.bump_mail_attempts(new) == 2


def test_digest_queries(store: Store) -> None:
    urgent = store.upsert_mail("family", INBOX, fields("g1", conversation="c1", at="2026-10-08T07:00:00+00:00"))
    store.set_mail_label(urgent, "urgent_reply", "Contract due today.")
    reply = store.upsert_mail("work", INBOX, fields("g2", conversation="c2", at="2026-10-06T07:00:00+00:00"))
    store.set_mail_label(reply, "reply", "Question about pricing.")
    too_old = store.upsert_mail("family", INBOX, fields("g3", conversation="c3", at="2026-09-20T07:00:00+00:00"))
    store.set_mail_label(too_old, "reply", "Old question.")
    action = store.upsert_mail("family", INBOX, fields("g4", conversation="c4", at="2026-10-08T06:00:00+00:00"))
    store.set_mail_label(action, "action", "Invoice to pay by Friday.")
    known = store.upsert_mail("family", INBOX, fields("g5", conversation="c5", sender="bob@example.com"))
    store.upsert_mail("family", SENT, fields("s9", conversation="c9", recipients="bob@example.com"))
    assert [m.id for m in store.mail_needing_reply("2026-10-01T00:00:00+00:00")] == [urgent, reply]
    assert [m.id for m in store.mail_actions("2026-10-07T00:00:00+00:00", "2026-10-01T00:00:00+00:00")] == [action]
    assert [m.id for m in store.unlabelled_from_known("2026-10-01T00:00:00+00:00")] == [known]


def test_search_over_sender_subject_and_summary(store: Store) -> None:
    first = store.upsert_mail("family", INBOX, fields("g1", subject="Olive harvest dates"))
    store.set_mail_label(first, "reply", "Asks about November shipping.")
    store.upsert_mail("work", INBOX, fields("g2", subject="Pricing", conversation="c2"))
    assert [m.id for m in store.search_mail("shipping")] == [first]
    assert [m.account for m in store.search_mail("anna", account="work")] == ["work"]
    assert store.search_mail("nothing-like-this") == []


def test_mail_by_graph_id(store: Store) -> None:
    mail_id = store.upsert_mail("family", INBOX, fields("g1"))
    found = store.mail_by_graph_id("family", "g1")
    assert found is not None and found.id == mail_id
    assert store.mail_by_graph_id("work", "g1") is None


def test_actions_count_from_when_they_were_labelled(store: Store) -> None:
    # sorted after the digest: still new for the next one, although it arrived before
    late = store.upsert_mail("family", INBOX, fields("g1", conversation="c1", at="2026-10-08T07:10:00+00:00"))
    store.set_mail_label(late, "action", "Confirm the appointment.")
    store._conn.execute("UPDATE mail_messages SET labelled_at = '2026-10-08T08:00:00+00:00' WHERE id = ?", (late,))
    shown = store.upsert_mail("family", INBOX, fields("g2", conversation="c2", at="2026-10-08T06:00:00+00:00"))
    store.set_mail_label(shown, "action", "Pay the invoice.")
    store._conn.execute("UPDATE mail_messages SET labelled_at = '2026-10-08T06:30:00+00:00' WHERE id = ?", (shown,))
    old = store.upsert_mail("family", INBOX, fields("g3", conversation="c3", at="2026-09-01T06:00:00+00:00"))
    store.set_mail_label(old, "action", "Old reminder.")  # labelled now, but older than the window
    since, floor = "2026-10-08T07:15:00+00:00", "2026-10-01T07:15:00+00:00"
    assert [m.id for m in store.mail_actions(since, floor)] == [late]


def test_a_known_sender_is_matched_exactly(store: Store) -> None:
    # "_" and "%" in an address are not wildcards: john_doe@ is not johnxdoe@
    store.upsert_mail("family", SENT, fields("s1", conversation="c1", recipients="johnxdoe@example.com"))
    store.upsert_mail("family", INBOX, fields("g1", conversation="c2", sender="john_doe@example.com"))
    assert store.unlabelled_from_known("2026-10-01T00:00:00+00:00") == []


def test_upgrade_to_v6_keeps_sorting_records_finds_correspondents_and_reads_sent_again(tmp_path: Path) -> None:
    path = tmp_path / "assistant.db"
    store = Store.open(path)
    store.upsert_mail(
        "family", SENT, fields("s1", sender="info@example.com", recipients="Bob@Example.com,carol@example.com")
    )
    kept = store.upsert_mail("family", INBOX, fields("g1"))
    store.record_triage(kept, "jev", 0.95, None)
    store.set_mail_delta_link("family", SENT, "https://graph.example/sent-delta")
    store.set_mail_delta_link("family", INBOX, "https://graph.example/inbox-delta")
    store._conn.executescript(  # back to version 5 as it was first built
        "BEGIN; DROP TABLE reminders; DROP TABLE followups; DROP TABLE initiative_log;"  # nor any later version
        " DROP TABLE known_devices; ALTER TABLE messages DROP COLUMN source_device;"
        " DROP TABLE doc_pages_fts; DROP TABLE doc_pages; DROP TABLE doc_files;"
        " ALTER TABLE attachments DROP COLUMN filename;"
        " ALTER TABLE messages DROP COLUMN footer; ALTER TABLE conversations DROP COLUMN kind;"
        f" DROP TABLE mail_correspondents; DROP INDEX mail_triage_created; ALTER TABLE mail_triage RENAME TO t;"
        f" {MIGRATION_V5}"
        " INSERT INTO mail_triage SELECT * FROM t; DROP TABLE t; PRAGMA user_version = 5; COMMIT;"
    )
    store.close()
    store = Store.open(path)
    assert store.has_written_to("bob@example.com") and store.has_written_to("carol@example.com")
    assert store.mail_delta_link("family", SENT) is None  # Sent is read again, now with Cc
    assert store.mail_delta_link("family", INBOX) == "https://graph.example/inbox-delta"
    store.remove_mail("family", INBOX, "g1")
    assert store.triage_since("2000-01-01T00:00:00+00:00") == [("jev", 0.95, None)]  # no longer tied to the mail
    store.close()


def test_correspondents_come_from_to_and_cc_of_sent_mail_only(store: Store) -> None:
    store.upsert_mail("family", INBOX, fields("g1", recipients="dave@example.com"))
    store.upsert_mail("family", SENT, fields("s1", sender="info@example.com", recipients="Eve@Example.com"))
    assert store.has_written_to(" EVE@example.com ") and not store.has_written_to("dave@example.com")
    assert not store.has_written_to("")


def test_a_reused_mail_number_starts_a_fresh_sorting_record(store: Store) -> None:
    store.record_triage(7, "gemma", 0.2, "reply")
    store._conn.execute("UPDATE mail_triage SET created_at = '2026-08-01T00:00:00+00:00'")
    store.record_triage(7, "jev", 0.97, None)  # a later mail got the number of a deleted one
    assert store.triage_since("2026-10-01T00:00:00+00:00") == [("jev", 0.97, None)]
