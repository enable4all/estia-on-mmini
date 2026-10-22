"""Reminders and wake-up calls the user sets: times, checks, the code-written notes and the scheduler.

Only the user's own words make a reminder (Gemma's set_reminder is refused in code after outside text; /remind is
typed). A reminder keeps the local time as meant, its IANA zone and the UTC instant of the next occurrence, so a
repeat stays at 08:00 across daylight saving time; a time in the spring-forward gap is read as RFC 5545 does (02:30
becomes 03:30) and the note says so; a time that occurs twice in autumn takes the first.

Delivery is at least once and never silent: the note of a firing is stored in the same transaction that marks the
reminder firing (the fence), then sent; a note that does not go out is tried every 5 minutes for an hour, then the
reminder is failed (`send_failed`). After a restart, missed reminders get one late note per user; one that was being
sent is listed "(may be a repeat)" and its old note is never sent again by recovery. Quiet hours and /quiet never
apply: the user set it. A call reminder sends its text first, always, then dials (once more after 2 minutes when not
answered); it is never placed more than 5 minutes late. Logs hold ids, times and fixed words, never the text.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.config import DEFAULT_NAME
from local_assistant.store import Reminder, ReminderRepeat, ReminderSource, Store

log = logging.getLogger(__name__)

REPEATS: tuple[ReminderRepeat, ...] = ("none", "daily", "weekdays", "weekly")
OUTCOMES = (
    "sent",
    "late",
    "maybe_repeat",
    "answered",
    "no_answer",
    "declined",
    "busy",
    "call_failed",
    "missed_call",
    "send_failed",
)
PROBLEMS = ("late", "maybe_repeat", "missed_call", "send_failed", "no_answer", "busy", "call_failed")
FAILED = ("send_failed", "call_failed")  # what /status and doctor count as failed
UNANSWERED = ("no_answer", "busy", "call_failed")  # a call that gets one more try after CALL_RETRY_S
TEXT_MAX = 200
MAX_AHEAD_S = 366 * 86_400
ON_TIME_S = 120.0  # up to 2 minutes late counts as on time and is not marked
CALL_LATE_S = 300.0  # a call reminder at most 5 minutes late (a quick restart) is still placed
RETRY_S = 300.0  # a note that did not go out: tried again every 5 minutes ...
RETRY_FOR_S = 3600.0  # ... for an hour
CALL_RETRY_S = 120.0  # an unanswered wake-up call is tried once more after 2 minutes
POLL_S = 30.0  # the scheduler sleeps at most this long (a clock change is caught)
LATE_LINES = 10
KEEP_DAYS = 30  # done and cancelled reminders are deleted after this many days
PROBLEM_DAYS = 7  # /reminders lists what went wrong in the last 7 days
WAKE_HOURS = range(4, 11)  # a call reminder from 04:00 to 10:59 is a wake-up call ("Good morning!")
CALLS_DAY_KEY = "reminders:calls_day"  # "YYYY-MM-DD N": call reminders placed on that local day
CALL_WARNED_KEY = "reminders:call_warned"  # the honest line came with the first call reminder
CONTRACT_KEY = "reminders:contract_told"  # + ":<aci>": that user's first confirmation said only it means one is set
MEANT = "%Y-%m-%d %H:%M"
PAST_DAYS = 2  # nearest_year: a guessed date further in the past than this means the next one
_HHMM = re.compile(r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$")
_DATE_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]([01]?[0-9]|2[0-3]):([0-5][0-9])$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPAN = re.compile(r"^([0-9]{1,4})([mhd])$")
_SPAN_MINUTES = {"m": 1, "h": 60, "d": 1440}


_WORDS = re.compile(r"\w+")
_LINKISH = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)+/\S*", re.IGNORECASE)
OWN_SHARE = 0.5  # the overlap rule: at least half a reminder's words are the user's


def _spaceless(char: str) -> bool:
    """A character of a script written without spaces (Japanese kana, Chinese and Japanese characters)."""
    return "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"


def _tokens(text: str) -> list[str]:
    """Words for the overlap rule: lowercase; a script without spaces counts per character; a word of one or two
    letters is left out (a, to, je) unless it holds a digit."""
    tokens: list[str] = []
    for word in _WORDS.findall(text.casefold()):
        spaceless = [char for char in word if _spaceless(char)]
        if spaceless:
            tokens += spaceless
            word = "".join(char for char in word if not _spaceless(char))
        if len(word) >= 3 or any(char.isdigit() for char in word):
            tokens.append(word)
    return tokens


def own_words(text: str, message: str) -> bool:
    """The overlap rule, used while her context still holds a reply or summary that followed outside text: a
    reminder's text must come from the user's own message: every number and link in it is in the message, and at
    least half its words are. Nothing to compare (an empty text) passes: the usual checks say why."""
    words = _tokens(text)
    if not words:
        return True
    said = set(_tokens(message))
    if any(any(char.isdigit() for char in word) and word not in said for word in words):
        return False
    if any(link.casefold().rstrip(".,;:!?") not in message.casefold() for link in _LINKISH.findall(text)):
        return False
    return sum(1 for word in words if word in said) >= OWN_SHARE * len(words)


class ReminderError(ValueError):
    """A reminder was refused: the message says why, in the tool's words for Gemma and /remind
    (prompts.REMINDER_ERRORS); `plain` in plain words for the user, `spoken` to say on a call ("" when the same)."""

    def __init__(self, key: str, **values: object) -> None:
        super().__init__(prompts.REMINDER_ERRORS[key].format(**values))
        self.key = key
        self.plain = prompts.REMINDER_REASONS[key].format(**values)
        self.spoken = prompts.REMINDER_REASONS_SPOKEN.get(key, "").format(**values)


def iso(epoch: float) -> str:
    """A UTC ISO timestamp for the database (as timefmt.utc_now writes them), from the injected clock."""
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


def resolve_local(meant: datetime, tz: ZoneInfo) -> tuple[datetime, bool]:
    """The real moment of a local wall time: in a spring-forward gap the time an hour on (RFC 5545: the offset before
    the gap), shifted=True; in an autumn overlap the first one. Never a silent fall back to UTC."""
    first = meant.replace(tzinfo=tz, fold=0)
    real = first.astimezone(UTC).astimezone(tz)
    return real, real.replace(tzinfo=None) != meant.replace(tzinfo=None, fold=0)


def _meant(text: str) -> datetime:
    return datetime.strptime(text, MEANT)


def _step(meant: datetime, repeat: ReminderRepeat) -> datetime:
    if repeat == "weekly":
        return meant + timedelta(days=7)
    meant += timedelta(days=1)
    if repeat == "weekdays":
        while meant.weekday() >= 5:
            meant += timedelta(days=1)
    return meant


def next_occurrence(local_time: str, repeat: ReminderRepeat, tz: ZoneInfo, *, after: float) -> tuple[str, float]:
    """The next local time as meant after the moment `after` (daily, weekdays or weekly from `local_time`), and its
    UTC instant: 08:00 stays 08:00 across daylight saving time."""
    meant = _step(_meant(local_time), repeat)
    while (due := resolve_local(meant, tz)[0].timestamp()) <= after:
        meant = _step(meant, repeat)
    return meant.strftime(MEANT), due


def parse_when(words: list[str]) -> tuple[tuple[str, object], int]:
    """The <when> of /remind: ("at", "17:00" or "YYYY-MM-DD HH:MM") or ("in", minutes), and how many words it took."""
    if not words:
        raise ReminderError("no_time")
    first = words[0].lower()
    if _DATE.match(first) and len(words) > 1 and _HHMM.match(words[1]):
        joined = f"{first} {words[1]}"
        _check_date(joined)
        return ("at", joined), 2
    if _HHMM.match(first):
        return ("at", first), 1
    span = _SPAN.match(first)
    if span and int(span.group(1)) > 0:
        return ("in", int(span.group(1)) * _SPAN_MINUTES[span.group(2)]), 1
    raise ReminderError("bad_when", value=words[0][:30])


def _check_date(value: str) -> datetime:
    found = _DATE_TIME.match(value.strip())
    if found is None:
        raise ReminderError("bad_time", value=value[:30])
    try:
        return datetime.strptime(f"{found.group(1)} {int(found.group(2)):02d}:{found.group(3)}", MEANT)
    except ValueError:
        raise ReminderError("bad_time", value=value[:30]) from None


def nearest_year(value: str, today: date, *, roll: bool = True) -> str:
    """Gemma's 'YYYY-MM-DD HH:MM' as the user meant it. The newest message's stamp shows the year ("[Mon 12 Oct 2026
    14:02]"), older stamps do not ("[Thu 8 Oct 09:41]"), so a year before this one is her guess while the month and day
    are the user's: read as the nearest such day to today (last year, this year or next). A day (with a guessed year or
    this one) more than 2 days in the past means the next one ("20 May" asked in October: next May; "5 January" asked
    in December: next January); a time that passed today or up to 2 days ago stays passed (refused: "that time has
    passed"). `roll=False` (a repeating reminder): the nearest such day only, so a long-past first day is refused
    instead of starting a year later. Anything else is returned as given; code's confirmation shows the date it
    understood (with the year when it is not this one)."""
    found = _DATE_TIME.match(value.strip())
    if found is None or int(found.group(1)[:4]) > today.year:
        return value
    written, month_day = int(found.group(1)[:4]), found.group(1)[5:]

    def on(year: int) -> date | None:
        try:
            return date.fromisoformat(f"{year}-{month_day}")
        except ValueError:  # 29 Feb in a year without it
            return None

    if written == today.year:
        days = [day for day in (on(today.year),) if day is not None]
    else:  # a tie goes to this year, then the next
        days = [day for day in (on(today.year), on(today.year + 1), on(today.year - 1)) if day is not None]
    if not days:
        return value
    meant = min(days, key=lambda day: abs((day - today).days))
    if roll and (today - meant).days > PAST_DAYS:
        ahead = [day for day in (on(today.year), on(today.year + 1)) if day is not None and day >= today]
        meant = min(ahead, default=meant)
    return f"{meant.isoformat()} {int(found.group(2)):02d}:{found.group(3)}"


def is_wake(local_time: str) -> bool:
    """A call reminder in the morning is a wake-up call ("Good morning!"); later it is a call that says why."""
    return _meant(local_time).hour in WAKE_HOURS


def _hhmm(meant: datetime) -> str:
    return f"{meant:%H:%M}"


def spoken_time(local_time: str) -> str:
    """ "7:00", "17:00": the time as the call's opening line says it."""
    meant = _meant(local_time)
    return f"{meant.hour}:{meant.minute:02d}"


def day_text(moment: datetime, today: datetime) -> str:
    """ "Tue 13 Oct", with the year when it is another year."""
    year = f" {moment.year}" if moment.year != today.year else ""
    return f"{moment:%a} {moment.day} {moment:%b}{year}"


def when_text(moment: datetime, today: datetime) -> str:
    """ "Tue 13 Oct 07:00" (the reminder's local time as the notes show it)."""
    return f"{day_text(moment, today)} {_hhmm(moment)}"


def _short_when(moment: datetime, today: datetime) -> str:
    """ "07:00" today, "Sun 11 Oct 07:00" another day (the late note)."""
    return _hhmm(moment) if moment.date() == today.date() else when_text(moment, today)


def _part_of_day(moment: datetime) -> str:
    return "morning" if moment.hour < 12 else "afternoon" if moment.hour < 17 else "evening"


def _repeat_words(repeat: ReminderRepeat, meant: datetime) -> str:
    return f"weekly on {meant:%a}" if repeat == "weekly" else repeat


STUCK_S = 2 * 3600.0  # firing this long (a note is tried for an hour): stuck, for doctor


def summary(count: int, first: tuple[float, bool] | None, failed: int, *, tz: ZoneInfo, now: float) -> str:
    """ "3 open · next Tue 13 Oct 07:00 (call) · 0 failed": /status and doctor (times and counts, never text)."""
    upcoming = ""
    if first is not None:
        moment = datetime.fromtimestamp(first[0], tz)
        upcoming = f" · next {when_text(moment, datetime.fromtimestamp(now, tz))}{' (call)' if first[1] else ''}"
    return f"{count} open{upcoming} · {failed} failed"


@dataclass(frozen=True)
class Created:
    reminder: Reminder
    note: str  # the code-written confirmation for the user (a note: in Gemma's history only as code's, code_notes)
    spoken: str  # the same in a line to say on a call (English)
    result: str  # what Gemma's tool gets


class Reminders:
    """Setting, listing and cancelling the user's reminders (the checks and the code's own words)."""

    def __init__(
        self,
        store: Store,
        tz: ZoneInfo,
        *,
        max_open: int = 50,
        max_per_day: int = 20,
        max_calls_per_day: int = 3,
        calls: bool = False,
        name: str = DEFAULT_NAME,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._tz = tz
        self.max_open = max_open
        self.max_per_day = max_per_day
        self.max_calls_per_day = max_calls_per_day
        self.calls = (
            calls  # "call me" places a Signal call; else it is a wake-up text (sent twice unless the user wrote)
        )
        self._name = name
        self._clock = clock
        self._listeners: list[Callable[[], None]] = []

    def on_change(self, listener: Callable[[], None]) -> None:
        """Called after a reminder is added or cancelled (the scheduler wakes at once)."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in self._listeners:
            listener()

    def local(self, epoch: float) -> datetime:
        return datetime.fromtimestamp(epoch, self._tz)

    def today(self) -> date:
        return self.local(self._clock()).date()

    def set(
        self,
        aci: str,
        *,
        text: str,
        at: str | None = None,
        in_minutes: object = None,
        call: bool = False,
        repeat: str = "none",
        source: ReminderSource = "chat",
        source_message_id: int | None = None,
    ) -> Created:
        """Check and save one reminder; ReminderError says why not."""
        words = " ".join(str(text or "").split())
        if not words:
            raise ReminderError("empty")
        if len(words) > TEXT_MAX:
            raise ReminderError("too_long")
        if repeat not in REPEATS:
            raise ReminderError("repeat")
        rhythm: ReminderRepeat = repeat
        now = self._clock()
        today = self.local(now)
        earliest = now - now % 60 + 60  # at least a minute ahead (counted in whole minutes)
        shifted = False
        if in_minutes is not None and in_minutes != "":
            minutes = _whole(in_minutes)
            if minutes < 1:
                raise ReminderError("too_soon")
            due = now + minutes * 60
            meant = self.local(due).replace(tzinfo=None, second=0, microsecond=0)
        elif at:
            meant = self._meant_at(str(at).strip(), today, earliest, rhythm)
            real, shifted = resolve_local(meant, self._tz)
            due = real.timestamp()
            if due < earliest:
                raise ReminderError("passed" if due < now else "too_soon")
        else:
            raise ReminderError("no_time")
        if due - now > MAX_AHEAD_S:
            raise ReminderError("too_far")
        wants_call = bool(call)
        self._check_limits(aci, now, today, meant, wants_call and self.calls)
        rid = self._store.add_reminder(
            aci,
            words,
            meant.strftime(MEANT),
            self._tz.key,
            due,
            repeat=rhythm,
            call=wants_call,
            source=source,
            source_message_id=source_message_id,
            at=iso(now),
        )
        reminder = self._store.reminder(rid)
        assert reminder is not None
        log.info("reminder %d set (%s, %s%s)", rid, source, rhythm, ", call" if wants_call else "")
        self._changed()
        return self._created(reminder, today, shifted=shifted)

    def _meant_at(self, value: str, today: datetime, earliest: float, repeat: ReminderRepeat) -> datetime:
        clock = _HHMM.match(value)
        if clock is not None:
            meant = today.replace(tzinfo=None, hour=int(clock.group(1)), minute=int(clock.group(2)), second=0)
            meant = meant.replace(microsecond=0)
            if resolve_local(meant, self._tz)[0].timestamp() < earliest:
                meant += timedelta(days=1)
        else:
            meant = _check_date(value)
        while repeat == "weekdays" and meant.weekday() >= 5:
            meant += timedelta(days=1)
        return meant

    def _check_limits(self, aci: str, now: float, today: datetime, meant: datetime, call: bool) -> None:
        mine = self._store.open_reminders(aci)
        if len(mine) >= self.max_open:
            raise ReminderError("max_open", limit=self.max_open)
        midnight = today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if self._store.count_reminders_created(aci, since=iso(midnight)) >= self.max_per_day:
            raise ReminderError("max_per_day", limit=self.max_per_day)
        if call:
            day = meant.strftime("%Y-%m-%d")
            same_day = [r for r in mine if r.call and r.local_time.startswith(day)]
            if len(same_day) >= self.max_calls_per_day:
                raise ReminderError("max_calls", limit=self.max_calls_per_day)

    def _created(self, reminder: Reminder, today: datetime, *, shifted: bool) -> Created:
        real = self.local(reminder.due_at)
        when = when_text(real, today)
        meant = _meant(reminder.local_time)
        shown = when
        if reminder.repeat != "none":
            shown = prompts.REMINDER_REPEAT_WHEN.format(
                repeat=_repeat_words(reminder.repeat, meant), time=_hhmm(meant), day=day_text(real, today)
            )
        if shifted:
            shown += prompts.REMINDER_SHIFTED.format(meant=_hhmm(meant), real=_hhmm(real))
        kind = ""
        if reminder.call and not self.calls:
            kind = prompts.REMINDER_CALLS_OFF
        elif reminder.call:
            kind = prompts.REMINDER_SET_WAKE if is_wake(reminder.local_time) else prompts.REMINDER_SET_CALL
        note = prompts.REMINDER_SET.format(id=reminder.id, when=shown, call=kind, text=reminder.text)
        told = f"{CONTRACT_KEY}:{reminder.peer_aci}"
        if not self._store.get_state(told):  # the first confirmation this user gets: the contract
            self._store.set_state(told, iso(self._clock()))
            note += "\n" + prompts.REMINDER_CONTRACT
        if reminder.call and self.calls and not self._store.get_state(CALL_WARNED_KEY):
            self._store.set_state(CALL_WARNED_KEY, iso(self._clock()))
            note += "\n\n" + prompts.REMINDER_HONEST.format(name=self._name)  # its own paragraph
        spoken = prompts.REMINDER_SPOKEN.format(when=f"{real:%A} {real.day} {real:%B}, {_hhmm(real)}")
        return Created(reminder, note, spoken, prompts.REMINDER_RESULT.format(id=reminder.id, when=when))

    def cancel(self, aci: str, number: int) -> str:
        cancelled = self._store.cancel_reminders(aci, number, at=iso(self._clock()))
        if not cancelled:
            raise ReminderError("no_reminder", number=number)
        self._changed()
        reminder = cancelled[0]
        log.info("reminder %d cancelled", reminder.id)
        return prompts.REMINDER_CANCELLED.format(id=reminder.id, when=self._when(reminder), text=reminder.text)

    def cancel_checkin(self, aci: str, number: int) -> str:
        cancelled = self._store.cancel_followups(aci, number, at=iso(self._clock()))
        if not cancelled:
            raise ReminderError("no_checkin", number=number)
        return prompts.CHECKIN_CANCELLED.format(id=cancelled[0].id, what=cancelled[0].what)

    def cancel_all(self, aci: str) -> str:
        now = iso(self._clock())
        reminders = len(self._store.cancel_reminders(aci, None, at=now))
        checkins = len(self._store.cancel_followups(aci, None, at=now))
        if reminders:
            self._changed()
        listed = [
            f"{count} {word}{'' if count == 1 else 's'}"
            for count, word in ((reminders, "reminder"), (checkins, "check-in"))
            if count
        ]
        if not listed:
            return prompts.REMINDERS_NOTHING
        return prompts.REMINDERS_CANCELLED.format(listed=" and ".join(listed))

    def _when(self, reminder: Reminder) -> str:
        meant = _meant(reminder.local_time)
        if reminder.repeat != "none":
            return f"{_repeat_words(reminder.repeat, meant)} {_hhmm(meant)}"
        return when_text(self.local(reminder.due_at), self.local(self._clock()))

    def listing(self, aci: str, *, marked: bool = False) -> str:
        """The list_reminders tool's text (as it always was): open reminders, check-ins she may send, what went wrong
        lately. `marked` (/reminders): each section under a bold heading, a blank line between (markup, sent as
        Signal's text styles); Gemma's tool result never changes with it."""
        now = self._clock()
        today = self.local(now)
        sections: list[tuple[str, list[str]]] = []
        mine = self._store.open_reminders(aci)
        if mine:
            sections.append(
                (prompts.REMINDERS_HEADER, [f"#{r.id} {self._when(r)}{self._kind(r)} · {r.text}" for r in mine])
            )
        checkins = self._store.open_followups(aci)
        if checkins:
            lines = []
            for checkin in checkins:
                due = self.local(checkin.due_at)
                lines.append(f"#c{checkin.id} {day_text(due, today)}, {_part_of_day(due)} · {checkin.what}")
            sections.append((prompts.REMINDERS_CHECKINS, lines))
        problems = self._store.reminders_with_outcome(aci, PROBLEMS, since=iso(now - PROBLEM_DAYS * 86_400))
        if problems:
            lines = []
            for r in problems:
                updated = datetime.fromisoformat(r.updated_at).astimezone(self._tz)
                why = prompts.REMINDER_OUTCOMES.get(r.outcome or "", r.outcome or "")
                lines.append(f"#{r.id} {when_text(updated, today)} · {r.text}: {why}")
            sections.append((prompts.REMINDERS_PROBLEMS, lines))
        first, last = ([prompts.REMINDERS_NONE], []) if not mine and not checkins else ([], [prompts.REMINDERS_FOOTER])
        if not marked:
            return "\n".join([*first, *(line for header, lines in sections for line in (header, *lines)), *last])
        blocks = ["\n".join([f"**{header.rstrip(':')}**", *lines]) for header, lines in sections]
        return "\n\n".join([*first, *blocks, *last])

    def open_times(self, aci: str) -> list[tuple[int, str]]:
        """The open reminders' numbers and local times ("HH:MM", as meant), as the listing shows them under its
        header: what talk may name as a reminder that exists (claims.restates)."""
        return [(r.id, _hhmm(_meant(r.local_time))) for r in self._store.open_reminders(aci)]

    def _kind(self, reminder: Reminder) -> str:
        if not reminder.call:
            return ""
        return " · call" if self.calls else " · wake-up text"

    def status_line(self) -> str:
        """/status: 'Reminders: 3 open · next Tue 13 Oct 07:00 (call) · 0 failed' (times and counts, never text)."""
        now = self._clock()
        everyone = self._store.open_reminders()
        failed = self._store.reminders_with_outcome(None, FAILED, since=iso(now - PROBLEM_DAYS * 86_400))
        first = (everyone[0].due_at, everyone[0].call and self.calls) if everyone else None
        return prompts.REMINDER_STATUS.format(summary=summary(len(everyone), first, len(failed), tz=self._tz, now=now))

    def next_call(self) -> str:
        """The next call reminder's time ("Tue 13 Oct 07:00"), or ""."""
        calls = [r for r in self._store.open_reminders() if r.call] if self.calls else []
        return when_text(self.local(calls[0].due_at), self.local(self._clock())) if calls else ""


def _whole(value: object) -> int:
    if isinstance(value, bool):
        raise ReminderError("bad_minutes")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        raise ReminderError("bad_minutes") from None


class CallPlacer(Protocol):
    """What the scheduler needs of the calls part (calls.CallManager)."""

    async def can_place(self, aci: str) -> str:
        """ "" when a call can be placed now, else a reason word: locked, in_call or not_ready."""

    async def place(self, aci: str, *, wake: bool, time: str, text: str) -> str:
        """Dial and wait until it is picked up or ends: answered, no_answer, declined, busy or call_failed."""


@dataclass
class _Retry:
    message_id: int
    first: float
    next: float


class ReminderScheduler:
    """Fires due reminders: sleeps until the next one (at most 30 s at a time) and wakes at once on a change."""

    def __init__(
        self,
        store: Store,
        reminders: Reminders,
        send: Callable[[int], Awaitable[None]],
        *,
        calls: CallPlacer | None = None,
        in_call: Callable[[], bool] = lambda: False,
        locked: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._reminders = reminders
        self._send = send  # the bridge's send: marks the stored note sent or failed
        self._calls = calls  # None: calls are off ("call me" reminders are texts)
        self._in_call = in_call
        self._locked = locked  # /lock: nothing goes out until /unlock; a call reminder then never calls
        self._clock = clock
        self._sleep = sleep
        self._tz = reminders._tz
        self._wake = asyncio.Event()
        self._retries: dict[int, _Retry] = {}
        self._held: set[int] = set()  # call reminders that came due while she was on a call
        self._held_locked: set[int] = set()  # call reminders that came due while she was locked
        self._tasks: set[asyncio.Task[None]] = set()  # call reminders being placed (a retry waits 2 minutes)
        self._pruned_day = ""
        reminders.on_change(self.notify)

    def notify(self) -> None:
        """Something changed (a reminder added or cancelled, a call ended): look again at once."""
        self._wake.set()

    async def run_forever(self) -> None:
        while True:
            failed = False
            try:
                await self.tick()
            except Exception:  # a TaskGroup task that raises would take the bridge down
                log.exception("reminders: a tick failed")
                failed = True
            await self._nap(failed)

    async def drain(self) -> None:
        """Wait for the call reminders being placed (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _nap(self, failed: bool) -> None:
        now = self._clock()
        wait = POLL_S
        if not self._in_call() and not self._locked():
            for at in (self._store.next_reminder_due(), *(r.next for r in self._retries.values())):
                if at is not None:
                    wait = min(wait, max(0.0, at - now))
        if failed:
            wait = max(wait, 1.0)
        sleeper = asyncio.ensure_future(self._sleep(wait))
        waker = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            waker.cancel()
            self._wake.clear()

    async def tick(self) -> None:
        now = self._clock()
        self._prune(now)
        due = self._store.due_reminders(now)
        if self._locked():  # /lock: everything waits (retries too), and a call reminder will be a text without a call
            self._held_locked.update(r.id for r in due if r.call)
            return
        if self._in_call():  # texts wait while she is on a call with the user, like every text
            self._held.update(r.id for r in due if r.call)
            return
        for reminder in due:
            if reminder.call:  # taken up now: its checks wait for the voice service, and it must not start twice
                self._store.start_firing(reminder.id, at=iso(now))
                task = asyncio.create_task(self._call_reminder(reminder), name=f"reminder-{reminder.id}")
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            else:
                await self._fire_text(reminder, now)
        for rid, retry in list(self._retries.items()):
            if retry.next <= now:
                await self._retry(rid, retry, now)

    # text reminders

    def _repeat_suffix(self, reminder: Reminder) -> str:
        if reminder.repeat == "none":
            return ""
        meant = _meant(reminder.local_time)
        return prompts.REMINDER_REPEATS.format(repeat=_repeat_words(reminder.repeat, meant), time=_hhmm(meant))

    def _fence(self, reminder: Reminder, text: str) -> int:
        """Store the note and mark the reminder firing in one transaction: after a crash it is known what was meant
        to go out."""
        with self._store.tx():
            conversation = self._store.current_conversation()
            out_id = self._store.add_outgoing(conversation.id, reminder.peer_aci, "note", text, None)
            self._store.fire_reminder(reminder.id, out_id, at=iso(self._clock()))
        return out_id

    def _sent(self, out_id: int) -> bool:
        return self._store.message(out_id).status == "sent"

    async def _fire_text(self, reminder: Reminder, now: float) -> None:
        text = prompts.REMINDER_NOTE.format(text=reminder.text) + self._repeat_suffix(reminder)
        out_id = self._fence(reminder, text)
        await self._send(out_id)
        if self._sent(out_id):
            log.info("reminder %d sent", reminder.id)
            self._complete(reminder, "sent")
        else:
            log.warning("reminder %d: the note did not go out; trying again in 5 minutes", reminder.id)
            self._retries[reminder.id] = _Retry(out_id, now, now + RETRY_S)

    async def _retry(self, rid: int, retry: _Retry, now: float) -> None:
        reminder = self._store.reminder(rid)
        if reminder is None or reminder.status != "firing":  # cancelled meanwhile
            del self._retries[rid]
            self._store.set_status(retry.message_id, "ignored")
            return
        await self._send(retry.message_id)
        if self._sent(retry.message_id):
            del self._retries[rid]
            log.info("reminder %d sent on a later try", rid)
            self._complete(reminder, "sent")
        elif now - retry.first >= RETRY_FOR_S - 1:
            del self._retries[rid]
            self._store.set_status(retry.message_id, "ignored")  # recovery must never send it after all
            log.error("reminder %d failed: the note could not be sent for an hour", rid)
            self._complete(reminder, "send_failed")
        else:
            retry.next = now + RETRY_S

    def _complete(self, reminder: Reminder, outcome: str) -> None:
        """Done, or open again for the next occurrence of a repeat; a note that never went out is failed."""
        now = self._clock()
        if reminder.repeat != "none":
            meant, due = next_occurrence(reminder.local_time, reminder.repeat, self._tz, after=now)
            self._store.reschedule_reminder(reminder.id, meant, due, outcome=outcome, at=iso(now))
        else:
            status: Literal["done", "failed"] = "failed" if outcome == "send_failed" else "done"
            self._store.finish_reminder(reminder.id, status, outcome, at=iso(now))

    # call reminders

    def _calls_today(self, now: float) -> int:
        day = self._reminders.local(now).strftime("%Y-%m-%d")
        saved = (self._store.get_state(CALLS_DAY_KEY) or "").split()
        return int(saved[1]) if len(saved) == 2 and saved[0] == day and saved[1].isdigit() else 0

    def _count_call(self, now: float) -> None:
        day = self._reminders.local(now).strftime("%Y-%m-%d")
        self._store.set_state(CALLS_DAY_KEY, f"{day} {self._calls_today(now) + 1}")

    async def _why_no_call(self, reminder: Reminder, now: float) -> str:
        if self._calls is None:
            return "calls_off"
        if reminder.id in self._held_locked:
            return "locked"
        if reminder.id in self._held:
            return "in_call"
        if now - reminder.due_at > CALL_LATE_S:
            return "late"
        if self._calls_today(now) >= self._reminders.max_calls_per_day:
            return "day_cap"
        return await self._calls.can_place(reminder.peer_aci)

    def _call_note(self, reminder: Reminder, why: str) -> str:
        wake = is_wake(reminder.local_time)
        meant = _meant(reminder.local_time)
        if why:
            limit = self._reminders.max_calls_per_day
            reason = prompts.NO_CALL_WHY.get(why, why).format(limit=limit, s="" if limit == 1 else "s")
            template = prompts.WAKE_NO_CALL if wake else prompts.CALL_REMINDER_NO_CALL
            text = template.format(time=_hhmm(meant), text=reminder.text, why=reason)
        else:
            template = prompts.WAKE_CALL_NOTE if wake else prompts.CALL_REMINDER_NOTE
            text = template.format(time=_hhmm(meant), text=reminder.text)
        return text + self._repeat_suffix(reminder)

    async def _call_reminder(self, reminder: Reminder) -> None:
        try:
            await self._place(reminder)
        except Exception:
            log.exception("reminder %d: the call reminder failed", reminder.id)
            current = self._store.reminder(reminder.id)
            if current is not None and current.status == "firing":
                self._complete(current, "call_failed")
        finally:
            self._held.discard(reminder.id)
            self._held_locked.discard(reminder.id)

    async def _place(self, reminder: Reminder) -> None:
        now = self._clock()
        why = await self._why_no_call(reminder, now)
        current = self._store.reminder(reminder.id)
        if current is None or current.status != "firing":  # cancelled while the checks ran
            log.info("reminder %d: cancelled before it went out", reminder.id)
            return
        out_id = self._fence(reminder, self._call_note(reminder, why))
        await self._send(out_id)  # the text first, always (also when the call cannot be placed)
        sent = self._sent(out_id)
        if not sent:
            self._store.set_status(out_id, "ignored")
        if why or self._calls is None:
            log.info("reminder %d: no call (%s)", reminder.id, why)
            if why not in ("in_call", "late", "locked"):  # still asleep, maybe: one more text unless the user wrote
                await self._second_text(reminder, out_id)
            self._finish_call(reminder.id, "sent" if sent else "send_failed")
            return
        self._count_call(now)
        wake = is_wake(reminder.local_time)
        first_at = _hhmm(self._reminders.local(now))
        outcome = await self._dial(reminder, wake)
        if outcome in UNANSWERED:
            await self._sleep(CALL_RETRY_S)
            current = self._store.reminder(reminder.id)
            if current is None or current.status != "firing":
                log.info("reminder %d: cancelled before the retry", reminder.id)
                return
            if self._store.wrote_after(reminder.peer_aci, out_id):  # "I'm up": no second call
                log.info("reminder %d: the user wrote; no retry", reminder.id)
                self._finish_call(reminder.id, outcome)
                return
            second_at = _hhmm(self._reminders.local(self._clock()))
            first = outcome
            again = await self._calls.can_place(reminder.peer_aci)
            outcome = "call_failed" if again else await self._dial(reminder, wake)
            if outcome in UNANSWERED:
                failed = first == "call_failed" and outcome == "call_failed"
                if wake:
                    template = prompts.WAKE_NOT_PLACED if failed else prompts.WAKE_UNANSWERED
                else:
                    template = prompts.CALL_REMINDER_NOT_PLACED if failed else prompts.CALL_REMINDER_UNANSWERED
                await self._note(reminder.peer_aci, template.format(first=first_at, second=second_at))
                if not failed and outcome == "call_failed":
                    outcome = first  # one no-answer is the better word for what happened
        log.info("reminder %d: call %s", reminder.id, outcome)
        self._finish_call(reminder.id, outcome)

    async def _dial(self, reminder: Reminder, wake: bool) -> str:
        assert self._calls is not None
        try:
            return await self._calls.place(
                reminder.peer_aci, wake=wake, time=spoken_time(reminder.local_time), text=reminder.text
            )
        except Exception:
            log.exception("reminder %d: placing the call failed", reminder.id)
            return "call_failed"

    async def _second_text(self, reminder: Reminder, out_id: int) -> None:
        await self._sleep(CALL_RETRY_S)
        current = self._store.reminder(reminder.id)
        if current is None or current.status != "firing" or self._store.wrote_after(reminder.peer_aci, out_id):
            return
        meant = _meant(reminder.local_time)
        if is_wake(reminder.local_time):
            text = prompts.WAKE_SECOND.format(time=_hhmm(meant), text=reminder.text)
        else:
            text = prompts.CALL_REMINDER_SECOND.format(text=reminder.text)
        await self._note(reminder.peer_aci, text)

    def _finish_call(self, rid: int, outcome: str) -> None:
        current = self._store.reminder(rid)
        if current is not None and current.status == "firing":
            self._complete(current, outcome)

    async def _note(self, aci: str, text: str) -> None:
        conversation = self._store.current_conversation()
        await self._send(self._store.add_outgoing(conversation.id, aci, "note", text, None))

    # start and housekeeping

    async def start_up(self, *, offline_since: float | None, started_at: float) -> None:
        """Before recovery, once signal-cli is up: finish what was being sent when the bridge stopped, and one late
        note per user for what was missed (a call is never placed late; a repeat appears once, then goes on)."""
        now = self._clock()
        today = self._reminders.local(now)
        late: dict[str, list[tuple[float, str]]] = {}
        for reminder in self._store.firing_reminders():
            if reminder.message_id is None:  # taken up, nothing sent yet: open again, handled like any due one below
                self._store.reopen_reminder(reminder.id, at=iso(now))
                continue
            note = self._store.message(reminder.message_id)
            if note.status == "sent":
                self._complete(reminder, "missed_call" if reminder.call else "sent")
                continue
            self._store.set_status(note.id, "ignored")  # maybe sent: never again by recovery
            moment = resolve_local(_meant(reminder.local_time), self._tz)[0]
            line = prompts.REMINDER_LATE_LINE.format(when=_short_when(moment, today), text=reminder.text)
            late.setdefault(reminder.peer_aci, []).append((moment.timestamp(), line + prompts.REMINDER_LATE_MAYBE))
            self._complete(reminder, "maybe_repeat")
        for reminder in self._store.due_reminders(now - ON_TIME_S):
            if reminder.call and now - reminder.due_at <= CALL_LATE_S:
                continue  # a quick restart: still placed, at once
            calling = reminder.call and self._calls is not None  # without calls a wake-up is a text, missed as one
            moment, outcome = self._last_missed(reminder, now), "missed_call" if calling else "late"
            when = _short_when(moment, today)
            if calling:
                template = prompts.REMINDER_LATE_WAKE if is_wake(reminder.local_time) else prompts.REMINDER_LATE_CALL
                line = template.format(when=when, text=reminder.text)
            else:
                line = prompts.REMINDER_LATE_LINE.format(when=when, text=reminder.text)
            late.setdefault(reminder.peer_aci, []).append((moment.timestamp(), line))
            self._complete(reminder, outcome)
        for aci, lines in late.items():
            await self._note(aci, self._late_note(sorted(lines), offline_since, started_at, today))
            log.info("reminders: late note with %d reminder(s)", len(lines))
        self._prune(now)

    def _last_missed(self, reminder: Reminder, now: float) -> datetime:
        """The latest occurrence at or before now (a repeat missed several times is listed once)."""
        meant = _meant(reminder.local_time)
        if reminder.repeat != "none":
            while resolve_local(following := _step(meant, reminder.repeat), self._tz)[0].timestamp() <= now:
                meant = following
        return resolve_local(meant, self._tz)[0]

    def _late_note(
        self, lines: list[tuple[float, str]], offline_since: float | None, started_at: float, today: datetime
    ) -> str:
        until = _hhmm(self._reminders.local(started_at))
        if offline_since is None:
            header = prompts.REMINDER_LATE_UNTIL.format(until=until)
        else:
            header = prompts.REMINDER_LATE.format(
                since=_short_when(self._reminders.local(offline_since), today), until=until
            )
        shown = [line for _, line in lines[:LATE_LINES]]
        if len(lines) > LATE_LINES:
            shown.append(prompts.REMINDER_LATE_MORE.format(count=len(lines) - LATE_LINES))
        return "\n".join([header, *shown])

    def _prune(self, now: float) -> None:
        day = self._reminders.local(now).strftime("%Y-%m-%d")
        if day == self._pruned_day:
            return
        self._pruned_day = day
        removed = self._store.prune_reminders(before=iso(now - KEEP_DAYS * 86_400))
        if removed:
            log.info("reminders: %d old reminder(s) deleted", removed)
