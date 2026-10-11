import http.server
import json
import socketserver
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import helper
import pytest
import recurring_ical_events

ADDRESS = "https://calendar.google.com/calendar/ical/owner%40example.com/private-0123456789abcdef/basic.ics"
FROM, TO = "2026-10-09T00:00:00+02:00", "2026-10-31T00:00:00+01:00"


def calendar(*events: str, zone: str = "Europe/Amsterdam") -> bytes:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Google Inc//Google Calendar 70.9054//EN"]
    lines += ["X-WR-CALNAME:Family", f"X-WR-TIMEZONE:{zone}", *events, "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode()


def event(uid: str, summary: str, start: str, end: str, *extra: str) -> str:
    return "\r\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{summary}",
            start,
            end,
            "DTSTAMP:20261001T000000Z",
            *extra,
            "END:VEVENT",
        ]
    )


def address_file(tmp_path: Path, address: str = ADDRESS, mode: int = 0o600) -> Path:
    path = tmp_path / "calendar.env"
    path.write_text(f"CALENDAR_ICS_URL={address}\n")
    path.chmod(mode)
    return path


def feed(data: bytes) -> Callable[[str], bytes]:
    def fetch(url: str) -> bytes:
        assert url == ADDRESS
        return data

    return fetch


def events(tmp_path: Path, data: bytes, *more: str, start: str = FROM, end: str = TO) -> tuple[int, dict[str, Any]]:
    argv = ["events", "--from", start, "--to", end, "--zone", "Europe/Amsterdam", *more]
    return helper.run(argv, fetch=feed(data), address_file=address_file(tmp_path))


def brief(found: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    return [(e["title"], e["start"], e["end"], e["zone"]) for e in found["events"]]


YOGA = event(
    "yoga@example.com",
    "Yoga",
    "DTSTART;TZID=Europe/Amsterdam:20261001T190000",
    "DTEND;TZID=Europe/Amsterdam:20261001T200000",
    "RRULE:FREQ=WEEKLY;BYDAY=TH",
    "EXDATE;TZID=Europe/Amsterdam:20261015T190000",
)
YOGA_MOVED = event(
    "yoga@example.com",
    "Yoga (Friday this week)",
    "DTSTART;TZID=Europe/Amsterdam:20261023T180000",
    "DTEND;TZID=Europe/Amsterdam:20261023T190000",
    "RECURRENCE-ID;TZID=Europe/Amsterdam:20261022T190000",
)


def test_a_range_comes_expanded_in_order_with_each_events_zone(tmp_path: Path) -> None:
    data = calendar(
        YOGA,
        YOGA_MOVED,
        event("trip@example.com", "Trip to Lisbon", "DTSTART;VALUE=DATE:20261010", "DTEND;VALUE=DATE:20261012"),
        event(
            "athens@example.com",
            "Athens call",
            "DTSTART;TZID=Europe/Athens:20261009T100000",
            "DTEND;TZID=Europe/Athens:20261009T110000",
            "LOCATION:Online",
        ),
        event(
            "gone@example.com",
            "Moved away",
            "DTSTART;TZID=Europe/Amsterdam:20261012T120000",
            "DTEND;TZID=Europe/Amsterdam:20261012T130000",
            "STATUS:CANCELLED",
        ),
    )
    code, found = events(tmp_path, data)
    assert code == 0 and found["ok"] and found["calendar"] == "Family" and not found["truncated"]
    assert brief(found) == [
        ("Athens call", "2026-10-09T10:00:00+03:00", "2026-10-09T11:00:00+03:00", "Europe/Athens"),
        ("Trip to Lisbon", "2026-10-10", "2026-10-12", ""),  # all day: the end date is exclusive
        ("Yoga (Friday this week)", "2026-10-23T18:00:00+02:00", "2026-10-23T19:00:00+02:00", "Europe/Amsterdam"),
        (
            "Yoga",
            "2026-10-29T19:00:00+01:00",
            "2026-10-29T20:00:00+01:00",
            "Europe/Amsterdam",
        ),  # after the clock change
    ]
    first = found["events"][0]
    assert (first["place"], first["all_day"], first["uid"], first["answer"]) == (
        "Online",
        False,
        "athens@example.com",
        "",
    )
    assert found["events"][1]["all_day"] is True
    assert "description" not in json.dumps(found).lower()


def test_utc_and_floating_times_take_the_calendars_zone(tmp_path: Path) -> None:
    data = calendar(
        event("utc@example.com", "Stand-up", "DTSTART:20261009T073000Z", "DTEND:20261009T074500Z"),
        event("float@example.com", "Dentist", "DTSTART:20261009T093000", "DTEND:20261009T100000"),
    )
    _, found = events(tmp_path, data)
    assert brief(found) == [
        ("Stand-up", "2026-10-09T09:30:00+02:00", "2026-10-09T09:45:00+02:00", "Europe/Amsterdam"),
        ("Dentist", "2026-10-09T09:30:00+02:00", "2026-10-09T10:00:00+02:00", "Europe/Amsterdam"),
    ]


def test_the_owners_answer_is_the_answer(tmp_path: Path) -> None:
    def invited(uid: str, *attendees: str) -> str:
        return event(
            uid,
            uid.split("@")[0],
            "DTSTART;TZID=Europe/Amsterdam:20261009T140000",
            "DTEND;TZID=Europe/Amsterdam:20261009T150000",
            *attendees,
        )

    data = calendar(
        invited("declined@example.com", "ATTENDEE;PARTSTAT=DECLINED:mailto:Owner@Example.com"),
        invited("open@example.com", "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:owner@example.com"),
        invited("other@example.com", "ATTENDEE;PARTSTAT=DECLINED:mailto:anna@example.com"),
    )
    _, found = events(tmp_path, data)
    assert {e["title"]: e["answer"] for e in found["events"]} == {
        "open": "needs-action",
        "other": "",  # someone else's answer is not the owner's
    }  # the owner declined "declined": it is left out (never shown, so never counted)


def test_the_daylight_saving_weekend_keeps_whole_days(tmp_path: Path) -> None:
    data = calendar(
        event(
            "film@example.com",
            "Late film",
            "DTSTART;TZID=Europe/Amsterdam:20261025T233000",
            "DTEND;TZID=Europe/Amsterdam:20261025T235900",
        ),
        event(
            "flight@example.com",
            "Early flight",
            "DTSTART;TZID=Europe/Amsterdam:20261026T003000",
            "DTEND;TZID=Europe/Amsterdam:20261026T020000",
        ),
    )
    _, found = events(tmp_path, data, start="2026-10-25T00:00:00+02:00", end="2026-10-26T00:00:00+01:00")
    assert [e["title"] for e in found["events"]] == ["Late film"]


FINE = event(
    "fine@example.com",
    "Fine",
    "DTSTART;TZID=Europe/Amsterdam:20261009T110000",
    "DTEND;TZID=Europe/Amsterdam:20261009T120000",
)


@pytest.mark.parametrize(
    "rule",
    [
        "RRULE:FREQ=WHENEVER",
        "RRULE:FREQ=WEEKLY;BYDAY=XX",
        "RRULE:FREQ=WEEKLY;UNTIL=notadate",  # unreadable: it used to take the whole calendar down
        "RRULE:FREQ=DAILY;INTERVAL=0",  # never moves on: it used to hang the expansion
        "RRULE:FREQ=SECONDLY",  # a flood: Google Calendar itself cannot make rules finer than daily
        "RRULE:FREQ=MINUTELY",
        "RRULE:FREQ=HOURLY",
        "RRULE:FREQ=DAILY;BYHOUR=1,2,3,4,5",
    ],
)
def test_one_broken_repeating_event_is_left_out_and_the_rest_shown(tmp_path: Path, rule: str) -> None:
    broken = event(
        "broken@example.com",
        "Broken",
        "DTSTART;TZID=Europe/Amsterdam:20151009T090000",  # starting long ago made the floods slow
        "DTEND;TZID=Europe/Amsterdam:20151009T100000",
        rule,
    )
    started = time.monotonic()
    code, found = events(tmp_path, calendar(broken, FINE))
    assert (code, [e["title"] for e in found["events"]]) == (0, ["Fine"])
    assert time.monotonic() - started < 5


def test_a_slow_repeating_event_is_dropped_on_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # valid, but never happens: the rule library searches to the year 9999 (about 1.6 s each)
    never = [
        event(
            f"never{n}@example.com",
            "Never",
            "DTSTART;TZID=Europe/Amsterdam:20261009T090000",
            "DTEND;TZID=Europe/Amsterdam:20261009T100000",
            "RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30",
        )
        for n in range(3)
    ]
    monkeypatch.setattr(helper, "SERIES_SECONDS", 0.2)
    started = time.monotonic()
    code, found = events(tmp_path, calendar(*never, FINE))
    assert (code, [e["title"] for e in found["events"]], found["truncated"]) == (0, ["Fine"], True)
    assert time.monotonic() - started < 3


def test_one_repeating_event_cannot_fill_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    daily = event(
        "daily@example.com",
        "Stand-up",
        "DTSTART;TZID=Europe/Amsterdam:20261009T080000",
        "DTEND;TZID=Europe/Amsterdam:20261009T081500",
        "RRULE:FREQ=DAILY",
    )
    monkeypatch.setattr(helper, "SERIES_INSTANCES", 5)
    _, found = events(tmp_path, calendar(daily, FINE))
    titles = [e["title"] for e in found["events"]]
    assert titles.count("Stand-up") == 5 and "Fine" in titles and found["truncated"]


def test_a_long_event_keeps_a_year_search_fast(tmp_path: Path) -> None:
    # an all-day school year overlapping every day used to make the search crawl past the time limit
    weekly = [
        event(
            f"w{n}@example.com",
            f"Weekly {n}",
            f"DTSTART;TZID=Europe/Amsterdam:2025{1 + n % 12:02d}{1 + n % 28:02d}T{8 + n % 10:02d}0000",
            f"DTEND;TZID=Europe/Amsterdam:2025{1 + n % 12:02d}{1 + n % 28:02d}T{9 + n % 10:02d}0000",
            "RRULE:FREQ=WEEKLY",
        )
        for n in range(100)
    ]
    school = event("school@example.com", "School year", "DTSTART;VALUE=DATE:20260901", "DTEND;VALUE=DATE:20270710")
    dentist = event(
        "dentist@example.com",
        "Dentist",
        "DTSTART;TZID=Europe/Amsterdam:20261113T093000",
        "DTEND;TZID=Europe/Amsterdam:20261113T100000",
    )
    started = time.monotonic()
    code, found = events(
        tmp_path, calendar(*weekly, school, dentist), "--words", "dentist", end="2027-10-09T00:00:00+02:00"
    )
    assert (code, [e["title"] for e in found["events"]]) == (0, ["Dentist"])
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "lines",
    [
        ("DTSTART;VALUE=DATE:99991231",),  # the end of time, no end given
        ("DTSTART:99991231T230000Z", "DTEND:99991231T233000Z"),  # converting it to local time overflows
        ("DTSTART;VALUE=DATE:99991231", "RRULE:FREQ=YEARLY"),
    ],
)
def test_an_event_at_the_end_of_time_is_left_out(tmp_path: Path, lines: tuple[str, ...]) -> None:
    far = "\r\n".join(["BEGIN:VEVENT", "UID:far@example.com", "SUMMARY:Far", *lines, "END:VEVENT"])
    code, found = events(tmp_path, calendar(far, FINE))
    assert (code, [e["title"] for e in found["events"]]) == (0, ["Fine"])


def test_declined_events_never_take_a_place_in_the_answer(tmp_path: Path) -> None:
    declined = event(
        "team@example.com",
        "Team meeting",
        "DTSTART;TZID=Europe/Amsterdam:20261012T100000",
        "DTEND;TZID=Europe/Amsterdam:20261012T110000",
        "RRULE:FREQ=WEEKLY",
        "ATTENDEE;PARTSTAT=DECLINED:mailto:owner@example.com",
    )
    offsite = event(
        "offsite@example.com",
        "Team meeting offsite",
        "DTSTART;TZID=Europe/Amsterdam:20270115T090000",
        "DTEND;TZID=Europe/Amsterdam:20270115T170000",
    )
    _, found = events(
        tmp_path, calendar(declined, offsite), "--words=team meeting", "--limit=10", end="2027-10-09T00:00:00+02:00"
    )
    assert [e["title"] for e in found["events"]] == ["Team meeting offsite"]


def test_search_falls_back_to_the_closest_matches(tmp_path: Path) -> None:
    data = calendar(
        event(
            "dentist@example.com",
            "Dentist",
            "DTSTART;TZID=Europe/Amsterdam:20261013T093000",
            "DTEND;TZID=Europe/Amsterdam:20261013T100000",
        ),
        event("maria@example.com", "Birthday Maria", "DTSTART;VALUE=DATE:20261114", "DTEND;VALUE=DATE:20261115"),
    )
    _, found = events(tmp_path, data, "--words=dentist appointment")  # Gemma added a word the title lacks
    assert ([e["title"] for e in found["events"]], found["partial"]) == (["Dentist"], True)
    _, found = events(tmp_path, data, "--words=Maria's birthday", end="2027-10-09T00:00:00+02:00")  # "s" is no word
    assert ([e["title"] for e in found["events"]], found["partial"]) == (["Birthday Maria"], False)


def test_the_expansion_has_a_hard_time_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Endless:
        def between(self, start: object, end: object) -> list[object]:
            while True:  # something the checks did not catch
                time.sleep(0.01)

    monkeypatch.setattr(helper, "EXPAND_SECONDS", 0.3)
    monkeypatch.setattr(recurring_ical_events, "of", lambda *args, **kwargs: Endless())
    started = time.monotonic()
    code, out = events(tmp_path, calendar(FINE))
    assert (code, out["error"]) == (1, "invalid") and "too long" in out["detail"]
    assert time.monotonic() - started < 3


def test_the_autumn_clock_change_keeps_the_last_hour(tmp_path: Path) -> None:
    data = calendar(
        event("trip@example.com", "Trip", "DTSTART;VALUE=DATE:20261024", "DTEND;VALUE=DATE:20261026"),
        event("monday@example.com", "Holiday Monday", "DTSTART;VALUE=DATE:20261026", "DTEND;VALUE=DATE:20261027"),
        event(
            "film@example.com",
            "Late film",
            "DTSTART;TZID=Europe/Amsterdam:20261025T233000",
            "DTEND;TZID=Europe/Amsterdam:20261025T235900",
        ),
    )
    _, found = events(tmp_path, data, start="2026-10-24T00:00:00+02:00", end="2026-10-26T00:00:00+01:00")
    assert [e["title"] for e in found["events"]] == ["Trip", "Late film"]


def test_long_titles_and_places_are_cut(tmp_path: Path) -> None:
    long = event(
        "long@example.com",
        "x" * 5000,
        "DTSTART;TZID=Europe/Amsterdam:20261009T090000",
        "DTEND;TZID=Europe/Amsterdam:20261009T100000",
        "LOCATION:" + "y" * 5000,
    )
    _, found = events(tmp_path, calendar(long))
    assert (len(found["events"][0]["title"]), len(found["events"][0]["place"])) == (500, 500)


def test_search_needs_every_word_in_the_title_or_place(tmp_path: Path) -> None:
    def at(day: int, title: str, place: str) -> str:
        return event(
            f"e{day}@example.com",
            title,
            f"DTSTART;TZID=Europe/Amsterdam:202610{day:02d}T090000",
            f"DTEND;TZID=Europe/Amsterdam:202610{day:02d}T093000",
            f"LOCATION:{place}",
        )

    data = calendar(
        at(12, "Dentist", "Prinsengracht 12"), at(13, "Dentist check", "Online"), at(14, "Lunch", "Dentist's café")
    )
    _, found = events(tmp_path, data, "--words", "dentist PRINSENGRACHT")
    assert [e["title"] for e in found["events"]] == ["Dentist"]
    _, found = events(tmp_path, data, "--words", "dentist", "--limit", "2")
    assert [e["title"] for e in found["events"]] == ["Dentist", "Dentist check"] and found["truncated"]


def test_words_and_limit_may_come_as_one_argument(tmp_path: Path) -> None:
    data = calendar(
        event(
            "dash@example.com",
            "-dentist reminder",
            "DTSTART;TZID=Europe/Amsterdam:20261012T090000",
            "DTEND;TZID=Europe/Amsterdam:20261012T093000",
        )
    )
    code, found = events(tmp_path, data, "--words=-dentist", "--limit=5")
    assert (code, [e["title"] for e in found["events"]]) == (0, ["-dentist reminder"])


def test_the_address_must_be_saved_private_and_googles(tmp_path: Path) -> None:
    def never(url: str) -> bytes:
        raise AssertionError("must not fetch")

    argv = ["events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam"]
    assert helper.run(argv, fetch=never, address_file=tmp_path / "missing.env") == (
        1,
        {"ok": False, "error": "not_set", "detail": "no saved address"},
    )
    code, out = helper.run(argv, fetch=never, address_file=address_file(tmp_path, mode=0o644))
    assert (code, out["error"]) == (1, "not_set") and "chmod 600" in out["detail"]
    other = address_file(tmp_path, "https://evil.example/calendar/ical/x/private-1/basic.ics")
    code, out = helper.run(argv, fetch=never, address_file=other)
    assert (code, out["error"]) == (1, "not_set") and "Google" in out["detail"]
    assert "evil" not in json.dumps(out)  # the address itself is never printed


@pytest.mark.parametrize(
    ("failure", "kind"),
    [(helper.Gone("HTTP 404"), "gone"), (helper.Unreachable("HTTP 503"), "unreachable"), (None, "invalid")],
)
def test_feed_failures_are_named(tmp_path: Path, failure: Exception | None, kind: str) -> None:
    def fetch(url: str) -> bytes:
        if failure is not None:
            raise failure
        return b"<html>not a calendar</html>"

    argv = ["events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam"]
    code, out = helper.run(argv, fetch=fetch, address_file=address_file(tmp_path))
    assert (code, out["ok"], out["error"]) == (1, False, kind)


def test_bad_arguments_are_refused(tmp_path: Path) -> None:
    for start, end in ((TO, FROM), ("2026-10-09T00:00:00", TO)):  # backwards; no offset
        code, out = events(tmp_path, calendar(), start=start, end=end)
        assert (code, out["error"]) == (1, "invalid")


class Server(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "https://evil.example/")
            self.end_headers()
        elif self.path == "/garbage":
            self.wfile.write(b"not http at all\r\n\r\n")
        elif self.path == "/gone":
            self.send_response(404)
            self.end_headers()
        else:
            body = b"BEGIN:VCALENDAR\r\n" + b"X" * 2000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


class QuickServer(http.server.ThreadingHTTPServer):
    def server_bind(self) -> None:  # HTTPServer looks up the host's full name: ~35 s with slow reverse DNS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", int(self.server_address[1])


@pytest.fixture
def local() -> Iterator[str]:
    server = QuickServer(("127.0.0.1", 0), Server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_fetch_follows_no_redirect_and_reads_a_limited_size(local: str) -> None:
    with pytest.raises(helper.Unreachable, match="redirect"):
        helper.fetch(f"{local}/moved")
    with pytest.raises(helper.Gone, match="404"):
        helper.fetch(f"{local}/gone")
    with pytest.raises(helper.Refused, match="too large"):
        helper.fetch(f"{local}/big", max_bytes=1000)
    with pytest.raises(helper.Unreachable):  # a broken HTTP answer is not an OSError
        helper.fetch(f"{local}/garbage")
    assert helper.fetch(f"{local}/big").startswith(b"BEGIN:VCALENDAR")


def test_main_prints_one_json_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(helper, "ADDRESS_FILE", tmp_path / "missing.env")
    monkeypatch.setattr("sys.argv", ["helper.py", "events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam"])
    with pytest.raises(SystemExit) as exc:
        helper.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "not_set", "detail": "no saved address"}


PUBLIC = "https://calendar.google.com/calendar/ical/holidays%40example.com/public/basic.ics"
WORK = "https://calendar.google.com/calendar/ical/alex%40example.com/private-fedcba9876543210/basic.ics"


def named_file(tmp_path: Path) -> Path:
    path = tmp_path / "calendar.env"
    path.write_text(
        f"CALENDAR_ICS_URL={ADDRESS}\nCALENDAR_ICS_URL_WORK={WORK}\nCALENDAR_ICS_URL_SCHOOL_DAYS={PUBLIC}\nOTHER=x\n"
    )
    path.chmod(0o600)
    return path


def test_each_named_calendar_reads_its_own_address(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return calendar()

    argv = ["events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam"]
    path = named_file(tmp_path)
    for name in ([], ["--calendar", "calendar"], ["--calendar", "work"], ["--calendar=school-days"]):
        code, out = helper.run([*argv, *name], fetch=fetch, address_file=path)
        assert (code, out["ok"]) == (0, True)
    assert fetched == [ADDRESS, ADDRESS, WORK, PUBLIC]  # school-days and school_days share one key


def test_a_named_calendar_without_an_address_is_not_set(tmp_path: Path) -> None:
    def never(url: str) -> bytes:
        raise AssertionError("must not fetch")

    argv = ["events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam"]
    code, out = helper.run([*argv, "--calendar", "family"], fetch=never, address_file=named_file(tmp_path))
    assert (code, out) == (1, {"ok": False, "error": "not_set", "detail": "no saved address for family"})
    for bad in ("Work", "../work", "-x", "a" * 21, ""):
        code, out = helper.run([*argv, f"--calendar={bad}"], fetch=never, address_file=named_file(tmp_path))
        assert (code, out["error"]) == (1, "invalid")


def test_a_public_address_is_accepted_too(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return calendar()

    argv = ["events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam"]
    code, out = helper.run(argv, fetch=fetch, address_file=address_file(tmp_path, PUBLIC))
    assert (code, out["ok"], fetched) == (0, True, [PUBLIC])
    assert helper.owner_of(PUBLIC) == "holidays@example.com"
