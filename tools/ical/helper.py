"""Calendar helper for local-assistant: one calendar per run, a Google calendar through its iCal address (secret or
public) or a CalDAV calendar (iCloud, Fastmail, Nextcloud) through its account.

The bridge runs it with fixed arguments (no shell) and reads one JSON object from stdout; exit 0 when ok, 1 on an
error ({"ok": false, "error": "not_set" | "gone" | "unreachable" | "invalid" | "no_password" | "password_refused" |
"no_calendar", "detail": …}):

    helper.py events --from <ISO time with offset> --to <ISO time with offset> --zone <IANA zone>
                     [--calendar <name>] [--words <words>] [--limit <n>] [--caldav]

A Google calendar's address is read from ~/.config/local-assistant/calendar.env (0600) and never printed:
CALENDAR_ICS_URL for the calendar named "calendar" (the default), CALENDAR_ICS_URL_<NAME> for the others ("school-days"
reads CALENDAR_ICS_URL_SCHOOL_DAYS). Only https://calendar.google.com/calendar/ical/… is fetched, with no proxies and no
redirects followed.

With --caldav the calendar is a CalDAV one: its server address, user name and calendar name (as the calendar app shows
it) come as one JSON line on stdin ({"server", "user", "calendar"}: never on the command line, which every user of the
Mac can see), and its app-specific password from calendar.env (CALDAV_PASSWORD, CALDAV_PASSWORD_<NAME>), never printed.
Only the read methods are ever sent (READ_METHODS: PROPFIND to find the account's calendars, REPORT for the events in
the time range; never PUT, POST, DELETE, MKCALENDAR or PROPPATCH: refused in code), over HTTPS only (the standard
library's http.client: no proxies, no cookies kept), to the configured host only on the same port (for iCloud also its
other icloud.com hosts: it answers from pNN-caldav.icloud.com), at most 3 redirects a request, all requests within
CALDAV_SECONDS (a timer per request) and the same size cap as a feed. XML with a document type or entities is refused.
An invalid answer comes with a fixed code (INVALID_CODES) the bridge may log.

Repeating events are expanded with recurring-ical-events: single events together, each repeating event on its own
within its own small time budget and instance cap, so one hostile or broken invitation is left out instead of taking
the calendar down. Rules that cannot be read, never move on (INTERVAL below 1) or repeat more often than daily (Google
Calendar cannot make those) are dropped first, and parsing plus expansion have a hard time limit. Only title, place,
times, the event's zone, the calendar owner's answer and the UID are passed on: no descriptions, attendee lists or
links.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import re
import signal
import ssl
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import xml.parsers.expat
from collections.abc import Callable
from dataclasses import dataclass
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
CALDAV_SECONDS = 12.0  # all of a CalDAV calendar's requests: with EXPAND_SECONDS, within the bridge's 30 s
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
# CalDAV: only these methods are ever sent (reading the account's calendars and the events); never PUT, POST, DELETE,
# MKCALENDAR, PROPPATCH or any other
READ_METHODS = frozenset({"PROPFIND", "REPORT"})
MAX_REDIRECTS = 3  # a request's redirects, at most
DAV_BYTES = 1024 * 1024  # a discovery answer (the account, its calendars), at most
ACCOUNT_CHARS = 4096  # the account's JSON line on stdin, at most
_REDIRECTS = frozenset({301, 302, 307, 308})  # 303 would turn the request into a GET: not followed
# providers whose CalDAV answers send the client on to other hosts of their own domain (iCloud: caldav.icloud.com,
# then pNN-caldav.icloud.com); every other server (Fastmail, Nextcloud, a NAS) is asked at its configured host only
MULTI_HOST_SITES = frozenset({"icloud.com"})
_DAV, _CAL = "{DAV:}", "{urn:ietf:params:xml:ns:caldav}"
# a CalDAV calendar's invalid answer comes with one of these fixed codes ("code"), which the bridge logs (its own copy,
# calendar_feed): a redirect not followed, an address of another server in an answer, an answer over its cap, XML with
# a document type or entities, an answer that is not CalDAV's, an HTTP status that is no answer, the account's line
# refused, expansion over its time, anything else
INVALID_CODES = frozenset(
    {
        "redirect_refused",
        "other_server",
        "too_large",
        "xml_refused",
        "not_dav",
        "http_status",
        "bad_account",
        "too_slow",
        "other",
    }
)
_XML = 'application/xml; charset="utf-8"'
PRINCIPAL_QUERY = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
)
HOME_QUERY = (
    b'<?xml version="1.0" encoding="utf-8"?>\n<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    b"<d:prop><c:calendar-home-set/><c:calendar-user-address-set/></d:prop></d:propfind>"
)
CALENDARS_QUERY = (
    b'<?xml version="1.0" encoding="utf-8"?>\n<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    b"<d:prop><d:resourcetype/><d:displayname/><c:supported-calendar-component-set/></d:prop></d:propfind>"
)

Fetch = Callable[[str], bytes]


@dataclass(frozen=True)
class Answer:
    """One HTTP answer: its status, its headers (names in lower case) and at most the size asked for (+1 byte)."""

    status: int
    headers: dict[str, str]
    body: bytes


# method, address, headers, body, time limit (seconds), size cap (bytes) -> the answer; Unreachable when none came
Transport = Callable[[str, str, dict[str, str], bytes, float, int], Answer]


class Refused(ValueError):
    """The helper's own refusal; its fixed text is passed on (never the address or event text), and for a CalDAV
    calendar its fixed code (INVALID_CODES)."""

    def __init__(self, text: str, code: str = "other") -> None:
        super().__init__(text)
        self.code = code


class Gone(Exception):
    """Google no longer serves this address (reset, or the calendar removed)."""


class Unreachable(Exception):
    """No usable answer from Google right now."""


class NoPassword(Exception):
    """No usable password is saved for this CalDAV calendar."""


class PasswordRefused(Exception):
    """The CalDAV server refused the user name or password (401; 403 before any answer came)."""


class NoCalendar(Exception):
    """No CalDAV account at that address, or no calendar of events by that name in it."""


class _NotFound(Exception):
    """404, 405 or 410: nothing to read at that address."""


class _NotDav(Exception):
    """The answer is not a CalDAV answer (no multistatus XML)."""


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


def password_key(name: str) -> str:
    """The calendar.env key of a CalDAV calendar's password: CALDAV_PASSWORD for the default one, else
    CALDAV_PASSWORD_<NAME>."""
    return "CALDAV_PASSWORD" if name == DEFAULT_CALENDAR else f"CALDAV_PASSWORD_{name.upper().replace('-', '_')}"


def _saved(path: Path, missing: str) -> dict[str, str]:
    """calendar.env's keys and values (one matching pair of quotes around a hand-edited value dropped, as the bridge's
    keyfile does: a password's own quotes are kept); Refused when the file is missing or not private."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        raise Refused(missing) from None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise Refused("calendar.env must be private (chmod 600)")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].strip()
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value
    return values


def load_address(path: Path, name: str = DEFAULT_CALENDAR) -> str:
    """The calendar's saved address; Refused when there is none, the file is not private or it is not Google's."""
    missing = "no saved address" + ("" if name == DEFAULT_CALENDAR else f" for {name}")
    address = _saved(path, missing).get(address_key(name), "")
    if not address:
        raise Refused(missing)
    if not address.startswith(FEED_PREFIX) or not address.endswith(".ics"):
        raise Refused("the saved address is not a Google iCal address")
    return address


def load_password(path: Path, name: str = DEFAULT_CALENDAR) -> str:
    """A CalDAV calendar's saved password; NoPassword when there is none or the file is not private."""
    missing = "no saved password" + ("" if name == DEFAULT_CALENDAR else f" for {name}")
    try:
        password = _saved(path, missing).get(password_key(name), "")
    except Refused as exc:
        raise NoPassword(str(exc)) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise NoPassword(f"cannot read calendar.env ({type(exc).__name__})") from None
    if not password:
        raise NoPassword(missing)
    return password


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


def site_of(host: str) -> str:
    """The hosts a server may send the client on to: a provider's own domain when it answers from several hosts
    (MULTI_HOST_SITES: "p42-caldav.icloud.com" is icloud.com), else the host itself. Never a guess at a registrable
    domain: on ts.net, duckdns.org or synology.me another customer's host would pass."""
    host = host.lower().rstrip(".")
    for site in MULTI_HOST_SITES:
        if host == site or host.endswith(f".{site}"):
            return site
    return host


def server_address(value: object) -> str:
    """The CalDAV server's address as configured: https, a host, no login, query or fragment; Refused otherwise."""
    if not isinstance(value, str) or not value or len(value) > 200 or not value.isprintable() or " " in value:
        raise Refused("the CalDAV server must be an https address", "bad_account")
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise Refused("the CalDAV server must be an https address", "bad_account") from None
    if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
        raise Refused("the CalDAV server must be an https address", "bad_account")
    if parts.query or parts.fragment or "?" in value or "#" in value or (port is not None and not 0 < port < 65536):
        raise Refused("the CalDAV server must be an https address without a query", "bad_account")
    return value


@dataclass(frozen=True)
class Account:
    """A CalDAV calendar: its server, the account's user name and the calendar's name as the calendar app shows it."""

    server: str
    user: str
    calendar: str


def account_of(line: str) -> Account:
    """The account from its JSON line (stdin); Refused when it is not one."""
    try:
        data = json.loads(line[:ACCOUNT_CHARS])
    except ValueError:
        raise Refused("the CalDAV account is not one JSON line", "bad_account") from None
    if not isinstance(data, dict):
        raise Refused("the CalDAV account is not one JSON line", "bad_account")
    server = server_address(data.get("server"))
    user, calendar = data.get("user"), data.get("calendar")
    if not isinstance(user, str) or not user.strip() or len(user) > 200 or not user.isprintable():
        raise Refused("the CalDAV user name must be 1-200 characters on one line", "bad_account")
    if not isinstance(calendar, str) or not calendar.strip() or len(calendar) > 100 or not calendar.isprintable():
        raise Refused("the CalDAV calendar name must be 1-100 characters on one line", "bad_account")
    return Account(server, user, calendar.strip())


def _https_connection(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())


def https_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
    max_bytes: int,
    *,
    connect: Callable[[str, int, float], http.client.HTTPConnection] = _https_connection,
) -> Answer:
    """One request over HTTPS with the standard library: no proxies (http.client never reads proxy settings), no
    cookies (none are sent, none kept), certificates checked, at most max_bytes + 1 of the answer read, and the whole
    request (connecting, the answer's headers and body) within `timeout` seconds: a timer, as each read's own timeout
    lets a server that sends a few bytes at a time go on."""
    if method not in READ_METHODS:
        raise Refused(f"{method} is not a read method: only PROPFIND and REPORT are sent")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise Refused("only https addresses are asked")
    connection = connect(parts.hostname, parts.port or 443, timeout)
    previous = signal.signal(signal.SIGALRM, _stop)
    signal.setitimer(signal.ITIMER_REAL, max(timeout, 0.001))
    try:
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(max_bytes + 1)
        return Answer(response.status, {name.lower(): value for name, value in response.getheaders()}, data)
    except _Alarm:
        raise Unreachable("no answer in time") from None
    except (OSError, http.client.HTTPException) as exc:
        raise Unreachable(type(exc).__name__) from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        connection.close()


def _xml(body: bytes) -> ET.Element:
    """An answer's XML, read by expat with a document type and entity declarations refused (no entity expansion, no
    outside files)."""

    def refuse(*args: Any) -> None:
        raise Refused("the answer's XML declares a document type or entities", "xml_refused")

    builder = ET.TreeBuilder()
    parser = xml.parsers.expat.ParserCreate(namespace_separator="}")
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse  # type: ignore[assignment]

    def tag(name: str) -> str:
        return "{" + name if "}" in name else name

    parser.StartElementHandler = lambda name, attributes: builder.start(tag(name), attributes)
    parser.EndElementHandler = lambda name: builder.end(tag(name))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(body, True)
        return builder.close()
    except xml.parsers.expat.ExpatError:
        raise _NotDav("the answer is not XML") from None
    except AssertionError:  # TreeBuilder: no root element
        raise _NotDav("the answer is not XML") from None


def _multistatus(body: bytes) -> list[tuple[str, ET.Element]]:
    """Each response's address and its properties that were found (status 200)."""
    root = _xml(body)
    if root.tag != f"{_DAV}multistatus":
        raise _NotDav("the answer is not a CalDAV answer")
    found: list[tuple[str, ET.Element]] = []
    for response in root.findall(f"{_DAV}response"):
        props = ET.Element("props")
        for propstat in response.findall(f"{_DAV}propstat"):
            if (propstat.findtext(f"{_DAV}status") or "").split()[1:2] != ["200"]:
                continue
            for prop in propstat.findall(f"{_DAV}prop"):
                props.extend(list(prop))
        found.append(((response.findtext(f"{_DAV}href") or "").strip(), props))
    return found


def _utc(moment: datetime) -> str:
    return moment.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def events_query(start: datetime, end: datetime) -> bytes:
    """A calendar-query REPORT: the events overlapping [start, end) with their iCalendar data."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:prop><c:calendar-data/></d:prop><c:filter>"
        f'<c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{_utc(start)}" '
        f'end="{_utc(end)}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>'
    ).encode()


class CalDav:
    """A read-only CalDAV client for one account: PROPFIND and REPORT only, the configured host only (iCloud: its
    icloud.com hosts), over HTTPS, all requests within one deadline (time.monotonic seconds)."""

    def __init__(
        self,
        server: str,
        user: str,
        password: str,
        *,
        transport: Transport,
        deadline: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        parts = urllib.parse.urlsplit(server)
        self._server = urllib.parse.urlunsplit(("https", parts.netloc, parts.path or "/", "", ""))
        self._host = (parts.hostname or "").lower()
        self._port = parts.port or 443
        self._login = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self._transport = transport
        self._deadline = deadline
        self._clock = clock
        self._accepted = False  # an answer came with the password: a later 403 is that address's, not the password's

    def allowed(self, url: str) -> bool:
        """HTTPS, no login in the address, the configured port, and the configured host only (for iCloud: a host of
        icloud.com, MULTI_HOST_SITES)."""
        try:
            parts = urllib.parse.urlsplit(url)
            port = parts.port or 443
        except ValueError:
            return False
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username is not None or parts.password is not None or port != self._port:
            return False
        return bool(host) and site_of(host) == site_of(self._host)

    def request(self, method: str, url: str, body: bytes, *, depth: str, max_bytes: int) -> tuple[str, bytes]:
        """One read request, its redirects followed within the rules; the address that answered and the answer."""
        if method not in READ_METHODS:
            raise Refused(f"{method} is not a read method: only PROPFIND and REPORT are sent")
        headers = {
            "Authorization": self._login,
            "Depth": depth,
            "Content-Type": _XML,
            "User-Agent": "local-assistant calendar helper",
        }
        for hop in range(MAX_REDIRECTS + 1):
            if not self.allowed(url):
                code = "redirect_refused" if hop else "other_server"
                raise Refused("an address of another server, or not https, is not asked", code)
            left = self._deadline - self._clock()
            if left <= 0:
                raise Unreachable("no answer in time")
            answer = self._transport(method, url, headers, body, left, max_bytes)
            if answer.status in _REDIRECTS:
                location = answer.headers.get("location", "")
                url = urllib.parse.urljoin(url, location) if location else ""  # none: refused at the next step
                continue
            if answer.status == 401 or (answer.status == 403 and not self._accepted):
                raise PasswordRefused(f"HTTP {answer.status}")
            if answer.status in (404, 405, 410):
                raise _NotFound(f"HTTP {answer.status}")
            if answer.status == 429 or answer.status >= 500:
                raise Unreachable(f"HTTP {answer.status}")
            if answer.status not in (200, 207):
                raise Refused(f"HTTP {answer.status}", "http_status")
            self._accepted = True
            if len(answer.body) > max_bytes:
                raise Refused(f"the answer is too large (over {max_bytes // 1024} KB)", "too_large")
            return url, answer.body
        raise Refused(f"more than {MAX_REDIRECTS} redirects", "redirect_refused")

    def _inside(self, base: str, href: str) -> str:
        url = urllib.parse.urljoin(base, href)
        if not href or not self.allowed(url):
            raise Refused("an address of another server, or not https, is not asked", "other_server")
        return url

    def principal(self) -> str:
        """The account's principal: asked at the configured address, then at /.well-known/caldav on its host."""
        parts = urllib.parse.urlsplit(self._server)
        known = urllib.parse.urlunsplit(("https", parts.netloc, "/.well-known/caldav", "", ""))
        for start in dict.fromkeys((self._server, known)):
            try:
                url, body = self.request("PROPFIND", start, PRINCIPAL_QUERY, depth="0", max_bytes=DAV_BYTES)
                found = _multistatus(body)
            except (_NotFound, _NotDav):
                continue
            for _, props in found:
                href = (props.findtext(f"{_DAV}current-user-principal/{_DAV}href") or "").strip()
                if href:
                    return self._inside(url, href)
        raise NoCalendar("no CalDAV account at that address")

    def home(self, principal: str) -> tuple[str, frozenset[str]]:
        """The account's calendar home, and its own addresses (for its answers to invitations)."""
        url, body = self.request("PROPFIND", principal, HOME_QUERY, depth="0", max_bytes=DAV_BYTES)
        for _, props in _multistatus(body):
            href = (props.findtext(f"{_CAL}calendar-home-set/{_DAV}href") or "").strip()
            if href:
                addresses = props.findall(f"{_CAL}calendar-user-address-set/{_DAV}href")
                mail = {(a.text or "").strip().lower().removeprefix("mailto:") for a in addresses}
                return self._inside(url, href), frozenset(a for a in mail if "@" in a and "/" not in a)
        raise NoCalendar("the account has no calendars")

    def calendar(self, home: str, name: str) -> str:
        """The address of the account's calendar of events with this name (case and spaces aside)."""
        url, body = self.request("PROPFIND", home, CALENDARS_QUERY, depth="1", max_bytes=DAV_BYTES)
        wanted = " ".join(name.split()).casefold()
        for href, props in _multistatus(body):
            if props.find(f"{_DAV}resourcetype/{_CAL}calendar") is None:
                continue
            kinds = props.findall(f"{_CAL}supported-calendar-component-set/{_CAL}comp")
            if kinds and not any(kind.get("name", "").upper() == "VEVENT" for kind in kinds):
                continue  # a list of reminders or tasks
            if " ".join((props.findtext(f"{_DAV}displayname") or "").split()).casefold() == wanted:
                return self._inside(url, href)
        raise NoCalendar("no calendar by that name")

    def events(self, calendar: str, start: datetime, end: datetime) -> list[bytes]:
        """The iCalendar data of every event object the server finds in [start, end) (a repeating event whole)."""
        _, body = self.request("REPORT", calendar, events_query(start, end), depth="1", max_bytes=MAX_BYTES)
        found = (props.findtext(f"{_CAL}calendar-data") or "" for _, props in _multistatus(body))
        return [data.encode() for data in found if data.strip()]


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


def _answer(component: Any, owners: frozenset[str]) -> str:
    """The calendar owner's answer to an invitation (any of the owner's addresses)."""
    attendees = component.get("ATTENDEE")
    for attendee in attendees if isinstance(attendees, list) else [attendees] if attendees else []:
        if str(attendee).lower().removeprefix("mailto:") in owners:
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
        deadline = time.monotonic() + EXPAND_SECONDS
        calendar = _parse(data, deadline)
        owners = frozenset({owner})
        return _expand(calendar, start.astimezone(zone), end.astimezone(zone), zone, owners, words, limit, deadline)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def expand_parts(
    parts: list[bytes],
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
    owners: frozenset[str],
    words: list[str],
    limit: int,
    *,
    name: str = "",
) -> dict[str, Any]:
    """expand() for a CalDAV calendar, whose events come one iCalendar object each: joined into one calendar first
    (each time zone once). An object that cannot be read is left out (truncated), the others are shown."""
    previous = signal.signal(signal.SIGALRM, _stop)
    try:
        deadline = time.monotonic() + EXPAND_SECONDS
        joined = icalendar.Calendar()
        joined.add("PRODID", "-//local-assistant//calendar helper//EN")
        joined.add("VERSION", "2.0")
        if name:
            joined.add("X-WR-CALNAME", name)
        zones: set[str] = set()
        unreadable = False
        for data in parts:
            try:
                calendar = _parse(data, deadline)
            except _TooSlow:
                raise
            except Exception:  # not iCalendar, or the library's own error on this one object: left out
                unreadable = True
                continue
            for component in calendar.subcomponents:
                if component.name == "VTIMEZONE":
                    tzid = str(component.get("TZID", ""))
                    if tzid in zones:
                        continue
                    zones.add(tzid)
                joined.add_component(component)
        found = _expand(joined, start.astimezone(zone), end.astimezone(zone), zone, owners, words, limit, deadline)
        found["truncated"] = found["truncated"] or unreadable
        return found
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _expand(
    calendar: Any,
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
    owners: frozenset[str],
    words: list[str],
    limit: int,
    deadline: float | None = None,
) -> dict[str, Any]:
    deadline = deadline if deadline is not None else time.monotonic() + EXPAND_SECONDS
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
        answer = _answer(component, owners)
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
    query.add_argument("--caldav", action="store_true")  # a CalDAV calendar: its account as one JSON line on stdin
    args = parser.parse_args(argv)
    if args.end <= args.start:
        raise Refused("bad arguments: --to must be after --from")
    return args


def _failure(kind: str, detail: str, code: str = "") -> tuple[int, dict[str, Any]]:
    """An error's JSON; `code`: an invalid CalDAV answer's fixed code (INVALID_CODES)."""
    return 1, {"ok": False, "error": kind, "detail": detail} | ({"code": code} if code else {})


def _stdin_line() -> str:
    return sys.stdin.readline(ACCOUNT_CHARS)


def run(
    argv: list[str],
    *,
    fetch: Fetch = fetch,
    address_file: Path = ADDRESS_FILE,
    transport: Transport = https_transport,
    source: Callable[[], str] = _stdin_line,
) -> tuple[int, dict[str, Any]]:
    """One query; every outcome is one JSON-ready object (never the address, the password or the account, never event
    text in an error)."""
    try:
        args = parse(argv)
    except Refused as exc:
        return _failure("invalid", str(exc))
    if args.caldav:
        return _caldav(args, address_file, transport, source)
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


def _caldav(
    args: argparse.Namespace, address_file: Path, transport: Transport, source: Callable[[], str]
) -> tuple[int, dict[str, Any]]:
    """A CalDAV calendar: its account, its password, then discovery and the events, read only."""
    try:
        account = account_of(source())
    except Refused as exc:
        return _failure("invalid", str(exc), "bad_account")
    try:
        password = load_password(address_file, args.calendar)
    except NoPassword as exc:
        return _failure("no_password", str(exc))
    try:
        client = CalDav(
            account.server, account.user, password, transport=transport, deadline=time.monotonic() + CALDAV_SECONDS
        )
        home, addresses = client.home(client.principal())
        parts = client.events(client.calendar(home, account.calendar), args.start, args.end)
        user = account.user.strip().lower()
        owners = addresses | ({user} if "@" in user else set())
        found = _WORD.findall(args.words.casefold())
        words = [word for word in found if len(word) > 1] or found
        return 0, expand_parts(
            parts, args.start, args.end, args.zone, owners, words, args.limit, name=account.calendar[:TEXT_CHARS]
        )
    except PasswordRefused as exc:
        return _failure("password_refused", str(exc))
    except NoCalendar as exc:
        return _failure("no_calendar", str(exc))
    except Unreachable as exc:
        return _failure("unreachable", str(exc))
    except _Alarm:  # the request's timer, a moment after its answer came
        return _failure("unreachable", "no answer in time")
    except _NotFound as exc:
        return _failure("invalid", str(exc), "http_status")
    except _NotDav as exc:
        return _failure("invalid", str(exc), "not_dav")
    except _TooSlow:
        return _failure("invalid", "the calendar took too long to expand", "too_slow")
    except Refused as exc:
        return _failure("invalid", str(exc), exc.code if exc.code in INVALID_CODES else "other")
    except Exception as exc:  # anything else: only its type is passed on, never its text
        return _failure("invalid", type(exc).__name__, "other")


def main() -> None:
    code, out = run(sys.argv[1:], address_file=ADDRESS_FILE, transport=https_transport, source=_stdin_line)
    print(json.dumps(out))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
