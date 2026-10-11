"""Outlook calendars through Graph (Calendars.Read only), against scripted HTTP: never the real Microsoft."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from local_assistant import calendar_graph
from local_assistant.calendar_feed import FeedError, FeedNoConsent, FeedSignIn
from local_assistant.calendar_graph import OutlookCalendars
from local_assistant.config import CalendarSource, Config, MailAccount
from local_assistant.graph import GRAPH
from local_assistant.mail_auth import CALENDAR_SCOPES, TokenStore
from local_assistant.mail_http import HttpResult
from local_assistant.part_status import PartStatus, Record
from tests.conftest import ME, TZ
from tests.fakes.fake_http import FakeHttp

TENANT = "11111111-2222-4333-8444-555555555555"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
VIEW = f"{GRAPH}/me/calendarView"
ADDRESS = "alex@work.example"
NOW = 1_791_452_400.0
SOURCE = CalendarSource("work", "Work", mailbox="office")


def day(month: int, number: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, number, hour, minute, tzinfo=TZ)


def item(
    subject: str,
    start: str,
    end: str,
    *,
    all_day: bool = False,
    place: str = "",
    response: str = "accepted",
    cancelled: bool = False,
    uid: str = "",
) -> dict[str, Any]:
    """An event as Graph sends it without Prefer: outlook.timezone (UTC, seven decimals, no offset)."""
    return {
        "subject": subject,
        "start": {"dateTime": f"{start}.0000000", "timeZone": "UTC"},
        "end": {"dateTime": f"{end}.0000000", "timeZone": "UTC"},
        "isAllDay": all_day,
        "isCancelled": cancelled,
        "location": {"displayName": place, "address": {"street": "Example Street 1"}, "uniqueId": "x"},
        "responseStatus": {"response": response, "time": "2026-10-01T08:00:00Z"},
        "iCalUId": uid or f"{subject}@example.com",
        "organizer": {"emailAddress": {"name": "Sam Example", "address": "sam@example.com"}},
    }


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class Office:
    """The Outlook calendar of mailbox "office", read through scripted Graph answers."""

    def __init__(self, tmp_path: Path, tz: ZoneInfo = TZ) -> None:
        self.config = Config(
            bot_number="+306900000000",
            allowed_acis=frozenset({ME}),
            timezone=tz,
            data_dir=tmp_path / "data",
            mail_tenant_id=TENANT,
            mail_client_id="66666666-7777-4888-9999-000000000000",
            mail_accounts=(MailAccount("office", ADDRESS),),
            calendar_enabled=True,
            calendar_sources=(SOURCE,),
        )
        self.tokens = TokenStore(self.config.mail_token_path)
        self.tokens.save(ADDRESS, "rt-1")
        self.http = FakeHttp()
        self.http.add(TOKEN_URL, 200, {"access_token": "at-calendar", "expires_in": 3600})
        self.clock = Clock()
        self.waits: list[float] = []
        self.status = PartStatus(tmp_path / "status.json", clock=self.clock)

        async def sleep(seconds: float) -> None:
            self.waits.append(seconds)

        self.calendars = OutlookCalendars(
            self.config, status=self.status, http=self.http, clock=self.clock, sleep=sleep
        )
        self.reader = self.calendars.reader(SOURCE)

    def view(self, *items: dict[str, Any], next_link: str = "") -> None:
        page: dict[str, Any] = {"value": list(items)}
        if next_link:
            page["@odata.nextLink"] = next_link
        self.http.add(VIEW, 200, page)

    def asked(self) -> list[dict[str, str]]:
        return [
            {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
            for url in self.http.urls()
            if url.startswith(VIEW)
        ]

    def token_requests(self) -> int:
        return len([url for url in self.http.urls() if url == TOKEN_URL])


async def test_events_come_from_calendar_view_in_utc_and_are_shown_in_local_time(tmp_path: Path) -> None:
    office = Office(tmp_path)
    office.view(
        item("Offsite", "2026-10-09T00:00:00", "2026-10-10T00:00:00", all_day=True),
        item("1:1 with Sam", "2026-10-09T08:30:00", "2026-10-09T09:00:00", place="Room 4 https://teams.example/x"),
        item("Review", "2026-10-09T12:00:00", "2026-10-09T13:00:00", response="notResponded"),
        item("Old plan", "2026-10-09T13:00:00", "2026-10-09T14:00:00", response="declined"),
        item("Moved away", "2026-10-09T14:00:00", "2026-10-09T15:00:00", cancelled=True),
    )
    agenda = await office.reader.events(day(10, 9), day(10, 11))
    assert [(e.title, e.start, e.end, e.all_day, e.place, e.response) for e in agenda.events] == [
        ("Offsite", day(10, 9), day(10, 10), True, "", "accepted"),
        ("1:1 with Sam", day(10, 9, 10, 30), day(10, 9, 11), False, "Room 4 https://teams.example/x", "accepted"),
        ("Review", day(10, 9, 14), day(10, 9, 15), False, "", "needs-action"),
    ]
    assert agenda.events[1].uid == "1:1 with Sam@example.com" and not agenda.truncated
    query = office.asked()[0]
    assert (query["startDateTime"], query["endDateTime"]) == ("2026-10-08T22:00:00Z", "2026-10-10T22:00:00Z")
    assert query["$select"] == "subject,start,end,location,isAllDay,isCancelled,responseStatus,iCalUId"
    assert (query["$orderby"], query["$top"]) == ("start/dateTime", "250")
    method, _, sent = office.http.requests[-1]
    assert (method, sent["authorization"], sent["prefer"]) == ("GET", "Bearer at-calendar", "")  # UTC: no Prefer
    assert office.http.requests[0][2]["scope"] == CALENDAR_SCOPES
    assert set(office.http.timeouts) == {calendar_graph.SOCKET_SECONDS} and calendar_graph.SOCKET_SECONDS <= 10
    assert office.status.snapshot() == {"calendar:work": Record(ok=NOW)}
    assert "sam@example.com" not in repr(agenda) and "Example Street" not in repr(agenda)


@pytest.mark.parametrize(
    ("start", "end", "first", "last"),
    [
        ("2026-10-09T00:00:00", "2026-10-10T00:00:00", 9, 10),  # midnight UTC: its date as it is
        ("2026-10-08T22:00:00", "2026-10-09T22:00:00", 9, 10),  # made in Amsterdam: its midnight, sent in UTC
        ("2026-10-09T04:00:00", "2026-10-10T04:00:00", 9, 10),  # made in New York
        ("2026-10-23T22:00:00", "2026-10-25T23:00:00", 24, 26),  # two days, over the change to winter time
    ],
)
async def test_an_all_day_event_keeps_its_day_whichever_way_graph_sends_it(
    tmp_path: Path, start: str, end: str, first: int, last: int
) -> None:
    office = Office(tmp_path)
    office.view(item("Offsite", start, end, all_day=True))
    agenda = await office.reader.events(day(10, 9), day(10, 27))
    assert [(e.start, e.end, e.all_day) for e in agenda.events] == [(day(10, first), day(10, last), True)]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-09T00:00:00", "2026-10-10T00:00:00"),  # midnight UTC is no Auckland afternoon
        ("2026-10-08T11:00:00", "2026-10-09T11:00:00"),  # made in Auckland (UTC+13)
    ],
)
async def test_an_all_day_event_far_east_of_utc_keeps_its_day(tmp_path: Path, start: str, end: str) -> None:
    auckland = ZoneInfo("Pacific/Auckland")
    office = Office(tmp_path, auckland)
    office.view(item("Offsite", start, end, all_day=True))
    agenda = await office.reader.events(datetime(2026, 10, 9, tzinfo=auckland), datetime(2026, 10, 11, tzinfo=auckland))
    assert [(e.start, e.end) for e in agenda.events] == [
        (datetime(2026, 10, 9, tzinfo=auckland), datetime(2026, 10, 10, tzinfo=auckland))
    ]


async def test_pages_are_followed_up_to_a_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_graph, "MAX_PAGES", 2)
    office = Office(tmp_path)
    office.view(item("One", "2026-10-09T08:00:00", "2026-10-09T09:00:00"), next_link=f"{VIEW}?$skip=100")
    office.view(item("Two", "2026-10-10T08:00:00", "2026-10-10T09:00:00"))
    agenda = await office.reader.events(day(10, 9), day(10, 11))
    assert [e.title for e in agenda.events] == ["One", "Two"] and not agenda.truncated
    office.http.answers[VIEW] = []
    office.view(item("One", "2026-10-09T08:00:00", "2026-10-09T09:00:00"), next_link=f"{VIEW}?$skip=100")
    agenda = await office.reader.events(day(10, 9), day(10, 11))
    assert len(agenda.events) == 2 and agenda.truncated  # more pages than the cap: said so


async def test_a_link_outside_graph_is_never_followed(tmp_path: Path) -> None:
    office = Office(tmp_path)
    office.view(item("One", "2026-10-09T08:00:00", "2026-10-09T09:00:00"), next_link="https://evil.example/next")
    with pytest.raises(FeedError):
        await office.reader.events(day(10, 9), day(10, 11))
    assert not any("evil" in url for url in office.http.urls())


async def test_search_keeps_the_events_with_most_of_the_words(tmp_path: Path) -> None:
    office = Office(tmp_path)
    office.view(
        item("Dentist check", "2026-10-13T07:30:00", "2026-10-13T08:00:00", place="Prinsengracht"),
        item("Team lunch", "2026-10-14T10:00:00", "2026-10-14T11:00:00"),
        item("Dentist", "2026-11-02T07:30:00", "2026-11-02T08:00:00"),
    )
    found = await office.reader.search("dentist Prinsengracht", day(10, 9), day(10, 9).replace(year=2027), 10)
    assert [e.title for e in found.events] == ["Dentist check"] and not found.partial
    closest = await office.reader.search("dentist harbour", day(10, 9), day(10, 9).replace(year=2027), 10)
    assert [e.title for e in closest.events] == ["Dentist check", "Dentist"] and closest.partial
    first = await office.reader.search("dentist", day(10, 9), day(10, 9).replace(year=2027), 1)
    assert [e.title for e in first.events] == ["Dentist check"] and first.truncated


CONSENT = {"error": "invalid_grant", "suberror": "consent_required", "error_codes": [65001]}


async def test_missing_consent_is_its_own_reason_and_is_asked_again_later(tmp_path: Path) -> None:
    office = Office(tmp_path)
    office.http.answers[TOKEN_URL] = []
    office.http.add(TOKEN_URL, 400, CONSENT)
    with pytest.raises(FeedNoConsent):
        await office.reader.events(day(10, 9), day(10, 10))
    assert office.status.snapshot() == {"calendar:work": Record(failed=NOW, reason="no_consent")}
    with pytest.raises(FeedNoConsent):
        await office.reader.events(day(10, 9), day(10, 10))
    assert office.token_requests() == 1  # not asked again at once
    office.clock.now += calendar_graph.CONSENT_RETRY_SECONDS
    office.http.answers[TOKEN_URL] = []
    office.http.add(TOKEN_URL, 200, {"access_token": "at-calendar", "expires_in": 3600})
    office.view()
    await office.reader.events(day(10, 9), day(10, 10))  # an administrator granted it: no new sign-in needed
    assert office.token_requests() == 2
    assert office.status.snapshot()["calendar:work"] == Record(ok=office.clock.now)


async def test_an_expired_sign_in_waits_for_a_new_one(tmp_path: Path) -> None:
    office = Office(tmp_path)
    office.http.answers[TOKEN_URL] = []
    office.http.add(TOKEN_URL, 400, {"error": "invalid_grant", "error_codes": [700082]})
    for _ in range(3):
        with pytest.raises(FeedSignIn):
            await office.reader.events(day(10, 9), day(10, 10))
    assert office.token_requests() == 1  # a dead refresh token is not sent again and again
    assert office.status.snapshot() == {"calendar:work": Record(failed=NOW, reason="sign_in")}
    office.tokens.save(ADDRESS, "rt-2")  # scripts/mail-login.sh
    office.http.answers[TOKEN_URL] = []
    office.http.add(TOKEN_URL, 200, {"access_token": "at-calendar", "expires_in": 3600})
    office.view()
    await office.reader.events(day(10, 9), day(10, 10))
    assert office.token_requests() == 2


@pytest.mark.parametrize(
    ("answer", "reason", "code"),
    [
        (HttpResult(403, {}, {"error": {"code": "ErrorAccessDenied"}}), "refused", 403),
        (HttpResult(429, {"retry-after": "60"}, None), "busy", 429),
        (HttpResult(200, {}, ["not", "an", "object"]), "bad_answer", 0),
        (OSError("no route"), "no_answer", 0),
    ],
)
async def test_other_failures_are_recorded_and_named(
    tmp_path: Path, answer: HttpResult | BaseException, reason: str, code: int
) -> None:
    office = Office(tmp_path)
    office.http.answers[VIEW] = [answer]
    with pytest.raises(FeedError) as failure:
        await office.reader.events(day(10, 9), day(10, 10))
    assert not isinstance(failure.value, FeedSignIn | FeedNoConsent)
    assert office.status.snapshot() == {"calendar:work": Record(failed=NOW, reason=reason, code=code)}
    assert all(wait <= calendar_graph.MAX_WAIT <= 5 for wait in office.waits)
    assert len(office.waits) <= calendar_graph.RETRIES <= 1


async def test_a_slow_calendar_has_a_time_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_graph, "READ_SECONDS", 0.2)
    office = Office(tmp_path)

    async def slow(*args: Any, **kwargs: Any) -> HttpResult:
        await asyncio.sleep(5)
        raise AssertionError("not reached")

    office.calendars = OutlookCalendars(office.config, status=office.status, http=slow, clock=office.clock)
    with pytest.raises(FeedError):
        await asyncio.wait_for(office.calendars.reader(SOURCE).events(day(10, 9), day(10, 10)), 2)
    assert office.status.snapshot() == {"calendar:work": Record(failed=NOW, reason="no_answer")}
