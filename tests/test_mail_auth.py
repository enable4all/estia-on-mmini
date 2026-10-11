import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from local_assistant.config import CalendarSource, Config, MailAccount
from local_assistant.mail_auth import (
    CALENDAR_SCOPES,
    SCOPES,
    AccessTokens,
    ConsentMissing,
    MailAuthError,
    SignInExpired,
    TokenStore,
    device_login,
)
from tests.conftest import ME
from tests.fakes.fake_http import FakeHttp

TENANT = "11111111-2222-4333-8444-555555555555"
CLIENT = "66666666-7777-4888-9999-000000000000"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
CODE_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode"
ME_URL = "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"


def config(tmp_path: Path) -> Config:
    return Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        mail_tenant_id=TENANT,
        mail_client_id=CLIENT,
        mail_accounts=(MailAccount("family", "info@example.com"),),
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1_791_452_400.0

    def __call__(self) -> float:
        return self.now


def test_token_store_is_private_and_atomic(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    assert store.refresh_token("info@example.com") is None
    store.save("Info@Example.com", "rt-1")
    store.save("contact@example.app", "rt-2")
    assert store.refresh_token("info@example.com") == "rt-1"
    path = tmp_path / "data" / "mail-tokens.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert set(json.loads(path.read_text())) == {"info@example.com", "contact@example.app"}
    assert not list(path.parent.glob("*.tmp"))


async def test_access_token_is_refreshed_cached_and_rotated(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    store.save("info@example.com", "rt-1")
    http, clock = FakeHttp(), Clock()
    http.add(TOKEN_URL, 200, {"access_token": "at-1", "expires_in": 3600, "refresh_token": "rt-2"})
    http.add(TOKEN_URL, 200, {"access_token": "at-2", "expires_in": 3600})
    tokens = AccessTokens(config(tmp_path), store, http=http, clock=clock)
    assert await tokens.get("info@example.com") == "at-1"
    assert await tokens.get("info@example.com") == "at-1"  # cached
    assert store.refresh_token("info@example.com") == "rt-2"  # rotated
    method, url, form = http.requests[0]
    assert (method, url, form["grant_type"], form["refresh_token"]) == ("POST", TOKEN_URL, "refresh_token", "rt-1")
    assert form["client_id"] == CLIENT and "Mail.Read" in form["scope"] and "Write" not in form["scope"]
    clock.now += 3600 - 60  # within two minutes of expiry: refreshed early
    assert await tokens.get("info@example.com") == "at-2"
    tokens.forget("info@example.com")
    assert len(http.requests) == 2


async def test_an_expired_sign_in_is_its_own_error(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    http = FakeHttp()
    tokens = AccessTokens(config(tmp_path), store, http=http, clock=Clock())
    with pytest.raises(SignInExpired):
        await tokens.get("info@example.com")  # never signed in
    store.save("info@example.com", "rt-1")
    http.add(TOKEN_URL, 400, {"error": "invalid_grant", "error_description": "AADSTS700082: expired"})
    with pytest.raises(SignInExpired):
        await tokens.get("info@example.com")


async def test_other_token_errors(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    store.save("info@example.com", "rt-1")
    http = FakeHttp()
    http.add(TOKEN_URL, 503, None)
    tokens = AccessTokens(config(tmp_path), store, http=http, clock=Clock())
    with pytest.raises(MailAuthError, match="HTTP 503"):
        await tokens.get("info@example.com")


async def test_device_login_saves_the_token_of_the_right_account(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    http = FakeHttp()
    http.add(
        CODE_URL,
        200,
        {
            "device_code": "dc",
            "user_code": "ABC123",
            "verification_uri": "https://microsoft.com/devicelogin",
            "interval": 1,
            "expires_in": 900,
            "message": "Go to https://microsoft.com/devicelogin and enter ABC123",
        },
    )
    http.add(TOKEN_URL, 400, {"error": "authorization_pending"})
    http.add(TOKEN_URL, 400, {"error": "slow_down"})
    http.add(
        TOKEN_URL,
        200,
        {"access_token": "at", "expires_in": 3600, "refresh_token": "rt-new", "scope": "Mail.Read User.Read"},
    )
    http.add(ME_URL, 200, {"mail": "Info@Example.com", "userPrincipalName": "info@example.onmicrosoft.com"})
    shown: list[str] = []
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    signed_in = await device_login(
        config(tmp_path), "info@example.com", store, http=http, out=shown.append, sleep=sleep
    )
    assert signed_in == "info@example.com"
    assert store.refresh_token("info@example.com") == "rt-new"
    assert shown[0] == "Go to https://microsoft.com/devicelogin and enter ABC123"
    assert waits == [1, 1, 6]  # slow_down adds 5 seconds
    assert http.requests[-1][2]["authorization"] == "Bearer at"


async def test_device_login_refuses_another_account(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    http = FakeHttp()
    http.add(
        CODE_URL,
        200,
        {
            "device_code": "dc",
            "user_code": "X",
            "verification_uri": "u",
            "interval": 1,
            "expires_in": 900,
            "message": "m",
        },
    )
    http.add(TOKEN_URL, 200, {"access_token": "at", "expires_in": 3600, "refresh_token": "rt"})
    http.add(ME_URL, 200, {"mail": "someone.else@example.com", "userPrincipalName": "someone.else@example.com"})

    async def sleep(seconds: float) -> None:
        return None

    with pytest.raises(MailAuthError, match=r"signed in as someone\.else@example\.com"):
        await device_login(config(tmp_path), "info@example.com", store, http=http, out=lambda _: None, sleep=sleep)
    assert store.refresh_token("info@example.com") is None


async def test_device_login_declined_or_unknown_address(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    http = FakeHttp()
    http.add(
        CODE_URL,
        200,
        {
            "device_code": "dc",
            "user_code": "X",
            "verification_uri": "u",
            "interval": 1,
            "expires_in": 900,
            "message": "m",
        },
    )
    http.add(TOKEN_URL, 400, {"error": "authorization_declined"})

    async def sleep(seconds: float) -> None:
        return None

    with pytest.raises(MailAuthError, match="authorization_declined"):
        await device_login(config(tmp_path), "info@example.com", store, http=http, out=lambda _: None, sleep=sleep)
    with pytest.raises(MailAuthError, match="not a configured mailbox"):
        await device_login(config(tmp_path), "nobody@example.com", store, http=http, out=lambda _: None, sleep=sleep)


async def test_tokens_are_kept_per_scope(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    store.save("info@example.com", "rt-1")
    http, clock = FakeHttp(), Clock()
    http.add(TOKEN_URL, 200, {"access_token": "at-mail", "expires_in": 3600})
    http.add(TOKEN_URL, 200, {"access_token": "at-calendar", "expires_in": 3600, "refresh_token": "rt-2"})
    http.add(TOKEN_URL, 200, {"access_token": "at-calendar-2", "expires_in": 3600})
    tokens = AccessTokens(config(tmp_path), store, http=http, clock=clock)
    assert await tokens.get("info@example.com") == "at-mail"
    assert await tokens.get("info@example.com", CALENDAR_SCOPES) == "at-calendar"  # never the mail token
    assert await tokens.get("info@example.com") == "at-mail"  # still cached
    assert [form["scope"] for _, _, form in http.requests] == [SCOPES, CALENDAR_SCOPES]
    assert CALENDAR_SCOPES == "https://graph.microsoft.com/Calendars.Read offline_access"
    assert store.refresh_token("info@example.com") == "rt-2"  # the same entry: Microsoft keeps the old one valid
    tokens.forget("info@example.com", CALENDAR_SCOPES)
    assert await tokens.get("info@example.com") == "at-mail"
    assert await tokens.get("info@example.com", CALENDAR_SCOPES) == "at-calendar-2"
    assert len(http.requests) == 3


@pytest.mark.parametrize(
    "answer",
    [
        {"error": "invalid_grant", "suberror": "consent_required", "error_codes": [65001]},
        {"error": "invalid_grant", "error_codes": [65001]},
        {"error": "invalid_grant", "suberror": "consent_required"},
        {"error": "consent_required"},
        {"error": "invalid_grant", "error_codes": [70000]},  # AADSTS70000: the scope is not granted (or no longer)
    ],
)
async def test_missing_consent_for_the_calendar_is_not_an_expired_sign_in(
    tmp_path: Path, answer: dict[str, object]
) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    store.save("info@example.com", "rt-1")
    http = FakeHttp()
    http.add(TOKEN_URL, 400, answer)
    tokens = AccessTokens(config(tmp_path), store, http=http, clock=Clock())
    with pytest.raises(ConsentMissing) as missing:
        await tokens.get("info@example.com", CALENDAR_SCOPES)
    assert not isinstance(missing.value, SignInExpired)
    with pytest.raises(SignInExpired):  # mail is unchanged: the same answer pauses the mailbox as before
        await tokens.get("info@example.com")
    assert store.refresh_token("info@example.com") == "rt-1"  # the refresh token is kept


async def test_an_expired_sign_in_is_expired_for_the_calendar_too(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    store.save("info@example.com", "rt-1")
    http = FakeHttp()
    http.add(TOKEN_URL, 400, {"error": "invalid_grant", "error_codes": [700082]})
    tokens = AccessTokens(config(tmp_path), store, http=http, clock=Clock())
    with pytest.raises(SignInExpired):
        await tokens.get("info@example.com", CALENDAR_SCOPES)


def code_answer(http: FakeHttp) -> None:
    http.add(
        CODE_URL,
        200,
        {
            "device_code": "dc",
            "user_code": "X",
            "verification_uri": "u",
            "interval": 1,
            "expires_in": 900,
            "message": "m",
        },
    )
    http.add(TOKEN_URL, 200, {"access_token": "at", "expires_in": 3600, "refresh_token": "rt"})
    http.add(ME_URL, 200, {"mail": "info@example.com"})


async def sleepless(seconds: float) -> None:
    return None


@pytest.mark.parametrize(("mail_only", "calendar"), [(False, True), (True, False)])
async def test_device_login_asks_for_the_calendar_when_the_mailbox_has_one(
    tmp_path: Path, mail_only: bool, calendar: bool
) -> None:
    cfg = replace(
        config(tmp_path),
        calendar_enabled=True,
        calendar_sources=(CalendarSource("family"), CalendarSource("office", mailbox="family")),
    )
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    http = FakeHttp()
    code_answer(http)
    shown: list[str] = []
    await device_login(
        cfg, "info@example.com", store, http=http, out=shown.append, sleep=sleepless, mail_only=mail_only
    )
    scope = http.requests[0][2]["scope"]
    assert scope.split() == [
        *SCOPES.split()[:2],
        *(["https://graph.microsoft.com/Calendars.Read"] if calendar else []),
        "offline_access",
    ]
    assert "Write" not in scope and "Shared" not in scope
    hint = [line for line in shown if "--mail-only" in line]
    assert len(hint) == (1 if calendar else 0)
    if calendar:
        assert "scripts/mail-login.sh info@example.com --mail-only" in hint[0] and "Calendars.Read" in hint[0]
        assert shown.index(hint[0]) < shown.index("m")  # said before the sign-in starts


async def test_device_login_without_a_calendar_asks_for_mail_only(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "data" / "mail-tokens.json")
    http = FakeHttp()
    code_answer(http)
    shown: list[str] = []
    cfg = replace(config(tmp_path), calendar_enabled=True)  # the Google calendar only
    await device_login(cfg, "info@example.com", store, http=http, out=shown.append, sleep=sleepless)
    assert http.requests[0][2]["scope"] == SCOPES and shown == ["m"]


@pytest.mark.parametrize(("mail_only", "calendar"), [(False, True), (True, False)])
async def test_only_a_sign_in_sets_its_time_and_renewing_the_token_keeps_it(
    tmp_path: Path, mail_only: bool, calendar: bool
) -> None:
    cfg = replace(
        config(tmp_path), calendar_enabled=True, calendar_sources=(CalendarSource("office", mailbox="family"),)
    )
    clock = Clock()
    store = TokenStore(tmp_path / "data" / "mail-tokens.json", clock=clock)
    store.save("info@example.com", "rt-old")  # signed in before sign-in times were kept
    assert store.signed_in_at("info@example.com") is None
    http = FakeHttp()
    code_answer(http)
    await device_login(
        cfg, "info@example.com", store, http=http, out=lambda _: None, sleep=sleepless, mail_only=mail_only
    )
    signed_in = clock.now
    assert store.signed_in_at("info@example.com") == signed_in
    assert store.signed_in_at("info@example.com", calendar=True) == (signed_in if calendar else None)
    clock.now += 3600  # mail's refresh an hour later; Microsoft sends a new refresh token
    http.answers[TOKEN_URL] = []
    http.add(TOKEN_URL, 200, {"access_token": "at-mail", "expires_in": 3600, "refresh_token": "rt-renewed"})
    await AccessTokens(cfg, store, http=http, clock=clock).get("info@example.com")
    assert store.refresh_token("info@example.com") == "rt-renewed"
    assert store.signed_in_at("info@example.com") == signed_in  # renewing is no sign-in
    assert store.signed_in_at("info@example.com", calendar=True) == (signed_in if calendar else None)


async def test_a_mail_only_sign_in_keeps_the_calendars_sign_in_time(tmp_path: Path) -> None:
    cfg = replace(
        config(tmp_path), calendar_enabled=True, calendar_sources=(CalendarSource("office", mailbox="family"),)
    )
    clock = Clock()
    store = TokenStore(tmp_path / "data" / "mail-tokens.json", clock=clock)
    for mail_only in (False, True):
        http = FakeHttp()
        code_answer(http)
        await device_login(
            cfg, "info@example.com", store, http=http, out=lambda _: None, sleep=sleepless, mail_only=mail_only
        )
        clock.now += 60
    assert store.signed_in_at("info@example.com") == Clock().now + 60
    assert store.signed_in_at("info@example.com", calendar=True) == Clock().now  # consent, once given, stays
