"""Microsoft sign-in for the mailboxes: device-code login (once per mailbox) and token refresh.

Delegated, read-only (`Mail.Read`, `User.Read`, `offline_access`; `Calendars.Read` too for a mailbox whose
calendar is in [[calendar.sources]]). Refresh tokens live in a private file (0600, folder 0700) and are
rotated when Microsoft returns a new one; access tokens stay in memory, one per mailbox and scope. Mail asks
for its own scopes, unchanged; a calendar asks for `Calendars.Read` alone from the same refresh token, so a
mailbox signed in before its calendar was added keeps its mail whatever the calendar gets. A refused refresh
(`invalid_grant`: password or MFA reset, revoked, long unused) is `SignInExpired`, which pauses that mailbox
until the user signs in again; for the calendar, a refusal for lack of consent (AADSTS65001, or AADSTS70000: the
scope is not granted) is `ConsentMissing` instead: an administrator grants it in Entra (no new sign-in), or a new
sign-in asks for it. Each mailbox's entry keeps when it was last signed in (and last with Calendars.Read), apart from
the token's renewals, so doctor never takes mail's hourly renewal for the sign-in that fixes a failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_assistant import prompts
from local_assistant.config import Config
from local_assistant.mail_http import Http, HttpResult, http

SCOPES = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access"
CALENDAR_SCOPE = "https://graph.microsoft.com/Calendars.Read"  # read-only: never Calendars.ReadWrite
CALENDAR_SCOPES = f"{CALENDAR_SCOPE} offline_access"  # a calendar's own token request
AUTHORITY = "https://login.microsoftonline.com"
ME_URL = "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
EARLY_REFRESH = 120.0  # renew access tokens two minutes before they expire
_EXPIRED = frozenset({"invalid_grant", "interaction_required", "consent_required"})
_SIGNED_IN = {False: "signed_in_at", True: "calendar_signed_in_at"}  # a mailbox entry's sign-in times
_NO_CONSENT = frozenset({65001, 70000})  # AADSTS65001: no consent to the scope; AADSTS70000: scope not granted


class MailAuthError(Exception):
    """Sign-in or token problem; the message is safe to log (no tokens)."""


class SignInExpired(MailAuthError):
    """The mailbox needs a new device-code sign-in."""

    def __init__(self, address: str) -> None:
        super().__init__(f"{address} needs a new sign-in")
        self.address = address


class ConsentMissing(MailAuthError):
    """The mailbox's sign-in has no consent for the calendar yet: an administrator grants it, or a new sign-in."""

    def __init__(self, address: str) -> None:
        super().__init__(f"{address} has no consent for its calendar yet")
        self.address = address


def _consent_missing(body: dict[str, Any]) -> bool:
    codes = body.get("error_codes")
    return (
        body.get("error") == "consent_required"
        or body.get("suberror") == "consent_required"
        or (isinstance(codes, list) and any(code in _NO_CONSENT for code in codes))
    )


class TokenStore:
    """Refresh tokens per mailbox address in one private JSON file, read fresh on every use."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock

    def refresh_token(self, address: str) -> str | None:
        entry = self._load().get(address.lower())
        token = entry.get("refresh_token") if isinstance(entry, dict) else None
        return token if isinstance(token, str) and token else None

    def signed_in_at(self, address: str, *, calendar: bool = False) -> float | None:
        """When the mailbox was last signed in (scripts/mail-login.sh; renewing its token is no sign-in), or None;
        with `calendar`, the last sign-in that asked for Calendars.Read too."""
        entry = self._load().get(address.lower())
        when = entry.get(_SIGNED_IN[calendar]) if isinstance(entry, dict) else None
        return float(when) if isinstance(when, int | float) and not isinstance(when, bool) else None

    def save(self, address: str, refresh_token: str, *, signed_in: bool = False, calendar: bool = False) -> None:
        """Keep the mailbox's refresh token: a renewal (the sign-in times stay) or a sign-in (`signed_in`, with
        Calendars.Read when `calendar`)."""
        data = self._load()
        now = self._clock()
        old = data.get(address.lower())
        entry: dict[str, Any] = {"refresh_token": refresh_token, "updated_at": now}
        if isinstance(old, dict):
            entry |= {key: old[key] for key in _SIGNED_IN.values() if key in old}
        if signed_in:
            entry[_SIGNED_IN[False]] = now
            if calendar:
                entry[_SIGNED_IN[True]] = now
        data[address.lower()] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, self.path)
        self.path.chmod(0o600)

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


@dataclass
class _Cached:
    token: str
    expires_at: float


class AccessTokens:
    def __init__(
        self, config: Config, store: TokenStore, *, http: Http = http, clock: Callable[[], float] = time.time
    ) -> None:
        self._token_url = f"{AUTHORITY}/{config.mail_tenant_id}/oauth2/v2.0/token"
        self._client_id = config.mail_client_id
        self._store = store
        self._http = http
        self._clock = clock
        self._cache: dict[tuple[str, str], _Cached] = {}  # (mailbox, scope): a calendar never gets mail's token

    async def get(self, address: str, scope: str = SCOPES) -> str:
        """A valid access token for the mailbox and scope; refreshes (and rotates the refresh token) when needed."""
        mailbox = address.lower()
        key = (mailbox, scope)
        cached = self._cache.get(key)
        if cached is not None and cached.expires_at - EARLY_REFRESH > self._clock():
            return cached.token
        refresh = self._store.refresh_token(mailbox)
        if refresh is None:
            raise SignInExpired(mailbox)
        result = await _post_form(
            self._http,
            self._token_url,
            {"grant_type": "refresh_token", "client_id": self._client_id, "refresh_token": refresh, "scope": scope},
        )
        body = result.body if isinstance(result.body, dict) else {}
        if result.status == 200 and isinstance(body.get("access_token"), str):
            token = str(body["access_token"])
            self._cache[key] = _Cached(token, self._clock() + float(body.get("expires_in", 3600)))
            if isinstance(body.get("refresh_token"), str):
                self._store.save(mailbox, body["refresh_token"])
            return token
        if scope != SCOPES and _consent_missing(body):  # checked first: Microsoft calls it invalid_grant too
            self._cache.pop(key, None)
            raise ConsentMissing(mailbox)
        if body.get("error") in _EXPIRED:
            self._cache.pop(key, None)
            raise SignInExpired(mailbox)
        raise MailAuthError(f"token endpoint HTTP {result.status}: {body.get('error', 'no error code')}")

    def forget(self, address: str, scope: str = SCOPES) -> None:
        """Drop the cached access token (after Graph answered 401)."""
        self._cache.pop((address.lower(), scope), None)


async def device_login(
    config: Config,
    address: str,
    store: TokenStore,
    *,
    http: Http = http,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    mail_only: bool = False,
) -> str:
    """Device-code sign-in for one configured mailbox; saves its refresh token only when the signed-in
    account is that mailbox. Returns the signed-in address. When the mailbox's calendar is in
    [[calendar.sources]] it asks for Calendars.Read too (one sign-in grants both), unless `mail_only`: a tenant
    that wants an administrator's approval for it would otherwise block the mail sign-in as well."""
    wanted = address.lower()
    account = next((account for account in config.mail_accounts if account.address == wanted), None)
    if account is None:
        raise MailAuthError(f"{address} is not a configured mailbox (see [[mail.accounts]] in the config)")
    calendar = next((source for source in config.calendars if source.mailbox == account.name), None)
    scope = SCOPES
    if calendar is not None and not mail_only:
        scope = SCOPES.replace(" offline_access", f" {CALENDAR_SCOPE} offline_access")
        out(prompts.MAIL_LOGIN_CALENDAR.format(address=wanted, calendar=calendar.name))
    base = f"{AUTHORITY}/{config.mail_tenant_id}/oauth2/v2.0"
    code = await _post_form(http, f"{base}/devicecode", {"client_id": config.mail_client_id, "scope": scope})
    body = code.body if isinstance(code.body, dict) else {}
    if code.status != 200 or "device_code" not in body:
        raise MailAuthError(f"device code request failed: HTTP {code.status} {body.get('error', '')}".strip())
    out(str(body.get("message") or f"Go to {body.get('verification_uri')} and enter {body.get('user_code')}"))
    interval = float(body.get("interval", 5))
    deadline = time.monotonic() + float(body.get("expires_in", 900))
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": config.mail_client_id,
        "device_code": str(body["device_code"]),
    }
    while time.monotonic() < deadline:
        await sleep(interval)
        result = await _post_form(http, f"{base}/token", form)
        token = result.body if isinstance(result.body, dict) else {}
        error = token.get("error")
        if result.status == 200 and isinstance(token.get("access_token"), str):
            signed_in = await _signed_in_address(http, token["access_token"])
            if wanted not in signed_in:
                raise MailAuthError(
                    f"signed in as {signed_in[0] if signed_in else 'an unknown account'}, expected {wanted}"
                )
            if not isinstance(token.get("refresh_token"), str):
                raise MailAuthError("no refresh token returned: is offline_access granted?")
            store.save(wanted, token["refresh_token"], signed_in=True, calendar=scope != SCOPES)
            return wanted
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            raise MailAuthError(f"sign-in stopped: {error or f'HTTP {result.status}'}")
    raise MailAuthError("the sign-in code expired; run the command again")


async def _signed_in_address(http: Http, access_token: str) -> list[str]:
    result = await http("GET", ME_URL, {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}, None)
    body = result.body if isinstance(result.body, dict) else {}
    return [str(body[key]).lower() for key in ("mail", "userPrincipalName") if body.get(key)]


async def _post_form(http: Http, url: str, data: dict[str, str]) -> HttpResult:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    return await http("POST", url, headers, urllib.parse.urlencode(data).encode())
