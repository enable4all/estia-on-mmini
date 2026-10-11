"""A pretend Spotify for the music tests and the Gemma test set: speakers, the user's playlists, Liked Songs, search."""

from __future__ import annotations

from typing import Any

from local_assistant.spotify import SpotifyError


class FakeSpotify:
    def __init__(self) -> None:
        self.speakers: list[dict[str, Any]] = [
            {
                "id": "kitchen",
                "name": "Kitchen speaker",
                "is_active": False,
                "is_restricted": False,
                "volume_percent": 80,
                "supports_volume": True,
            },
            {
                "id": "bedroom",
                "name": "Bedroom speaker",
                "is_active": False,
                "is_restricted": False,
                "volume_percent": 20,
                "supports_volume": True,
            },
        ]
        self.asleep: set[str] = set()  # speaker names Spotify cannot see until they are woken
        self.playlists = [
            {
                "name": "Road Trip",
                "uri": "spotify:playlist:road",
                "external_urls": {"spotify": "https://open.spotify.com/playlist/road"},
            },
        ]
        self.liked_tracks = [
            {"name": "Blue in Green", "uri": "spotify:track:big", "artists": [{"name": "Miles Davis"}]}
        ]
        self.found: dict[tuple[str, str], list[dict[str, Any]]] = {
            ("miles davis", "artist"): [
                {
                    "name": "Miles Davis",
                    "uri": "spotify:artist:miles",
                    "external_urls": {"spotify": "https://open.spotify.com/artist/miles"},
                }
            ],
            ("jazz", "playlist"): [
                {
                    "name": "Jazz Classics",
                    "uri": "spotify:playlist:jazz",
                    "external_urls": {"spotify": "https://open.spotify.com/playlist/jazz"},
                }
            ],
        }
        self.playing: dict[str, Any] | None = None
        self.calls: list[tuple[str, Any]] = []
        self.fail: SpotifyError | None = None

    def _check(self, name: str, arg: Any = None) -> None:
        self.calls.append((name, arg))
        if self.fail is not None:
            raise self.fail

    async def devices(self) -> list[dict[str, Any]]:
        self._check("devices")
        return [dict(s) for s in self.speakers if s["name"] not in self.asleep]

    async def player(self) -> dict[str, Any] | None:
        self._check("player")
        return self.playing

    async def my_playlists(self) -> list[dict[str, Any]]:
        self._check("my_playlists")
        return self.playlists

    async def liked(self, limit: int = 50) -> list[dict[str, Any]]:
        self._check("liked")
        return self.liked_tracks

    async def search(self, text: str, kind: str, limit: int = 5) -> list[dict[str, Any]]:
        self._check("search", (text, kind))
        return self.found.get((text.casefold(), kind), [])

    async def transfer(self, device_id: str) -> None:
        self._check("transfer", device_id)
        for s in self.speakers:
            s["is_active"] = s["id"] == device_id

    async def play(self, device_id: str, *, context_uri: str | None = None, uris: list[str] | None = None) -> None:
        self._check("play", (device_id, context_uri, tuple(uris or ())))
        speaker = next(s for s in self.speakers if s["id"] == device_id)
        self.playing = {
            "is_playing": True,
            "device": speaker,
            "item": {"name": "Something", "artists": [{"name": "Someone"}]},
        }

    async def resume(self) -> None:
        self._check("resume")

    async def pause(self) -> None:
        self._check("pause")

    async def skip(self, forward: bool) -> None:
        self._check("skip", forward)

    async def volume(self, percent: int, device_id: str | None = None) -> None:
        self._check("volume", (percent, device_id))
        for s in self.speakers:
            if (device_id is None and s["is_active"]) or s["id"] == device_id:
                s["volume_percent"] = percent
