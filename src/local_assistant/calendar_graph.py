"""Outlook calendars of the signed-in mailboxes, read through Microsoft Graph with delegated Calendars.Read only.

Each mailbox listed in [[calendar.sources]] gives one calendar: its default calendar (`/me/calendarView`; other
calendars and calendars shared by others are not read). The calendars have their own Graph: the Calendars.Read token
(never mail's), at most one retry with a short wait, a short socket timeout and a time limit per calendar, so a slow
Microsoft never holds the morning digest. Times are asked in UTC (no `Prefer: outlook.timezone`) and shown in the
configured zone; an all-day item keeps its day (`_day`). Only the subject, start, end, the place's display name, all-day,
cancelled, the owner's answer and the iCal UID are asked for: no bodies, no attendees, no organizer's address.
Declined and cancelled events are left out, as the calendar helper does. Event text is data, cleaned where it is shown
(calendar_tools). Nothing is cached.

A refused sign-in pauses the calendar until the mailbox's refresh token changes (a new sign-in, or mail renewing it),
so a dead token is not sent again and again; missing consent is asked again after a while (an administrator may grant
it with no new sign-in) or at once after a new sign-in.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Any

from local_assistant.calendar_feed import (
    SEARCH_LIMIT,
    Agenda,
    Event,
    FeedError,
    FeedNoConsent,
    FeedSignIn,
    best_matches,
)
from local_assistant.config import CalendarSource, Config, MailAccount
from local_assistant.graph import Graph, GraphError
from local_assistant.mail_auth import (
    CALENDAR_SCOPES,
    AccessTokens,
    ConsentMissing,
    MailAuthError,
    SignInExpired,
    TokenStore,
)
from local_assistant.mail_http import TimedHttp, http
from local_assistant.part_status import PartStatus, Reason, calendar_part

log = logging.getLogger(__name__)

SOCKET_SECONDS = 10.0  # one request
RETRIES = 1  # after 429/503/504
MAX_WAIT = 3.0  # the longest wait Graph may ask for before that retry
READ_SECONDS = 20.0  # one calendar's whole read (token, pages, retry): the digest gives the calendars 35 s
PAGE_SIZE = 250  # $top: calendarView takes 1 to 1000
MAX_PAGES = 4  # 1,000 events (a year of a busy work calendar): more is said to be cut short
CONSENT_RETRY_SECONDS = 900.0  # missing consent: Microsoft is asked again after this long
TEXT_CHARS = 500  # title and place passed on, at most (as the calendar helper)
SELECT = "subject,start,end,location,isAllDay,isCancelled,responseStatus,iCalUId"
_ANSWERS = {"notResponded": "needs-action", "tentativelyAccepted": "tentative", "accepted": "accepted"}


def _utc(moment: datetime) -> str:
    """2026-10-08T22:00:00Z: no offset to encode (an unencoded "+" would reach Graph as a space)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OutlookCalendars:
    """The calendars' own Graph and token cache; `reader` gives one calendar's reader."""

    def __init__(
        self,
        config: Config,
        *,
        status: PartStatus | None = None,
        http: TimedHttp = http,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._status = status or PartStatus(None)
        self._clock = clock
        self._tokens = TokenStore(config.mail_token_path)
        timed = functools.partial(http, socket_timeout=SOCKET_SECONDS)
        self._graph = Graph(
            AccessTokens(config, self._tokens, http=timed, clock=clock),
            http=timed,
            sleep=sleep,
            retries=RETRIES,
            scope=CALENDAR_SCOPES,
            max_wait=MAX_WAIT,
        )

    def reader(self, source: CalendarSource) -> OutlookReader:
        account = self._config.calendar_mailbox(source)
        if account is None:
            raise ValueError(f"calendar {source.name}: no mailbox {source.mailbox}")
        return OutlookReader(source, account, self._graph, self._tokens, self._config, self._status, self._clock)


class OutlookReader:
    """One mailbox's default calendar."""

    def __init__(
        self,
        source: CalendarSource,
        account: MailAccount,
        graph: Graph,
        tokens: TokenStore,
        config: Config,
        status: PartStatus,
        clock: Callable[[], float],
    ) -> None:
        self._source = source
        self._address = account.address
        self._graph = graph
        self._tokens = tokens
        self._tz = config.timezone
        self._status = status
        self._clock = clock
        self._part = calendar_part(source.name)
        self._paused: tuple[FeedError, str | None, float] | None = None  # why, the refresh token then, when

    async def events(self, start: datetime, end: datetime) -> Agenda:
        """Every event overlapping [start, end), in start order."""
        events, truncated = await self._read(start, end)
        return Agenda(tuple(events), truncated)

    async def search(self, words: str, start: datetime, end: datetime, limit: int = SEARCH_LIMIT) -> Agenda:
        """The first events whose title or place has every word (or the most of them): filtered here, within the cap."""
        events, truncated = await self._read(start, end)
        found, partial = best_matches(events, words)
        return Agenda(tuple(found[:limit]), truncated or len(found) > limit, partial)

    async def _read(self, start: datetime, end: datetime) -> tuple[list[Event], bool]:
        self._still_paused()
        try:
            found = await asyncio.wait_for(self._pages(start, end), READ_SECONDS)
        except SignInExpired:
            raise self._pause(FeedSignIn("the mailbox needs a new sign-in"), "sign_in") from None
        except ConsentMissing:
            raise self._pause(FeedNoConsent("Calendars.Read is not granted yet"), "no_consent") from None
        except GraphError as exc:
            reason: Reason = "busy" if exc.status == 429 else "bad_answer" if exc.status in (0, 200) else "refused"
            self._failed(reason, exc.status if reason != "bad_answer" else 0)
            log.warning("calendar %s: %s", self._source.name, exc)
            raise FeedError("unreachable") from None
        except MailAuthError as exc:
            self._failed("refused")
            log.warning("calendar %s: %s", self._source.name, exc)
            raise FeedError("unreachable") from None
        except (OSError, TimeoutError) as exc:
            self._failed("no_answer")
            log.warning("calendar %s: no answer (%s)", self._source.name, type(exc).__name__)
            raise FeedError("unreachable") from None
        except (ValueError, TypeError, AttributeError) as exc:
            self._failed("bad_answer")
            log.warning("calendar %s: unreadable answer (%s)", self._source.name, type(exc).__name__)
            raise FeedError("invalid answer from Microsoft") from None
        self._status.ok(self._part)
        return found

    def _still_paused(self) -> None:
        """Raise the pause's reason without asking Microsoft, while nothing that could fix it happened."""
        if self._paused is None:
            return
        error, token, since = self._paused
        renewed = self._tokens.refresh_token(self._address) != token
        waited = isinstance(error, FeedNoConsent) and self._clock() - since >= CONSENT_RETRY_SECONDS
        if renewed or waited:
            self._paused = None
            return
        raise error

    def _pause(self, error: FeedError, reason: Reason) -> FeedError:
        self._paused = (error, self._tokens.refresh_token(self._address), self._clock())
        self._failed(reason)
        log.warning("calendar %s: %s; paused", self._source.name, error)
        return error

    def _failed(self, reason: Reason, code: int = 0) -> None:
        self._status.failed(self._part, reason, code)

    async def _pages(self, start: datetime, end: datetime) -> tuple[list[Event], bool]:
        link = (
            f"/me/calendarView?startDateTime={_utc(start)}&endDateTime={_utc(end)}"
            f"&$select={SELECT}&$orderby=start/dateTime&$top={PAGE_SIZE}"
        )
        events: list[Event] = []
        for _ in range(MAX_PAGES):
            page = await self._graph.get(self._address, link)
            items = page.get("value")
            if not isinstance(items, list):
                raise ValueError("no value list")
            events += [event for item in items if (event := self._event(item)) is not None]
            following = page.get("@odata.nextLink")
            if not isinstance(following, str):
                return events, False
            link = following  # Graph checks it stays under graph.microsoft.com/v1.0
        return events, True

    def _event(self, item: Any) -> Event | None:
        if not isinstance(item, dict) or item.get("isCancelled") is True:
            return None
        status = item.get("responseStatus")
        answer = str(status.get("response") or "") if isinstance(status, dict) else ""
        if answer == "declined":  # the owner is not going: never shown, so never counted
            return None
        all_day = item.get("isAllDay") is True
        try:
            start, end = self._moment(item["start"], all_day), self._moment(item["end"], all_day)
        except (KeyError, TypeError, ValueError):
            return None
        location = item.get("location")
        place = location.get("displayName") if isinstance(location, dict) else ""  # never the street address
        return Event(
            title=str(item.get("subject") or "")[:TEXT_CHARS],
            place=str(place or "")[:TEXT_CHARS],
            start=start,
            end=max(end, start),
            all_day=all_day,
            zone="",
            response=_ANSWERS.get(answer, ""),
            uid=str(item.get("iCalUId") or ""),
        )

    def _moment(self, value: Any, all_day: bool) -> datetime:
        """Graph's {"dateTime": "2026-10-09T08:30:00.0000000", "timeZone": "UTC"} in the configured zone."""
        moment = datetime.fromisoformat(str(value["dateTime"]))
        utc = value.get("timeZone") in (None, "UTC")
        if all_day:
            return datetime.combine(self._day(moment, utc), clock_time(), self._tz)
        if not utc:
            raise ValueError("not UTC")  # asked in UTC: anything else would be read wrong
        return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(self._tz)

    def _day(self, moment: datetime, utc: bool) -> date:
        """An all-day item's day. Midnight is its date as it is (Graph may send midnight UTC). Any other UTC time is
        the midnight of the zone the item was made in, sent in UTC (Amsterdam's 9 Oct comes as 8 Oct 22:00): the
        local midnight nearest to it, right while the two zones are less than 12 hours apart."""
        if not utc or moment.time() == clock_time():
            return moment.date()
        local = (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(self._tz)
        return local.date() + timedelta(days=1) if local.hour >= 12 else local.date()
