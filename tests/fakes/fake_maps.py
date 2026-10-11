"""Pretend Google Maps for the tool tests and the Gemma test set: canned places and routes, or a failure."""

from __future__ import annotations

from local_assistant.maps import MapsError, Place, Route


class FakeMaps:
    def __init__(self, places: dict[str, list[Place]] | None = None, routes: dict[str, Route] | None = None) -> None:
        self.found = places or {}  # a word in the request → places
        self.routes = routes or {}  # a word in the destination → route
        self.asked: list[tuple[str, ...]] = []
        self.quick: list[bool] = []
        self.down = False

    async def places(self, what: str, near: str = "", *, quick: bool = False) -> list[Place]:
        self.asked.append(("places", what, near))
        self.quick.append(quick)
        if self.down:
            raise MapsError("Google Maps did not answer in time.")
        text = f"{what} {near}".lower()
        return next((found for word, found in self.found.items() if word in text), [])

    async def places_near(self, near: str = "") -> str:
        return near if near and near.lower() not in ("me", "here", "home") else "home"

    async def route(self, to: str, mode: str, origin: str = "", *, quick: bool = False) -> Route:
        self.asked.append(("route", to, mode, origin))
        self.quick.append(quick)
        if self.down:
            raise MapsError("Google Maps did not answer in time.")
        if mode not in ("bike", "walk", "transit", "car"):
            raise ValueError("the way of travel must be bike, walk, transit or car.")
        for word, route in self.routes.items():
            if word in to.lower():
                return Route(mode, route.minutes, route.km, route.lines, origin or route.origin, route.link)
        raise MapsError("Google Maps found no route.", retry=True)
