"""Calendar helper for local-assistant: one Google calendar per run, through its iCal address (secret or public).

The bridge runs it with fixed arguments (no shell) and reads one JSON object from stdout; exit 0 when ok, 1 on an
error ({"ok": false, "error": "not_set" | "gone" | "unreachable" | "invalid", "detail": …}):

    helper.py events --from <ISO time with offset> --to <ISO time with offset> --zone <IANA zone>
                     [--calendar <name>] [--words <words>] [--limit <n>]

The address is read from ~/.config/local-assistant/calendar.env (0600) and never printed: CALENDAR_ICS_URL for the
calendar named "calendar" (the default), CALENDAR_ICS_URL_<NAME> for the others ("school-days" reads
CALENDAR_ICS_URL_SCHOOL_DAYS). Only https://calendar.google.com/calendar/ical/… is fetched, with no proxies and no redirects followed. Repeating events
are expanded with recurring-ical-events: single events together, each repeating event on its own within its own small
time budget and instance cap, so one hostile or broken invitation is left out instead of taking the calendar down. Rules
that cannot be read, never move on (INTERVAL below 1) or repeat more often than daily (Google Calendar cannot make
those) are dropped first, and parsing plus expansion have a hard time limit. Only title, place, times, the event's zone, the calendar
owner's answer and the UID are passed on: no descriptions, attendee lists or links.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import signal
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
import recurring_ical_events

ADDRESS_FILE = Path.home() / ".config" / "local-assistant" / "calendar.env"
FEED_PREFIX = "https://calendar.google.com/calendar/ical/"
MAX_BYTES = 20 * 1024 * 1024
FETCH_TIMEOUT = 20.0  # the bridge waits 30 s
MAX_INSTANCES = 2000  # occurrences looked at, in all
SERIES_INSTANCES = 400  # occurrences of one repeating event (a daily one for a year fits)
SERIES_SECONDS = 0.5  # expanding one repeating event
EXPAND_SECONDS = 15.0  # parsing and expanding, in all
TEXT_CHARS = 500  # title and place passed on, at most
YEARS = range(1900, 2201)  # dates outside overflow the time-zone arithmetic (a year-9999 invitation)
_FAST = frozenset({"SECONDLY", "MINUTELY", "HOURLY"})
_FINE_PARTS = ("BYSECOND", "BYMINUTE", "BYHOUR")
DEFAULT_LIMIT = 1000
DEFAULT_CALENDAR = "calendar"  # the first calendar's name: its address is CALENDAR_ICS_URL, as before names
_WORD = re.compile(r"\w+")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")  # the bridge's rule for calendar names

Fetch = Callable[[str], bytes]


class Refused(ValueError):
    """The helper's own refusal; its fixed text is passed on (never the address or event text)."""


class Gone(Exception):
    """Google no longer serves this address (reset, or the calendar removed)."""


class Unreachable(Exception):
    """No usable answer from Google right now."""


class _TooSlow(Exception):
    """Parsing and expanding ran past EXPAND_SECONDS."""


class _SeriesTooSlow(Exception):
    """One repeating event ran past SERIES_SECONDS: it is left out."""


class _Broken(Exception):
    """Expanding some events failed with the libraries' own error: they are left out."""


class _Alarm(Exception):
    """The interval timer went off."""


def _stop(signum: int, frame: object) -> None:
    raise _Alarm()


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect comes back as an error: the secret address must not travel anywhere else."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def address_key(name: str) -> str:
    """The calendar.env key of a calendar: CALENDAR_ICS_URL for the default one, else CALENDAR_ICS_URL_<NAME>."""
    return "CALENDAR_ICS_URL" if name == DEFAULT_CALENDAR else f"CALENDAR_ICS_URL_{name.upper().replace('-', '_')}"


def load_address(path: Path, name: str = DEFAULT_CALENDAR) -> str:
    """The calendar's saved address; Refused when there is none, the file is not private or it is not Google's."""
    missing = "no saved address" + ("" if name == DEFAULT_CALENDAR else f" for {name}")
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        raise Refused(missing) from None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise Refused("calendar.env must be private (chmod 600)")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    address = values.get(address_key(name), "")
    if not address:
        raise Refused(missing)
    if not address.startswith(FEED_PREFIX) or not address.endswith(".ics"):
        raise Refused("the saved address is not a Google iCal address")
    return address


def owner_of(address: str) -> str:
    """The calendar's ID from its address: the owner's email for a primary calendar."""
    return urllib.parse.unquote(urllib.parse.urlsplit(address).path.split("/")[3]).lower()


def fetch(url: str, *, max_bytes: int = MAX_BYTES, timeout: float = FETCH_TIMEOUT) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "local-assistant calendar helper"})
    try:
        with opener.open(request, timeout=timeout) as response:
            data: bytes = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise Unreachable(f"HTTP {exc.code}: a redirect is not followed") from None
        if exc.code in (401, 403, 404, 410):
            raise Gone(f"HTTP {exc.code}") from None
        raise Unreachable(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
        raise Unreachable(type(exc).__name__) from None
    if len(data) > max_bytes:
        raise Refused(f"the calendar is too large (over {max_bytes // (1024 * 1024)} MB)")
    return data


def _moment(value: date | datetime, zone: ZoneInfo) -> datetime:
    """A start or end as an aware datetime: a date is its local midnight, a floating time is in the zone."""
    if not isinstance(value, datetime):
        return datetime.combine(value, clock_time(), zone)
    return value if value.tzinfo is not None else value.replace(tzinfo=zone)


def _shown(value: date | datetime, zone: ZoneInfo) -> str:
    if not isinstance(value, datetime):
        return value.isoformat()
    return _moment(value, zone).isoformat()


def _zone_name(value: date | datetime) -> str:
    """The event's own IANA zone ("" for all-day, floating and UTC times)."""
    key = getattr(value.tzinfo, "key", "") if isinstance(value, datetime) else ""
    return "" if key in ("", "UTC", "Etc/UTC") else str(key)


def _answer(component: Any, owner: str) -> str:
    attendees = component.get("ATTENDEE")
    for attendee in attendees if isinstance(attendees, list) else [attendees] if attendees else []:
        if str(attendee).lower() == f"mailto:{owner}":
            return str(attendee.params.get("PARTSTAT", "")).lower()
    return ""


def _usable(component: Any) -> bool:
    """False for an event whose start, end or repeat rule cannot be read, whose dates lie outside 1900-2200, whose
    rule never moves on (INTERVAL < 1), or which repeats more often than daily: recurring-ical-events would stop,
    overflow, loop forever or flood on those."""
    try:
        for name in ("DTSTART", "DTEND"):
            if name in component and component[name].dt.year not in YEARS:
                return False
        rules = component.get("RRULE")
        for rule in rules if isinstance(rules, list) else [rules] if rules is not None else []:
            frequency = rule.get("FREQ")
            if not frequency or str(frequency[0]).upper() in _FAST or any(part in rule for part in _FINE_PARTS):
                return False
            if any(int(value) < 1 for value in rule.get("INTERVAL", [1])):
                return False
    except (ValueError, TypeError, KeyError):  # icalendar's BrokenCalendarProperty is a ValueError
        return False
    return True


def _repeats(group: list[Any]) -> bool:
    return any(component.get("RRULE") or component.get("RDATE") for component in group)


def _part(
    calendar: Any,
    others: list[Any],
    components: list[Any],
    start: datetime,
    end: datetime,
    deadline: float,
    budget: float,
) -> list[Any]:
    """The occurrences of some events between start and end, expanded on their own within their time budget (the
    calendar's own properties and time zones apply)."""
    if not components:
        return []
    part = icalendar.Calendar()
    part.update(calendar)
    part.subcomponents = [*others, *components]
    seconds = min(budget, deadline - time.monotonic())
    if seconds <= 0:
        raise _TooSlow()
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return list(recurring_ical_events.of(part, skip_bad_series=True).between(start, end))
    except _Alarm:
        if budget >= EXPAND_SECONDS or time.monotonic() >= deadline - 0.05:
            raise _TooSlow() from None
        raise _SeriesTooSlow() from None
    except Exception as exc:  # anything else in these events (the libraries' own errors): leave them out
        if time.monotonic() >= deadline - 0.05:
            raise _TooSlow() from None
        raise _Broken(type(exc).__name__) from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _singles(
    calendar: Any, others: list[Any], singles: list[Any], start: datetime, end: datetime, deadline: float
) -> tuple[list[Any], bool]:
    """The single events' occurrences; if expanding them together fails, one at a time, leaving out those that fail."""
    try:
        return _part(calendar, others, singles, start, end, deadline, EXPAND_SECONDS), False
    except _Broken:
        found: list[Any] = []
        for component in singles:
            try:
                found += _part(calendar, others, [component], start, end, deadline, EXPAND_SECONDS)
            except _Broken:
                continue
        return found, True


def _parse(data: bytes, deadline: float) -> Any:
    signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline - time.monotonic()))
    try:
        calendar = icalendar.Calendar.from_ical(data)
    except _Alarm:
        raise _TooSlow() from None
    except ValueError:
        raise Refused("the answer is not an iCalendar file") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if not isinstance(calendar, icalendar.Calendar):
        raise Refused("the answer is not an iCalendar file")
    return calendar


def expand(
    data: bytes, start: datetime, end: datetime, zone: ZoneInfo, owner: str, words: list[str], limit: int
) -> dict[str, Any]:
    """The events overlapping [start, end) in start order (at most `limit`), as the bridge reads them.

    Raises _TooSlow when parsing and expanding take longer than EXPAND_SECONDS."""
    previous = signal.signal(signal.SIGALRM, _stop)
    try:
        return _expand(data, start.astimezone(zone), end.astimezone(zone), zone, owner, words, limit)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _expand(
    data: bytes, start: datetime, end: datetime, zone: ZoneInfo, owner: str, words: list[str], limit: int
) -> dict[str, Any]:
    deadline = time.monotonic() + EXPAND_SECONDS
    calendar = _parse(data, deadline)
    others = [component for component in calendar.subcomponents if component.name != "VEVENT"]
    groups: dict[str, list[Any]] = {}  # an event and its moved instances share a UID
    for component in calendar.subcomponents:
        if component.name == "VEVENT" and _usable(component):
            groups.setdefault(str(component.get("UID", "")) or f"#{id(component)}", []).append(component)
    singles = [component for group in groups.values() if not _repeats(group) for component in group]
    occurrences, truncated = _singles(calendar, others, singles, start, end, deadline)
    for group in (group for group in groups.values() if _repeats(group)):
        try:
            found = _part(calendar, others, group, start, end, deadline, SERIES_SECONDS)
        except (_SeriesTooSlow, _Broken):
            truncated = True  # this one repeating event is left out
            continue
        if len(found) > SERIES_INSTANCES:
            found = sorted(found, key=lambda c: _moment(c["DTSTART"].dt, zone))[:SERIES_INSTANCES]
            truncated = True
        occurrences += found
    occurrences.sort(key=lambda c: (_moment(c["DTSTART"].dt, zone), isinstance(c["DTSTART"].dt, datetime)))
    candidates: list[tuple[int, dict[str, Any]]] = []  # how many of the words each event has, the event
    for seen, component in enumerate(occurrences):
        if seen >= MAX_INSTANCES:
            truncated = True
            break
        first = component["DTSTART"].dt
        last = component["DTEND"].dt if component.get("DTEND") else first
        if not isinstance(first, datetime) and last == first:
            last = first + timedelta(days=1)
        begin, finish = _moment(first, zone), _moment(last, zone)
        if begin >= end or (begin < start and finish <= start):  # outside [start, end)
            continue
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        answer = _answer(component, owner)
        if answer == "declined":  # the calendar's owner is not going: never shown, so never counted
            continue
        title = str(component.get("SUMMARY", ""))[:TEXT_CHARS]
        place = str(component.get("LOCATION", ""))[:TEXT_CHARS]
        score = len(set(words) & set(_WORD.findall(f"{title} {place}".casefold()))) if words else 0
        if words and not score:
            continue
        candidates.append(
            (
                score,
                {
                    "title": title,
                    "place": place,
                    "start": _shown(first, zone),
                    "end": _shown(last, zone),
                    "all_day": not isinstance(first, datetime),
                    "zone": _zone_name(first),
                    "answer": answer,
                    "uid": str(component.get("UID", "")),
                },
            )
        )
    best = max((score for score, _ in candidates), default=0)  # every word if any event has them all
    events = [event for score, event in candidates if score == best]
    if len(events) > limit:
        events, truncated = events[:limit], True
    name = str(calendar.get("X-WR-CALNAME", ""))[:TEXT_CHARS]
    partial = bool(words) and bool(events) and best < len(set(words))
    return {"ok": True, "calendar": name, "events": events, "truncated": truncated, "partial": partial}


def _aware(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError("needs an offset")
    return moment


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise argparse.ArgumentTypeError("unknown zone") from None


def _name(value: str) -> str:
    if not _NAME.match(value):
        raise argparse.ArgumentTypeError("a calendar name is lowercase letters, digits, - or _")
    return value


def _count(value: str) -> int:
    number = int(value)
    if not 1 <= number <= DEFAULT_LIMIT:
        raise argparse.ArgumentTypeError(f"must be 1 to {DEFAULT_LIMIT}")
    return number


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # a usage error is a refusal, not an exit with text on stderr
        raise Refused(f"bad arguments: {message}")


def parse(argv: list[str]) -> argparse.Namespace:
    parser = _Parser(prog="helper.py", add_help=False)
    actions = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    query = actions.add_parser("events", add_help=False)
    query.add_argument("--from", dest="start", type=_aware, required=True)
    query.add_argument("--to", dest="end", type=_aware, required=True)
    query.add_argument("--zone", type=_zone, required=True)
    query.add_argument("--calendar", type=_name, default=DEFAULT_CALENDAR)
    query.add_argument("--words", default="")
    query.add_argument("--limit", type=_count, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)
    if args.end <= args.start:
        raise Refused("bad arguments: --to must be after --from")
    return args


def _failure(kind: str, detail: str) -> tuple[int, dict[str, Any]]:
    return 1, {"ok": False, "error": kind, "detail": detail}


def run(argv: list[str], *, fetch: Fetch = fetch, address_file: Path = ADDRESS_FILE) -> tuple[int, dict[str, Any]]:
    """One query; every outcome is one JSON-ready object (never the address, never event text in an error)."""
    try:
        args = parse(argv)
    except Refused as exc:
        return _failure("invalid", str(exc))
    try:
        address = load_address(address_file, args.calendar)
    except Refused as exc:
        return _failure("not_set", str(exc))
    except (OSError, UnicodeDecodeError) as exc:
        return _failure("not_set", f"cannot read calendar.env ({type(exc).__name__})")
    try:
        data = fetch(address)
        found = _WORD.findall(args.words.casefold())
        words = [word for word in found if len(word) > 1] or found  # the "s" of "Maria's" is not a word to find
        return 0, expand(data, args.start, args.end, args.zone, owner_of(address), words, args.limit)
    except _TooSlow:
        return _failure("invalid", "the calendar took too long to expand")
    except Gone as exc:
        return _failure("gone", str(exc))
    except Unreachable as exc:
        return _failure("unreachable", str(exc))
    except Refused as exc:
        return _failure("invalid", str(exc))
    except Exception as exc:  # anything else from the libraries: only its type is passed on, never its text
        return _failure("invalid", type(exc).__name__)


def main() -> None:
    code, out = run(sys.argv[1:], address_file=ADDRESS_FILE)
    print(json.dumps(out))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
