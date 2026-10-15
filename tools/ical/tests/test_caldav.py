"""CalDAV calendars (iCloud, Fastmail, Nextcloud) in the calendar helper: read-only requests (PROPFIND and REPORT only),
HTTPS only, the configured host only (iCloud: its icloud.com hosts), recorded answers (no network)."""

import base64
import http.client
import http.server
import io
import itertools
import json
import socketserver
import ssl
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from xml.sax.saxutils import escape

import helper
import icalendar
import pytest

FROM, TO = "2026-10-09T00:00:00+02:00", "2026-10-31T00:00:00+01:00"
ICLOUD = "https://caldav.icloud.com"
HOME = "https://p42-caldav.icloud.com:443/1234567890/calendars/"
FAMILY = "https://p42-caldav.icloud.com:443/1234567890/calendars/family-0a1b2c/"
PASSWORD = "abcd-efgh-ijkl-mnop"  # privacy-ok: made up, an app-specific password's shape
ACCOUNT = {"server": ICLOUD, "user": "alex@example.com", "calendar": "Family"}


@dataclass(frozen=True)
class Sent:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timeout: float


class Server:
    """Recorded answers by method and address; every request is kept. An address it has no answer for: 404."""

    def __init__(self, answers: dict[tuple[str, str], helper.Answer]) -> None:
        self.answers = answers
        self.sent: list[Sent] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float, max_bytes: int
    ) -> helper.Answer:
        self.sent.append(Sent(method, url, headers, body, timeout))
        return self.answers.get((method, url), helper.Answer(404, {}, b""))

    def asked(self) -> list[tuple[str, str]]:
        return [(sent.method, sent.url) for sent in self.sent]


def multistatus(*responses: str) -> helper.Answer:
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<multistatus xmlns="DAV:">' + "".join(responses) + "</multistatus>"
    return helper.Answer(207, {"content-type": "text/xml; charset=UTF-8"}, body.encode())


def response(href: str, props: str, *, missing: str = "") -> str:
    found = f"<propstat><prop>{props}</prop><status>HTTP/1.1 200 OK</status></propstat>"
    lost = f"<propstat><prop>{missing}</prop><status>HTTP/1.1 404 Not Found</status></propstat>" if missing else ""
    return f"<response><href>{href}</href>{found}{lost}</response>"


def principal_answer(href: str = "/1234567890/principal/") -> helper.Answer:
    return multistatus(response("/", f"<current-user-principal><href>{href}</href></current-user-principal>"))


def home_answer(home: str = HOME, address: str = "mailto:alex@example.com") -> helper.Answer:
    cal = 'xmlns:C="urn:ietf:params:xml:ns:caldav"'
    props = (
        f"<C:calendar-home-set {cal}><href>{home}</href></C:calendar-home-set>"
        f"<C:calendar-user-address-set {cal}><href>{address}</href><href>/1234567890/principal/</href>"
        "</C:calendar-user-address-set>"
    )
    return multistatus(response("/1234567890/principal/", props))


def calendar_entry(href: str, name: str, *components: str) -> str:
    cal = 'xmlns:C="urn:ietf:params:xml:ns:caldav"'
    kinds = "".join(f'<C:comp name="{kind}"/>' for kind in components)
    supported = f"<C:supported-calendar-component-set {cal}>{kinds}</C:supported-calendar-component-set>"
    return response(
        href,
        f"<resourcetype><collection/><C:calendar {cal}/></resourcetype><displayname>{name}</displayname>"
        + (supported if components else ""),
    )


def calendars_answer() -> helper.Answer:
    return multistatus(
        response("/1234567890/calendars/", "<resourcetype><collection/></resourcetype>", missing="<displayname/>"),
        calendar_entry("/1234567890/calendars/home/", "Home", "VEVENT"),
        calendar_entry("/1234567890/calendars/family-0a1b2c/", " Family ", "VEVENT", "VTODO"),
        calendar_entry("/1234567890/calendars/tasks/", "Family", "VTODO"),  # a reminders list: no events
        response(
            "/1234567890/calendars/inbox/",
            '<resourcetype><collection/><C:schedule-inbox xmlns:C="urn:ietf:params:xml:ns:caldav"/></resourcetype>',
        ),
    )


def ical(*events: str) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Apple Inc.//iCloud//EN", *events, "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


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


AMSTERDAM = "\r\n".join(
    [
        "BEGIN:VTIMEZONE",
        "TZID:Europe/Amsterdam",
        "BEGIN:STANDARD",
        "DTSTART:19701025T030000",
        "TZOFFSETFROM:+0200",
        "TZOFFSETTO:+0100",
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
        "END:STANDARD",
        "BEGIN:DAYLIGHT",
        "DTSTART:19700329T020000",
        "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0200",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
        "END:DAYLIGHT",
        "END:VTIMEZONE",
    ]
)
FOOTBALL = ical(
    AMSTERDAM,
    event(
        "football@example.com",
        "Football",
        "DTSTART;TZID=Europe/Amsterdam:20261003T100000",
        "DTEND;TZID=Europe/Amsterdam:20261003T113000",
        "RRULE:FREQ=WEEKLY;BYDAY=SA",
        "LOCATION:Sports park",
    ),
)
DENTIST = ical(
    event("dentist@example.com", "Dentist", "DTSTART:20261014T080000Z", "DTEND:20261014T090000Z"),
)
DECLINED = ical(
    event(
        "party@example.com",
        "Party",
        "DTSTART:20261016T180000Z",
        "DTEND:20261016T220000Z",
        "ATTENDEE;PARTSTAT=DECLINED:mailto:alex@example.com",
    ),
)


def events_answer(*parts: str, cdata: bool = False) -> helper.Answer:
    cal = 'xmlns:C="urn:ietf:params:xml:ns:caldav"'
    found = [
        response(
            f"/1234567890/calendars/family-0a1b2c/{number}.ics",
            f'<getetag>"{number}"</getetag><C:calendar-data {cal}>'
            + (f"<![CDATA[{part}]]>" if cdata else escape(part))
            + "</C:calendar-data>",
        )
        for number, part in enumerate(parts)
    ]
    return multistatus(*found)


def icloud(*parts: str, cdata: bool = False) -> dict[tuple[str, str], helper.Answer]:
    """iCloud's way: the principal on caldav.icloud.com, the calendars on a pNN-caldav host of the same domain."""
    return {
        ("PROPFIND", f"{ICLOUD}/"): principal_answer(),
        ("PROPFIND", f"{ICLOUD}/1234567890/principal/"): home_answer(),
        ("PROPFIND", HOME): calendars_answer(),
        ("REPORT", FAMILY): events_answer(*parts, cdata=cdata),
    }


def password_file(tmp_path: Path, key: str = "CALDAV_PASSWORD_FAMILY", mode: int = 0o600) -> Path:
    path = tmp_path / "calendar.env"
    path.write_text(
        f"CALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/x/private-1/basic.ics\n{key}={PASSWORD}\n"
    )
    path.chmod(mode)
    return path


def never(url: str) -> bytes:
    raise AssertionError("a CalDAV calendar never fetches an iCal address")


def caldav(
    tmp_path: Path,
    server: Server,
    *more: str,
    account: dict[str, str] | None = None,
    path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    argv = ["events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam", "--calendar", "family", "--caldav"]
    line = json.dumps(account or ACCOUNT)
    code, out = helper.run(
        [*argv, *more],
        fetch=never,
        address_file=path or password_file(tmp_path),
        transport=server,
        source=lambda: line,
    )
    if out.get("error") == "invalid":  # every invalid answer carries a fixed code the bridge may log
        assert out["code"] in helper.INVALID_CODES
    return code, out


def test_an_icloud_calendar_is_found_by_its_name_and_its_events_expanded(tmp_path: Path) -> None:
    server = Server(icloud(FOOTBALL, DENTIST))
    code, out = caldav(tmp_path, server)
    assert (code, out["ok"], out["calendar"], out["truncated"]) == (0, True, "Family", False)
    assert [(e["title"], e["start"], e["place"]) for e in out["events"]] == [
        ("Football", "2026-10-10T10:00:00+02:00", "Sports park"),
        ("Dentist", "2026-10-14T08:00:00+00:00", ""),  # UTC as written: the bridge shows local time
        ("Football", "2026-10-17T10:00:00+02:00", "Sports park"),
        ("Football", "2026-10-24T10:00:00+02:00", "Sports park"),
    ]  # the 31st's starts after the range
    assert server.asked() == [
        ("PROPFIND", f"{ICLOUD}/"),
        ("PROPFIND", f"{ICLOUD}/1234567890/principal/"),
        ("PROPFIND", HOME),
        ("REPORT", FAMILY),
    ]
    depth = [sent.headers["Depth"] for sent in server.sent]
    assert depth == ["0", "0", "1", "1"]
    report = server.sent[-1].body.decode()
    assert "calendar-query" in report and 'start="20261008T220000Z"' in report and 'end="20261030T230000Z"' in report


def test_cdata_calendar_data_reads_the_same(tmp_path: Path) -> None:
    code, out = caldav(tmp_path, Server(icloud(DENTIST, cdata=True)))
    assert (code, [e["title"] for e in out["events"]]) == (0, ["Dentist"])


def test_every_request_is_read_only_with_basic_auth_and_no_cookie(tmp_path: Path) -> None:
    answers = icloud(DENTIST)
    first = answers[("PROPFIND", f"{ICLOUD}/")]
    answers[("PROPFIND", f"{ICLOUD}/")] = helper.Answer(207, {"set-cookie": "session=abc"}, first.body)
    server = Server(answers)
    code, _ = caldav(tmp_path, server)
    assert code == 0
    for sent in server.sent:
        assert sent.method in ("PROPFIND", "REPORT")
        assert sent.url.startswith("https://")
        assert (
            sent.headers["Authorization"]
            == "Basic " + base64.b64encode(f"alex@example.com:{PASSWORD}".encode()).decode()
        )
        assert not any(name.lower() == "cookie" for name in sent.headers)
        assert 0 < sent.timeout <= helper.CALDAV_SECONDS


@pytest.mark.parametrize("method", ["PUT", "POST", "DELETE", "MKCALENDAR", "PROPPATCH", "GET", "MOVE", "COPY", "LOCK"])
def test_only_propfind_and_report_are_ever_sent(method: str) -> None:
    server = Server({})
    client = helper.CalDav(ICLOUD, "alex@example.com", PASSWORD, transport=server, deadline=1e12)
    with pytest.raises(helper.Refused, match="read"):
        client.request(method, f"{ICLOUD}/", b"", depth="0", max_bytes=1000)
    connected: list[str] = []

    def connect(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
        connected.append(host)  # never: should the check break, this test still opens no connection
        raise AssertionError("no connection")

    with pytest.raises(helper.Refused, match="read"):
        helper.https_transport(method, f"{ICLOUD}/", {}, b"", 1.0, 1000, connect=connect)
    assert server.sent == [] and connected == []
    assert frozenset({"PROPFIND", "REPORT"}) == helper.READ_METHODS


def test_a_redirect_within_the_domain_is_followed_with_the_same_method(tmp_path: Path) -> None:
    answers = icloud(DENTIST)
    answers[("PROPFIND", f"{ICLOUD}/")] = helper.Answer(301, {"location": "https://p42-caldav.icloud.com/"}, b"")
    answers[("PROPFIND", "https://p42-caldav.icloud.com/")] = principal_answer()
    answers[("PROPFIND", "https://p42-caldav.icloud.com/1234567890/principal/")] = home_answer()
    server = Server(answers)
    code, out = caldav(tmp_path, server)
    assert (code, [e["title"] for e in out["events"]]) == (0, ["Dentist"])
    assert server.asked()[:3] == [
        ("PROPFIND", f"{ICLOUD}/"),
        ("PROPFIND", "https://p42-caldav.icloud.com/"),
        ("PROPFIND", "https://p42-caldav.icloud.com/1234567890/principal/"),
    ]


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/",  # another domain
        "http://p42-caldav.icloud.com/",  # not HTTPS
        "https://p42-caldav.icloud.com:8443/",  # another port
        "https://alex:pw@p42-caldav.icloud.com/",  # privacy-ok: a made-up login in the address
        "https://icloud.com.evil.example/",
        "",
    ],
)
def test_a_redirect_elsewhere_is_never_followed(tmp_path: Path, location: str) -> None:
    server = Server({("PROPFIND", f"{ICLOUD}/"): helper.Answer(302, {"location": location}, b"")})
    code, out = caldav(tmp_path, server)
    assert (code, out["error"], out["code"]) == (1, "invalid", "redirect_refused")
    assert server.asked() == [("PROPFIND", f"{ICLOUD}/")]


def test_at_most_three_redirects(tmp_path: Path) -> None:
    hops = [f"https://p{n}-caldav.icloud.com/" for n in range(5)]
    answers = {("PROPFIND", f"{ICLOUD}/"): helper.Answer(307, {"location": hops[0]}, b"")}
    answers |= {("PROPFIND", hop): helper.Answer(307, {"location": nxt}, b"") for hop, nxt in itertools.pairwise(hops)}
    server = Server(answers)
    code, out = caldav(tmp_path, server)
    assert (code, out["error"], out["code"]) == (1, "invalid", "redirect_refused") and "redirect" in out["detail"]
    assert server.asked() == [("PROPFIND", url) for url in (f"{ICLOUD}/", *hops[:3])]  # the first, then 3 redirects


def test_an_address_in_an_answer_outside_the_domain_is_never_asked(tmp_path: Path) -> None:
    answers = icloud(DENTIST)
    answers[("PROPFIND", f"{ICLOUD}/1234567890/principal/")] = home_answer(home="https://evil.example/calendars/")
    server = Server(answers)
    code, out = caldav(tmp_path, server)
    assert (code, out["error"], out["code"]) == (1, "invalid", "other_server")
    assert not any("evil" in url for _, url in server.asked())


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_password_is_named(tmp_path: Path, status: int) -> None:
    server = Server({("PROPFIND", f"{ICLOUD}/"): helper.Answer(status, {}, b"Unauthorized")})
    code, out = caldav(tmp_path, server)
    assert (code, out["error"]) == (1, "password_refused")
    assert PASSWORD not in json.dumps(out) and "alex" not in json.dumps(out)
    assert server.asked() == [("PROPFIND", f"{ICLOUD}/")]  # no second try with the same password


def test_a_403_after_the_password_was_accepted_is_not_a_refused_password(tmp_path: Path) -> None:
    """A REPORT refused on a shared or restricted calendar: a new password would not help."""
    answers = icloud(DENTIST)
    answers[("REPORT", FAMILY)] = helper.Answer(403, {}, b"Forbidden")
    code, out = caldav(tmp_path, Server(answers))
    assert (code, out["error"], out["detail"], out["code"]) == (1, "invalid", "HTTP 403", "http_status")
    answers[("REPORT", FAMILY)] = helper.Answer(401, {}, b"Unauthorized")  # a 401 is the password at any step
    code, out = caldav(tmp_path, Server(answers))
    assert (code, out["error"]) == (1, "password_refused")


def test_no_calendar_by_that_name(tmp_path: Path) -> None:
    code, out = caldav(tmp_path, Server(icloud(DENTIST)), account=ACCOUNT | {"calendar": "Holidays"})
    assert (code, out["error"]) == (1, "no_calendar")
    assert "Home" not in json.dumps(out)  # the account's calendar names are never printed
    code, out = caldav(tmp_path, Server(icloud(DENTIST)), account=ACCOUNT | {"calendar": "family"})  # case aside
    assert code == 0


def test_a_reminders_list_with_the_same_name_is_not_a_calendar(tmp_path: Path) -> None:
    answers = icloud(DENTIST)
    answers[("PROPFIND", HOME)] = multistatus(calendar_entry("/1234567890/calendars/tasks/", "Family", "VTODO"))
    code, out = caldav(tmp_path, Server(answers))
    assert (code, out["error"]) == (1, "no_calendar")


@pytest.mark.parametrize(
    ("answer", "kind", "fixed"),
    [
        (helper.Answer(503, {}, b""), "unreachable", None),
        (helper.Answer(429, {}, b""), "unreachable", None),
        (helper.Answer(207, {}, b"<html>not xml"), "invalid", "not_dav"),
        (helper.Answer(400, {}, b""), "invalid", "http_status"),
        (helper.Answer(404, {}, b""), "invalid", "http_status"),
    ],
)
def test_failures_are_named(tmp_path: Path, answer: helper.Answer, kind: str, fixed: str | None) -> None:
    answers = icloud(DENTIST)
    answers[("REPORT", FAMILY)] = answer
    code, out = caldav(tmp_path, Server(answers))
    assert (code, out["error"], out.get("code")) == (1, kind, fixed)


def test_no_answer_is_unreachable(tmp_path: Path) -> None:
    def down(method: str, url: str, headers: dict[str, str], body: bytes, timeout: float, size: int) -> helper.Answer:
        raise helper.Unreachable("ConnectionRefusedError")

    code, out = caldav(tmp_path, down)  # type: ignore[arg-type]
    assert (code, out["error"]) == (1, "unreachable")


def test_the_requests_share_one_time_limit() -> None:
    clock = iter([0.0, 5.0, 21.0])
    server = Server(icloud(DENTIST))
    client = helper.CalDav(
        ICLOUD, "alex@example.com", PASSWORD, transport=server, deadline=20.0, clock=lambda: next(clock)
    )
    client.request("PROPFIND", f"{ICLOUD}/", b"", depth="0", max_bytes=1000)
    client.request("PROPFIND", f"{ICLOUD}/1234567890/principal/", b"", depth="0", max_bytes=1000)
    with pytest.raises(helper.Unreachable, match="time"):
        client.request("PROPFIND", HOME, b"", depth="1", max_bytes=1000)
    assert [sent.timeout for sent in server.sent] == [20.0, 15.0]


def test_an_answer_over_the_size_cap_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "MAX_BYTES", 2000)
    code, out = caldav(tmp_path, Server(icloud(DENTIST, FOOTBALL, DENTIST, FOOTBALL, DENTIST, FOOTBALL)))
    assert (code, out["error"], out["code"]) == (1, "invalid", "too_large") and "too large" in out["detail"]


@pytest.mark.parametrize(
    "body",
    [
        b'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><multistatus xmlns="DAV:">'
        b"<response><href>&b;</href></response></multistatus>",
        b'<!DOCTYPE m SYSTEM "file:///etc/passwd"><multistatus xmlns="DAV:"/>',
    ],
)
def test_xml_with_a_doctype_or_entities_is_refused(tmp_path: Path, body: bytes) -> None:
    server = Server({("PROPFIND", f"{ICLOUD}/"): helper.Answer(207, {}, body)})
    code, out = caldav(tmp_path, server)
    assert (code, out["error"], out["code"]) == (1, "invalid", "xml_refused")


def test_one_unreadable_event_is_left_out_and_the_rest_shown(tmp_path: Path) -> None:
    code, out = caldav(tmp_path, Server(icloud("BEGIN:VCALENDAR\r\nnot a calendar at all", DENTIST)))
    assert (code, [e["title"] for e in out["events"]], out["truncated"]) == (0, ["Dentist"], True)


def test_an_event_object_the_library_cannot_read_is_left_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = icalendar.Calendar.from_ical

    def from_ical(data: bytes, *args: Any, **kwargs: Any) -> Any:
        if b"Dentist" in data:
            raise KeyError("the library's own surprise")
        return real(data, *args, **kwargs)

    monkeypatch.setattr(icalendar.Calendar, "from_ical", from_ical)
    code, out = caldav(tmp_path, Server(icloud(DENTIST, FOOTBALL)))
    assert (code, [e["title"] for e in out["events"]], out["truncated"]) == (0, ["Football"] * 3, True)


def test_the_owners_answer_comes_from_the_accounts_addresses(tmp_path: Path) -> None:
    answers = icloud(DECLINED, DENTIST)
    code, out = caldav(tmp_path, Server(answers))
    assert (code, [e["title"] for e in out["events"]]) == (0, ["Dentist"])  # declined: left out
    answers[("PROPFIND", f"{ICLOUD}/1234567890/principal/")] = home_answer(address="mailto:other@example.com")
    code, out = caldav(tmp_path, Server(answers), account=ACCOUNT | {"user": "someone"})
    assert [e["title"] for e in out["events"]] == ["Dentist", "Party"]  # someone else declined: still shown


def test_a_search_reads_the_same_way(tmp_path: Path) -> None:
    code, out = caldav(tmp_path, Server(icloud(FOOTBALL, DENTIST)), "--words=dentist", "--limit=5")
    assert (code, [e["title"] for e in out["events"]]) == (0, ["Dentist"])


def test_a_server_without_a_principal_at_its_address_is_found_through_well_known(tmp_path: Path) -> None:
    """Nextcloud's way: /.well-known/caldav sends the client to /remote.php/dav/ on the same host."""
    cloud = "https://cloud.example.com"
    home = f"{cloud}/remote.php/dav/calendars/alex/"
    answers = {
        ("PROPFIND", f"{cloud}/.well-known/caldav"): helper.Answer(301, {"location": "/remote.php/dav/"}, b""),
        ("PROPFIND", f"{cloud}/remote.php/dav/"): principal_answer("/remote.php/dav/principals/users/alex/"),
        ("PROPFIND", f"{cloud}/remote.php/dav/principals/users/alex/"): home_answer(home=home),
        ("PROPFIND", home): multistatus(calendar_entry("/remote.php/dav/calendars/alex/personal/", "Personal")),
        ("REPORT", f"{home}personal/"): events_answer(DENTIST),
    }
    server = Server(answers)
    account = {"server": cloud, "user": "alex", "calendar": "Personal"}
    code, out = caldav(tmp_path, server, account=account)
    assert (code, [e["title"] for e in out["events"]]) == (0, ["Dentist"])
    assert server.asked()[:3] == [
        ("PROPFIND", f"{cloud}/"),  # 404: not here
        ("PROPFIND", f"{cloud}/.well-known/caldav"),
        ("PROPFIND", f"{cloud}/remote.php/dav/"),
    ]


def test_fastmail_with_its_dav_path(tmp_path: Path) -> None:
    fastmail = "https://caldav.fastmail.com/dav/"
    home = "https://caldav.fastmail.com/dav/calendars/user/alex@example.com/"
    answers = {
        ("PROPFIND", fastmail): principal_answer("/dav/principals/user/alex@example.com/"),
        ("PROPFIND", "https://caldav.fastmail.com/dav/principals/user/alex@example.com/"): home_answer(home=home),
        ("PROPFIND", home): multistatus(calendar_entry(f"{home}Default/", "Calendar", "VEVENT")),
        ("REPORT", f"{home}Default/"): events_answer(DENTIST),
    }
    account = {"server": fastmail, "user": "alex@example.com", "calendar": "Calendar"}
    code, out = caldav(tmp_path, Server(answers), account=account)
    assert (code, [e["title"] for e in out["events"]]) == (0, ["Dentist"])


def test_without_a_saved_password_nothing_is_asked(tmp_path: Path) -> None:
    server = Server(icloud(DENTIST))
    code, out = caldav(tmp_path, server, path=password_file(tmp_path, key="CALDAV_PASSWORD_WORK"))
    assert (code, out) == (1, {"ok": False, "error": "no_password", "detail": "no saved password for family"})
    code, out = caldav(tmp_path, server, path=password_file(tmp_path, mode=0o644))
    assert (code, out["error"]) == (1, "no_password") and "chmod 600" in out["detail"]
    code, out = caldav(tmp_path, server, path=tmp_path / "missing.env")
    assert (code, out["error"]) == (1, "no_password")
    assert server.sent == []


@pytest.mark.parametrize(
    ("saved", "password"),
    [
        ("pa55word'", "pa55word'"),  # a quote of the password's own is kept (a Radicale or Baikal account password)
        ('"pa55word"\'', '"pa55word"\''),
        ("'pa55word'", "pa55word"),  # one matching pair around a hand-edited value is dropped, as keyfile does
        ('" pa55word "', "pa55word"),
        ("''pa55word''", "'pa55word'"),
    ],
)
def test_only_one_matching_pair_of_quotes_is_dropped(tmp_path: Path, saved: str, password: str) -> None:
    path = tmp_path / "calendar.env"
    path.write_text(f"CALDAV_PASSWORD_FAMILY={saved}\n")
    path.chmod(0o600)
    assert helper.load_password(path, "family") == password


def test_the_default_calendar_reads_its_own_key() -> None:
    assert helper.password_key("calendar") == "CALDAV_PASSWORD"
    assert helper.password_key("school-days") == "CALDAV_PASSWORD_SCHOOL_DAYS"


@pytest.mark.parametrize(
    "account",
    [
        ACCOUNT | {"server": "http://caldav.icloud.com"},
        ACCOUNT | {"server": "https://alex:pw@caldav.icloud.com"},  # privacy-ok: a made-up login
        ACCOUNT | {"server": "https://caldav.icloud.com/?x=1"},
        ACCOUNT | {"server": "caldav.icloud.com"},
        ACCOUNT | {"user": ""},
        ACCOUNT | {"user": "alex\n"},
        ACCOUNT | {"calendar": ""},
        {"server": ICLOUD},
    ],
)
def test_a_bad_account_is_refused_before_anything_is_asked(tmp_path: Path, account: dict[str, str]) -> None:
    server = Server(icloud(DENTIST))
    code, out = caldav(tmp_path, server, account=account)
    assert (code, out["error"], out["code"]) == (1, "invalid", "bad_account") and server.sent == []


def test_main_reads_the_account_from_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(helper, "ADDRESS_FILE", password_file(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ACCOUNT) + "\n"))
    argv = ["helper.py", "events", "--from", FROM, "--to", TO, "--zone", "Europe/Amsterdam", "--calendar", "family"]
    monkeypatch.setattr("sys.argv", [*argv, "--caldav"])
    seen: list[str] = []

    def transport(method: str, url: str, headers: dict[str, str], body: bytes, timeout: float, size: int) -> Any:
        seen.append(url)
        raise helper.Unreachable("offline")

    monkeypatch.setattr(helper, "https_transport", transport)
    with pytest.raises(SystemExit) as exc:
        helper.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["error"] == "unreachable"
    assert seen == [f"{ICLOUD}/"]


@pytest.mark.parametrize(
    ("host", "site"),
    [
        ("p42-caldav.icloud.com", "icloud.com"),
        ("Caldav.iCloud.COM.", "icloud.com"),
        ("icloud.com.evil.example", "icloud.com.evil.example"),
        ("caldav.fastmail.com", "caldav.fastmail.com"),
        ("cloud.example.co.uk", "cloud.example.co.uk"),
        ("nas.tail1234.ts.net", "nas.tail1234.ts.net"),
        ("Cloud.Example.COM.", "cloud.example.com"),
    ],
)
def test_only_a_provider_that_answers_from_several_hosts_has_a_site_of_its_own(host: str, site: str) -> None:
    """iCloud answers from pNN-caldav.icloud.com; every other server is its own host only."""
    assert helper.site_of(host) == site


@pytest.mark.parametrize(
    ("server", "other"),
    [
        ("https://nas.tail1234.ts.net", "https://evil.tail9999.ts.net/"),  # another tailnet: a valid certificate too
        ("https://alex.duckdns.org", "https://attacker.duckdns.org/"),  # another customer of a DynDNS service
        ("https://alex.synology.me:5006", "https://mallory.synology.me:5006/"),
        ("https://cloud.example.co.uk", "https://other.example.co.uk/"),
        ("https://caldav.fastmail.com/dav/", "https://evil.fastmail.com/dav/"),
    ],
)
def test_a_self_hosted_server_allows_only_its_own_host(tmp_path: Path, server: str, other: str) -> None:
    client = helper.CalDav(server, "alex", PASSWORD, transport=Server({}), deadline=1e12)
    assert client.allowed(server.rstrip("/") + "/")
    assert not client.allowed(other)
    start = server.rstrip("/") + "/"
    sent = Server({("PROPFIND", start): helper.Answer(301, {"location": other}, b"")})
    code, out = caldav(tmp_path, sent, account=ACCOUNT | {"server": server})
    assert (code, out["error"]) == (1, "invalid")
    assert not any(url.startswith(other) for _, url in sent.asked())  # the password never goes there


def test_an_address_by_number_allows_only_itself() -> None:
    client = helper.CalDav("https://192.168.1.10", "alex", PASSWORD, transport=Server({}), deadline=1e12)
    assert client.allowed("https://192.168.1.10/remote.php/dav/")
    assert not client.allowed("https://192.168.1.11/")
    assert not client.allowed("https://168.1.10/")


class Local(http.server.BaseHTTPRequestHandler):
    paths: ClassVar[list[str]] = []

    def do_PROPFIND(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        Local.paths.append(self.path)
        body = b"<multistatus xmlns='DAV:'>" + b"x" * 2000 + b"</multistatus>"
        self.send_response(207)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "a=b")
        self.end_headers()
        self.wfile.write(body)

    def do_REPORT(self) -> None:
        """A slow server: its answer comes a few bytes at a time, each in time for a read's own timeout."""
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(207)
        self.send_header("Content-Length", "1000")
        self.end_headers()
        try:
            for _ in range(100):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.05)
        except OSError:  # the client gave up
            return

    def log_message(self, *args: Any) -> None:
        return None


class QuickServer(http.server.ThreadingHTTPServer):
    def server_bind(self) -> None:  # HTTPServer looks up the host's full name: ~35 s with slow reverse DNS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", int(self.server_address[1])


@pytest.fixture
def local() -> Iterator[int]:
    server = QuickServer(("127.0.0.1", 0), Local)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield int(server.server_address[1])
    server.shutdown()


def test_a_slow_answer_ends_at_the_time_limit(local: int) -> None:
    """The time limit is the whole request's, not each read's: a server sending a byte at a time is cut off."""

    def connect(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", local, timeout=timeout)

    started = time.monotonic()
    with pytest.raises(helper.Unreachable, match="time"):
        helper.https_transport("REPORT", "https://caldav.example.com/dav/", {}, b"", 0.5, 2000, connect=connect)
    assert time.monotonic() - started < 1.5


def test_the_requests_and_the_expansion_end_before_the_bridge_stops_waiting() -> None:
    """The bridge waits 30 s (calendar_helper_timeout): the helper's own reason comes first, with time to start."""
    assert helper.CALDAV_SECONDS + helper.EXPAND_SECONDS <= 28


def test_the_real_connection_checks_the_certificate_and_the_host_name() -> None:
    connection = helper._https_connection("caldav.example.com", 443, 1.0)  # built only: nothing is connected
    assert isinstance(connection, http.client.HTTPSConnection) and connection.sock is None
    context: ssl.SSLContext = connection._context  # type: ignore[attr-defined]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_the_transport_reads_a_limited_size_and_lowercases_headers(local: int) -> None:
    def connect(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
        assert (host, port) == ("caldav.example.com", 443)
        return http.client.HTTPConnection("127.0.0.1", local, timeout=timeout)

    answer = helper.https_transport(
        "PROPFIND", "https://caldav.example.com/dav/", {"Depth": "0"}, b"<x/>", 5.0, 100, connect=connect
    )
    assert answer.status == 207 and len(answer.body) == 101 and answer.headers["set-cookie"] == "a=b"
    helper.https_transport("PROPFIND", "https://caldav.example.com/dav/x?a=1", {}, b"", 5.0, 100, connect=connect)
    assert Local.paths[-2:] == ["/dav/", "/dav/x?a=1"]
    with pytest.raises(helper.Refused, match="https"):
        helper.https_transport("PROPFIND", "http://caldav.example.com/", {}, b"", 5.0, 100, connect=connect)
