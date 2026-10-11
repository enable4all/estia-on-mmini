from pathlib import Path

import pytest

from local_assistant.config import Config
from local_assistant.music_tools import MusicTools
from local_assistant.spotify import SpotifyError
from local_assistant.spotify_auth import LIFETIME_SECONDS, SpotifySignInExpired
from tests.conftest import ME
from tests.fakes.fake_spotify import FakeSpotify

NOW = 1_800_000_000.0


class FakeTokens:
    def __init__(self, authorized_at: float | None = NOW) -> None:
        self.authorized_at = authorized_at

    def signed_in(self) -> bool:
        return self.authorized_at is not None

    def expires_at(self) -> float | None:
        return None if self.authorized_at is None else self.authorized_at + LIFETIME_SECONDS


def make(tmp_path: Path, spotify: FakeSpotify, tokens: FakeTokens | None = None) -> tuple[MusicTools, list[str]]:
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path,
        spotify_client_id="0123456789abcdef0123456789abcdef",
        spotify_default_device="Kitchen speaker",
        spotify_bose=(("Kitchen speaker", "192.168.1.40"),),
    )
    woken: list[str] = []

    async def wake(host: str) -> bool:
        woken.append(host)
        spotify.asleep.discard("Kitchen speaker")
        return True

    async def no_sleep(seconds: float) -> None:
        return None

    music = MusicTools(spotify, tokens or FakeTokens(), cfg, wake=wake, sleep=no_sleep, clock=lambda: NOW)
    return music, woken


async def test_play_some_jazz_on_the_default_speaker_starts_at_30(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    result = await music.play("jazz")
    assert result == "Done: playing on Kitchen speaker at 30 % (the note says what)."
    assert ("transfer", "kitchen") in spotify.calls and (
        "play",
        ("kitchen", "spotify:playlist:jazz", ()),
    ) in spotify.calls
    assert ("volume", (30, "kitchen")) in spotify.calls  # a speaker that was not playing starts at 30 %
    assert (
        music.take_note()
        == "Music: playing Jazz Classics on Kitchen speaker at 30 % · https://open.spotify.com/playlist/jazz"
    )
    assert music.take_note() == ""


@pytest.mark.parametrize(
    ("what", "expected"),
    [
        ("my road trip playlist", ("play", ("kitchen", "spotify:playlist:road", ()))),  # own playlist first
        ("Liked Songs", ("play", ("kitchen", None, ("spotify:track:big",)))),
        ("Miles Davis", ("play", ("kitchen", "spotify:artist:miles", ()))),
    ],
)
async def test_matching_order(tmp_path: Path, what: str, expected: tuple[str, object]) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    assert (await music.play(what)).startswith("Done: playing")
    assert expected in spotify.calls


async def test_nothing_matches(tmp_path: Path) -> None:
    music, _ = make(tmp_path, FakeSpotify())
    assert await music.play("zzqx") == 'Nothing on Spotify matched "zzqx".'


async def test_a_sleeping_bose_is_woken_then_used(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    spotify.asleep.add("Kitchen speaker")
    music, woken = make(tmp_path, spotify)
    assert (await music.play("jazz")).startswith("Done")
    assert woken == ["192.168.1.40"]


async def test_a_speaker_that_stays_away_is_said_plainly(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    spotify.asleep.add("Bedroom speaker")
    music, woken = make(tmp_path, spotify)
    result = await music.play("jazz", speaker="Bedroom speaker")
    assert result.startswith("Not done: Bedroom speaker isn't answering") and "Kitchen speaker" in result
    assert woken == []  # no address configured for it: nothing to wake


async def test_volume_never_goes_above_the_cap(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    spotify.speakers[0]["is_active"] = True  # playing at 80 %
    music, _ = make(tmp_path, spotify)
    await music.play("jazz")
    assert ("volume", (60, "kitchen")) in spotify.calls  # brought down to the cap
    spotify.calls.clear()
    spotify.playing = {"device": spotify.speakers[0], "is_playing": True}
    assert await music.control("louder") == "Done: volume 60 % (the most allowed)."
    assert await music.control("volume", 95) == "Done: volume 60 % (the most allowed)."
    assert await music.control("quieter") == "Done: volume 50 %."


async def test_controls(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    assert await music.control("pause") == "Done: paused."
    assert await music.control("next") == "Done: next track."
    assert await music.control("previous") == "Done: previous track."
    assert await music.control("resume") == "Done: playing again."
    assert [c[0] for c in spotify.calls] == ["pause", "skip", "skip", "resume"]
    assert (
        await music.control("dance") == "Unknown action: use pause, resume, next, previous, louder, quieter or volume."
    )


async def test_errors_are_said_plainly(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    spotify.fail = SpotifyError("x", 404, "NO_ACTIVE_DEVICE")
    assert await music.control("pause") == "Nothing is playing on a speaker right now."
    spotify.fail = SpotifyError("x", 403, "PREMIUM_REQUIRED")
    assert await music.control("pause") == "Not done: Spotify says this needs Premium."
    spotify.fail = SpotifyError("Spotify is not reachable (OSError)")
    assert await music.control("pause") == "Spotify isn't answering right now."


async def test_signed_out_or_ended(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)

    async def expired() -> list[dict[str, object]]:
        raise SpotifySignInExpired("ended")

    spotify.devices = expired  # type: ignore[method-assign]
    assert await music.play("jazz") == "Spotify needs a new sign-in: run scripts/spotify-login.sh on the Mac."


async def test_status_and_what_is_playing(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    assert await music.status() == "Nothing is playing. Speakers Spotify sees: Kitchen speaker, Bedroom speaker."


def test_status_line_and_the_reminder_before_the_sign_in_ends(tmp_path: Path) -> None:
    music, _ = make(tmp_path, FakeSpotify())
    assert music.status_line().startswith("Music: Spotify signed in until ")
    assert music.reminder() is None
    old = FakeTokens(authorized_at=NOW - LIFETIME_SECONDS + 10 * 86_400)
    music, _ = make(tmp_path, FakeSpotify(), old)
    assert music.reminder() == "Spotify's sign-in ends in 10 days: run scripts/spotify-login.sh on the Mac."
    music, _ = make(tmp_path, FakeSpotify(), FakeTokens(authorized_at=None))
    assert music.status_line() == "Music: Spotify needs a sign-in (scripts/spotify-login.sh)"


@pytest.mark.parametrize("what", ["some jazz", "a bit of jazz music", "Jazz please", "some jazz songs"])
async def test_filler_words_are_dropped_before_searching(tmp_path: Path, what: str) -> None:
    music, _ = make(tmp_path, FakeSpotify())
    assert await music.play(what) == "Done: playing on Kitchen speaker at 30 % (the note says what)."
    assert "Jazz Classics" in music.take_note()


@pytest.mark.parametrize(
    "what", ["my liked songs", "My Liked Songs", "liked songs", "my music", "my favourites", "play my liked songs."]
)
async def test_every_way_of_asking_for_liked_songs_plays_them(tmp_path: Path, what: str) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    assert (await music.play(what)).startswith("Done")
    assert ("play", ("kitchen", None, ("spotify:track:big",))) in spotify.calls
    assert not [c for c in spotify.calls if c[0] == "search"]


async def test_the_volume_is_set_before_the_music_starts(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    await music.play("jazz")
    names = [c[0] for c in spotify.calls if c[0] in ("transfer", "volume", "play")]
    assert names == ["transfer", "volume", "play"]
    spotify.calls.clear()
    spotify.speakers[0]["volume_percent"] = 90  # already playing, and louder than the cap
    await music.play("jazz")
    assert [c[0] for c in spotify.calls if c[0] in ("volume", "play")] == ["volume", "play"]


async def test_gemma_sees_no_spotify_names_only_the_note_does(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    result = await music.play("jazz")
    assert result == "Done: playing on Kitchen speaker at 30 % (the note says what)." and "Jazz Classics" not in result
    assert "Jazz Classics" in music.take_note()
    spotify.playing = {
        "is_playing": True,
        "device": {"name": "Kitchen speaker", "volume_percent": 30},
        "item": {"name": "So What", "artists": [{"name": "Miles Davis"}]},
    }
    status = await music.status()
    assert "So What" not in status and "Miles Davis" not in status
    assert (
        status
        == "Done: playing on Kitchen speaker at 30 % (the note says what). Speakers Spotify sees: Kitchen speaker, Bedroom speaker."
    )
    assert music.take_note() == "Music: So What by Miles Davis on Kitchen speaker at 30 %"


async def test_volume_needs_a_number_and_after_outside_text_may_only_go_down(tmp_path: Path) -> None:
    spotify = FakeSpotify()
    music, _ = make(tmp_path, spotify)
    spotify.playing = {"device": spotify.speakers[0], "is_playing": True}
    spotify.speakers[0]["volume_percent"] = 40
    assert await music.control("volume") == "Say which volume: 0-100."
    assert await music.control("volume", 20, only_down=True) == "Done: volume 20 %."
    assert (await music.control("volume", 50, only_down=True)).startswith("Not done: music starts or gets louder")
