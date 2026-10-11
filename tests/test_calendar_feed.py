import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from local_assistant.calendar_feed import CalendarFeed, FeedError, FeedGone, FeedNotSet
from local_assistant.config import CalendarSource, Config
from local_assistant.part_status import PartStatus, Record
from tests.conftest import ME, TZ

FAKE = Path(__file__).parent / "fakes" / "fake_ical_helper.py"


def at(value: str, zone: str = "Europe/Amsterdam") -> str:
    """'2026-10-09 09:30' in a zone as the helper writes it; a bare date stays a date (all day)."""
    if len(value) == 10:
        return value
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(zone)).isoformat()


def day(year: int, month: int, number: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, number, hour, minute, tzinfo=TZ)


class Calendar:
    """The fake helper's calendar in a file, and a config that runs the fake helper."""

    def __init__(self, tmp_path: Path, timeout: float = 5.0) -> None:
        self.state = tmp_path / "calendar.json"
        self.data: dict[str, Any] = {"calendar": "Family", "events": []}
        self.save()
        self.config = Config(
            bot_number="+306900000000",
            allowed_acis=frozenset({ME}),
            data_dir=tmp_path / "data",
            calendar_enabled=True,
            calendar_helper=(sys.executable, str(FAKE), str(self.state)),
            calendar_helper_timeout=timeout,
        )

    def add(self, title: str, start: str, end: str, *, zone: str = "Europe/Amsterdam", **fields: Any) -> None:
        item = {
            "title": title,
            "place": "",
            "start": at(start, zone),
            "end": at(end, zone),
            "all_day": len(start) == 10,
        }
        item |= {"zone": "" if len(start) == 10 else zone, "answer": "", "uid": f"{title}@example.com"} | fields
        self.data["events"].append(item)
        self.save()

    def save(self) -> None:
        self.state.write_text(json.dumps(self.data))

    def calls(self) -> list[list[str]]:
        path = Path(f"{self.state}.calls")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def env(self) -> list[str]:
        names: list[str] = json.loads(Path(f"{self.state}.env").read_text())
        return names

    def feed(self, status: PartStatus | None = None) -> CalendarFeed:
        return CalendarFeed(self.config, status=status)


async def test_events_come_in_local_time_and_keep_their_zone_when_its_clock_differs(tmp_path: Path) -> None:
    calendar = Calendar(tmp_path)
    calendar.add("Trip to Lisbon", "2026-10-10", "2026-10-12")
    calendar.add("Athens call", "2026-10-09 10:00", "2026-10-09 11:00", zone="Europe/Athens", place="Online")
    calendar.add("Berlin meeting", "2026-10-09 12:00", "2026-10-09 13:00", zone="Europe/Berlin")
    calendar.add("Call with Anna", "2026-10-09 14:00", "2026-10-09 15:00", answer="needs-action")
    agenda = await calendar.feed().events(day(2026, 10, 9), day(2026, 10, 12))
    events = {e.title: e for e in agenda.events}
    athens = events["Athens call"]
    assert (athens.start, athens.end, athens.zone, athens.place) == (
        day(2026, 10, 9, 9),
        day(2026, 10, 9, 10),
        "Europe/Athens",
        "Online",
    )
    assert events["Berlin meeting"].zone == ""  # the same clock as Amsterdam: nothing to add
    trip = events["Trip to Lisbon"]
    assert (trip.all_day, trip.start, trip.end) == (True, day(2026, 10, 10), day(2026, 10, 12))  # end exclusive
    assert events["Call with Anna"].response == "needs-action" and not agenda.truncated
    assert [e.title for e in agenda.events] == ["Athens call", "Berlin meeting", "Call with Anna", "Trip to Lisbon"]


async def test_the_helper_gets_fixed_arguments_and_a_clean_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-leak")
    calendar = Calendar(tmp_path)
    await calendar.feed().events(day(2026, 10, 9), day(2026, 10, 11))
    await calendar.feed().search("Maria birthday", day(2026, 10, 9, 9, 15), day(2027, 10, 9, 9, 15))
    assert calendar.calls() == [
        [
            "events",
            "--from",
            "2026-10-09T00:00:00+02:00",
            "--to",
            "2026-10-11T00:00:00+02:00",
            "--zone",
            "Europe/Amsterdam",
        ],
        [
            "events",
            "--from",
            "2026-10-09T09:15:00+02:00",
            "--to",
            "2027-10-09T09:15:00+02:00",
            "--zone",
            "Europe/Amsterdam",
            "--words=Maria birthday",  # one argument: words starting with "-" are never read as an option
            "--limit=10",
        ],
    ]
    assert "OPENROUTER_API_KEY" not in calendar.env() and set(calendar.env()) <= {
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "LANG",
        "PATH",
        "__CF_USER_TEXT_ENCODING",
    }


@pytest.mark.parametrize(
    ("state", "error", "message"),
    [
        ({"error": "gone"}, FeedGone, "no longer works"),
        ({"error": "not_set"}, FeedNotSet, "not set up"),
        ({"error": "unreachable"}, FeedError, "unreachable"),
        ({"error": "invalid"}, FeedError, "invalid"),
        ({"raw": "Traceback (most recent call last): secret stuff"}, FeedError, "no answer"),
    ],
)
async def test_failures_are_named_and_never_pass_on_the_helpers_text(
    tmp_path: Path, state: dict[str, Any], error: type[Exception], message: str
) -> None:
    calendar = Calendar(tmp_path)
    calendar.data = state
    calendar.save()
    with pytest.raises(error, match=message) as exc:
        await calendar.feed().events(day(2026, 10, 9), day(2026, 10, 10))
    assert "secret stuff" not in str(exc.value) and "Traceback" not in str(exc.value)


async def test_a_slow_or_missing_helper_is_a_failure(tmp_path: Path) -> None:
    calendar = Calendar(tmp_path, timeout=0.3)
    calendar.data["sleep"] = 5
    calendar.save()
    started = time.monotonic()
    with pytest.raises(FeedError, match="unreachable"):
        await calendar.feed().events(day(2026, 10, 9), day(2026, 10, 10))
    assert time.monotonic() - started < 3
    missing = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        calendar_enabled=True,
        calendar_helper=(str(tmp_path / "no-such-python"), "-I", "helper.py"),
    )
    with pytest.raises(FeedError, match="not installed"):
        await CalendarFeed(missing).events(day(2026, 10, 9), day(2026, 10, 10))


async def test_a_cut_short_answer_is_flagged(tmp_path: Path) -> None:
    calendar = Calendar(tmp_path)
    for minute in range(12):
        calendar.add(f"Dentist {minute}", f"2026-10-13 09:{minute:02d}", f"2026-10-13 09:{minute + 1:02d}")
    agenda = await calendar.feed().search("dentist", day(2026, 10, 9), day(2027, 10, 9))
    assert len(agenda.events) == 10 and agenda.truncated


async def test_a_cancelled_read_stops_the_helper(tmp_path: Path) -> None:
    calendar = Calendar(tmp_path)
    calendar.data["sleep"] = 30
    calendar.save()
    with pytest.raises(TimeoutError):  # an outer limit (the digest's, mini_status') gives up first
        await asyncio.wait_for(calendar.feed().events(day(2026, 10, 9), day(2026, 10, 10)), 0.5)
    pid = int(Path(f"{calendar.state}.pid").read_text())
    await asyncio.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # no helper left behind


async def test_closest_matches_are_flagged(tmp_path: Path) -> None:
    calendar = Calendar(tmp_path)
    calendar.data["partial"] = True  # the helper found events with some of the words only
    calendar.add("Dentist", "2026-10-13 09:30", "2026-10-13 10:00")
    agenda = await calendar.feed().search("dentist", day(2026, 10, 9), day(2027, 10, 9))
    assert agenda.partial and [e.title for e in agenda.events] == ["Dentist"]


NOW = 1_791_452_400.0


async def test_a_real_read_is_recorded(tmp_path: Path) -> None:
    calendar = Calendar(tmp_path)
    status = PartStatus(tmp_path / "status.json", clock=lambda: NOW)
    await calendar.feed(status).events(day(2026, 10, 9), day(2026, 10, 10))
    assert status.snapshot() == {"calendar": Record(ok=NOW)}


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"error": "gone"}, "gone"),
        ({"error": "not_set"}, "not_set"),
        ({"error": "unreachable"}, "no_answer"),
        ({"error": "invalid"}, "bad_answer"),
        ({"raw": "Traceback (most recent call last): secret stuff"}, "helper_failed"),
        ({"sleep": 5}, "no_answer"),
    ],
)
async def test_a_failed_read_is_recorded_with_its_reason(tmp_path: Path, state: dict[str, Any], reason: str) -> None:
    calendar = Calendar(tmp_path, timeout=0.3)
    calendar.data = state
    calendar.save()
    status = PartStatus(tmp_path / "status.json", clock=lambda: NOW)
    with pytest.raises(FeedError):
        await calendar.feed(status).events(day(2026, 10, 9), day(2026, 10, 10))
    assert status.snapshot() == {"calendar": Record(failed=NOW, reason=reason)}
    assert "secret stuff" not in (tmp_path / "status.json").read_text()


async def test_a_missing_helper_is_recorded(tmp_path: Path) -> None:
    missing = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        calendar_enabled=True,
        calendar_helper=(str(tmp_path / "no-such-python"), "-I", "helper.py"),
    )
    status = PartStatus(tmp_path / "status.json", clock=lambda: NOW)
    with pytest.raises(FeedError):
        await CalendarFeed(missing, status=status).events(day(2026, 10, 9), day(2026, 10, 10))
    assert status.snapshot() == {"calendar": Record(failed=NOW, reason="helper_missing")}


class Several(Calendar):
    """The default Google calendar and two named ones ("school-days", "club") on the fake helper."""

    def __init__(self, tmp_path: Path, timeout: float = 5.0) -> None:
        super().__init__(tmp_path, timeout)
        self.data["calendars"] = {"school-days": {"events": []}, "club": {"events": []}}
        self.save()
        self.config = replace(
            self.config,
            calendar_sources=(
                CalendarSource("calendar", "Family"),
                CalendarSource("school-days"),
                CalendarSource("club", "Club ⚽"),
            ),
        )

    def add_to(self, name: str, title: str, start: str, end: str, **fields: Any) -> None:
        item = {"title": title, "place": "", "start": at(start), "end": at(end), "all_day": len(start) == 10}
        item |= {"zone": "", "answer": "", "uid": f"{title}@example.com"} | fields
        self.data["calendars"][name]["events"].append(item)
        self.save()


async def test_several_calendars_are_read_each_by_name_and_merged(tmp_path: Path) -> None:
    calendars = Several(tmp_path)
    calendars.add("Dinner", "2026-10-09 19:00", "2026-10-09 21:00")
    calendars.add_to("school-days", "Autumn break", "2026-10-09", "2026-10-10")
    calendars.add_to("club", "Football", "2026-10-09 16:00", "2026-10-09 17:30")
    feed = calendars.feed()
    assert feed.several
    agenda = await feed.events(day(2026, 10, 9), day(2026, 10, 10))
    assert [(e.calendar, e.title) for e in agenda.events] == [
        ("School days", "Autumn break"),
        ("Club ⚽", "Football"),
        ("Family", "Dinner"),
    ]
    assert agenda.missed == () and not agenda.truncated
    asked = sorted(call[7:] for call in calendars.calls())  # each helper run names its calendar, but the default
    assert asked == [[], ["--calendar", "club"], ["--calendar", "school-days"]]


async def test_one_failing_calendar_never_hides_the_others(tmp_path: Path) -> None:
    calendars = Several(tmp_path)
    calendars.add("Dinner", "2026-10-09 19:00", "2026-10-09 21:00")
    calendars.data["calendars"]["school-days"] = {"error": "gone"}
    calendars.data["calendars"].pop("club")  # its address was never saved
    calendars.save()
    status = PartStatus(tmp_path / "status.json", clock=lambda: NOW)
    agenda = await calendars.feed(status).events(day(2026, 10, 9), day(2026, 10, 10))
    assert [e.title for e in agenda.events] == ["Dinner"]
    assert [(m.source.name, m.reason) for m in agenda.missed] == [("school-days", "gone"), ("club", "not_set")]
    assert status.snapshot() == {
        "calendar": Record(ok=NOW),
        "calendar:school-days": Record(failed=NOW, reason="gone"),
        "calendar:club": Record(failed=NOW, reason="not_set"),
    }


async def test_the_calendars_are_read_at_the_same_time(tmp_path: Path) -> None:
    calendars = Several(tmp_path)
    calendars.data["sleep"] = 1
    for name in ("school-days", "club"):
        calendars.data["calendars"][name]["sleep"] = 1
    calendars.save()
    started = time.monotonic()
    await calendars.feed().events(day(2026, 10, 9), day(2026, 10, 10))
    assert time.monotonic() - started < 2.5  # three helpers of a second each, side by side


async def test_the_same_event_in_two_calendars_is_shown_once(tmp_path: Path) -> None:
    calendars = Several(tmp_path)
    calendars.add("Parents' evening", "2026-10-09 19:00", "2026-10-09 20:00", uid="evening@example.com")
    calendars.add_to(
        "school-days", "Parents' evening", "2026-10-09 19:00", "2026-10-09 20:00", uid="evening@example.com"
    )
    calendars.add_to(
        "school-days", "Parents' evening", "2026-10-16 19:00", "2026-10-16 20:00", uid="evening@example.com"
    )
    agenda = await calendars.feed().events(day(2026, 10, 9), day(2026, 10, 20))
    assert [(e.calendar, e.start.day) for e in agenda.events] == [("Family", 9), ("School days", 16)]


async def test_a_search_over_several_calendars_keeps_the_best_matches(tmp_path: Path) -> None:
    calendars = Several(tmp_path)
    calendars.add("Dentist Maria", "2026-10-13 09:30", "2026-10-13 10:00")
    calendars.add_to("club", "Maria birthday", "2026-11-02", "2026-11-03")
    calendars.add_to("school-days", "Maria birthday party", "2026-11-03 15:00", "2026-11-03 17:00")
    found = await calendars.feed().search("Maria birthday", day(2026, 10, 9), day(2027, 10, 9))
    assert [(e.calendar, e.title) for e in found.events] == [
        ("Club ⚽", "Maria birthday"),
        ("School days", "Maria birthday party"),
    ]
    assert not found.partial
    one = await calendars.feed().search("Maria", day(2026, 10, 9), day(2027, 10, 9), limit=2)
    assert len(one.events) == 2 and one.truncated


async def test_a_single_named_calendar_reads_its_own_address(tmp_path: Path) -> None:
    calendars = Several(tmp_path)
    calendars.config = replace(calendars.config, calendar_sources=(CalendarSource("club"),))
    calendars.add_to("club", "Football", "2026-10-09 16:00", "2026-10-09 17:30")
    feed = calendars.feed()
    agenda = await feed.events(day(2026, 10, 9), day(2026, 10, 10))
    assert not feed.several and [(e.calendar, e.title) for e in agenda.events] == [("", "Football")]
    calendars.data["calendars"]["club"] = {"error": "gone"}
    calendars.save()
    with pytest.raises(FeedGone):  # one calendar: as before
        await feed.events(day(2026, 10, 9), day(2026, 10, 10))
