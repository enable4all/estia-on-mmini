import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

import pytest

from local_assistant.maps import PLACES_URL, ROUTES_URL, Maps, MapsError, find_pin, forget_home_address
from local_assistant.part_status import PartStatus, Record
from local_assistant.store import Store
from tests.fakes.fake_http import FakeHttp

HOME = "Home Street 1, 1000 Town, NL"
PLACES = {
    "places": [
        {
            "displayName": {"text": "Trattoria Uno", "languageCode": "en"},
            "formattedAddress": "Example Street 5, 1050 Town",
            "rating": 4.6,
            "userRatingCount": 812,
            "priceLevel": "PRICE_LEVEL_MODERATE",
            "currentOpeningHours": {
                "openNow": True,
                "weekdayDescriptions": [
                    f"{day}: 5:00 – 10:00 PM"
                    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
                ],
            },
            "googleMapsUri": "https://maps.google.com/?cid=123",
        },
        {
            "displayName": {"text": "Bar Due"},
            "formattedAddress": "Other Street 2",
            "googleMapsUri": "javascript:alert(1)",
        },
    ]
}
HOME_LOCATION = {"places": [{"location": {"latitude": 52.37312, "longitude": 4.89201}}]}
ROUTE = {
    "routes": [
        {
            "duration": "1490s",
            "distanceMeters": 7420,
            "legs": [
                {
                    "steps": [
                        {},
                        {"transitDetails": {"transitLine": {"nameShort": "13", "vehicle": {"type": "TRAM"}}}},
                        {"transitDetails": {"transitLine": {"nameShort": "52", "vehicle": {"type": "SUBWAY"}}}},
                    ]
                }
            ],
        }
    ]
}


class Clock:
    def __init__(self) -> None:
        self.now = 1_791_640_000.0  # Saturday 10 Oct 2026, ~14:00 in Amsterdam

    def __call__(self) -> float:
        return self.now


def keys(tmp_path: Path, text: str = "GOOGLE_MAPS_API_KEY=g-key\n") -> Path:
    path = tmp_path / "web.env"
    path.write_text(text)
    os.chmod(path, 0o600)
    return path


def maps(
    tmp_path: Path,
    http: FakeHttp,
    store: Store,
    *,
    cap: int = 30,
    clock: Clock | None = None,
    region: str = "",
    zone: str = "Europe/Amsterdam",
    status: PartStatus | None = None,
    **kw: str,
) -> Maps:
    return Maps(
        http,
        keys(tmp_path, **kw),
        store,
        home=HOME,
        daily_cap=cap,
        tz=ZoneInfo(zone),
        region=region,
        clock=clock or Clock(),
        day=lambda: "2026-10-10",
        status=status,
    )


def sent_json(http: FakeHttp, index: int) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(http.sent[index][1] or b"{}")
    return body


async def test_places_near_home_with_rating_price_and_todays_hours(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)  # the home area, looked up once
    http.add(PLACES_URL, 200, PLACES)
    found = await maps(tmp_path, http, store).places("italian restaurant")
    first = found[0]
    assert (first.name, first.address, first.rating, first.ratings, first.price) == (
        "Trattoria Uno",
        "Example Street 5, 1050 Town",
        4.6,
        812,
        "€€",
    )
    assert first.open_now is True and first.today == "Saturday: 5:00 – 10:00 PM"
    assert first.url == "https://maps.google.com/?cid=123"
    assert found[1].url == "" and found[1].rating is None  # an odd link is dropped
    home_lookup, search = sent_json(http, 0), sent_json(http, 1)
    assert home_lookup["textQuery"] == HOME and http.sent[0][0]["X-Goog-FieldMask"] == "places.location"
    assert search["textQuery"] == "italian restaurant" and search["maxResultCount"] == 5
    assert search["locationBias"] == {"circle": {"center": {"latitude": 52.37, "longitude": 4.89}, "radius": 3000.0}}
    headers = http.sent[1][0]
    assert headers["X-Goog-Api-Key"] == "g-key" and "places.currentOpeningHours.openNow" in headers["X-Goog-FieldMask"]


async def test_the_home_area_is_looked_up_once(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    search = maps(tmp_path, http, store)
    await search.places("pizza")
    await search.places("sushi")
    assert [sent_json(http, n).get("textQuery") for n in range(3)] == [HOME, "pizza", "sushi"]


async def test_a_place_named_by_the_user_goes_into_the_question(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    await maps(tmp_path, http, store).places("cafe", near="the city centre")
    assert sent_json(http, 0)["textQuery"] == "cafe near the city centre"  # no home lookup needed
    assert "locationBias" not in sent_json(http, 0) and len(http.requests) == 1


async def test_a_pin_is_where_the_user_is_for_three_hours(tmp_path: Path, store: Store) -> None:
    clock = Clock()
    http = FakeHttp()
    http.add(PLACES_URL, 200, PLACES)
    search = maps(tmp_path, http, store, clock=clock)
    search.set_here(52.37312, 4.89201)
    await search.places("coffee")
    assert sent_json(http, 0)["locationBias"]["circle"]["center"] == {"latitude": 52.37, "longitude": 4.89}
    clock.now += 3 * 3600 + 1
    assert search.here() is None


async def test_a_route_by_transit_from_the_pin_with_lines_and_a_link(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    search = maps(tmp_path, http, store)
    search.set_here(52.37312, 4.89201)
    route = await search.route("Schiphol Airport", "transit")
    assert (route.minutes, route.km, route.lines) == (25, 7.4, ("tram 13", "metro 52"))
    assert route.origin.startswith("where you sent your location at ")
    assert route.link == "https://www.google.com/maps/dir/?api=1&destination=Schiphol+Airport&travelmode=transit"
    body = sent_json(http, 0)
    assert body["origin"] == {"location": {"latLng": {"latitude": 52.37312, "longitude": 4.89201}}}
    assert body["destination"] == {"address": "Schiphol Airport"} and body["travelMode"] == "TRANSIT"
    assert "routes.duration" in http.sent[0][0]["X-Goog-FieldMask"]


async def test_a_route_by_bike_from_home_or_a_named_place(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, {"routes": [{"duration": "600s", "distanceMeters": 2500}]})
    http.add(PLACES_URL, 200, HOME_LOCATION)
    search = maps(tmp_path, http, store)
    home = await search.route("Vondelpark", "bike")
    assert home.origin == "home" and home.lines == () and (home.minutes, home.km) == (10, 2.5)
    assert sent_json(http, 0)["origin"] == {"address": HOME} and sent_json(http, 0)["travelMode"] == "BICYCLE"
    assert "origin=52.37%2C4.89" in home.link and "travelmode=bicycling" in home.link
    named = await search.route("Vondelpark", "walk", origin="Centraal Station")
    assert named.origin == "Centraal Station" and sent_json(http, 2)["origin"] == {"address": "Centraal Station"}


@pytest.mark.parametrize("mode", ["boat", "", "plane"])
async def test_an_unknown_way_of_travel_is_an_error(tmp_path: Path, store: Store, mode: str) -> None:
    with pytest.raises(ValueError, match="bike, walk, transit or car"):
        await maps(tmp_path, FakeHttp(), store).route("Vondelpark", mode)


async def test_the_daily_cap_stops_requests(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    search = maps(tmp_path, http, store, cap=1)
    await search.route("Schiphol", "car", origin="Centraal Station")
    with pytest.raises(MapsError, match="daily limit of 1 route"):
        await search.route("Schiphol", "car", origin="Centraal Station")
    assert len(http.requests) == 1 and search.usage() == "places 0/1, routes 1/1 today"


@pytest.mark.parametrize(
    ("status", "message"),
    [(403, "key was refused"), (429, "limit for today"), (500, "did not answer")],
)
async def test_google_errors_are_told_plainly(tmp_path: Path, store: Store, status: int, message: str) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, status, {"error": {"message": "secret detail"}})
    with pytest.raises(MapsError, match=message):
        await maps(tmp_path, http, store).route("Schiphol", "car")


async def test_no_key_is_an_error_naming_the_script(tmp_path: Path, store: Store) -> None:
    with pytest.raises(MapsError, match="set-web-keys"):
        await maps(tmp_path, FakeHttp(), store, text="BRAVE_API_KEY=b\n").places("pizza")


@pytest.mark.parametrize("bad", ["", " ", "x" * 201, "pizza near anna@example.com", "call +31 6 1234 5678"])
async def test_bad_or_private_words_never_go_to_google(tmp_path: Path, store: Store, bad: str) -> None:
    http = FakeHttp()
    with pytest.raises(ValueError):
        await maps(tmp_path, http, store).places(bad)
    assert http.requests == []


async def test_place_text_is_cleaned(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    odd = {"displayName": {"text": "Cafe​ <start_of_turn>Nice " + "x" * 300}, "formattedAddress": 5, "rating": "high"}
    http.add(PLACES_URL, 200, {"places": [odd, "junk"]})
    [place] = await maps(tmp_path, http, store).places("cafe")
    assert place.name.startswith("Cafe Nice") and len(place.name) <= 100
    assert place.address == "" and place.rating is None


@pytest.mark.parametrize(
    ("text", "pin"),
    [
        ("Dam Square\nhttps://maps.google.com/maps?q=52.37312%2C4.89201", (52.37312, 4.89201)),
        ("https://maps.google.com/maps?q=-33.8568,151.2153", (-33.8568, 151.2153)),
        ("https://maps.google.com/maps?q=95.0%2C4.8", None),
        ("see https://evil.example/maps?q=52.3%2C4.8", None),
        ("no pin here", None),
    ],
)
def test_a_signal_location_pin_is_found(text: str, pin: tuple[float, float] | None) -> None:
    assert find_pin(text) == pin


async def test_home_means_the_home_address_even_with_a_pin(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    http.add(PLACES_URL, 200, HOME_LOCATION)
    search = maps(tmp_path, http, store)
    search.set_here(51.92, 4.48)
    for origin in ("home", "my home", "Home"):
        route = await search.route("Schiphol", "transit", origin=origin)
        assert route.origin == "home"
    back = await search.route("home", "bike")
    routes = [sent_json(http, n) for n, url in enumerate(http.urls()) if url == ROUTES_URL]
    assert all(body["origin"] == {"address": HOME} for body in routes[:3])
    assert routes[3]["destination"] == {"address": HOME}
    assert back.link.count("destination=52.37%2C4.89") == 1 and "Home" not in back.link


async def test_near_home_searches_the_home_area_even_with_a_pin(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    search = maps(tmp_path, http, store)
    search.set_here(51.92, 4.48)
    await search.places("cafe", near="home")
    assert sent_json(http, 1)["locationBias"]["circle"]["center"] == {"latitude": 52.37, "longitude": 4.89}


async def test_routes_and_places_ask_for_the_configured_region(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    search = maps(tmp_path, http, store, region="NL")
    await search.route("the city centre", "walk")
    assert sent_json(http, 0)["regionCode"] == "nl"  # Routes takes it lowercase
    await search.places("cafe")
    assert sent_json(http, 2)["regionCode"] == "NL"  # Places uppercase; the home lookup (1) names none


async def test_without_a_region_none_is_sent(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    search = maps(tmp_path, http, store)
    await search.route("the city centre", "walk")
    await search.places("cafe")
    assert "regionCode" not in sent_json(http, 0) and "regionCode" not in sent_json(http, 2)
    assert sent_json(http, 0)["languageCode"] == "en"


async def test_where_near_me_was_is_told(tmp_path: Path, store: Store) -> None:
    clock = Clock()
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    http.add(ROUTES_URL, 200, ROUTE)
    search = maps(tmp_path, http, store, clock=clock)
    assert (await search.places_near()) == "home"
    search.set_here(52.37, 4.89)
    assert (await search.places_near()).startswith("where you sent your location at ")
    route = await search.route("Schiphol", "transit")
    assert route.origin.startswith("where you sent your location at ")


async def test_a_failed_home_lookup_is_not_repeated_today(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, {"places": []})  # Google cannot place the address
    search = maps(tmp_path, http, store)
    await search.places("pizza")
    await search.places("sushi")
    assert [sent_json(http, n).get("textQuery") for n in range(len(http.requests))] == [HOME, "pizza", "sushi"]


async def test_todays_hours_are_found_by_the_day_name(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    shifted = {
        "displayName": {"text": "Odd"},
        "currentOpeningHours": {"weekdayDescriptions": ["Saturday: 9 AM – 5 PM", "Sunday: Closed"]},
    }
    http.add(PLACES_URL, 200, {"places": [shifted]})
    [place] = await maps(tmp_path, http, store).places("odd")
    assert place.today == "Saturday: 9 AM – 5 PM"


async def test_todays_hours_follow_the_configured_time_zone(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    # Saturday afternoon in Amsterdam is already Sunday in Auckland
    [first, _] = await maps(tmp_path, http, store, zone="Pacific/Auckland").places("italian")
    assert first.today == "Sunday: 5:00 – 10:00 PM"


async def test_an_invalid_key_says_the_key_was_refused(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 400, {"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
    with pytest.raises(MapsError, match="key was refused"):
        await maps(tmp_path, http, store).route("Schiphol", "car")


async def test_no_route_can_be_tried_again_but_an_outage_cannot(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, {"routes": []})
    with pytest.raises(MapsError) as no_route:
        await maps(tmp_path, http, store).route("Nowhere", "car")
    assert no_route.value.retry
    down = FakeHttp()
    down.add(ROUTES_URL, 503, None)
    with pytest.raises(MapsError) as outage:
        await maps(tmp_path, down, store).route("Schiphol", "car")
    assert not outage.value.retry


@pytest.mark.parametrize(
    ("text", "pin"),
    [
        ("https://maps.google.com/maps?q=52.1%2C1234.5", None),  # not a longitude
        ("how far is https://maps.google.com/maps?q=52.37%2C4.89 from me?", None),  # a link inside a question
        ("Dam\nDam 1, Amsterdam\nhttps://maps.google.com/maps?q=52.37312%2C4.89201", (52.37312, 4.89201)),
    ],
)
def test_only_a_location_share_is_a_pin(text: str, pin: tuple[float, float] | None) -> None:
    assert find_pin(text) == pin


NOW = 1_791_452_400.0


def recorder(tmp_path: Path) -> PartStatus:
    return PartStatus(tmp_path / "status.json", clock=lambda: NOW)


async def test_google_answers_are_recorded_and_our_own_limits_are_not(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    status = recorder(tmp_path)
    search = maps(tmp_path, http, store, cap=1, status=status)
    await search.route("Schiphol", "car", origin="Centraal Station")
    assert status.snapshot() == {"places": Record(ok=NOW)}
    with pytest.raises(MapsError, match="daily limit"):
        await search.route("Schiphol", "car", origin="Centraal Station")  # our own cap: Google was not asked
    with pytest.raises(ValueError):
        await search.places("pizza near anna@example.com")
    assert status.snapshot() == {"places": Record(ok=NOW)}


@pytest.mark.parametrize(
    ("answer", "record"),
    [
        ((403, {"error": {"message": "secret detail"}}), Record(failed=NOW, reason="refused", code=403)),
        ((400, {"error": {"status": "API_KEY_INVALID"}}), Record(failed=NOW, reason="refused", code=400)),
        ((429, None), Record(failed=NOW, reason="busy", code=429)),
        ((500, None), Record(failed=NOW, reason="refused", code=500)),
        (TimeoutError(), Record(failed=NOW, reason="no_answer")),
    ],
)
async def test_google_failures_are_recorded(tmp_path: Path, store: Store, answer: object, record: Record) -> None:
    http = FakeHttp()
    if isinstance(answer, BaseException):
        http.fail(PLACES_URL, answer)
    else:
        assert isinstance(answer, tuple)
        http.add(PLACES_URL, *answer)
    status = recorder(tmp_path)
    with pytest.raises(MapsError):
        await maps(tmp_path, http, store, status=status).places("pizza", "Town Centre")
    assert status.snapshot() == {"places": record}
    assert "secret detail" not in (tmp_path / "status.json").read_text()


async def test_a_missing_google_key_is_recorded(tmp_path: Path, store: Store) -> None:
    status = recorder(tmp_path)
    with pytest.raises(MapsError):
        await maps(tmp_path, FakeHttp(), store, status=status, text="BRAVE_API_KEY=b\n").places("pizza", "Town")
    assert status.snapshot() == {"places": Record(failed=NOW, reason="no_key")}


# ------------------------------------------------------------------ the home address stays in the private config


def stored_values(store: Store) -> list[str]:
    return [str(row[0]) for row in store._conn.execute("SELECT value FROM state")]


def database_bytes(tmp_path: Path) -> bytes:
    folder = tmp_path / "data"
    return b"".join(path.read_bytes() for path in sorted(folder.glob("assistant.db*")))


async def test_the_home_area_is_kept_without_the_address(tmp_path: Path, store: Store) -> None:
    """Only the rounded centre and a tag of the address (to notice a new one) are kept, never the address."""
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    await maps(tmp_path, http, store).places("pizza")
    area = store.get_state("maps_home_area") or ""
    centre, _, tag = area.partition("|")
    assert centre == "52.37,4.89" and len(tag) == 64 and all(ch in "0123456789abcdef" for ch in tag)
    assert not any(HOME in value or "Home Street" in value for value in stored_values(store))
    await maps(tmp_path, http, store).places("sushi")  # the same address: no new lookup
    assert [sent_json(http, n).get("textQuery") for n in range(len(http.requests))] == [HOME, "pizza", "sushi"]


async def test_a_new_home_address_is_looked_up_again(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, HOME_LOCATION)
    http.add(PLACES_URL, 200, PLACES)
    await maps(tmp_path, http, store).places("pizza")
    moved = Maps(http, keys(tmp_path), store, home="New Road 9, 2000 City, NL", daily_cap=30, tz=ZoneInfo("UTC"))
    await moved.places("sushi")
    asked = [sent_json(http, n).get("textQuery") for n in range(len(http.requests))]
    assert asked == [HOME, "pizza", "New Road 9, 2000 City, NL", "sushi"]
    assert not any("New Road" in value for value in stored_values(store))


async def test_a_failed_home_lookup_keeps_no_address(tmp_path: Path, store: Store) -> None:
    http = FakeHttp()
    http.add(PLACES_URL, 200, {"places": []})
    await maps(tmp_path, http, store).places("pizza")
    assert store.get_state("maps_home_failed_2026-10-10")  # the day's mark is there
    assert not any("Home Street" in value for value in stored_values(store))


def test_at_start_an_address_kept_by_an_earlier_version_is_removed(tmp_path: Path, store: Store) -> None:
    """The area of the configured address keeps its centre (with the tag); the failed-lookup rows go; afterwards the
    address is in no byte of the database or its log (secure_delete, then the log is written back)."""
    store.set_state("maps_home_area", f"52.37,4.89|{HOME}")
    store.set_state("maps_home_failed_2026-10-09", HOME)
    store.set_state("maps_home_failed_2026-10-10", HOME)
    store.set_state("maps_places_2026-10-10", "3")  # the day's counter stays
    assert HOME.encode() in database_bytes(tmp_path)
    forget_home_address(store, HOME, keys(tmp_path))
    area = store.get_state("maps_home_area") or ""
    assert area.startswith("52.37,4.89|") and HOME not in area
    assert store.get_state("maps_home_failed_2026-10-09") is None
    assert store.get_state("maps_home_failed_2026-10-10") is None
    assert store.get_state("maps_places_2026-10-10") == "3"
    assert HOME.encode() not in database_bytes(tmp_path)


async def test_after_the_tidy_the_centre_is_used_without_a_lookup(tmp_path: Path, store: Store) -> None:
    store.set_state("maps_home_area", f"52.37,4.89|{HOME}")
    forget_home_address(store, HOME, keys(tmp_path))
    http = FakeHttp()
    http.add(PLACES_URL, 200, PLACES)
    await maps(tmp_path, http, store).places("pizza")
    assert [sent_json(http, n).get("textQuery") for n in range(len(http.requests))] == ["pizza"]
    assert sent_json(http, 0)["locationBias"]["circle"]["center"] == {"latitude": 52.37, "longitude": 4.89}


@pytest.mark.parametrize(
    ("home", "key_text"),
    [("", "GOOGLE_MAPS_API_KEY=g-key\n"), ("Other Lane 3", "GOOGLE_MAPS_API_KEY=g-key\n"), (HOME, "")],
    ids=["maps off", "another address", "no key"],
)
def test_at_start_an_old_area_that_cannot_be_kept_goes(tmp_path: Path, store: Store, home: str, key_text: str) -> None:
    store.set_state("maps_home_area", f"52.37,4.89|{HOME}")
    forget_home_address(store, home, keys(tmp_path, key_text))
    assert store.get_state("maps_home_area") is None
    assert HOME.encode() not in database_bytes(tmp_path)


def test_a_kept_tag_is_left_as_it_is(tmp_path: Path, store: Store) -> None:
    tag = "ab" * 32
    store.set_state("maps_home_area", f"52.37,4.89|{tag}")
    store.set_state("maps_home_failed_2026-10-10", tag)
    forget_home_address(store, HOME, keys(tmp_path))
    assert store.get_state("maps_home_area") == f"52.37,4.89|{tag}"
    assert store.get_state("maps_home_failed_2026-10-10") == tag


def address_forms(address: str) -> list[bytes]:
    return [form.encode() for form in (address, quote_plus(address), quote(address), quote(address, safe=""))]


async def test_a_route_to_or_from_home_keeps_the_address_out_of_the_link(tmp_path: Path, store: Store) -> None:
    """The Directions link is sent as a note and stored: a home end is the rounded centre, never the address."""
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    http.add(PLACES_URL, 200, HOME_LOCATION)
    search = maps(tmp_path, http, store)
    back = await search.route("home", "bike")
    out = await search.route("Vondelpark", "walk", origin="home")
    assert sent_json(http, 0)["destination"] == {"address": HOME}  # the route itself is from the address
    assert "destination=52.37%2C4.89" in back.link and "origin=52.37%2C4.89" in out.link
    for link in (back.link, out.link):
        assert not any(form.decode() in link for form in address_forms(HOME)) and "Street" not in link


async def test_without_a_home_centre_a_route_home_has_no_link_and_from_home_no_origin(
    tmp_path: Path, store: Store
) -> None:
    http = FakeHttp()
    http.add(ROUTES_URL, 200, ROUTE)
    http.add(PLACES_URL, 200, {"places": []})  # Google cannot place the address
    search = maps(tmp_path, http, store)
    back = await search.route("home", "bike")
    out = await search.route("Vondelpark", "walk", origin="home")
    assert back.link == ""
    assert out.link == "https://www.google.com/maps/dir/?api=1&destination=Vondelpark&travelmode=walking"


def directions_note(store: Store, address: str, *, plus: bool = True) -> int:
    encoded = quote_plus(address) if plus else quote(address, safe="")
    link = f"https://www.google.com/maps/dir/?api=1&origin={encoded}&destination=Vondelpark&travelmode=bicycling"
    conversation = store.current_conversation()
    return store.add_outgoing(conversation.id, "aci-1", "note", f"Sources:\n- Directions: {link}", None)


def fts_hits(store: Store, words: str) -> int:
    return int(
        store._conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?", (words,)).fetchone()[0]
    )


@pytest.mark.parametrize("plus", [True, False], ids=["plus", "percent"])
def test_at_start_directions_notes_lose_the_address(tmp_path: Path, store: Store, plus: bool) -> None:
    """Directions notes an earlier version sent hold the address in their link: at start that part becomes the
    rounded centre (kept) or "home", the search index follows, and no byte of the database or its log holds it."""
    store.set_state("maps_home_area", f"52.37,4.89|{HOME}")
    note = directions_note(store, HOME, plus=plus)
    other = store.add_outgoing(store.current_conversation().id, "aci-1", "note", "Sources:\n- Other Lane 3", None)
    words = "street AND town" if plus else "20street AND 20town"  # the search index's words for each form
    assert fts_hits(store, words) == 1
    forget_home_address(store, HOME, keys(tmp_path))
    text = str(store._conn.execute("SELECT text FROM messages WHERE id = ?", (note,)).fetchone()[0])
    assert "origin=52.37%2C4.89&destination=Vondelpark" in text
    assert store._conn.execute("SELECT text FROM messages WHERE id = ?", (other,)).fetchone()[0].endswith("Lane 3")
    assert fts_hits(store, words) == 0 and fts_hits(store, "vondelpark") == 1
    data = database_bytes(tmp_path)
    assert not any(form in data for form in address_forms(HOME))


def test_at_start_a_directions_note_without_a_kept_centre_says_home(tmp_path: Path, store: Store) -> None:
    note = directions_note(store, HOME)
    forget_home_address(store, HOME, keys(tmp_path))
    text = str(store._conn.execute("SELECT text FROM messages WHERE id = ?", (note,)).fetchone()[0])
    assert "origin=home&destination=Vondelpark" in text
    assert not any(form in database_bytes(tmp_path) for form in address_forms(HOME))
