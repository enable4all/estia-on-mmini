"""Wake a Bose SoundTouch speaker from standby over its local API (port 8090), so Spotify can see it again.

Best effort: Bose ended its cloud service on 2026-05-06 and documents the local API only loosely. Only the private
addresses from [spotify.bose] are ever called (the config refuses others), with short timeouts and no proxies.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request

TIMEOUT = 3.0


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Only the configured address is ever contacted: a redirect fails instead of being followed."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_KEY = '<key state="{state}" sender="Gabbo">POWER</key>'


async def wake(host: str, *, port: int = 8090) -> bool:
    """Power the speaker on when it is in standby; True when a power key was sent, False when it was on already or
    did not answer."""

    def call() -> bool:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
        base = f"http://{host}:{port}"
        try:
            with opener.open(f"{base}/now_playing", timeout=TIMEOUT) as response:
                now = response.read(64 * 1024).decode(errors="replace")
            if 'source="STANDBY"' not in now:
                return False
            for state in ("press", "release"):
                request = urllib.request.Request(
                    f"{base}/key",
                    data=_KEY.format(state=state).encode(),
                    method="POST",
                    headers={"Content-Type": "application/xml"},
                )
                with opener.open(request, timeout=TIMEOUT) as response:
                    response.read(1024)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    return await asyncio.to_thread(call)
