from datetime import datetime
from pathlib import Path

import pytest

from local_assistant.config import Config, MailAccount
from local_assistant.mail_digest import NOTE_CHARS, MailDigest, MailNumbers
from local_assistant.part_status import PartStatus, Reason, mail_part
from local_assistant.store import MailFields, Store
from tests.conftest import ME, TZ
from tests.no_links import CAP_WORDS, CASES, PLANTED, assert_no_link, assert_no_tappable_link, capped

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
    assert digest.compose() == (  # the day's numbers in the morning digest's order: family's mail, then work's
        "Mail, Thu 8 Oct:\n"
        "Needs a reply:\n"
        "1. [family] Anna Smith: Contract needs a signature today. (today)\n"
        "3. [work] Bob Lee: Asks about November shipping. (2 d)\n"
        "Actions:\n"
        "2. [family] Acme Billing: Invoice to pay by Friday. (1 d)"
    )


def test_the_morning_digest_gives_each_mailbox_its_own_block(tmp_path: Path, store: Store) -> None:
    """The morning digest's part: a bold heading per mailbox, its replies then its actions, the day's numbers in the
    order shown; the mail_digest tool (Gemma's) keeps its own lines, numbers as given here."""
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
    add(store, "g4", "reply", "Wants the school form back.", "2026-10-07T15:00:00+00:00", sender="Mia Jansen")
    assert digest.digest_part() == (
        "**Mail (family)**\n"
        "Needs a reply:\n"
        "1. Anna Smith: Contract needs a signature today. (today)\n"
        "2. Mia Jansen: Wants the school form back. (1 d)\n"
        "Actions:\n"
        "3. Acme Billing: Invoice to pay by Friday. (1 d)\n"
        "\n"
        "**Mail (work)**\n"
        "Needs a reply:\n"
        "4. Bob Lee: Asks about November shipping. (2 d)"
    )
    assert "4. [work] Bob Lee: Asks about November shipping. (2 d)" in digest.compose()  # the tool's lines


def test_the_digest_reads_one_to_n_also_when_the_tool_gave_numbers_first(tmp_path: Path, store: Store) -> None:
    """ "Any mail?" at 08:00 runs the mail_digest tool before the 09:15 digest: numbers follow the digest's order
    (each mailbox's replies, then its actions) whoever reads first, and the tool keeps its own lines."""
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    add(store, "g1", "reply", "Asks about November shipping.", "2026-10-06T07:00:00+00:00", account="work")
    add(store, "g2", "urgent_reply", "Contract needs a signature today.", "2026-10-08T06:00:00+00:00")
    add(store, "g3", "action", "Invoice to pay by Friday.", "2026-10-07T20:00:00+00:00", sender="Acme Billing")
    add(store, "g4", "reply", "Wants the school form back.", "2026-10-07T15:00:00+00:00", sender="Mia Jansen")
    tool = digest.compose()
    assert tool.splitlines()[2:4] == [
        "1. [family] Anna Smith: Contract needs a signature today. (today)",
        "4. [work] Anna Smith: Asks about November shipping. (2 d)",
    ]
    numbers = [line.split(".")[0] for line in digest.digest_part().splitlines() if line[:1].isdigit()]
    assert numbers == ["1", "2", "3", "4"]


def test_the_limit_counts_all_mailboxes_together(tmp_path: Path, store: Store) -> None:
    """At most BLOCK_LIMIT replies (and BLOCK_LIMIT actions) over all mailboxes, as the tool shows them; the rest is
    counted under each mailbox, so a digest with two mailboxes is no longer than one with a single mailbox."""
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    for n in range(8):
        add(store, f"f{n}", "reply", f"Family {n}.", f"2026-10-07T{n:02d}:00:00+00:00")
        add(store, f"w{n}", "reply", f"Work {n}.", f"2026-10-07T{n:02d}:30:00+00:00", account="work")
    lines = digest.digest_part().splitlines()
    assert [line.split(".")[0] for line in lines if line[:1].isdigit()] == [str(n) for n in range(1, 11)]
    assert lines.count("…and 3 more") == 2
    assert lines[lines.index("**Mail (work)**") - 2 : lines.index("**Mail (work)**")] == ["…and 3 more", ""]
    assert "Family 4." in lines[6] and "Work 0." in lines[11]


def test_one_mailbox_with_mail_is_still_named_when_several_are_set_up(tmp_path: Path, store: Store) -> None:
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    assert digest.digest_part() == "Mail: nothing needs you today."
    add(store, "g1", "reply", "Asks about November shipping.", "2026-10-06T07:00:00+00:00", account="work")
    assert digest.digest_part().splitlines()[0] == "**Mail (work)**"
    one = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        mail_tenant_id="11111111-2222-4333-8444-555555555555",
        mail_client_id="66666666-7777-4888-9999-000000000000",
        mail_accounts=(MailAccount("work", "contact@example.app"),),
    )
    alone = MailDigest(store, one, MailNumbers(store, TZ, Clock(DIGEST_TIME)), clock=Clock(DIGEST_TIME))
    assert alone.digest_part() == "**Mail**\nNeeds a reply:\n1. Anna Smith: Asks about November shipping. (2 d)"


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


# a mailbox that has not been read (failing, its last good read older than about two sync rounds) gets one line, in
# the digest, the call's glance and the mail tools' headers; the rest of the digest stays


MONDAY_0910 = datetime(2026, 10, 5, 9, 10, tzinfo=TZ).timestamp()


def stale_work(clock: Clock, *, ok: float | None = MONDAY_0910, reason: Reason = "sign_in") -> PartStatus:
    """'work' read fine at `ok`, failing since five minutes later; 'family' read fine just now."""
    status = PartStatus(None, clock=clock)
    now = clock.now
    if ok is not None:
        clock.now = ok
        status.ok(mail_part("work"))
    clock.now = (ok or MONDAY_0910) + 300
    status.failed(mail_part("work"), reason)
    clock.now = now
    status.ok(mail_part("family"))
    return status


def with_status(tmp_path: Path, store: Store, clock: Clock, status: PartStatus) -> MailDigest:
    return MailDigest(store, config(tmp_path), MailNumbers(store, TZ, clock), clock=clock, status=status)


def test_a_mailbox_not_read_since_monday_gets_one_line_and_the_rest_stays(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    digest = with_status(tmp_path, store, clock, stale_work(clock))
    line = "[work] not read since Mon 09:10: needs sign-in."
    assert digest.stale_lines() == [line]
    assert digest.digest_part() == f"Mail: nothing needs you today.\n\n{line}"
    add(store, "g1", "reply", "Asks about November shipping.", "2026-10-06T07:00:00+00:00", sender="Bob Lee")
    part = digest.digest_part()
    assert part.startswith("**Mail (family)**\nNeeds a reply:\n1. Bob Lee: Asks about November shipping. (2 d)")
    assert part.endswith(f"\n\n{line}")


def test_one_bad_round_is_not_flagged(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    digest = with_status(tmp_path, store, clock, stale_work(clock, ok=DIGEST_TIME - 9 * 60, reason="no_answer"))
    assert digest.stale_lines() == []  # two rounds of 5 minutes: read 9 minutes ago is not stale yet
    clock.now += 2 * 60
    assert digest.stale_lines() == ["[work] not read since 09:06: no answer."]
    assert digest.digest_part().endswith("\n\n[work] not read since 09:06: no answer.")


def test_a_working_mailbox_or_none_on_record_is_never_flagged(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    status = PartStatus(None, clock=clock)
    clock.now = MONDAY_0910
    status.ok(mail_part("work"))  # working, last read on Monday: the bridge was down, not the mailbox
    clock.now = DIGEST_TIME
    assert with_status(tmp_path, store, clock, status).stale_lines() == []
    assert make(tmp_path, store, clock).stale_lines() == []  # no status: nothing known


def test_a_mailbox_never_read_says_not_read_yet(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    digest = with_status(tmp_path, store, clock, stale_work(clock, ok=None, reason="refused"))
    assert digest.stale_lines() == ["[work] not read yet: refused by the service."]


def test_a_mailbox_not_read_for_a_week_names_the_date(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    week_ago = datetime(2026, 10, 1, 8, 0, tzinfo=TZ).timestamp()
    digest = with_status(tmp_path, store, clock, stale_work(clock, ok=week_ago))
    assert digest.stale_lines() == ["[work] not read since 1 Oct 08:00: needs sign-in."]


def test_the_call_glance_counts_stale_mailboxes_without_their_names(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    digest = with_status(tmp_path, store, clock, stale_work(clock))
    glance = digest.glance()
    assert glance == "Mail digest: nothing needs a reply or an action.\n1 mailbox not read since Mon 09:10."
    assert "work" not in glance and "sign-in" not in glance
    add(store, "g1", "reply", "Asks about November shipping.", "2026-10-06T07:00:00+00:00", sender="Bob Lee")
    assert digest.glance() == "Mail digest: 1 to answer.\n1 mailbox not read since Mon 09:10."


def test_two_stale_mailboxes_are_one_count_since_the_later_read(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    status = stale_work(clock)
    tuesday = datetime(2026, 10, 6, 11, 30, tzinfo=TZ).timestamp()
    clock.now = tuesday
    status.ok(mail_part("family"))
    clock.now = tuesday + 300
    status.failed(mail_part("family"), "no_answer")
    clock.now = DIGEST_TIME
    digest = with_status(tmp_path, store, clock, status)
    assert digest.stale_lines() == [
        "[family] not read since Tue 11:30: no answer.",
        "[work] not read since Mon 09:10: needs sign-in.",
    ]
    assert digest.glance().endswith("\n2 mailboxes not read since Tue 11:30.")  # neither read since Tuesday


def test_one_bad_round_after_a_long_downtime_is_not_flagged_either(tmp_path: Path, store: Store) -> None:
    clock = Clock(MONDAY_0910)
    status = PartStatus(None, clock=clock)
    status.ok(mail_part("work"))  # read fine on Monday, then the bridge was down until Thursday
    clock.now = DIGEST_TIME - 60
    status.failed(mail_part("work"), "no_answer")  # the first round after the start fails
    clock.now = DIGEST_TIME
    digest = with_status(tmp_path, store, clock, status)
    assert digest.stale_lines() == []
    clock.now += 5 * 60  # the next round failed too (or has not fixed it): flagged
    assert digest.stale_lines() == ["[work] not read since Mon 09:10: no answer."]


def test_the_digest_leaves_out_every_link_and_the_mail_tool_keeps_its_text(tmp_path: Path, store: Store) -> None:
    """Owner, 21 Oct: the 09:15 digest quotes a sender, a summary or a subject without its links (never shown, not
    defanged; a sender with only an address is named by the part before the @). The mail_digest tool's text, Gemma's,
    stays byte for byte as in 0.9.5."""
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    for n, link in enumerate(PLANTED):
        add(
            store,
            f"g{n}",
            "reply",
            f"Asks you to log in at {link} today.",
            f"2026-10-0{n + 3}T07:00:00+00:00",
            sender=f"Security {link}",
            subject=f"Subject {link}",
        )
    add(
        store,
        "g8",
        "action",
        "",
        "2026-10-08T07:00:00+00:00",
        sender="",
        address="phish@evil.example",
        subject="Pay at evil.com/pay now",
    )
    add(
        store,
        "g9",
        "action",
        "",
        "2026-10-08T07:30:00+00:00",
        sender="paypal.com@evil.example",
        address="paypal.com@evil.example",
        subject="https://evil.example/login",
    )
    part = digest.digest_part()
    assert_no_link(part, "Security: Asks you to log in at today.", "phish: Pay at now (today)")
    assert part == (
        "**Mail (family)**\nNeeds a reply:\n"
        + "".join(f"{n + 1}. Security: Asks you to log in at today. ({5 - n} d)\n" for n in range(5))
        + "Actions:\n6. phish: Pay at now (today)\n7. (no name): (no subject) (today)"
    )
    assert digest.compose() == (
        "Mail, Thu 8 Oct:\n"
        "Needs a reply:\n"
        "1. [family] Security https://evil.example/login: Asks you to log in at https://evil.example/login today. (5 d)\n"
        "2. [family] Security www.evil.example/x: Asks you to log in at www.evil.example/x today. (4 d)\n"
        "3. [family] Security evil.com: Asks you to log in at evil.com today. (3 d)\n"
        "4. [family] Security **evil**.com: Asks you to log in at **evil**.com today. (2 d)\n"
        "5. [family] Security phish@evil.example: Asks you to log in at phish@evil.example today. (1 d)\n"
        "Actions:\n"
        "6. [family] phish@evil.example: Pay at evil.com/pay now (today)\n"
        "7. [family] paypal.com@evil.example: https://evil.example/login (today)"
    )


@pytest.mark.parametrize("text", [case[0] for case in CASES])
def test_the_digest_leaves_out_what_the_re_review_got_through(tmp_path: Path, store: Store, text: str) -> None:
    """The re-review and the net (21 Oct): a planted link in a sender, a subject and a summary leaves the digest with
    no tappable link (a broken word may stay; the link is gone). Gemma's mail_digest tool keeps them byte for byte."""
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    add(store, "g1", "reply", "", "2026-10-06T07:00:00+00:00", sender=text, subject=text)
    add(store, "g2", "reply", text, "2026-10-07T07:00:00+00:00", sender="Bank")
    part = digest.digest_part()
    lines = part.splitlines()[2:]
    assert lines[0].startswith("1. ") and lines[0].endswith("(2 d)") and lines[1].startswith("2. Bank: ")
    assert_no_tappable_link(part)
    assert f"1. [family] {text}: {text} (2 d)\n2. [family] Bank: {text} (1 d)" in digest.compose()


@pytest.mark.parametrize("word", CAP_WORDS)
def test_the_digest_s_cap_never_makes_a_link_of_a_word_that_is_none(tmp_path: Path, store: Store, word: str) -> None:
    digest = make(tmp_path, store, Clock(DIGEST_TIME))
    add(
        store,
        "g1",
        "reply",
        "",
        "2026-10-07T07:00:00+00:00",
        sender=capped(NOTE_CHARS, word),
        subject=capped(NOTE_CHARS, word),
    )
    part = digest.digest_part()
    assert "1. AAAA" in part
    assert_no_tappable_link(part)
