"""Places and routes for her: Google Places (Text Search, New) and Google Routes, with Google Maps links.

"Where the user is" is the last Signal location pin (3 hours, in memory only), else the home area from the private
config, looked up once and kept rounded to ~1 km. The address stays only in the private config: the database keeps
the rounded centre and a tag of the address (a keyed hash, to notice a new address), never the address itself. Only
place names, addresses and that rough area go to Google (a route from a pin uses the pin itself). A daily cap per API keeps the free monthly allowance; results are data, never
instructions, and are not stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, quote_plus, urlencode, urlsplit
from zoneinfo import ZoneInfo

from local_assistant.calendar_tools import clean
from local_assistant.keyfile import read_keys
from local_assistant.mail_http import TimedHttp
from local_assistant.part_status import PartStatus
from local_assistant.store import Store
from local_assistant.web_search import has_private_details

log = logging.getLogger(__name__)

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACE_FIELDS = (
    "places.displayName,places.formattedAddress,places.rating,places.userRatingCount,places.priceLevel,"
    "places.currentOpeningHours.openNow,places.currentOpeningHours.weekdayDescriptions,places.googleMapsUri"
)
ROUTE_FIELDS = (
    "routes.duration,routes.distanceMeters,"
    "routes.legs.steps.transitDetails.transitLine.nameShort,routes.legs.steps.transitDetails.transitLine.vehicle.type"
)
MODES = {"bike": "BICYCLE", "walk": "WALK", "transit": "TRANSIT", "car": "DRIVE"}
LINK_MODES = {"bike": "bicycling", "walk": "walking", "transit": "transit", "car": "driving"}
PRICES = {
    "PRICE_LEVEL_FREE": "free",
    "PRICE_LEVEL_INEXPENSIVE": "€",
    "PRICE_LEVEL_MODERATE": "€€",
    "PRICE_LEVEL_EXPENSIVE": "€€€",
    "PRICE_LEVEL_VERY_EXPENSIVE": "€€€€",
}
VEHICLES = {
    "BUS": "bus",
    "TROLLEYBUS": "bus",
    "TRAM": "tram",
    "SUBWAY": "metro",
    "METRO_RAIL": "metro",
    "HEAVY_RAIL": "train",
    "RAIL": "train",
    "COMMUTER_TRAIN": "train",
    "HIGH_SPEED_TRAIN": "train",
    "LONG_DISTANCE_TRAIN": "train",
    "FERRY": "ferry",
}
MAX_PLACES = 5
WORDS_CHARS = 200
NAME_CHARS = 100
ADDRESS_CHARS = 150
PIN_SECONDS = 3 * 3600.0
BIAS_METRES = 3000.0
HOME_AREA_KEY = "maps_home_area"  # "lat,lng" rounded to 2 decimals, and the tag of the address it belongs to
HOME_FAILED_PREFIX = "maps_home_failed_"  # + the day: the tag of an address Google could not place that day
_TAG = re.compile(r"^[0-9a-f]{64}$")
# Signal's location share ends with this link on its own line; a link inside a question is not "where I am"
_PIN = re.compile(r"https://maps\.google\.com/maps\?q=(-?\d{1,2}(?:\.\d+)?)(?:%2C|,)(-?\d{1,3}(?:\.\d+)?)(?![\d.])\s*$")
HOME_WORDS = frozenset({"home", "my home", "my house", "my place", "home address", "at home"})
HERE_WORDS = frozenset({"", "me", "here", "my location", "current location", "where i am", "near me", "nearby"})


class MapsError(Exception):
    def __init__(self, message: str, *, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry  # Gemma may correct the request and ask again in the same answer (no route, unknown start)


@dataclass(frozen=True)
class Place:
    name: str
    address: str
    rating: float | None
    ratings: int | None  # how many ratings
    price: str  # "€€" or ""
    open_now: bool | None
    today: str  # today's opening hours, e.g. "Saturday: 5:00 – 10:00 PM"
    url: str  # a Google Maps link, or ""


@dataclass(frozen=True)
class Route:
    mode: str  # bike, walk, transit, car
    minutes: int
    km: float
    lines: tuple[str, ...]  # by public transport: "tram 13", "metro 52"
    origin: str  # "where you sent your location", "home" or the place the user named
    link: str  # Google Maps directions; "" for a route home whose area is not known (never the address)


class Mapper(Protocol):
    async def places(self, what: str, near: str = "", *, quick: bool = False) -> list[Place]: ...

    async def places_near(self, near: str = "") -> str: ...

    async def route(self, to: str, mode: str, origin: str = "", *, quick: bool = False) -> Route: ...


def find_pin(text: str) -> tuple[float, float] | None:
    """The coordinates of a Signal location pin (Signal sends `https://maps.google.com/maps?q=<lat>%2C<lng>`)."""
    if "?" in text.replace("maps?q=", ""):
        return None  # a question with a link in it, not a location share
    match = _PIN.search(text.strip())
    if match is None:
        return None
    lat, lng = float(match.group(1)), float(match.group(2))
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def _today_name() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def home_tag(address: str, key: str) -> str:
    """The address's tag: HMAC-SHA256 keyed with the Google Maps key, which is never in the database, so the database
    alone cannot confirm a guessed address near the kept centre. A new key reads as a new address: one lookup."""
    return hmac.new(key.encode(), address.strip().encode(), hashlib.sha256).hexdigest()


def forget_home_address(store: Store, home: str, keys_path: Path) -> None:
    """At start: no row keeps the home address, which belongs only in the private config. An earlier version kept it
    beside the home area ("lat,lng|<address>"), in the day's failed-lookup row and in the Directions links of its
    Sources notes. The area of the configured address keeps its centre with the address's tag; any other old area,
    and every failed-lookup row that holds no tag, is deleted (secure_delete overwrites the row); in a note the
    address's link form becomes the kept centre, else "home" (the search index follows); then the log is written
    back into the file at once."""
    changed = False
    home = home.strip()
    key = read_keys(keys_path, ("GOOGLE_MAPS_API_KEY",)).get("GOOGLE_MAPS_API_KEY", "") if home else ""
    area, _, tail = (store.get_state(HOME_AREA_KEY) or "").partition("|")
    if tail and not _TAG.match(tail):
        if home and key and tail == home and "," in area:
            store.set_state(HOME_AREA_KEY, f"{area}|{home_tag(home, key)}")
        else:
            store.delete_state(HOME_AREA_KEY)
        changed = True
    for name, value in store.states_with_prefix(HOME_FAILED_PREFIX):
        if not _TAG.match(value):
            store.delete_state(name)
            changed = True
    if home:
        area, _, tail = (store.get_state(HOME_AREA_KEY) or "").partition("|")
        kept = bool(key) and "," in area and tail == home_tag(home, key)
        spot = quote_plus(area) if kept else "home"
        forms = dict.fromkeys((quote_plus(home), quote(home), quote(home, safe="")))
        changed = store.rewrite_notes(list(forms), spot) > 0 or changed
    if changed:
        store.checkpoint()
        log.info("maps: the home address was removed from the database")


class Maps:
    def __init__(
        self,
        http: TimedHttp,
        keys_path: Path,
        store: Store,
        *,
        home: str,
        daily_cap: int,
        tz: ZoneInfo,
        region: str = "",
        clock: Callable[[], float] = time.time,
        day: Callable[[], str] = _today_name,
        limit_s: float = 8.0,
        quick_limit_s: float = 5.0,
        status: PartStatus | None = None,
    ) -> None:
        self._http = http
        self._keys_path = keys_path
        self._store = store
        self._home = home.strip()
        self._cap = daily_cap
        self._tz = tz
        self._region = region.strip()  # a two-letter country code; empty: none is sent
        self._clock = clock
        self._day = day
        self._limit_s = limit_s
        self._quick_limit_s = quick_limit_s
        self._pin: tuple[float, float, float] | None = None  # lat, lng, when
        self._status = status or PartStatus(None)  # what Google's last real answer was (doctor, /status)

    def set_here(self, lat: float, lng: float) -> None:
        """The user sent a location pin: "near me" for the next 3 hours (memory only, never logged)."""
        self._pin = (lat, lng, self._clock())

    def here(self) -> tuple[float, float] | None:
        if self._pin is None or self._clock() - self._pin[2] > PIN_SECONDS:
            return None
        return self._pin[0], self._pin[1]

    def _pin_label(self) -> str:
        sent = datetime.fromtimestamp(self._pin[2] if self._pin else self._clock(), self._tz)
        return f"where you sent your location at {sent:%H:%M}"

    async def places_near(self, near: str = "") -> str:
        """Where a search for `near` is centred, for the answer: a named place, "home", the pin, or "" unknown."""
        word = " ".join(near.split()).casefold()
        if word and word not in HOME_WORDS | HERE_WORDS:
            return " ".join(near.split())
        if word not in HOME_WORDS and self.here() is not None:
            return self._pin_label()
        return "home" if await self._home_center(quick=False) is not None else ""

    def usage(self) -> str:
        if not self._key():
            return "key missing (scripts/set-web-keys.sh)"
        places, routes = self._used("places"), self._used("routes")
        return f"places {places}/{self._cap}, routes {routes}/{self._cap} today"

    async def places(self, what: str, near: str = "", *, quick: bool = False) -> list[Place]:
        """Up to 5 places for `what`, near a place the user named, else near them (pin) or home."""
        what, near = _words(what, "what to look for"), " ".join(near.split())
        word = near.casefold()
        named = bool(word) and word not in HOME_WORDS | HERE_WORDS
        if named:
            near = _words(near, "the place")
        query = f"{what} near {near}" if named else what
        body: dict[str, Any] = {"textQuery": query, "maxResultCount": MAX_PLACES, "languageCode": "en"}
        if self._region:
            body["regionCode"] = self._region.upper()
        if named:
            center = None
        elif word in HOME_WORDS or self.here() is None:
            center = await self._home_center(quick)
        else:
            center = self._rough(self.here())
        if center is not None:
            body["locationBias"] = {
                "circle": {"center": {"latitude": center[0], "longitude": center[1]}, "radius": BIAS_METRES}
            }
        answer = await self._ask("places", PLACES_URL, PLACE_FIELDS, body, quick)
        items = answer.get("places") if isinstance(answer, dict) else None
        if not isinstance(items, list):
            return []
        weekday = datetime.fromtimestamp(self._clock(), self._tz).strftime("%A")
        return [place for item in items[:MAX_PLACES] if (place := _place(item, weekday)) is not None]

    async def route(self, to: str, mode: str, origin: str = "", *, quick: bool = False) -> Route:
        """Time, distance and (by public transport) lines from `origin`, else from the pin, else from home."""
        mode = mode.strip().casefold()
        if mode not in MODES:
            raise ValueError("the way of travel must be bike, walk, transit or car.")
        to, origin = " ".join(to.split()), " ".join(origin.split())
        to_home = to.casefold() in HOME_WORDS
        if to_home:
            if not self._home:
                raise MapsError("there is no home address in the config.", retry=True)
            to = self._home
        to = _words(to, "where to")
        here = self.here()
        word = origin.casefold()
        named = ""  # a start the user named goes into the link; from the pin none: the phone uses where it is
        from_home = word in HOME_WORDS or (word in HERE_WORDS and here is None)
        if from_home:
            if not self._home:
                raise MapsError(
                    "I don't know where you are: send your location in Signal, or name where you start.", retry=True
                )
            start: dict[str, Any] = {"address": self._home}
            label = "home"
        elif word in HERE_WORDS and here is not None:
            start = {"location": {"latLng": {"latitude": here[0], "longitude": here[1]}}}
            label = self._pin_label()
        else:
            origin = _words(origin, "where from")
            start, label, named = {"address": origin}, origin, origin
        body = {
            "origin": start,
            "destination": {"address": to},
            "travelMode": MODES[mode],
            "languageCode": "en",
        }
        if self._region:
            body["regionCode"] = self._region.lower()  # Routes takes it lowercase, Places uppercase
        answer = await self._ask("routes", ROUTES_URL, ROUTE_FIELDS, body, quick)
        routes = answer.get("routes") if isinstance(answer, dict) else None
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise MapsError("Google Maps found no route.", retry=True)
        first = routes[0]
        seconds = _seconds(first.get("duration"))
        metres = first.get("distanceMeters")
        lines = _lines(first.get("legs"))
        km = round(metres / 1000, 1) if isinstance(metres, int | float) and not isinstance(metres, bool) else 0.0
        # the link is sent and stored as a note: a home end is the rounded centre, never the address (from home
        # without a centre: no origin, the phone uses where it is; to home without one: no link)
        centre = await self._home_center(quick) if to_home or from_home else None
        spot = f"{centre[0]},{centre[1]}" if centre is not None else ""
        start_at = spot if from_home else named
        link = {"api": "1"} | ({"origin": start_at} if start_at else {})
        link |= {"destination": spot if to_home else to, "travelmode": LINK_MODES[mode]}
        url = "" if to_home and not spot else f"https://www.google.com/maps/dir/?{urlencode(link)}"
        return Route(mode, round(seconds / 60), km, lines, label, url)

    @staticmethod
    def _rough(spot: tuple[float, float] | None) -> tuple[float, float] | None:
        return None if spot is None else (round(spot[0], 2), round(spot[1], 2))

    async def _home_center(self, quick: bool) -> tuple[float, float] | None:
        """The home area rounded to ~1 km, looked up once (a failed lookup is not repeated the same day). Kept with
        the address's tag, never the address (no key: nothing is looked up, nothing kept)."""
        if not self._home:
            return None
        key = self._key()
        tag = home_tag(self._home, key) if key else ""
        area, _, kept = (self._store.get_state(HOME_AREA_KEY) or "").partition("|")
        if tag and kept == tag and "," in area:
            lat, lng = area.split(",", 1)
            return float(lat), float(lng)
        failed_key = f"{HOME_FAILED_PREFIX}{self._day()}"
        if tag and self._store.get_state(failed_key) == tag:
            return None
        try:
            answer = await self._ask(
                "places", PLACES_URL, "places.location", {"textQuery": self._home, "maxResultCount": 1}, quick
            )
            spot = answer["places"][0]["location"]
            center = round(float(spot["latitude"]), 2), round(float(spot["longitude"]), 2)
        except (MapsError, LookupError, TypeError, ValueError) as exc:
            log.warning("maps: the home area could not be looked up: %s", type(exc).__name__)
            if tag:
                self._store.set_state(failed_key, tag)
            return None
        self._store.set_state(HOME_AREA_KEY, f"{center[0]},{center[1]}|{tag}")
        return center

    def _key(self) -> str:
        return read_keys(self._keys_path, ("GOOGLE_MAPS_API_KEY",)).get("GOOGLE_MAPS_API_KEY", "")

    def _counter(self, api: str) -> str:
        return f"maps_{api}_{self._day()}"

    def _used(self, api: str) -> int:
        try:
            return int(self._store.get_state(self._counter(api)) or 0)
        except ValueError:
            return 0

    async def _ask(self, api: str, url: str, fields: str, body: dict[str, Any], quick: bool) -> Any:
        key = self._key()
        if not key:
            self._status.failed("places", "no_key")
            raise MapsError("there is no Google Maps key yet: the user runs scripts/set-web-keys.sh")
        if self._used(api) >= self._cap:
            what = "place searches" if api == "places" else "route" + (" searches" if self._cap != 1 else " search")
            raise MapsError(f"the daily limit of {self._cap} {what} is reached; it starts again tomorrow.")
        self._store.set_state(self._counter(api), str(self._used(api) + 1))
        headers = {"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields}
        limit = self._quick_limit_s if quick else self._limit_s
        try:
            answer = await asyncio.wait_for(
                self._http("POST", url, headers, json.dumps(body).encode(), socket_timeout=limit), limit
            )
        except Exception as exc:  # network, timeout, an unreadable body
            log.warning("maps: %s failed: %s", api, type(exc).__name__)
            self._status.failed("places", "no_answer")
            raise MapsError("Google Maps did not answer in time.") from None
        if answer.status == 200:
            self._status.ok("places")
            return answer.body
        log.warning("maps: %s answered HTTP %d", api, answer.status)
        self._status.failed("places", "busy" if answer.status == 429 else "refused", answer.status)
        if answer.status in (401, 403) or (answer.status == 400 and "API_KEY_INVALID" in json.dumps(answer.body)):
            raise MapsError("the Google key was refused (is the API switched on and the key allowed for it?).")
        if answer.status == 429:
            raise MapsError("Google's limit for today is reached.")
        raise MapsError(f"Google Maps did not answer (HTTP {answer.status}).")


def _words(text: str, what: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise ValueError(f"{what} is empty.")
    if len(text) > WORDS_CHARS:
        raise ValueError(f"{what} is too long (at most {WORDS_CHARS} characters).")
    if has_private_details(text):
        raise ValueError(
            "not looked up: the words hold an email address, an account number or a long number such as a phone "
            "number, and private details never go to Google. Leave them out."
        )
    return text


def _place(item: object, weekday: str) -> Place | None:
    if not isinstance(item, dict):
        return None
    shown = item.get("displayName")
    raw_name = shown.get("text") if isinstance(shown, dict) else None
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    address = item.get("formattedAddress")
    rating = item.get("rating")
    count = item.get("userRatingCount")
    hours = item.get("currentOpeningHours")
    open_now = hours.get("openNow") if isinstance(hours, dict) else None
    days = hours.get("weekdayDescriptions") if isinstance(hours, dict) else None
    listed = [day for day in days if isinstance(day, str)] if isinstance(days, list) else []
    today = next((day for day in listed if day.startswith(f"{weekday}:")), "")  # by name: the order may vary
    url = item.get("googleMapsUri")
    return Place(
        name=clean(raw_name[: NAME_CHARS * 4], NAME_CHARS),
        address=clean(address[: ADDRESS_CHARS * 4], ADDRESS_CHARS) if isinstance(address, str) else "",
        rating=float(rating) if isinstance(rating, int | float) and not isinstance(rating, bool) else None,
        ratings=count if isinstance(count, int) and not isinstance(count, bool) else None,
        price=PRICES.get(str(item.get("priceLevel")), ""),
        open_now=open_now if isinstance(open_now, bool) else None,
        today=clean(today, 80),
        url=url if isinstance(url, str) and _is_maps_link(url) else "",
    )


def _is_maps_link(url: str) -> bool:
    """Only plain https links on Google's own hosts reach the Sources note."""
    if any(ch.isspace() or ord(ch) < 32 or ch in '<>"\\' for ch in url) or len(url) > 500:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    return parts.scheme == "https" and (host == "google.com" or host.endswith(".google.com") or host == "goo.gl")


def _seconds(value: object) -> float:
    """Routes gives "1490s"."""
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?s", value):
        return float(value[:-1])
    return 0.0


def _lines(legs: object) -> tuple[str, ...]:
    """The public transport lines in order, e.g. ("tram 13", "metro 52")."""
    found: list[str] = []
    for leg in legs if isinstance(legs, list) else []:
        steps = leg.get("steps") if isinstance(leg, dict) else None
        for step in steps if isinstance(steps, list) else []:
            details = step.get("transitDetails") if isinstance(step, dict) else None
            line = details.get("transitLine") if isinstance(details, dict) else None
            if not isinstance(line, dict):
                continue
            vehicle = line.get("vehicle")
            kind = vehicle.get("type") if isinstance(vehicle, dict) else None
            name = line.get("nameShort")
            label = " ".join(
                part
                for part in (VEHICLES.get(str(kind), str(kind).lower() if kind else ""), clean(str(name or ""), 20))
                if part
            )
            if label:
                found.append(label)
    return tuple(found[:6])
