"""The calendars: events in local time from each configured calendar, or why a calendar could not be read.

A Google calendar is read through the calendar helper (tools/ical): the bridge runs it with fixed arguments (no shell,
a clean environment, a time limit) and reads one JSON object back; it never holds the calendar's address. Only the
helper's own error codes are believed, never its text. An Outlook calendar is read through Graph (calendar_graph).
With several calendars each is read at the same time (a helper process of its own per Google calendar), one failing
calendar never hides the others (it is named in `Agenda.missed`), each event carries its calendar's name, and an event
in two calendars (the same UID and start) is kept once. With one calendar nothing changes: its failure is raised as
before. Nothing is cached or stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from datetime import time as clock_time
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from local_assistant.config import DEFAULT_CALENDAR, CalendarSource, Config
from local_assistant.part_status import PartStatus, calendar_part

log = logging.getLogger(__name__)

SEARCH_LIMIT = 10
_WORD = re.compile(r"\w+")  # the helper's words


class FeedError(Exception):
    """The calendar could not be read; the message is one of the bridge's own words."""

    reason = "unreachable"


class FeedGone(FeedError):
    """Google no longer serves the saved address (reset, or the calendar removed)."""

    reason = "gone"


class FeedNotSet(FeedError):
    """No usable address is saved (scripts/set-calendar-url.sh)."""

    reason = "not_set"


class FeedSignIn(FeedError):
    """An Outlook calendar's mailbox needs a new sign-in (scripts/mail-login.sh)."""

    reason = "sign_in"


class FeedNoConsent(FeedError):
    """An Outlook calendar's mailbox has no consent for Calendars.Read yet (an administrator, or a new sign-in)."""

    reason = "no_consent"


@dataclass(frozen=True)
class Event:
    title: str  # as the calendar has it (cleaned where it is shown)
    place: str
    start: datetime  # in the configured time zone; an all-day item starts at local midnight
    end: datetime  # exclusive: an all-day item ends at the midnight after its last day
    all_day: bool
    zone: str  # the event's own time zone when its clock differs from the configured one, else ""
    response: str  # the calendar owner's answer to an invitation ("needs-action", "declined", …); "" if none
    uid: str
    calendar: str = ""  # its calendar's shown name when there are several, else ""


@dataclass(frozen=True)
class Missed:
    """A calendar that could not be read this time, and why (a FeedError's reason)."""

    source: CalendarSource
    reason: str


@dataclass(frozen=True)
class Agenda:
    events: tuple[Event, ...]
    truncated: bool  # the helper stopped early (a very long range or a hostile rule), or a cap was reached
    partial: bool = False  # a search found no event with every word: these have the most of them
    missed: tuple[Missed, ...] = ()  # with several calendars: those that could not be read


class Reader(Protocol):
    """One calendar."""

    async def events(self, start: datetime, end: datetime) -> Agenda: ...

    async def search(self, words: str, start: datetime, end: datetime, limit: int = SEARCH_LIMIT) -> Agenda: ...


def search_words(words: str) -> list[str]:
    """The words a search looks for, as the helper takes them: one-letter words only when there is nothing else."""
    found = _WORD.findall(words.casefold())
    return [word for word in found if len(word) > 1] or found


def best_matches(events: Sequence[Event], words: str) -> tuple[list[Event], bool]:
    """The events whose title or place has the most of the words (every word when any event has them all), and
    whether those are only the closest matches."""
    wanted = set(search_words(words))
    scored = [(len(wanted & set(_WORD.findall(f"{e.title} {e.place}".casefold()))), e) for e in events]
    best = max((score for score, _ in scored), default=0)
    found = [event for score, event in scored if best and score == best]
    return found, bool(found) and best < len(wanted)


def _helper_env() -> dict[str, str]:
    """The helper gets a clean environment: no API keys or other secrets of the bridge's shell."""
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG") if key in os.environ}
    return env | {"PATH": "/usr/bin:/bin"}


class CalendarFeed:
    """Every configured calendar, read together. `outlook` makes the reader of an Outlook calendar
    (calendar_graph.OutlookCalendars.reader); it is needed only when the config has one."""

    def __init__(
        self,
        config: Config,
        *,
        status: PartStatus | None = None,
        outlook: Callable[[CalendarSource], Reader] | None = None,
    ) -> None:
        self._status = status or PartStatus(None)  # what each calendar's last real read did (doctor, /status)
        self._readers: list[tuple[CalendarSource, Reader]] = []
        for source in config.calendars:
            if not source.mailbox:
                self._readers.append((source, IcalReader(config, source, status=self._status)))
            elif outlook is not None:
                self._readers.append((source, outlook(source)))
            else:
                raise ValueError(f"calendar {source.name}: an Outlook calendar needs its reader")

    @property
    def sources(self) -> tuple[CalendarSource, ...]:
        return tuple(source for source, _ in self._readers)

    @property
    def several(self) -> bool:
        return len(self._readers) > 1

    async def events(self, start: datetime, end: datetime) -> Agenda:
        """Every event overlapping [start, end), in start order."""
        return await self._all(lambda reader: reader.events(start, end))

    async def search(self, words: str, start: datetime, end: datetime, limit: int = SEARCH_LIMIT) -> Agenda:
        """The first events between start and end whose title or place has every word (or the most of them)."""
        agenda = await self._all(lambda reader: reader.search(words, start, end, limit))
        if not self.several:
            return agenda
        found, partial = best_matches(agenda.events, words)
        return replace(
            agenda, events=tuple(found[:limit]), truncated=agenda.truncated or len(found) > limit, partial=partial
        )

    async def _all(self, read: Callable[[Reader], Awaitable[Agenda]]) -> Agenda:
        if not self.several:
            return await read(self._readers[0][1])  # one calendar: its failure is raised, as before names
        results = await asyncio.gather(*(self._one(source, reader, read) for source, reader in self._readers))
        events: list[Event] = []
        seen: set[tuple[str, datetime]] = set()
        missed: list[Missed] = []
        truncated = partial = False
        for (source, _), result in zip(self._readers, results, strict=True):
            if isinstance(result, Missed):
                missed.append(result)
                continue
            truncated |= result.truncated
            partial |= result.partial
            for event in result.events:
                if event.uid and (event.uid, event.start) in seen:
                    continue  # the same invitation in two calendars
                seen.add((event.uid, event.start))
                events.append(replace(event, calendar=source.shown))
        events.sort(key=lambda event: (event.start, not event.all_day))
        return Agenda(tuple(events), truncated, partial, tuple(missed))

    @staticmethod
    async def _one(
        source: CalendarSource, reader: Reader, read: Callable[[Reader], Awaitable[Agenda]]
    ) -> Agenda | Missed:
        try:
            return await read(reader)
        except FeedError as exc:
            return Missed(source, exc.reason)
        except Exception:  # one calendar's surprise never takes the others down
            log.exception("calendar %s: read failed unexpectedly", source.name)
            return Missed(source, "unreachable")


class IcalReader:
    """One Google calendar, through the calendar helper."""

    def __init__(self, config: Config, source: CalendarSource, *, status: PartStatus | None = None) -> None:
        self._tz = config.timezone
        self._command = config.calendar_helper
        self._timeout = config.calendar_helper_timeout
        self._status = status or PartStatus(None)
        self._part = calendar_part(source.name)
        self._which = [] if source.name == DEFAULT_CALENDAR else ["--calendar", source.name]

    async def events(self, start: datetime, end: datetime) -> Agenda:
        """Every event overlapping [start, end), in start order."""
        return self._agenda(await self._run(self._range(start, end)))

    async def search(self, words: str, start: datetime, end: datetime, limit: int = SEARCH_LIMIT) -> Agenda:
        """The first events between start and end whose title or place has every word."""
        # one argument each: words starting with "-" are never read as an option
        return self._agenda(await self._run([*self._range(start, end), f"--words={words}", f"--limit={limit}"]))

    def _range(self, start: datetime, end: datetime) -> list[str]:
        return ["events", "--from", start.isoformat(), "--to", end.isoformat(), "--zone", self._tz.key, *self._which]

    async def _run(self, args: list[str]) -> dict[str, Any]:
        """Run the helper; its JSON on success, a FeedError naming what went wrong otherwise."""
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=_helper_env(),
            )
        except OSError as exc:  # not set up yet (tools/ical/.venv missing) or broken
            log.warning("calendar helper did not start: %s", type(exc).__name__)
            self._status.failed(self._part, "helper_missing")
            raise FeedError("the calendar helper is not installed (cd tools/ical && uv sync --frozen)") from None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self._timeout)
        except TimeoutError:
            await self._stop(proc)
            log.warning("calendar helper timed out after %.0f s", self._timeout)
            self._status.failed(self._part, "no_answer")
            raise FeedError("unreachable: no answer in time") from None
        except BaseException:  # an outer limit or a shutdown cancelled the read: no helper is left behind
            await asyncio.shield(self._stop(proc))
            raise
        try:
            data = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            data = None
        if not isinstance(data, dict):
            log.warning("calendar helper gave no JSON (exit %s)", proc.returncode)
            self._status.failed(self._part, "helper_failed")
            raise FeedError("no answer from the calendar helper")
        error = data.get("error") if not data.get("ok") else None
        log.info(
            "calendar helper: %s in %d ms",
            error or f"{len(data.get('events') or [])} events",
            (time.monotonic() - started) * 1000,
        )
        if error == "gone":
            self._status.failed(self._part, "gone")
            raise FeedGone("the address no longer works")
        if error == "not_set":
            self._status.failed(self._part, "not_set")
            raise FeedNotSet("the calendar is not set up")
        if error is not None:
            self._status.failed(self._part, "no_answer" if error == "unreachable" else "bad_answer")
            raise FeedError("unreachable" if error == "unreachable" else "invalid answer from Google")
        self._status.ok(self._part)
        return data

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

    def _agenda(self, data: dict[str, Any]) -> Agenda:
        events = [event for item in data.get("events") or [] if (event := self._event(item)) is not None]
        return Agenda(tuple(events), bool(data.get("truncated")), bool(data.get("partial")))

    def _event(self, item: Any) -> Event | None:
        if not isinstance(item, dict):
            return None
        try:
            start, end = self._moment(item["start"]), self._moment(item["end"])
        except (KeyError, TypeError, ValueError):
            return None
        return Event(
            title=str(item.get("title") or ""),
            place=str(item.get("place") or ""),
            start=start,
            end=max(end, start),
            all_day=bool(item.get("all_day")),
            zone=self._other_zone(start, item.get("zone")),
            response=str(item.get("answer") or ""),
            uid=str(item.get("uid") or ""),
        )

    def _moment(self, value: Any) -> datetime:
        text = str(value)
        if len(text) == 10:  # an all-day date: its local midnight
            return datetime.combine(date.fromisoformat(text), clock_time(), self._tz)
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            raise ValueError("no offset")
        return moment.astimezone(self._tz)

    def _other_zone(self, moment: datetime, name: Any) -> str:
        """The event's own zone, when its clock differs from the configured zone at that moment."""
        if not isinstance(name, str) or not name:
            return ""
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return ""
        return name if moment.astimezone(zone).utcoffset() != moment.utcoffset() else ""
