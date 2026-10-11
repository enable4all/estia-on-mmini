"""Spotify sign-in and tokens: Authorization Code with PKCE (the user's own app; a Client ID, no secret).

Spotify has no device-code sign-in and allows only a loopback redirect, so on the headless Mini the login prints the
sign-in address, the user signs in on another computer, the browser fails to open http://127.0.0.1:8888/callback
there, and the user pastes that address back. The refresh token lives in a private file (0600, folder 0700); Spotify
ends it 6 months after the sign-in (refreshing does not extend it), so the sign-in time is kept with it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_assistant.config import DEFAULT_NAME
from local_assistant.mail_http import Http, HttpResult, http

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-playback-state user-modify-playback-state playlist-read-private user-library-read"
LIFETIME_SECONDS = 182 * 86_400  # refresh tokens end 6 months after the sign-in (Spotify, 2026-06-18)
EARLY_REFRESH = 60.0


class SpotifyAuthError(Exception):
    """The sign-in or a token refresh failed."""


class SpotifySignInExpired(SpotifyAuthError):
    """Never signed in, or Spotify ended the sign-in (6 months, or revoked): run scripts/spotify-login.sh."""


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def code_from(pasted: str, state: str) -> str:
    """The code from the address the browser was sent to; the state must be ours."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted.strip()).query)
    if query.get("state", [""])[0] != state:
        raise SpotifyAuthError("the pasted address is not from this sign-in (state differs): try again")
    if "error" in query:
        raise SpotifyAuthError(f"Spotify said: {query['error'][0]}")
    code = query.get("code", [""])[0]
    if not code:
        raise SpotifyAuthError("the pasted address has no code: copy the whole address from the browser")
    return code


@dataclass
class _Access:
    token: str
    expires_at: float


class SpotifyTokens:
    """The refresh token in a private file, read fresh on every refresh; the access token in memory."""

    def __init__(
        self, path: Path, client_id: str, *, http: Http = http, clock: Callable[[], float] = time.time
    ) -> None:
        self.path = path
        self.client_id = client_id
        self._http = http
        self._clock = clock
        self._access: _Access | None = None

    def signed_in(self) -> bool:
        data = self._load()
        return isinstance(data.get("refresh_token"), str) and "ended_at" not in data

    def signed_in_at(self) -> float | None:
        """When the user last signed in (scripts/spotify-login.sh; a refresh keeps it), or None."""
        authorized = self._load().get("authorized_at")
        return float(authorized) if isinstance(authorized, int | float) else None

    def expires_at(self) -> float | None:
        """When Spotify ends the sign-in (6 months after it), or None when not signed in."""
        signed_in = self.signed_in_at()
        return None if signed_in is None else signed_in + LIFETIME_SECONDS

    async def exchange(self, code: str, verifier: str) -> None:
        """Finish the sign-in: the code from the redirect for the first tokens."""
        result = await self._post(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": self.client_id,
                "code_verifier": verifier,
            }
        )
        body = result.body if isinstance(result.body, dict) else {}
        refresh = body.get("refresh_token")
        if result.status != 200 or not isinstance(refresh, str) or not isinstance(body.get("access_token"), str):
            raise SpotifyAuthError(f"the sign-in failed: HTTP {result.status} {body.get('error', '')}".strip())
        self._save({"refresh_token": refresh, "scope": str(body.get("scope", "")), "authorized_at": self._clock()})
        self._keep(body)

    async def access_token(self) -> str:
        """A valid access token; refreshes when needed (keeping the old refresh token unless Spotify sends one)."""
        if self._access is not None and self._access.expires_at - EARLY_REFRESH > self._clock():
            return self._access.token
        saved = self._load()
        refresh = saved.get("refresh_token")
        if not isinstance(refresh, str) or "ended_at" in saved:
            raise SpotifySignInExpired("Spotify is not signed in")
        result = await self._post(
            {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": self.client_id}
        )
        body = result.body if isinstance(result.body, dict) else {}
        if result.status == 200 and isinstance(body.get("access_token"), str):
            if isinstance(body.get("refresh_token"), str) and body["refresh_token"] != refresh:
                self._save({**saved, "refresh_token": body["refresh_token"]})
            return self._keep(body)
        if body.get("error") == "invalid_grant":
            self._access = None
            self._save({**saved, "ended_at": self._clock()})
            raise SpotifySignInExpired("Spotify ended the sign-in")
        raise SpotifyAuthError(f"token endpoint HTTP {result.status}: {body.get('error', 'no error code')}")

    def forget(self) -> None:
        """Drop the access token (after the API answered 401)."""
        self._access = None

    def _keep(self, body: dict[str, Any]) -> str:
        token = str(body["access_token"])
        self._access = _Access(token, self._clock() + float(body.get("expires_in", 3600)))
        return token

    async def _post(self, form: dict[str, str]) -> HttpResult:
        data = urllib.parse.urlencode(form).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        try:
            return await self._http("POST", TOKEN_URL, headers, data)
        except OSError as exc:
            raise SpotifyAuthError(f"Spotify's sign-in service is not reachable ({type(exc).__name__})") from exc

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # missing, unreadable, not UTF-8, not JSON: not signed in
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, self.path)
        self.path.chmod(0o600)


async def login(
    tokens: SpotifyTokens,
    client_id: str = "",
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    name: str = DEFAULT_NAME,
) -> None:
    """The paste sign-in for a headless Mini (scripts/spotify-login.sh); `name`: hers, who reminds the user."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    write("1. Open this address on a computer with a browser and sign in to Spotify (your Premium account):")
    write(authorize_url(client_id or tokens.client_id, challenge, state))
    write("2. The browser then fails to open a 127.0.0.1 page: that is expected. Copy that page's whole address.")
    pasted = read("3. Paste the address here: ")
    await tokens.exchange(code_from(pasted, state), verifier)
    write(f"Spotify: signed in (valid for 6 months; {name} reminds you before it ends).")
