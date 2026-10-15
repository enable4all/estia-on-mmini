"""The calendar notes she sends first: what is kept (uids and times, in memory), what counts as a change, the note's
words from a fresh read, and the early start (a fake feed; fake clock)."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from local_assistant.calendar_feed import Agenda, Event, FeedError, Missed
from local_assistant.calendar_watch import CalendarWatch
from local_assistant.config import CalendarSource

TZ = ZoneInfo("Europe/Amsterdam")


def at(moment: str) -> datetime:
    return datetime.fromisoformat(moment).replace(tzinfo=TZ)


NOW = at("2026-10-14 09:00")


def event(title: str, start: str, minutes: int = 60, *, uid: str = "", calendar: str = "", **more: object) -> Event:
    begin = at(start)
    base = Event(title, "", begin, begin + timedelta(minutes=minutes), False, "", "", uid or f"{title}@example.com")
    return replace(base, calendar=calendar, **more)  # type: ignore[arg-type]


class Feed:
    def __init__(self, *names: str) -> None:
        self._sources = tuple(CalendarSource(name) for name in names) or (CalendarSource("calendar"),)
        self.items: list[Event] = []
        self.failed: set[str] = set()  # shown names that cannot be read now
        self.error: Exception | None = None
        self.truncated = False
        self.reads: list[tuple[datetime, datetime]] = []

    @property
    def sources(self) -> tuple[CalendarSource, ...]:
        return self._sources

    @property
    def several(self) -> bool:
        return len(self._sources) > 1

    async def events(self, start: datetime, end: datetime) -> Agenda:
        self.reads.append((start, end))
        if self.error is not None:
            raise self.error
        missed = tuple(Missed(source, "unreachable") for source in self._sources if source.shown in self.failed)
        shown = [e for e in self.items if e.calendar not in self.failed and e.start < end and e.end > start]
        if self.several:  # as CalendarFeed: calendars in order, an invitation in two of them kept once, the first
            order = [source.shown for source in self._sources]
            seen: set[tuple[str, datetime]] = set()
            kept: list[Event] = []
            for item in sorted(shown, key=lambda e: order.index(e.calendar)):
                if (item.uid, item.start) not in seen:
                    seen.add((item.uid, item.start))
                    kept.append(item)
            shown = kept
        return Agenda(tuple(sorted(shown, key=lambda e: e.start)), self.truncated, missed=missed)


def watch(feed: Feed) -> CalendarWatch:
    return CalendarWatch(feed, TZ, digest_minute=9 * 60 + 15)


async def checked(cal: CalendarWatch, moment: datetime) -> bool:
    await cal.check(moment.timestamp())
    return cal.pending(moment.timestamp())


async def test_the_first_read_is_only_the_baseline_and_a_move_of_fifteen_minutes_is_told() -> None:
    feed = Feed()
    feed.items = [event("Dentist", "2026-10-14 15:00"), event("Lunch", "2026-10-14 12:30")]
    cal = watch(feed)
    assert not await checked(cal, NOW)  # nothing known before: nothing to tell
    feed.items = [event("Dentist", "2026-10-14 16:30"), event("Lunch", "2026-10-14 12:40")]  # +90 and +10 min
    assert await checked(cal, NOW + timedelta(minutes=30))
    note = await cal.change_note((NOW + timedelta(minutes=31)).timestamp())
    assert note is not None
    assert note[0] == "Calendar change: Dentist moved 15:00 → 16:30 (today)."
    cal.told(note[1])
    assert not cal.pending((NOW + timedelta(minutes=32)).timestamp())
    assert await cal.change_note((NOW + timedelta(minutes=33)).timestamp()) is None  # told: not again


async def test_a_move_to_another_day_names_both_days() -> None:
    feed = Feed()
    feed.items = [event("Review", "2026-10-14 18:00")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.items = [event("Review", "2026-10-15 10:00")]
    note = await cal.change_note((NOW + timedelta(minutes=30)).timestamp())
    assert note is not None and note[0] == "Calendar change: Review moved today 18:00 → tomorrow 10:00."


async def test_gone_only_while_its_calendar_read_fine_both_times() -> None:
    feed = Feed()
    feed.items = [event("Dentist", "2026-10-14 15:00")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.error = FeedError("unreachable")  # a failing calendar never looks like cancelled events
    assert not await checked(cal, NOW + timedelta(minutes=30))
    feed.error = None
    feed.items = []
    assert not await checked(cal, NOW + timedelta(minutes=60))  # after a failed read: a new baseline
    feed.items = [event("Dentist", "2026-10-14 15:00")]
    await cal.check((NOW + timedelta(minutes=90)).timestamp())
    feed.items = []
    note = await cal.change_note((NOW + timedelta(minutes=120)).timestamp())
    assert note is not None and note[0] == "Calendar change: your 15:00 event is no longer in the calendar."


async def test_a_cut_short_read_is_no_read() -> None:
    feed = Feed()
    feed.items = [event("Dentist", "2026-10-14 15:00")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.items, feed.truncated = [], True
    assert not await checked(cal, NOW + timedelta(minutes=30))


async def test_with_several_calendars_one_that_fails_is_never_cancelled_and_names_come_first() -> None:
    feed = Feed("work", "family")
    feed.items = [
        event("1:1 with Sam", "2026-10-14 14:00", calendar="Work"),
        event("Swim", "2026-10-14 16:00", calendar="Family"),
    ]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.failed = {"Family"}
    feed.items = [event("1:1 with Sam", "2026-10-14 15:00", calendar="Work")]
    note = await cal.change_note((NOW + timedelta(minutes=30)).timestamp())
    assert note is not None and note[0] == "Calendar change: Work: 1:1 with Sam moved 14:00 → 15:00 (today)."
    cal.told(note[1])
    feed.failed = set()
    feed.items.append(event("Swim", "2026-10-14 16:00", calendar="Family"))
    assert not await checked(cal, NOW + timedelta(minutes=60))  # Family read again: a new baseline
    feed.items = []
    note = await cal.change_note((NOW + timedelta(minutes=90)).timestamp())
    assert note is not None and note[0].splitlines() == [
        "**Calendar changes**",
        "• your 15:00 event (Work) is no longer in the calendar",
        "• your 16:00 event (Family) is no longer in the calendar",
    ]


async def test_more_changes_than_the_note_shows_end_in_one_line_with_no_bullet() -> None:
    feed = Feed()
    feed.items = [event(f"Item {n}", f"2026-10-14 {12 + n}:00") for n in range(7)]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.items = []
    note = await cal.change_note((NOW + timedelta(minutes=30)).timestamp())
    assert note is not None
    lines = note[0].splitlines()
    assert len(lines) == 1 + 5 + 1 and lines[-1] == "…and 2 more"  # not an item: no "•"


async def test_an_invitation_in_two_calendars_is_never_cancelled_when_one_of_them_fails_once() -> None:
    feed = Feed("google", "work")  # the feed keeps the invitation once, under the first calendar that read fine
    feed.items = [
        event("Team sync", "2026-10-14 15:00", uid="sync", calendar="Google"),
        event("Team sync", "2026-10-14 15:00", uid="sync", calendar="Work"),
    ]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.failed = {"Google"}  # now only under Work
    assert not await checked(cal, NOW + timedelta(minutes=30))
    feed.failed = set()  # under Google again, so no longer under Work
    assert not await checked(cal, NOW + timedelta(minutes=60))
    assert await cal.change_note((NOW + timedelta(minutes=61)).timestamp()) is None
    feed.items = [event("Team sync", "2026-10-14 15:00", uid="sync", calendar="Work")]  # left Google only
    assert not await checked(cal, NOW + timedelta(minutes=90))
    feed.items = [event("Team sync", "2026-10-14 16:00", uid="sync", calendar="Work")]  # moved: told once
    note = await cal.change_note((NOW + timedelta(minutes=120)).timestamp())
    assert note is not None and note[0] == "Calendar change: Work: Team sync moved 15:00 → 16:00 (today)."
    cal.told(note[1])
    feed.items = []  # gone from both, both read fine
    note = await cal.change_note((NOW + timedelta(minutes=150)).timestamp())
    assert note is not None and note[0] == "Calendar change: your 16:00 event (Work) is no longer in the calendar."


async def test_gone_waits_while_another_calendar_cannot_be_read_since_it_may_hold_the_event() -> None:
    feed = Feed("work", "family")
    feed.items = [event("Dentist", "2026-10-14 15:00", calendar="Work")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.failed = {"Family"}
    feed.items = []
    assert not await checked(cal, NOW + timedelta(minutes=30))  # Family could hold the invitation now
    feed.failed = set()
    note = await cal.change_note((NOW + timedelta(minutes=60)).timestamp())  # in no calendar, all read fine
    assert note is not None and note[0] == "Calendar change: your 15:00 event (Work) is no longer in the calendar."


async def test_repeating_all_day_far_off_new_and_begun_events_are_never_told() -> None:
    feed = Feed()
    feed.items = [
        event("Standup", "2026-10-14 10:00", uid="standup"),
        event("Standup", "2026-10-15 10:00", uid="standup"),  # a repeating event shares its uid
        replace(event("Holiday", "2026-10-14 00:00", 1440), all_day=True),
        event("Trip", "2026-10-15 20:00"),  # more than 24 hours ahead
        event("Coffee", "2026-10-14 09:30"),
    ]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.items = [
        event("Standup", "2026-10-14 11:00", uid="standup"),
        event("Standup", "2026-10-15 10:00", uid="standup"),
        replace(event("Holiday", "2026-10-15 00:00", 1440), all_day=True),
        event("Trip", "2026-10-15 22:00"),
        event("New thing", "2026-10-14 13:00"),  # new events are not reported
    ]
    assert not await checked(cal, NOW + timedelta(minutes=45))  # Coffee began: gone after it began is no news


async def test_a_declined_event_that_moves_is_not_told() -> None:
    feed = Feed()
    feed.items = [event("Party", "2026-10-14 19:00")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.items = [event("Party", "2026-10-14 20:00", response="declined")]
    assert not await checked(cal, NOW + timedelta(minutes=30))


async def test_a_change_whose_event_began_while_it_waited_expires() -> None:
    feed = Feed()
    feed.items = [event("Dentist", "2026-10-14 09:40")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.items = [event("Dentist", "2026-10-14 10:30")]
    assert await checked(cal, NOW + timedelta(minutes=30))
    assert cal.expire((NOW + timedelta(minutes=45)).timestamp()) == 1
    assert not cal.pending((NOW + timedelta(minutes=45)).timestamp())


async def test_drop_and_forget() -> None:
    feed = Feed()
    feed.items = [event("Dentist", "2026-10-14 15:00")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    feed.items = [event("Dentist", "2026-10-14 17:00")]
    assert await checked(cal, NOW + timedelta(minutes=30))
    cal.drop()  # the day's cap: counts as told
    assert not await checked(cal, NOW + timedelta(minutes=60))
    cal.forget()
    feed.items = [event("Dentist", "2026-10-14 18:00")]
    assert not await checked(cal, NOW + timedelta(minutes=90))  # a new baseline


async def test_a_check_every_half_hour() -> None:
    cal = watch(Feed())
    assert cal.due_check(NOW.timestamp())
    await cal.check(NOW.timestamp())
    assert not cal.due_check((NOW + timedelta(minutes=29)).timestamp())
    assert cal.due_check((NOW + timedelta(minutes=30)).timestamp())


async def test_reads_reach_two_days_ahead_and_keep_no_titles() -> None:
    feed = Feed()
    feed.items = [event("Secret project", "2026-10-14 15:00", uid="e1")]
    cal = watch(feed)
    await cal.check(NOW.timestamp())
    assert feed.reads == [(NOW, NOW + timedelta(hours=48))]
    assert "Secret project" not in repr(vars(cal))  # times and uids only


async def test_an_early_start_before_the_digest_time() -> None:
    feed = Feed()
    feed.items = [
        event("Night shift ends", "2026-10-14 23:00", 120),  # begins today: not tomorrow's first
        event("Dentist", "2026-10-15 07:30"),
        event("Gym", "2026-10-15 07:00", response="declined"),
    ]
    cal = watch(feed)
    evening = at("2026-10-14 20:00").timestamp()
    assert await cal.early_start(evening) is True
    assert await cal.early_note(evening) == "Early start tomorrow: 07:30 Dentist."
    feed.items = [event("Dentist", "2026-10-15 09:30")]
    assert await cal.early_start(evening) is False and await cal.early_note(evening) is None
    feed.error = FeedError("unreachable")
    assert await cal.early_start(evening) is None
