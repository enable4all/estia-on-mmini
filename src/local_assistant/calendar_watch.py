"""Calendar notes she sends first, written by code (no Gemma): an event moved or gone, and an early start tomorrow.

Every 30 minutes outside quiet hours the calendars are read live for the next 48 hours, through the same feed as
Gemma's calendar tools. Kept in memory only, by uid across all calendars: each timed event's start and end and the
calendar it was last seen in (its name only to show it), for uids that appear once in that window (a repeating event
shares its uid: never watched). No title is kept and nothing goes to disk. The first read after a start, after a failed
or cut-short read, under /quiet, or of a calendar that could not be read is only the baseline: changes during downtime
are never reported, and a failing calendar never looks like cancelled events.

A change is an event that starts within 24 hours and has not begun, moved by 15 minutes or more from what the user last
knew, or gone: its uid is in no calendar while every calendar read fine, its own both times (the feed keeps an
invitation that is in two calendars once, under the first that read fine, so a calendar that could not be read may
hold it). The read reaches 48 hours ahead, so a meeting moved to tomorrow afternoon is a move, not a cancellation. New events are not reported. The note is written when it goes out, from a
fresh read (titles cleaned, capped and without links as in the digest, the calendar's name first when there are
several), so what no longer holds is left out. An early start: at early_check, when tomorrow's first timed event starts before the digest
time, the digest would come too late for it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from typing import Protocol
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.calendar_feed import Agenda, Event, FeedError
from local_assistant.calendar_tools import CALENDAR_CHARS, clean, clean_note
from local_assistant.config import CalendarSource

log = logging.getLogger(__name__)

WATCH_S = 1800.0  # a read every 30 minutes (outside quiet hours)
READ_HOURS = 48
REPORT_HOURS = 24  # only events that start within 24 hours are reported
MOVED_S = 15 * 60.0  # moved by at least this much
TITLE_CHARS = 60
NOTE_LINES = 5


@dataclass(frozen=True)
class Times:
    """What is kept of an event: its times, never its title."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Change:
    calendar: str  # its calendar's shown name with several calendars, else ""
    uid: str
    old: Times  # what the user last knew
    new: Times | None  # None: gone


class Feed(Protocol):
    """calendar_feed.CalendarFeed: every configured calendar, read together."""

    @property
    def sources(self) -> tuple[CalendarSource, ...]: ...

    @property
    def several(self) -> bool: ...

    async def events(self, start: datetime, end: datetime) -> Agenda: ...


@dataclass(frozen=True)
class Read:
    current: dict[str, Event]  # uid → the timed event, for uids that appear once in the read
    present: set[str]  # every uid in the read (repeating, all-day, two diverging copies): never gone
    fine: set[str]  # the calendars that read fine
    complete: bool  # every calendar read fine


def _day(moment: datetime, today: date) -> str:
    if moment.date() == today:
        return "today"
    if moment.date() == today + timedelta(days=1):
        return "tomorrow"
    return f"{moment:%a} {moment.day} {moment:%b}"


class CalendarWatch:
    def __init__(
        self,
        feed: Feed,
        tz: ZoneInfo,
        *,
        digest_minute: int = 9 * 60 + 15,
        clock: Callable[[], float] = time.time,
        read_seconds: float = 45.0,
    ) -> None:
        self._feed = feed
        self._tz = tz
        self._digest_minute = digest_minute
        self._clock = clock
        self._read_seconds = read_seconds  # the helper's own limit is 30 s
        self._known: dict[str, tuple[str, Times]] = {}  # uid → its calendar and times, as the user last knew them
        self._pending: dict[str, Change] = {}  # by uid
        self._last_check = float("-inf")

    def due_check(self, now: float) -> bool:
        return now - self._last_check >= WATCH_S

    async def check(self, now: float) -> None:
        """One read: what changed since the user last knew it (no text is kept)."""
        self._last_check = now
        await self._update(now)

    def pending(self, now: float) -> bool:
        """Whether a change waits to be told (only events that have not begun)."""
        moment = datetime.fromtimestamp(now, self._tz)
        return any(self._ahead(change, moment) for change in self._pending.values())

    def expire(self, now: float) -> int:
        """Drop the changes whose event began while they waited; how many (the gate logs them as skipped)."""
        moment = datetime.fromtimestamp(now, self._tz)
        gone = [key for key, change in self._pending.items() if not self._ahead(change, moment)]
        for key in gone:
            del self._pending[key]
        return len(gone)

    async def change_note(self, now: float) -> tuple[str, list[Change]] | None:
        """The note as it goes out now, from a fresh read (titles only here, never kept), and the changes it tells;
        None when nothing still holds."""
        events = await self._update(now)
        if events is None:
            return None
        moment = datetime.fromtimestamp(now, self._tz)
        changes = sorted(
            (change for change in self._pending.values() if self._ahead(change, moment)),
            key=lambda change: change.old.start,
        )
        if not changes:
            return None
        lines = [self._line(change, events, moment.date()) for change in changes]
        if len(lines) == 1:
            return prompts.CALENDAR_CHANGE.format(line=lines[0]), changes
        shown = [f"• {line}" for line in lines[:NOTE_LINES]]
        if len(lines) > NOTE_LINES:
            shown.append(prompts.CALENDAR_CHANGES_MORE.format(count=len(lines) - NOTE_LINES))
        return "\n".join([prompts.CALENDAR_CHANGES, *shown]), changes

    def told(self, changes: list[Change]) -> None:
        """These changes went out (or were dropped by the gate): what the user knows now."""
        for change in changes:
            self._pending.pop(change.uid, None)
            if change.uid not in self._known:
                continue  # forgotten meanwhile: never brought back
            if change.new is None:
                del self._known[change.uid]
            else:
                self._known[change.uid] = (change.calendar, change.new)

    def drop(self) -> None:
        """Every waiting change counts as told (the gate dropped it: the day's cap, /quiet)."""
        self.told(list(self._pending.values()))

    def forget(self) -> None:
        """Nothing known any more: the next read is a new baseline (under /quiet, after a failed read)."""
        self._known.clear()
        self._pending.clear()

    async def early_start(self, now: float) -> bool | None:
        """Whether tomorrow's first timed event starts before the digest time; None when the calendar could not be
        read (tried again later)."""
        ok, first = await self._first_tomorrow(now)
        return (first is not None) if ok else None

    async def early_note(self, now: float) -> str | None:
        """The early-start note, from a fresh read; None when there is none (any more) or the read failed."""
        ok, first = await self._first_tomorrow(now)
        if not ok or first is None:
            return None
        return prompts.EARLY_START.format(time=f"{first.start:%H:%M}", title=self._title(first))

    # reading

    @staticmethod
    def _ahead(change: Change, moment: datetime) -> bool:
        return change.old.start > moment and (change.new is None or change.new.start > moment)

    async def _update(self, now: float) -> dict[str, Event] | None:
        read = await self._read(now)
        if read is None:
            self.forget()
            return None
        moment = datetime.fromtimestamp(now, self._tz)
        horizon = moment + timedelta(hours=REPORT_HOURS)
        pending: dict[str, Change] = {}
        for uid, (calendar, old) in list(self._known.items()):
            event = read.current.get(uid)
            if old.start <= moment:
                del self._known[uid]  # begun: nothing to tell any more
            elif uid not in read.present:
                if calendar not in read.fine or old.start > horizon:
                    del self._known[uid]  # its calendar could not be read (a new baseline), or not reported yet
                elif read.complete:
                    pending[uid] = Change(calendar, uid, old, None)
                # else another calendar could not be read and may hold it now: kept, nothing told
            elif event is None:
                pass  # there, but not once (repeating now, all-day, two copies that differ): nothing told
            elif old.start > horizon:
                self._known[uid] = (event.calendar, Times(event.start, event.end))  # not reported yet: followed
            elif (
                abs((event.start - old.start).total_seconds()) >= MOVED_S
                and event.response != "declined"  # declined: she won't go anyway
            ):
                pending[uid] = Change(event.calendar, uid, old, Times(event.start, event.end))
            else:
                self._known[uid] = (event.calendar, old)  # where it is now (an invitation in two calendars)
        for uid, event in read.current.items():
            self._known.setdefault(uid, (event.calendar, Times(event.start, event.end)))  # new: never reported
        self._pending = pending
        return read.current

    async def _read(self, now: float) -> Read | None:
        """The next 48 hours: the timed events whose uid appears once, every uid, and the calendars that read fine;
        None when the read failed or was cut short (then nothing may look cancelled)."""
        start = datetime.fromtimestamp(now, self._tz)
        agenda = await self._agenda(start, start + timedelta(hours=READ_HOURS))
        if agenda is None or agenda.truncated:
            return None
        failed = {missed.source.shown for missed in agenda.missed}
        names = [source.shown for source in self._feed.sources] if self._feed.several else [""]
        fine = {name for name in names if name not in failed}
        counts = Counter(event.uid for event in agenda.events if event.uid)
        current = {
            event.uid: event
            for event in agenda.events
            if event.uid and not event.all_day and counts[event.uid] == 1  # a repeating event shares its uid
        }
        return Read(current, set(counts), fine, not failed)

    async def _agenda(self, start: datetime, end: datetime) -> Agenda | None:
        try:
            return await asyncio.wait_for(self._feed.events(start, end), self._read_seconds)
        except FeedError as exc:
            log.info("calendar watch: not read (%s)", exc.reason)
        except TimeoutError:
            log.info("calendar watch: not read in time")
        except Exception:  # a surprise in one read never stops the initiative
            log.exception("calendar watch: the read failed")
        return None

    async def _first_tomorrow(self, now: float) -> tuple[bool, Event | None]:
        today = datetime.fromtimestamp(now, self._tz).date()
        begin = datetime.combine(today + timedelta(days=1), clock_time(), self._tz)
        until = begin + timedelta(minutes=self._digest_minute)
        agenda = await self._agenda(begin, until)
        if agenda is None or (agenda.missed and len(agenda.missed) == len(self._feed.sources)):
            return False, None
        early = [
            event
            for event in agenda.events
            if not event.all_day and event.response != "declined" and begin <= event.start < until
        ]
        return True, min(early, key=lambda event: event.start) if early else None

    # the note's words

    def _title(self, event: Event) -> str:
        """The title as the digest shows it: cleaned, capped and without links (code's note, never Gemma's)."""
        title = clean_note(event.title, TITLE_CHARS) or "(no title)"
        return f"{clean(event.calendar, CALENDAR_CHARS)}: {title}" if event.calendar else title

    def _line(self, change: Change, events: dict[str, Event], today: date) -> str:
        old = change.old.start
        if change.new is None:
            day = "" if old.date() == today else f" {_day(old, today)}"
            calendar = f" ({clean(change.calendar, CALENDAR_CHARS)})" if change.calendar else ""
            return prompts.CALENDAR_GONE_EVENT.format(time=f"{old:%H:%M}", day=day, calendar=calendar)
        new = change.new.start
        title = self._title(events[change.uid])
        if old.date() == new.date():
            moved = prompts.CALENDAR_MOVED.format(title=title, old=f"{old:%H:%M}", new=f"{new:%H:%M}")
            return f"{moved} ({_day(new, today)})"
        return prompts.CALENDAR_MOVED.format(
            title=title, old=f"{_day(old, today)} {old:%H:%M}", new=f"{_day(new, today)} {new:%H:%M}"
        )
