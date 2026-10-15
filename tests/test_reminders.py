"""Reminders and wake-up calls: times, checks, notes, the scheduler and the restart (fake clock, never wall time)."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from local_assistant import prompts
from local_assistant.reminders import (
    CALLS_DAY_KEY,
    ReminderError,
    Reminders,
    ReminderScheduler,
    nearest_year,
    next_occurrence,
    own_words,
    parse_when,
    resolve_local,
)
from local_assistant.store import Store
from tests.conftest import ME, STRANGER

TZ = ZoneInfo("Europe/Amsterdam")


def at(text: str, tz: ZoneInfo = TZ) -> float:
    """Epoch seconds of a local 'YYYY-MM-DD HH:MM[:SS]'."""
    return datetime.fromisoformat(text).replace(tzinfo=tz).timestamp()


NOW = at("2026-10-12 14:02:30")  # a Monday afternoon


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def service(store: Store, clock: Clock, **options: object) -> Reminders:
    settings: dict[str, object] = {"calls": True}
    settings.update(options)
    return Reminders(store, TZ, clock=clock, **settings)  # type: ignore[arg-type]


# times


def test_a_time_in_the_spring_forward_gap_moves_an_hour_on_and_says_so() -> None:
    moment, shifted = resolve_local(datetime(2026, 3, 29, 2, 30), TZ)
    assert shifted and (moment.hour, moment.minute) == (3, 30) and moment.utcoffset() is not None
    assert moment.timestamp() == at("2026-03-29 03:30")


def test_a_time_that_occurs_twice_in_autumn_takes_the_first() -> None:
    moment, shifted = resolve_local(datetime(2026, 10, 25, 2, 30), TZ)
    assert not shifted
    assert moment.timestamp() == datetime(2026, 10, 25, 0, 30, tzinfo=UTC).timestamp()  # still summer time (+2)


def test_repeats_keep_their_local_time_across_both_dst_changes() -> None:
    meant, due = next_occurrence("2026-10-24 08:00", "daily", TZ, after=at("2026-10-24 08:00"))
    assert meant == "2026-10-25 08:00" and due == datetime(2026, 10, 25, 7, 0, tzinfo=UTC).timestamp()  # winter
    meant, due = next_occurrence("2026-03-28 08:00", "daily", TZ, after=at("2026-03-28 08:00"))
    assert meant == "2026-03-29 08:00" and due == datetime(2026, 3, 29, 6, 0, tzinfo=UTC).timestamp()  # summer


def test_weekdays_skip_the_weekend_and_weekly_keeps_the_day() -> None:
    assert next_occurrence("2026-10-16 08:00", "weekdays", TZ, after=at("2026-10-16 08:00"))[0] == "2026-10-19 08:00"
    assert next_occurrence("2026-10-12 08:00", "weekly", TZ, after=at("2026-10-12 08:00"))[0] == "2026-10-19 08:00"
    # long missed (the bridge was down): the next one after now, not one in the past
    assert next_occurrence("2026-10-01 08:00", "daily", TZ, after=NOW)[0] == "2026-10-13 08:00"


def test_a_zone_other_than_the_macs() -> None:
    york = ZoneInfo("America/New_York")
    meant, due = next_occurrence("2026-10-12 08:00", "daily", york, after=at("2026-10-12 08:00", york))
    assert meant == "2026-10-13 08:00" and due == at("2026-10-13 08:00", york)


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        (["17:00"], ("at", "17:00")),
        (["2026-10-13", "07:00"], ("at", "2026-10-13 07:00")),
        (["20m"], ("in", 20)),
        (["2h"], ("in", 120)),
        (["3d"], ("in", 4320)),
    ],
)
def test_the_remind_command_times(words: list[str], expected: tuple[str, object]) -> None:
    assert parse_when(words) == (expected, len(words))


@pytest.mark.parametrize("words", [["soon"], ["25:00"], ["0m"], ["2026-13-01", "07:00"], []])
def test_unreadable_command_times(words: list[str]) -> None:
    with pytest.raises(ReminderError):
        parse_when(words)


# the date Gemma writes: a day long past means the next one


@pytest.mark.parametrize(
    ("value", "today", "meant"),
    [
        ("2026-05-20 09:00", date(2026, 10, 12), "2027-05-20 09:00"),  # "20 May" asked in October: next May
        ("2026-01-05 09:00", date(2026, 12, 20), "2027-01-05 09:00"),  # "5 January" asked in December
        ("2025-01-05 09:00", date(2026, 12, 20), "2027-01-05 09:00"),  # a guessed year: the nearest, next January
        ("2026-10-09 07:00", date(2026, 10, 12), "2027-10-09 07:00"),  # 3 days past: the next one
        ("2026-10-10 07:00", date(2026, 10, 12), "2026-10-10 07:00"),  # 2 days past: refused later ("passed")
        ("2026-10-12 07:00", date(2026, 10, 12), "2026-10-12 07:00"),  # earlier today: refused later
        ("2026-10-13 07:00", date(2026, 10, 12), "2026-10-13 07:00"),  # ahead: as written
        ("2027-05-20 09:00", date(2026, 10, 12), "2027-05-20 09:00"),
        ("2026-02-29 09:00", date(2026, 10, 12), "2026-02-29 09:00"),  # no such day: refused later ("bad_time")
        ("17:00", date(2026, 10, 12), "17:00"),
    ],
)
def test_a_day_long_past_is_read_as_the_next_one(value: str, today: date, meant: str) -> None:
    assert nearest_year(value, today) == meant


def test_a_repeating_reminder_never_starts_a_year_later() -> None:
    # "every day at 7 from 20 May" asked in October is a mistake to refuse, not a daily reminder from next May
    assert nearest_year("2026-05-20 07:00", date(2026, 10, 12), roll=False) == "2026-05-20 07:00"
    assert nearest_year("2025-10-13 07:00", date(2026, 10, 12), roll=False) == "2026-10-13 07:00"  # a guessed year


# setting one


def test_a_time_today_or_tomorrow_and_the_code_note(store: Store) -> None:
    reminders = service(store, Clock())
    created = reminders.set(ME, at="17:00", text="call the dentist", source_message_id=None)
    assert created.reminder.local_time == "2026-10-12 17:00" and created.reminder.due_at == at("2026-10-12 17:00")
    assert created.note == (
        '**Reminder set** (#1): Mon 12 Oct 17:00 · "call the dentist". /reminders to see or cancel.\n'
        "Only this note means a reminder is set."  # the contract, with the first one only
    )
    assert created.result.startswith("Set as #1 for Mon 12 Oct 17:00;")
    assert created.spoken == "Reminder set for Monday 12 October, 17:00."
    passed = reminders.set(ME, at="14:02", text="too late today")  # this minute: tomorrow
    assert passed.reminder.local_time == "2026-10-13 14:02"
    next_minute = reminders.set(ME, at="14:03", text="just in time")  # 30 s away: still today
    assert next_minute.reminder.local_time == "2026-10-12 14:03"


def test_the_first_confirmation_each_user_gets_says_only_this_note_means_one_is_set(store: Store) -> None:
    reminders = service(store, Clock())
    first = reminders.set(ME, at="17:00", text="call the dentist")
    assert first.note.splitlines()[1:] == [prompts.REMINDER_CONTRACT] == ["Only this note means a reminder is set."]
    assert prompts.REMINDER_CONTRACT not in reminders.set(ME, at="18:00", text="cook").note
    assert prompts.REMINDER_CONTRACT not in service(store, Clock()).set(ME, at="19:00", text="walk").note  # kept
    other = reminders.set(STRANGER, at="17:00", text="water the plants")  # another user's first one
    assert other.note.endswith("\n" + prompts.REMINDER_CONTRACT)
    assert prompts.REMINDER_CONTRACT not in first.spoken and prompts.REMINDER_CONTRACT not in first.result


def test_a_date_and_time_and_minutes_from_now(store: Store) -> None:
    reminders = service(store, Clock())
    dated = reminders.set(ME, at="2026-10-14 09:30", text="dentist")
    assert dated.reminder.due_at == at("2026-10-14 09:30")
    soon = reminders.set(ME, in_minutes=20, text="pizza")
    assert soon.reminder.due_at == NOW + 1200 and soon.reminder.local_time == "2026-10-12 14:22"
    assert reminders.set(ME, in_minutes="5", text="tea").reminder.due_at == NOW + 300  # "5" from Gemma counts


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"at": "2026-10-12 13:00"}, "has passed"),
        ({"at": "2027-11-01 09:00"}, "366 days"),
        ({"in_minutes": 0}, "at least a minute"),
        ({"in_minutes": "soon"}, "whole number of minutes"),
        ({"at": "tomorrow"}, "HH:MM"),
        ({}, "give a time"),
        ({"at": "17:00", "text": "   "}, "empty"),
        ({"at": "17:00", "text": "x" * 201}, "200 characters"),
        ({"at": "17:00", "repeat": "hourly"}, "repeat must be"),
    ],
)
def test_times_and_texts_that_are_refused(store: Store, options: dict[str, object], message: str) -> None:
    reminders = service(store, Clock())
    arguments: dict[str, object] = {"text": "call mum"}
    arguments.update(options)
    with pytest.raises(ReminderError, match=message):
        reminders.set(ME, **arguments)  # type: ignore[arg-type]
    assert store.open_reminders() == []


@pytest.mark.parametrize(
    ("key", "values", "plain", "spoken"),
    [
        ("no_time", {}, "no time was given", "no time was given"),
        ("bad_time", {"value": "x"}, "the time could not be read", "the time could not be read"),
        ("passed", {}, "that time has passed", "that time has passed"),
        ("too_far", {}, "that is more than 366 days ahead", "that is more than 366 days ahead"),
        ("too_long", {}, "the text is longer than 200 characters", "the text is too long"),
        (
            "max_open",
            {"limit": 50},
            "you have 50 open reminders; cancel one first with /reminders cancel N",
            "you have 50 open reminders; cancel one first",
        ),
        ("max_per_day", {"limit": 20}, "20 new reminders a day is the limit; try again tomorrow", None),
        (
            "max_calls",
            {"limit": 3},
            "3 calls that day is the limit; cancel one with /reminders cancel N, or ask for a text",
            "3 calls that day is the limit",
        ),
    ],
)
def test_a_refusal_has_a_plain_reason_for_the_user_and_one_to_say(
    key: str, values: dict[str, object], plain: str, spoken: str | None
) -> None:
    # Gemma's tool keeps its own wording (str(error)); the user reads and hears plain words
    error = ReminderError(key, **values)
    assert str(error) == prompts.REMINDER_ERRORS[key].format(**values)
    assert error.plain == plain and (error.spoken or error.plain) == (spoken or plain)


@pytest.mark.parametrize("key", sorted(prompts.REMINDER_ERRORS))
def test_every_refusal_has_a_plain_reason_without_the_tools_words(key: str) -> None:
    error = ReminderError(key, value="x", limit=50, number=7)
    for words in (error.plain, error.spoken or error.plain):
        assert words and not any(jargon in words for jargon in ("HH:MM", "in_minutes", "YYYY", "repeat must"))
    assert "/" not in (error.spoken or error.plain)  # nothing to type is said on a call


def test_the_text_is_kept_on_one_line_in_the_users_words(store: Store) -> None:
    created = service(store, Clock()).set(ME, at="17:00", text="  bel de\n tandarts  ")
    assert created.reminder.text == "bel de tandarts"  # another language stays as it was given


def test_a_gap_time_is_said_in_the_note(store: Store) -> None:
    clock = Clock(at("2026-03-28 12:00"))
    created = service(store, clock).set(ME, at="2026-03-29 02:30", text="night thing")
    assert created.reminder.due_at == at("2026-03-29 03:30")
    assert "02:30 does not exist that day" in created.note and "03:30" in created.note


def test_limits_name_the_limit_and_how_to_make_room(store: Store) -> None:
    clock = Clock()
    reminders = service(store, clock, max_open=2, max_per_day=3)
    reminders.set(ME, at="17:00", text="one")
    reminders.set(ME, at="18:00", text="two")
    with pytest.raises(ReminderError, match=r"2 open reminders.*/reminders cancel"):
        reminders.set(ME, at="19:00", text="three")
    reminders.set(STRANGER, at="19:00", text="someone else's count is their own")
    reminders.cancel(ME, 1)
    reminders.set(ME, at="19:00", text="three")
    reminders.cancel(ME, 2)
    with pytest.raises(ReminderError, match=r"3 new reminders a day.*/reminders cancel"):
        reminders.set(ME, at="20:00", text="four")
    clock.now += 86_400  # a new day
    reminders.set(ME, at="20:00", text="four")


def test_at_most_three_call_reminders_on_one_day(store: Store) -> None:
    reminders = service(store, Clock())
    for hour in (6, 7, 8):
        reminders.set(ME, at=f"2026-10-13 0{hour}:00", text="wake up", call=True)
    with pytest.raises(ReminderError, match=r"3 calls on that day"):
        reminders.set(ME, at="2026-10-13 09:00", text="wake up", call=True)
    reminders.set(ME, at="2026-10-13 09:00", text="a text is fine")
    reminders.set(ME, at="2026-10-14 06:00", text="another day", call=True)


def test_a_call_reminder_says_wake_up_call_and_the_honest_line_once(store: Store) -> None:
    reminders = service(store, Clock(), name="Tiffany")
    first = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    assert first.note.startswith('**Reminder set** (#1): Tue 13 Oct 07:00 · wake-up call · "wake up".')
    assert "Tiffany runs on one Mac" in first.note and "keep your phone's own alarm" in first.note
    assert first.note.splitlines()[1] == prompts.REMINDER_CONTRACT  # the contract first, then the honest line
    assert first.note.endswith("\n\n" + prompts.REMINDER_HONEST.format(name="Tiffany"))  # its own paragraph
    second = reminders.set(ME, at="2026-10-13 17:00", text="call the dentist", call=True)
    assert " · call · " in second.note and "own alarm" not in second.note


def test_without_calls_a_call_me_reminder_is_a_wake_up_text(store: Store) -> None:
    reminders = service(store, Clock(), calls=False)
    created = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    assert created.reminder.call and "calls are off: a text only" in created.note
    assert "own alarm" not in created.note  # the honest line is about calls
    assert "#1 Tue 13 Oct 07:00 · wake-up text · wake up" in reminders.listing(ME)
    assert reminders.next_call() == "" and "(call)" not in reminders.status_line()
    for hour in (5, 6, 8):  # no call limit for texts
        reminders.set(ME, at=f"2026-10-13 0{hour}:00", text="wake up", call=True)


async def test_a_wake_up_text_without_calls_comes_twice_unless_the_user_wrote(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock, calls=None, calls_on=False)
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert [text for _, text in sent.texts] == [
        "Wake-up: 07:00 · wake up. No call: calls are off.",
        "Wake-up: 07:00 · wake up (second text).",
    ]


def test_weekdays_start_on_a_weekday_and_the_note_says_the_rhythm(store: Store) -> None:
    clock = Clock(at("2026-10-17 10:00"))  # a Saturday
    created = service(store, clock).set(ME, at="08:00", text="take the pills", repeat="weekdays")
    assert created.reminder.local_time == "2026-10-19 08:00"  # Monday
    assert "weekdays at 08:00 (first: Mon 19 Oct)" in created.note


def test_listing_cancelling_and_the_status_line(store: Store) -> None:
    reminders = service(store, Clock())
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    reminders.set(ME, at="08:00", text="take the pills", repeat="weekdays")
    reminders.set(STRANGER, at="09:00", text="not mine")
    said = store.add_incoming(store.current_conversation().id, ME, 1, "text", "I'll call the dentist")
    assert said is not None
    store._conn.execute(
        "INSERT INTO followups (peer_aci, what, source_message_id, due_at, expires_at, status, created_at,"
        " updated_at) VALUES (?, 'call the dentist', ?, ?, ?, 'open', 'x', 'x')",
        (ME, said, at("2026-10-15 17:00"), at("2026-10-17 17:00")),
    )
    listing = reminders.listing(ME)  # what the list_reminders tool gives Gemma: as before
    assert listing.splitlines() == [
        "Reminders:",
        "#1 Tue 13 Oct 07:00 · call · wake up",
        "#2 weekdays 08:00 · take the pills",
        "Check-ins I may send:",
        "#c1 Thu 15 Oct, evening · call the dentist",
        "/reminders cancel N (or cN), or /reminders cancel all",
    ]
    assert reminders.listing(ME, marked=True) == (  # /reminders: bold headings, a blank line between sections
        "**Reminders**\n"
        "#1 Tue 13 Oct 07:00 · call · wake up\n"
        "#2 weekdays 08:00 · take the pills\n"
        "\n"
        "**Check-ins I may send**\n"
        "#c1 Thu 15 Oct, evening · call the dentist\n"
        "\n"
        "/reminders cancel N (or cN), or /reminders cancel all"
    )
    assert reminders.status_line() == "Reminders: 3 open · next Tue 13 Oct 07:00 (call) · 0 failed"
    assert reminders.next_call() == "Tue 13 Oct 07:00"
    assert reminders.cancel(ME, 1) == "Cancelled #1: Tue 13 Oct 07:00 · wake up"
    with pytest.raises(ReminderError, match="no open reminder #3"):
        reminders.cancel(ME, 3)  # someone else's
    assert reminders.cancel_checkin(ME, 1) == "Cancelled check-in #c1: call the dentist"
    assert reminders.cancel_all(ME) == "Cancelled 1 reminder."
    assert reminders.cancel_all(ME) == "Nothing to cancel."
    assert reminders.listing(ME) == prompts.REMINDERS_NONE


# the scheduler


class Sent:
    """The bridge's send: marks the stored note sent (or failed), like App._send."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.texts: list[tuple[str, str]] = []
        self.fail = False

    async def __call__(self, out_id: int) -> None:
        message = self.store.message(out_id)
        if self.fail:
            self.store.set_status(out_id, "failed")
            return
        self.texts.append((message.peer_aci, message.text))
        self.store.mark_sent(out_id, 1)


class Calls:
    def __init__(self, outcomes: list[str], *, ready: str = "") -> None:
        self.outcomes = outcomes
        self.ready = ready
        self.placed: list[tuple[str, bool, str, str]] = []
        self.on_place: Callable[[], None] | None = None

    async def can_place(self, aci: str) -> str:
        return self.ready

    async def place(self, aci: str, *, wake: bool, time: str, text: str) -> str:
        self.placed.append((aci, wake, time, text))
        if self.on_place is not None:
            self.on_place()
        return self.outcomes.pop(0) if self.outcomes else "answered"


class Naps:
    """The injected sleep: moves the clock on and returns at once."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.now += seconds
        await asyncio.sleep(0)


def scheduler(
    store: Store,
    clock: Clock,
    *,
    calls: Calls | None = None,
    in_call: Callable[[], bool] = lambda: False,
    locked: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    **options: object,
) -> tuple[Reminders, ReminderScheduler, Sent]:
    calls_on = bool(options.pop("calls_on", True))
    reminders = service(store, clock, calls=calls_on, **options)
    sent = Sent(store)
    run = ReminderScheduler(
        store,
        reminders,
        sent,
        calls=calls,
        in_call=in_call,
        locked=locked,
        clock=clock,
        sleep=sleep or Naps(clock),
    )
    return reminders, run, sent


async def test_a_reminder_fires_on_time_as_a_note_out_of_gemmas_history(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock)
    created = reminders.set(ME, at="17:00", text="call the dentist")
    clock.now = at("2026-10-12 16:59:59")
    await run.tick()
    assert sent.texts == []
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    assert sent.texts == [(ME, "Reminder: call the dentist")]
    done = store.reminder(created.reminder.id)
    assert done is not None and (done.status, done.outcome, done.fired) == ("done", "sent", 1)
    note = store.message(done.message_id or 0)
    assert (note.kind, note.status) == ("note", "sent")  # a note: never in Gemma's history
    await run.tick()
    assert len(sent.texts) == 1


async def test_a_reminder_at_night_still_comes(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock)
    reminders.set(ME, at="03:00", text="take the bread out")  # quiet hours are for her own messages only
    clock.now = at("2026-10-13 03:00")
    await run.tick()
    assert sent.texts == [(ME, "Reminder: take the bread out")]


async def test_a_repeat_says_how_to_stop_and_goes_on(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock)
    created = reminders.set(ME, at="08:00", text="take the pills", repeat="weekdays")
    clock.now = at("2026-10-13 08:00")
    await run.tick()
    assert sent.texts == [(ME, "Reminder: take the pills (weekdays at 08:00 · /reminders to stop)")]
    again = store.reminder(created.reminder.id)
    assert again is not None and (again.status, again.local_time, again.fired) == ("open", "2026-10-14 08:00", 1)


async def test_adding_a_reminder_wakes_the_scheduler_at_once(store: Store) -> None:
    clock = Clock()
    forever = asyncio.Event()

    async def long_sleep(seconds: float) -> None:
        await forever.wait()  # only a wake-up ends it

    reminders, run, sent = scheduler(store, clock, sleep=long_sleep)
    task = asyncio.create_task(run.run_forever())
    try:
        await asyncio.sleep(0.05)
        reminders.set(ME, in_minutes=1, text="tea")
        clock.now += 60
        reminders.set(ME, at="20:00", text="later")  # any change wakes it
        async with asyncio.timeout(2):
            while not sent.texts:
                await asyncio.sleep(0.01)
        assert sent.texts == [(ME, "Reminder: tea")]
    finally:
        task.cancel()


async def test_a_note_that_cannot_be_sent_is_tried_every_5_minutes_for_an_hour(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock)
    created = reminders.set(ME, at="17:00", text="call mum")
    sent.fail = True
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    firing = store.reminder(created.reminder.id)
    assert firing is not None and firing.status == "firing"
    for _ in range(11):  # 17:05 ... 17:55
        clock.now += 300
        await run.tick()
    still = store.reminder(created.reminder.id)
    assert still is not None and still.status == "firing"
    clock.now += 300  # an hour of tries
    await run.tick()
    failed = store.reminder(created.reminder.id)
    assert failed is not None and (failed.status, failed.outcome) == ("failed", "send_failed")
    assert store.message(failed.message_id or 0).status == "ignored"  # recovery never sends it again
    assert "1 failed" in reminders.status_line()
    assert "could not be sent" in reminders.listing(ME)
    marked = reminders.listing(ME, marked=True)  # nothing open: the none line, then what went wrong under its heading
    assert marked.startswith(f"{prompts.REMINDERS_NONE}\n\n**Missed or failed (last 7 days)**\n#1 ")
    assert marked.endswith("call mum: could not be sent")


async def test_a_retry_that_gets_through_finishes_it(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock)
    created = reminders.set(ME, at="17:00", text="call mum")
    sent.fail = True
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    sent.fail = False
    clock.now += 300
    await run.tick()
    assert sent.texts == [(ME, "Reminder: call mum")]
    done = store.reminder(created.reminder.id)
    assert done is not None and (done.status, done.outcome) == ("done", "sent")


async def test_reminders_wait_while_she_is_on_a_call(store: Store) -> None:
    clock = Clock()
    calling = [True]
    reminders, run, sent = scheduler(store, clock, in_call=lambda: calling[0])
    reminders.set(ME, at="17:00", text="call mum")
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    assert sent.texts == []
    calling[0] = False
    clock.now += 600
    await run.tick()
    assert sent.texts == [(ME, "Reminder: call mum")]


async def test_old_reminders_are_pruned_after_30_days(store: Store) -> None:
    clock = Clock()
    reminders, run, _ = scheduler(store, clock)
    created = reminders.set(ME, at="17:00", text="call mum")
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    clock.now += 31 * 86_400
    await run.tick()
    assert store.reminder(created.reminder.id) is None


# wake-up calls


async def test_a_wake_up_call_sends_the_text_first_then_calls(store: Store) -> None:
    clock = Clock()
    calls = Calls(["answered"])
    order: list[str] = []
    reminders, run, sent = scheduler(store, clock, calls=calls)
    calls.on_place = lambda: order.append(f"call after {len(sent.texts)} text(s)")
    created = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert sent.texts == [(ME, "Wake-up call: 07:00 · wake up. Good morning!")]
    assert order == ["call after 1 text(s)"]
    assert calls.placed == [(ME, True, "7:00", "wake up")]
    done = store.reminder(created.reminder.id)
    assert done is not None and (done.status, done.outcome) == ("done", "answered")
    assert store.get_state(CALLS_DAY_KEY) == "2026-10-13 1"


async def test_an_afternoon_call_reminder_says_why_she_calls(store: Store) -> None:
    clock = Clock()
    calls = Calls(["answered"])
    reminders, run, sent = scheduler(store, clock, calls=calls)
    reminders.set(ME, at="17:00", text="call the dentist", call=True)
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    await run.drain()
    assert sent.texts == [(ME, "Reminder (calling you now): call the dentist")]
    assert calls.placed == [(ME, False, "17:00", "call the dentist")]


@pytest.mark.parametrize("first", ["no_answer", "busy", "call_failed"])
async def test_no_answer_is_retried_once_after_2_minutes_then_one_note(store: Store, first: str) -> None:
    clock = Clock()
    calls = Calls([first, "no_answer"])
    naps = Naps(clock)
    reminders, run, sent = scheduler(store, clock, calls=calls, sleep=naps)
    created = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert len(calls.placed) == 2 and 120 in naps.slept
    assert sent.texts[-1] == (ME, "Wake-up call not answered (07:00, 07:02).")
    assert len(sent.texts) == 2  # no second text before the retry
    done = store.reminder(created.reminder.id)
    assert done is not None and done.outcome == "no_answer"
    assert store.get_state(CALLS_DAY_KEY) == "2026-10-13 1"  # a retry belongs to the same wake-up


async def test_two_failed_dials_say_it_could_not_call(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock, calls=Calls(["call_failed", "call_failed"]))
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert sent.texts[-1] == (ME, "Wake-up call: could not call (07:00, 07:02).")


@pytest.mark.parametrize("first", ["declined", "answered"])
async def test_a_decline_or_an_answer_gets_no_retry(store: Store, first: str) -> None:
    clock = Clock()
    calls = Calls([first])
    reminders, run, sent = scheduler(store, clock, calls=calls)
    created = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert len(calls.placed) == 1 and len(sent.texts) == 1
    done = store.reminder(created.reminder.id)
    assert done is not None and done.outcome == first


async def test_a_message_from_the_user_before_the_retry_cancels_it(store: Store) -> None:
    clock = Clock()
    calls = Calls(["no_answer"])

    async def nap(seconds: float) -> None:
        clock.now += seconds
        store.add_incoming(store.current_conversation().id, ME, 5, "text", "I'm up")

    reminders, run, sent = scheduler(store, clock, calls=calls, sleep=nap)
    created = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert len(calls.placed) == 1 and len(sent.texts) == 1
    done = store.reminder(created.reminder.id)
    assert done is not None and (done.status, done.outcome) == ("done", "no_answer")


async def test_cancelling_during_the_wait_stops_the_retry(store: Store) -> None:
    clock = Clock()
    calls = Calls(["no_answer"])
    reminders, run, sent = scheduler(store, clock, calls=calls)

    async def nap(seconds: float) -> None:
        clock.now += seconds
        reminders.cancel(ME, 1)

    run._sleep = nap
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert len(calls.placed) == 1 and len(sent.texts) == 1


@pytest.mark.parametrize(
    ("ready", "why"),
    [("not_ready", "the voice service is not ready"), ("in_call", "we were already on a call")],
)
async def test_no_call_when_it_cannot_be_placed_the_text_says_why(store: Store, ready: str, why: str) -> None:
    clock = Clock()
    calls = Calls([], ready=ready)
    reminders, run, sent = scheduler(store, clock, calls=calls)
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert calls.placed == []
    assert sent.texts[0] == (ME, f"Wake-up: 07:00 · wake up. No call: {why}.")


async def test_without_a_call_a_second_text_follows_after_2_minutes_unless_the_user_wrote(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock, calls=Calls([], ready="not_ready"))
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert [text for _, text in sent.texts] == [
        "Wake-up: 07:00 · wake up. No call: the voice service is not ready.",
        "Wake-up: 07:00 · wake up (second text).",
    ]


async def test_the_daily_call_limit_holds_at_firing_too(store: Store) -> None:
    clock = Clock()
    calls = Calls([])
    reminders, run, sent = scheduler(store, clock, calls=calls, max_calls_per_day=1)
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    store.set_state(CALLS_DAY_KEY, "2026-10-13 1")  # one call already today
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert calls.placed == [] and "No call: that was the limit of 1 call a day." in sent.texts[0][1]


async def test_a_call_reminder_due_during_a_call_is_a_text_after_it(store: Store) -> None:
    clock = Clock()
    calling = [True]
    calls = Calls([])
    reminders, run, sent = scheduler(store, clock, calls=calls, in_call=lambda: calling[0])
    reminders.set(ME, at="17:00", text="call the dentist", call=True)
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    calling[0] = False
    clock.now += 30
    await run.tick()
    await run.drain()
    assert calls.placed == []
    assert sent.texts == [(ME, "Reminder: call the dentist. No call: we were already on a call.")]


# restart


def offline(store: Store, clock: Clock) -> tuple[Reminders, ReminderScheduler, Sent]:
    return scheduler(store, clock, calls=Calls(["answered"]))


async def test_missed_reminders_get_one_late_note_after_a_restart(store: Store) -> None:
    clock = Clock(at("2026-10-12 20:00"))
    reminders, _, _ = scheduler(store, clock)
    wake = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True).reminder.id
    pills = reminders.set(ME, at="2026-10-13 08:00", text="take the pills", repeat="daily").reminder.id
    mum = reminders.set(ME, at="2026-10-13 08:30", text="call mum").reminder.id
    soon = reminders.set(ME, at="2026-10-13 09:04", text="on time").reminder.id
    # 08:30 was being sent when the bridge stopped: its note never got its Signal timestamp
    note = store.add_outgoing(store.current_conversation().id, ME, "note", "Reminder: call mum", None)
    store.fire_reminder(mum, note)
    clock.now = at("2026-10-13 09:05")
    _, run, sent = offline(store, clock)
    await run.start_up(offline_since=at("2026-10-13 06:40"), started_at=clock.now)
    assert sent.texts == [
        (
            ME,
            "**Late, I was offline 06:40-09:05:**\n"
            "• 07:00 wake-up call: wake up (not placed late)\n"
            "• 08:00 take the pills\n"
            "• 08:30 call mum (may be a repeat)",
        )
    ]
    assert store.message(note).status == "ignored"  # recovery never resends the old note
    outcomes = {rid: (r.status, r.outcome) for rid in (wake, pills, mum) if (r := store.reminder(rid))}
    assert outcomes == {wake: ("done", "missed_call"), pills: ("open", "late"), mum: ("done", "maybe_repeat")}
    pills_now = store.reminder(pills)
    assert pills_now is not None and pills_now.local_time == "2026-10-14 08:00"  # then it goes on
    on_time = store.reminder(soon)
    assert on_time is not None and on_time.status == "open"  # 1 minute late: sent as usual, not marked
    await run.tick()
    assert sent.texts[-1] == (ME, "Reminder: on time")


async def test_a_call_reminder_at_most_5_minutes_late_is_still_placed(store: Store) -> None:
    clock = Clock(at("2026-10-12 20:00"))
    reminders, _, _ = scheduler(store, clock)
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:04")
    calls = Calls(["answered"])
    _, run, sent = scheduler(store, clock, calls=calls)
    await run.start_up(offline_since=at("2026-10-13 06:58"), started_at=clock.now)
    assert sent.texts == []  # no late note
    await run.tick()
    await run.drain()
    assert calls.placed == [(ME, True, "7:00", "wake up")]


async def test_a_note_that_went_out_before_the_restart_is_finished_not_repeated(store: Store) -> None:
    clock = Clock()
    reminders, _, _ = scheduler(store, clock)
    rid = reminders.set(ME, at="17:00", text="call mum", repeat="daily").reminder.id
    note = store.add_outgoing(store.current_conversation().id, ME, "note", "Reminder: call mum", None)
    store.fire_reminder(rid, note)
    store.mark_sent(note, 1)
    clock.now = at("2026-10-12 17:10")
    _, run, sent = offline(store, clock)
    await run.start_up(offline_since=at("2026-10-12 17:01"), started_at=clock.now)
    assert sent.texts == []
    after = store.reminder(rid)
    assert after is not None and (after.status, after.local_time, after.outcome) == ("open", "2026-10-13 17:00", "sent")


async def test_a_long_late_list_is_cut_at_10_lines_and_another_day_is_named(store: Store) -> None:
    clock = Clock(at("2026-10-11 20:00"))
    reminders, _, _ = scheduler(store, clock)
    for minute in range(12):
        reminders.set(ME, at=f"2026-10-11 21:{minute:02d}", text=f"thing {minute}")
    clock.now = at("2026-10-12 09:00")
    _, run, sent = offline(store, clock)
    await run.start_up(offline_since=at("2026-10-11 20:30"), started_at=clock.now)
    [(_, text)] = sent.texts
    lines = text.splitlines()
    assert lines[0] == "**Late, I was offline Sun 11 Oct 20:30-09:00:**"
    assert lines[1] == "• Sun 11 Oct 21:00 thing 0" and len(lines) == 12
    assert lines[-1] == "…and 2 more: /reminders"


async def test_each_user_gets_their_own_late_note(store: Store) -> None:
    clock = Clock()
    reminders, _, _ = scheduler(store, clock)
    reminders.set(ME, at="17:00", text="mine")
    reminders.set(STRANGER, at="17:00", text="theirs")
    clock.now = at("2026-10-12 18:00")
    _, run, sent = offline(store, clock)
    await run.start_up(offline_since=None, started_at=clock.now)
    assert sorted(sent.texts) == sorted(
        [
            (ME, "**Late, I was offline until 18:00:**\n• 17:00 mine"),
            (STRANGER, "**Late, I was offline until 18:00:**\n• 17:00 theirs"),
        ]
    )


async def test_a_call_reminder_starts_once_even_if_the_scheduler_looks_again_at_once(store: Store) -> None:
    clock = Clock()
    gate = asyncio.Event()

    class SlowVoice(Calls):
        async def can_place(self, aci: str) -> str:
            await gate.wait()  # the voice service takes a moment to answer
            return ""

    calls = SlowVoice(["answered"])
    reminders, run, sent = scheduler(store, clock, calls=calls)
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await asyncio.sleep(0)
    await run.tick()  # a notify (another reminder set, a call ended) while the first one waits
    assert store.next_reminder_due() is None  # nothing left for the scheduler's nap to wake for at once
    gate.set()
    await run.drain()
    assert len(calls.placed) == 1 and len(sent.texts) == 1


async def test_a_call_reminder_started_but_not_yet_sent_is_taken_up_again_after_a_restart(store: Store) -> None:
    clock = Clock(at("2026-10-12 20:00"))
    reminders, _, _ = scheduler(store, clock)
    rid = reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True).reminder.id
    store.start_firing(rid)  # the bridge stopped while the voice service was being asked
    clock.now = at("2026-10-13 07:03")
    calls = Calls(["answered"])
    _, run, sent = scheduler(store, clock, calls=calls)
    await run.start_up(offline_since=at("2026-10-13 07:01"), started_at=clock.now)
    await run.tick()
    await run.drain()
    assert calls.placed == [(ME, True, "7:00", "wake up")] and len(sent.texts) == 1  # no late note: placed now


async def test_a_call_reminder_cancelled_while_it_is_being_taken_up_stays_cancelled(store: Store) -> None:
    clock = Clock()
    reminders, run, sent = scheduler(store, clock)

    class Cancelling(Calls):
        async def can_place(self, aci: str) -> str:
            reminders.cancel(ME, 1)  # the user cancels it at that very moment
            return ""

    run._calls = calls = Cancelling([])
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 07:00")
    await run.tick()
    await run.drain()
    assert calls.placed == [] and sent.texts == []
    cancelled = store.reminder(1)
    assert cancelled is not None and cancelled.status == "cancelled"


async def test_a_missed_wake_up_text_without_calls_is_a_plain_late_line(store: Store) -> None:
    clock = Clock(at("2026-10-12 20:00"))
    reminders, _, _ = scheduler(store, clock, calls_on=False)
    reminders.set(ME, at="2026-10-13 07:00", text="wake up", call=True)
    clock.now = at("2026-10-13 09:00")
    _, run, sent = scheduler(store, clock, calls=None, calls_on=False)
    await run.start_up(offline_since=at("2026-10-13 06:00"), started_at=clock.now)
    assert sent.texts == [(ME, "**Late, I was offline 06:00-09:00:**\n• 07:00 wake up")]


# the lock (/lock): reminders wait, a call reminder becomes a text without a call


async def test_reminders_wait_while_she_is_locked_and_come_after_the_unlock(store: Store) -> None:
    clock = Clock()
    locked = [True]
    reminders, run, sent = scheduler(store, clock, locked=lambda: locked[0])
    created = reminders.set(ME, at="17:00", text="call mum")
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    clock.now += 3_600
    await run.tick()
    assert sent.texts == []  # nothing goes out while locked, and nothing is given up on
    reminder = store.reminder(created.reminder.id)
    assert reminder is not None and reminder.status == "open"
    locked[0] = False
    await run.tick()
    assert sent.texts == [(ME, "Reminder: call mum")]


async def test_a_call_reminder_due_while_locked_never_calls(store: Store) -> None:
    clock = Clock()
    locked = [True]
    calls = Calls([])
    reminders, run, sent = scheduler(store, clock, calls=calls, locked=lambda: locked[0])
    reminders.set(ME, at="17:00", text="call the dentist", call=True)
    clock.now = at("2026-10-12 17:00")
    await run.tick()
    locked[0] = False
    clock.now += 60  # unlocked within the 5 minutes a call may be late: still no call
    await run.tick()
    await run.drain()
    assert calls.placed == []
    assert sent.texts == [(ME, "Reminder: call the dentist. No call: I was locked (/lock).")]


# after outside text in the conversation: a reminder's words must come from the user's message (the overlap rule)


@pytest.mark.parametrize(
    ("text", "message", "own"),
    [
        ("call mum", "remind me at 5 to call mum", True),
        ("calling mum", "remind me to call mum", True),  # half its words are the user's
        ("dentist appointment", "remind me about the dentist", True),
        ("water the plants", "[voice] remind me to water the plants", True),
        ("Pay the invoice", "remind me to pay the invoice at 17:00", True),
        ("pay invoice 4711 at the portal", "remind me about that at 17:00", False),  # her words, not the user's
        (
            "transfer 500 euro",
            "remind me to transfer the money, the euro amount",
            False,
        ),  # a number the user never said
        ("visit https://evil.example/login", "remind me to visit the login page", False),  # a link the user never sent
        ("visit https://example.com/me", "remind me to visit https://example.com/me tonight", True),
        ("薬を飲む", "[voice] 9時に薬を飲むことを思い出させて", True),  # Japanese by characters
        ("請求書を払う", "それを思い出させて", False),
        ("", "remind me", True),  # nothing to compare: the usual check says it is empty
    ],
)
def test_a_reminders_words_must_overlap_the_users_message(text: str, message: str, own: bool) -> None:
    assert own_words(text, message) is own


def test_the_overlap_refusal_points_to_remind() -> None:
    error = ReminderError("not_your_words")
    assert "/remind" in error.plain and "/" not in error.spoken and "outside" in error.plain
