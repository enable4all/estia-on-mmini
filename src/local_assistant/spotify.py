"""A small client for Spotify's Web API (the player, search, the user's playlists and Liked Songs).

Standard library only, on mail_http (no proxies, no redirects): only https://api.spotify.com/v1/ is called. A 401
refreshes the access token once; a 429 waits once (Retry-After, at most 5 s); errors carry Spotify's reason.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

from local_assistant.mail_http import Http, http
from local_assistant.part_status import PartStatus
from local_assistant.spotify_auth import SpotifyAuthError, SpotifySignInExpired, SpotifyTokens

API = "https://api.spotify.com/v1"
MAX_WAIT = 5.0


class SpotifyError(Exception):
    def __init__(self, message: str, status: int = 0, reason: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class Spotify:
    def __init__(
        self,
        tokens: SpotifyTokens,
        *,
        http: Http = http,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        status: PartStatus | None = None,
    ) -> None:
        self._tokens = tokens
        self._http = http
        self._sleep = sleep
        self._status = status or PartStatus(None)  # what Spotify's last real answer was (doctor, /status)

    async def request(
        self, method: str, path: str, *, query: dict[str, Any] | None = None, body: dict[str, Any] | None = None
    ) -> Any:
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(2):
            token = await self._token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            try:
                result = await self._http(method, url, headers, data if method != "GET" else None)
            except OSError as exc:
                self._status.failed("spotify", "no_answer")
                raise SpotifyError(f"Spotify is not reachable ({type(exc).__name__})") from exc
            if result.status == 401 and attempt == 0:
                self._tokens.forget()
                continue
            if result.status == 429 and attempt == 0:
                await self._sleep(min(MAX_WAIT, _seconds(result.headers.get("retry-after"))))
                continue
            break
        if result.status == 429:
            self._status.failed("spotify", "busy", 429)
        elif result.status == 401 or result.status >= 500:
            self._status.failed("spotify", "refused", result.status)
        else:  # also "no active device" or "not allowed now": Spotify answered with the sign-in working
            self._status.ok("spotify")
        if 200 <= result.status < 300:
            return result.body
        error = result.body.get("error") if isinstance(result.body, dict) else None
        reason = str(error.get("reason") or "") if isinstance(error, dict) else ""
        message = str(error.get("message") or "") if isinstance(error, dict) else ""
        raise SpotifyError(f"Spotify HTTP {result.status} {reason or message}".strip(), result.status, reason)

    async def _token(self) -> str:
        """The access token; a sign-in that ended or a token service out of reach is recorded."""
        try:
            return await self._tokens.access_token()
        except SpotifySignInExpired:
            self._status.failed("spotify", "sign_in")
            raise
        except SpotifyAuthError as exc:
            self._status.failed("spotify", "no_answer" if isinstance(exc.__cause__, OSError) else "refused")
            raise

    async def devices(self) -> list[dict[str, Any]]:
        body = await self.request("GET", "/me/player/devices")
        items = body.get("devices") if isinstance(body, dict) else None
        return [d for d in items if isinstance(d, dict)] if isinstance(items, list) else []

    async def player(self) -> dict[str, Any] | None:
        """What is playing where, or None when nothing is (204)."""
        body = await self.request("GET", "/me/player")
        return body if isinstance(body, dict) else None

    async def my_playlists(self) -> list[dict[str, Any]]:
        body = await self.request("GET", "/me/playlists", query={"limit": 50})
        items = body.get("items") if isinstance(body, dict) else None
        return [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []

    async def liked(self, limit: int = 50) -> list[dict[str, Any]]:
        body = await self.request("GET", "/me/tracks", query={"limit": limit})
        items = body.get("items") if isinstance(body, dict) else None
        tracks = [item.get("track") for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        return [t for t in tracks if isinstance(t, dict)]

    async def search(self, text: str, kind: str, limit: int = 5) -> list[dict[str, Any]]:
        body = await self.request("GET", "/search", query={"q": text, "type": kind, "limit": min(limit, 10)})
        group = body.get(f"{kind}s") if isinstance(body, dict) else None
        items = group.get("items") if isinstance(group, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    async def transfer(self, device_id: str) -> None:
        await self.request("PUT", "/me/player", body={"device_ids": [device_id], "play": False})

    async def play(self, device_id: str, *, context_uri: str | None = None, uris: list[str] | None = None) -> None:
        body: dict[str, Any] = {}
        if context_uri:
            body["context_uri"] = context_uri
        if uris:
            body["uris"] = uris[:100]
        await self.request("PUT", "/me/player/play", query={"device_id": device_id}, body=body)

    async def resume(self) -> None:
        await self.request("PUT", "/me/player/play")

    async def pause(self) -> None:
        await self.request("PUT", "/me/player/pause")

    async def skip(self, forward: bool) -> None:
        await self.request("POST", "/me/player/next" if forward else "/me/player/previous")

    async def volume(self, percent: int, device_id: str | None = None) -> None:
        query: dict[str, Any] = {"volume_percent": max(0, min(100, percent))}
        if device_id:
            query["device_id"] = device_id
        await self.request("PUT", "/me/player/volume", query=query)


def _seconds(value: str | None) -> float:
    try:
        return max(0.0, float(value or 1))
    except ValueError:
        return 1.0
