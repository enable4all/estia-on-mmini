import contextlib
import json
from pathlib import Path

import pytest

from local_assistant.mail_http import HttpResult
from local_assistant.part_status import PartStatus, Record
from local_assistant.spotify import API, Spotify, SpotifyError
from local_assistant.spotify_auth import TOKEN_URL, SpotifyAuthError, SpotifySignInExpired, SpotifyTokens
from tests.fakes.fake_http import FakeHttp

CLIENT = "0123456789abcdef0123456789abcdef"


async def signed_in(tmp_path: Path, http: FakeHttp) -> SpotifyTokens:
    http.add(TOKEN_URL, 200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    tokens = SpotifyTokens(tmp_path / "t.json", CLIENT, http=http)
    await tokens.exchange("C", "V")
    return tokens


async def test_a_401_refreshes_once(tmp_path: Path) -> None:
    http = FakeHttp()
    tokens = await signed_in(tmp_path, http)
    http.answers[TOKEN_URL] = [HttpResult(200, {}, {"access_token": "A2", "expires_in": 3600})]
    http.add(f"{API}/me/player/devices", 401, {"error": {"status": 401, "message": "expired"}})
    http.add(f"{API}/me/player/devices", 200, {"devices": [{"id": "d1", "name": "Kitchen speaker"}]})
    devices = await Spotify(tokens, http=http).devices()
    assert devices == [{"id": "d1", "name": "Kitchen speaker"}]
    assert [r[2]["authorization"] for r in http.requests if r[1].startswith(API)] == ["Bearer A1", "Bearer A2"]


async def test_a_429_waits_once_then_gives_up(tmp_path: Path) -> None:
    http = FakeHttp()
    tokens = await signed_in(tmp_path, http)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    http.add(f"{API}/me/player/next", 429, None, {"retry-after": "30"})
    with pytest.raises(SpotifyError) as caught:
        await Spotify(tokens, http=http, sleep=sleep).skip(forward=True)
    assert waits == [5.0] and caught.value.status == 429  # waits at most 5 s, once


async def test_nothing_playing_is_none_and_errors_carry_the_reason(tmp_path: Path) -> None:
    http = FakeHttp()
    tokens = await signed_in(tmp_path, http)
    http.add(f"{API}/me/player", 204, None)
    spotify = Spotify(tokens, http=http)
    assert await spotify.player() is None
    http.add(f"{API}/me/player/pause", 404, {"error": {"status": 404, "message": "x", "reason": "NO_ACTIVE_DEVICE"}})
    with pytest.raises(SpotifyError) as caught:
        await spotify.pause()
    assert caught.value.reason == "NO_ACTIVE_DEVICE" and caught.value.status == 404


async def test_play_names_the_device_and_what_to_play(tmp_path: Path) -> None:
    http = FakeHttp()
    tokens = await signed_in(tmp_path, http)
    sent: list[bytes | None] = []
    original = http.__call__

    async def recording(method: str, url: str, headers: dict[str, str], body: bytes | None) -> HttpResult:
        if url.startswith(f"{API}/me/player/play"):
            sent.append(body)
        return await original(method, url, headers, body)

    http.add(f"{API}/me/player/play?device_id=d1", 204, None)
    await Spotify(tokens, http=recording).play("d1", context_uri="spotify:playlist:p1")
    assert sent and json.loads(sent[0] or b"{}") == {"context_uri": "spotify:playlist:p1"}


async def test_search_caps_at_ten_results(tmp_path: Path) -> None:
    http = FakeHttp()
    tokens = await signed_in(tmp_path, http)
    http.add(f"{API}/search?q=jazz&type=playlist&limit=10", 200, {"playlists": {"items": [None, {"uri": "u"}]}})
    assert await Spotify(tokens, http=http).search("jazz", "playlist", limit=50) == [{"uri": "u"}]


NOW = 1_791_452_400.0


def recorder(tmp_path: Path) -> PartStatus:
    return PartStatus(tmp_path / "status.json", clock=lambda: NOW)


@pytest.mark.parametrize(
    ("status", "body", "record"),
    [
        (200, {"devices": []}, Record(ok=NOW)),
        (404, {"error": {"status": 404, "reason": "NO_ACTIVE_DEVICE"}}, Record(ok=NOW)),  # Spotify answered
        (429, None, Record(failed=NOW, reason="busy", code=429)),
        (401, {"error": {"status": 401}}, Record(failed=NOW, reason="refused", code=401)),
        (502, None, Record(failed=NOW, reason="refused", code=502)),
    ],
)
async def test_spotify_answers_are_recorded(tmp_path: Path, status: int, body: object, record: Record) -> None:
    http = FakeHttp()
    tokens = await signed_in(tmp_path, http)
    http.add(f"{API}/me/player/devices", status, body)
    recorded = recorder(tmp_path)

    async def sleep(seconds: float) -> None:
        return None

    with contextlib.suppress(SpotifyError):  # the failures raise; what matters here is what was recorded
        await Spotify(tokens, http=http, sleep=sleep, status=recorded).devices()
    assert recorded.snapshot() == {"spotify": record}


async def test_spotify_out_of_reach_or_signed_out_is_recorded(tmp_path: Path) -> None:
    http = FakeHttp()
    tokens = await signed_in(tmp_path, http)
    recorded = recorder(tmp_path)
    spotify = Spotify(tokens, http=http, status=recorded)
    http.fail(f"{API}/me/player/devices", OSError("down"))
    with pytest.raises(SpotifyError):
        await spotify.devices()
    assert recorded.snapshot() == {"spotify": Record(failed=NOW, reason="no_answer")}
    tokens.forget()
    http.answers[TOKEN_URL] = [OSError("down")]
    with pytest.raises(SpotifyAuthError):
        await spotify.devices()
    assert recorded.snapshot()["spotify"].reason == "no_answer"
    http.answers[TOKEN_URL] = [HttpResult(500, {}, {"error": "server_error"})]
    with pytest.raises(SpotifyAuthError):
        await spotify.devices()
    assert recorded.snapshot()["spotify"].reason == "refused"
    http.answers[TOKEN_URL] = [HttpResult(400, {}, {"error": "invalid_grant"})]
    with pytest.raises(SpotifySignInExpired):
        await spotify.devices()
    assert recorded.snapshot()["spotify"] == Record(failed=NOW, reason="sign_in")
