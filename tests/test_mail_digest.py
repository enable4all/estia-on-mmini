from datetime import datetime
from pathlib import Path

from local_assistant.config import Config, MailAccount
from local_assistant.mail_digest import MailDigest, MailNumbers
from local_assistant.store import MailFields, Store
from tests.conftest import ME, TZ

# Thursday 2026-10-08 09:15 in Amsterdam
DIGEST_TIME = datetime(2026, 10, 8, 9, 15, tzinfo=TZ).timestamp()


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def config(tmp_path: Path) -> Config:
    return Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        mail_tenant_id="11111111-2222-4333-8444-555555555555",
        mail_client_id="66666666-7777-4888-9999-000000000000",
        mail_accounts=(MailAccount("family", "info@example.com"), MailAccount("work", "contact@example.app")),
    )


def add(
    store: Store,
    graph_id: str,
    label: str | None,
    summary: str,
    at: str,
    *,
    account: str = "family",
    sender: str = "Anna Smith",
    subject: str = "Subject",
    address: str = "anna@example.com",
) -> int:
    mail_id = store.upsert_mail(
        account,
        "inbox",
        MailFields(
            graph_id,
            "c-" + graph_id,
            "<x>",
            sender,
            address,
            "info@example.com",
            subject,
            at,
            False,
            False,
            "normal",
            True,
        ),
    )
    if label:
        store.set_mail_label(mail_id, label, summary)
    return mail_id


def make(tmp_path: Path, store: Store, clock: Clock) -> MailDigest:
    return MailDigest(store, config(tmp_path), MailNumbers(store, TZ, clock), clock=clock)


def test_compose_lists_replies_then_actions_with_day_numbers(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    digest = make(tmp_path, store, clock)
    add(
        store,
        "g1",
        "reply",
        "Asks about November shipping.",
        "2026-10-06T07:00:00+00:00",
        sender="Bob Lee",
        account="work",
    )
    add(store, "g2", "urgent_reply", "Contract needs a signature today.", "2026-10-08T06:00:00+00:00")
    add(store, "g3", "action", "Invoice to pay by Friday.", "2026-10-07T20:00:00+00:00", sender="Acme Billing")
    add(store, "g4", "reply", "Too old.", "2026-09-28T07:00:00+00:00")
    add(store, "g5", "noise", "Newsletter.", "2026-10-08T05:00:00+00:00")
    assert digest.compose() == (
        "Mail, Thu 8 Oct:\n"
        "Needs a reply:\n"
        "1. [family] Anna Smith: Contract needs a signature today. (today)\n"
        "2. [work] Bob Lee: Asks about November shipping. (2 d)\n"
        "Actions:\n"
        "3. [family] Acme Billing: Invoice to pay by Friday. (1 d)"
    )


def test_unlabelled_mail_from_known_people_shows_its_subject(tmp_path: Path, store: Store) -> None:
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    add(
        store,
        "g1",
        None,
        "",
        "2026-10-08T06:00:00+00:00",
        sender="Carl",
        subject="Dinner Saturday?",
        address="carl@example.com",
    )
    store.upsert_mail(
        "family",
        "sent",
        MailFields(
            "s1",
            "c-x",
            "<y>",
            "",
            "info@example.com",
            "carl@example.com",
            "Hi",
            "2026-10-01T06:00:00+00:00",
            True,
            False,
            "normal",
            True,
        ),
    )
    assert "1. [family] Carl: Dinner Saturday? (today)" in digest.compose()


def test_empty_day_and_long_lists(tmp_path: Path, store: Store) -> None:
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    assert digest.compose() == "Mail: nothing needs you today."
    for n in range(13):
        add(store, f"g{n}", "reply", f"Question {n}.", f"2026-10-07T{n:02d}:00:00+00:00")
    text = digest.compose()
    assert "10. [family]" in text and "11. [family]" not in text
    assert text.endswith("…and 3 more")


def test_numbers_stay_for_the_day_and_restart_the_next(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    numbers = MailNumbers(store, TZ, clock)
    a, b = (
        add(store, "g1", "reply", "A.", "2026-10-08T06:00:00+00:00"),
        add(store, "g2", "reply", "B.", "2026-10-08T06:00:00+00:00"),
    )
    assert (numbers.number(a), numbers.number(b), numbers.number(a)) == (1, 2, 1)
    assert numbers.mail_id(2) == b and numbers.mail_id(9) is None
    clock.now += 86_400
    assert numbers.number(b) == 1


# a call's glance: the day's mail as numbers only, never a sender, subject or summary (no outside text on the call)


def test_the_call_glance_counts_without_words_or_numbers_of_the_day(tmp_path: Path, store: Store) -> None:
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    assert digest.glance() == "Mail digest: nothing needs a reply or an action."
    add(store, "g1", "reply", "Asks about November shipping.", "2026-10-06T07:00:00+00:00", sender="Bob Lee")
    add(store, "g2", "urgent_reply", "Contract needs a signature today.", "2026-10-08T06:00:00+00:00")
    add(store, "g3", "action", "Invoice to pay by Friday.", "2026-10-07T20:00:00+00:00", sender="Acme Billing")
    add(store, "g4", "reply", "Too old.", "2026-09-28T07:00:00+00:00")
    add(store, "g5", "noise", "Newsletter.", "2026-10-08T05:00:00+00:00")
    glance = digest.glance()
    assert glance == "Mail digest: 2 to answer (1 urgent), 1 action."
    for words in ("Bob", "Anna", "Acme", "Contract", "Invoice", "shipping", "Subject"):
        assert words not in glance
    assert store.get_state("mail:numbers") is None  # counting gives no mail its number of the day
    add(store, "g6", "reply", "One more.", "2026-10-08T07:00:00+00:00")
    assert digest.glance() == "Mail digest: 3 to answer (1 urgent), 1 action."
