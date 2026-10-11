import asyncio
import sqlite3
from pathlib import Path

import pytest

from local_assistant.mail_digest import EMPTY, MailDigest, MailNumbers
from local_assistant.morning_digest import DIGEST_DAY_KEY, MorningDigest
from local_assistant.store import Store
from tests.conftest import TZ
from tests.test_calendar_tools import Home
from tests.test_mail_digest import DIGEST_TIME, Clock, add, config


class Sent(list[str]):
    async def __call__(self, text: str) -> None:
        self.append(text)


def mail_only(tmp_path: Path, store: Store, clock: Clock, ready: bool = True) -> tuple[MorningDigest, Sent]:
    sent = Sent()
    mail = MailDigest(store, config(tmp_path), MailNumbers(store, TZ, clock), clock=clock)
    digest = MorningDigest(
        store, config(tmp_path), mail=mail, calendar=None, send=sent, clock=clock, mail_ready=lambda: ready
    )
    return digest, sent


def with_calendar(
    tmp_path: Path, store: Store, *, mail: bool = True, seconds: float = 30.0
) -> tuple[MorningDigest, Sent, Home]:
    home = Home(tmp_path)  # Friday 9 Oct 2026 09:15
    clock = Clock(home.clock())
    sent = Sent()
    digest = MailDigest(store, config(tmp_path), MailNumbers(store, TZ, clock), clock=clock) if mail else None
    morning = MorningDigest(
        store, config(tmp_path), mail=digest, calendar=home.tools, send=sent, clock=clock, calendar_seconds=seconds
    )
    return morning, sent, home


async def test_the_digest_is_sent_once_a_day_at_its_time(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME - 60)
    digest, sent = mail_only(tmp_path, store, clock)
    await digest.tick()
    assert sent == []
    clock.now = DIGEST_TIME
    await digest.tick()
    await digest.tick()
    assert sent == ["Mail: nothing needs you today."]
    clock.now += 2 * 86_400  # Saturday too
    await digest.tick()
    assert len(sent) == 2


async def test_a_missed_digest_comes_at_start_until_noon(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME + 2 * 3600)  # 11:15, the bridge just started
    digest, sent = mail_only(tmp_path, store, clock)
    await digest.tick()
    assert len(sent) == 1
    late = Clock(DIGEST_TIME + 86_400 + 4 * 3600)  # next day 13:15
    later, sent_late = mail_only(tmp_path, store, late)
    await later.tick()
    assert sent_late == []


async def test_the_day_of_the_update_never_gets_a_second_digest(tmp_path: Path, store: Store) -> None:
    store.set_state("mail:digest_day", "2026-10-08")  # sent at 09:15 by the mail digest before the update
    assert DIGEST_DAY_KEY == "mail:digest_day"
    digest, sent = mail_only(tmp_path, store, Clock(DIGEST_TIME + 3600))
    await digest.tick()
    assert sent == []


async def test_actions_come_once_since_the_previous_digest(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    digest, sent = mail_only(tmp_path, store, clock)
    paid = add(store, "g1", "action", "Pay the invoice.", "2026-10-08T05:00:00+00:00")
    store._conn.execute("UPDATE mail_messages SET labelled_at = '2026-10-08T05:30:00+00:00' WHERE id = ?", (paid,))
    await digest.tick()
    assert "Pay the invoice." in sent[0]
    clock.now += 86_400
    await digest.tick()
    assert sent[1] == "Mail: nothing needs you today."


async def test_an_action_sorted_after_the_digest_comes_the_next_day(tmp_path: Path, store: Store) -> None:
    clock = Clock(DIGEST_TIME)
    digest, sent = mail_only(tmp_path, store, clock)
    late = add(store, "g1", None, "", "2026-10-08T07:10:00+00:00", subject="Appointment")  # 09:10, not sorted yet
    await digest.tick()
    assert sent == [EMPTY]
    store.set_mail_label(late, "action", "Confirm the dentist appointment.")
    store._conn.execute("UPDATE mail_messages SET labelled_at = '2026-10-08T07:30:00+00:00' WHERE id = ?", (late,))
    clock.now += 86_400
    await digest.tick()
    assert "Confirm the dentist appointment." in sent[1]


async def test_a_digest_that_failed_is_tried_again(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(DIGEST_TIME)
    digest, sent = mail_only(tmp_path, store, clock)
    mail = digest._mail
    assert mail is not None
    compose = mail.compose
    tries: list[int] = []

    def flaky(header: str | None = None) -> str:
        tries.append(1)
        if len(tries) == 1:
            raise sqlite3.OperationalError("database is locked")
        return compose(header)

    monkeypatch.setattr(mail, "compose", flaky)
    with pytest.raises(sqlite3.OperationalError):
        await digest.tick()
    clock.now += 60
    await digest.tick()  # a minute later: still today's digest
    assert sent == [EMPTY]


async def test_the_digest_waits_for_the_first_mail_sync_after_a_start(tmp_path: Path, store: Store) -> None:
    # a restart in the morning must not send "nothing needs you" before the mail has been synced
    clock = Clock(DIGEST_TIME + 600)
    digest, sent = mail_only(tmp_path, store, clock, ready=False)
    await digest.tick()
    assert sent == []
    add(store, "g1", "reply", "Anna asks to move Friday's meeting.", "2026-10-08T06:00:00+00:00")
    digest, sent = mail_only(tmp_path, store, clock, ready=True)
    await digest.tick()
    assert "Anna asks to move Friday's meeting." in sent[0]


async def test_the_calendar_comes_first_then_the_mail(tmp_path: Path, store: Store) -> None:
    morning, sent, home = with_calendar(tmp_path, store)
    home.add("Dentist", "2026-10-09 09:30", "2026-10-09 10:00", place="Prinsengracht 12")
    home.add("Trip to Lisbon", "2026-10-10", "2026-10-12")
    add(store, "g1", "reply", "Anna asks to move Friday's meeting.", "2026-10-09T06:00:00+00:00")
    await morning.tick()
    assert sent == [
        "Fri 9 Oct\n"
        "Calendar today:\n"
        "- 09:30–10:00 Dentist (Prinsengracht 12)\n"
        "Tomorrow: all day: Trip to Lisbon\n"
        "Mail:\n"
        "Needs a reply:\n"
        "1. [family] Anna Smith: Anna asks to move Friday's meeting. (today)"
    ]
    assert morning._mail is not None
    assert morning._mail.compose().startswith("Mail, Fri 9 Oct:\nNeeds a reply:")  # the mail_digest tool: mail only


async def test_a_calendar_only_digest_does_not_wait_for_mail(tmp_path: Path, store: Store) -> None:
    morning, sent, _ = with_calendar(tmp_path, store, mail=False)
    await morning.tick()
    assert sent == ["Fri 9 Oct\nCalendar today: nothing\nTomorrow: nothing"]


async def test_google_down_or_slow_never_holds_the_mail_back(tmp_path: Path, store: Store) -> None:
    morning, sent, home = with_calendar(tmp_path, store, seconds=0.05)
    home.fail("unreachable")
    await morning.tick()
    assert sent == ["Fri 9 Oct\nCalendar: not available right now\nMail: nothing needs you today."]

    async def hangs() -> str:
        await asyncio.sleep(3600)
        return ""

    store.set_state(DIGEST_DAY_KEY, "")
    home.tools.digest_block = hangs  # type: ignore[method-assign]
    await morning.tick()
    assert sent[1] == sent[0]


async def test_a_broken_calendar_block_is_not_available(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    morning, sent, home = with_calendar(tmp_path, store, mail=False)

    async def broken() -> str:
        raise KeyError("start")

    monkeypatch.setattr(home.tools, "digest_block", broken)
    await morning.tick()
    assert sent == ["Fri 9 Oct\nCalendar: not available right now"]
