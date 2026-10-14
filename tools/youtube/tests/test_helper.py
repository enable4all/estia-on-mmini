"""The YouTube helper against recorded responses (YouTube's real shapes, made-up captions): no network."""

import io
import json
import time
from collections.abc import Callable
from typing import Any

import helper
import pytest
import requests
from requests.structures import CaseInsensitiveDict

VIDEO = "AbCdEfGhI_-"  # 11 characters: letters, digits, _ and -
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"
PLAYER = "https://www.youtube.com/youtubei/v1/player"
TIMEDTEXT = "https://www.youtube.com/api/timedtext"
WATCH_HTML = '<html><script>ytcfg.set({"INNERTUBE_API_KEY": "not-a-real-key"});</script></html>'


def track(language: str, *, asr: bool = False, url: str = "") -> dict[str, Any]:
    entry: dict[str, Any] = {
        "baseUrl": url or f"{TIMEDTEXT}?v={VIDEO}&lang={language}" + ("&kind=asr" if asr else "") + "&fmt=srv3",
        "name": {"runs": [{"text": language}]},
        "languageCode": language,
        "isTranslatable": True,
    }
    if asr:
        entry["kind"] = "asr"
    return entry


def player(*tracks: dict[str, Any], status: str = "OK", reason: str = "", default: int | None = None) -> str:
    """YouTube's player answer; `default`: the index of the caption track YouTube shows by default (audioTracks)."""
    data: dict[str, Any] = {"playabilityStatus": {"status": status, **({"reason": reason} if reason else {})}}
    if tracks:
        listing: dict[str, Any] = {
            "captionTracks": list(tracks),
            "translationLanguages": [{"languageCode": "de", "languageName": {"runs": [{"text": "German"}]}}],
        }
        if default is not None:
            indices = list(range(len(tracks)))
            listing["audioTracks"] = [{"captionTrackIndices": indices, "defaultCaptionTrackIndex": default}]
            listing["defaultAudioTrackIndex"] = 0
        data["captions"] = {"playerCaptionsTracklistRenderer": listing}
    return json.dumps(data)


def timedtext(*lines: tuple[float, float, str]) -> str:
    body = "".join(f'<text start="{start}" dur="{dur}">{text}</text>' for start, dur, text in lines)
    return f'<?xml version="1.0" encoding="utf-8" ?><transcript>{body}</transcript>'


LECTURE = timedtext(
    (0.0, 3.2, "Welcome to the lecture on &amp;amp;\nbridges."),
    (3.2, 2.0, "   "),
    (65.5, 4.0, "Steel beams carry the load."),
    (3725.0, 5.0, "The end, after an hour."),
)

Answer = tuple[int, str, dict[str, str]]
Route = Callable[[requests.PreparedRequest], Answer]


class Recorded(helper.GuardedAdapter):
    """The guarded adapter with recorded answers instead of the network; every request that got past the guard is
    kept in `asked`."""

    def __init__(self, routes: dict[str, Route | Answer], deadline: float) -> None:
        super().__init__(deadline)
        self.routes = routes
        self.asked: list[str] = []
        self.proxies: list[Any] = []

    def transport(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        url = str(request.url)
        self.asked.append(url)
        self.proxies.append(kwargs.get("proxies"))
        for prefix, answer in self.routes.items():
            if url.startswith(prefix):
                status, body, headers = answer(request) if callable(answer) else answer
                response = requests.Response()
                response.status_code = status
                response._content = body.encode()
                response.raw = io.BytesIO()  # read already, as after a real request
                response.headers = CaseInsensitiveDict(headers)
                response.url = url
                response.request = request
                response.encoding = "utf-8"
                return response
        raise requests.ConnectionError("no recorded answer")


def ok(body: str) -> Answer:
    return 200, body, {}


def youtube(
    *tracks: dict[str, Any], captions: str = LECTURE, status: str = "OK", reason: str = "", default: int | None = None
) -> dict[str, Route | Answer]:
    return {
        WATCH: ok(WATCH_HTML),
        PLAYER: ok(player(*tracks, status=status, reason=reason, default=default)),
        TIMEDTEXT: ok(captions),
    }


def run(routes: dict[str, Route | Answer], *argv: str) -> tuple[int, dict[str, Any], Recorded]:
    made: list[Recorded] = []

    def adapter(deadline: float) -> Recorded:
        made.append(Recorded(routes, deadline))
        return made[-1]

    code, out = helper.run(["captions", VIDEO, *argv], adapter=adapter)
    return code, out, made[0] if made else Recorded(routes, 0.0)


def test_manual_captions_come_with_their_start_times_and_language() -> None:
    code, out, recorded = run(youtube(track("en", asr=True), track("en")))
    assert code == 0
    assert out == {
        "ok": True,
        "language": "en",
        "generated": False,
        "length": 3730.0,
        "cut": False,
        "segments": [
            [0.0, "Welcome to the lecture on & bridges."],
            [65.5, "Steel beams carry the load."],
            [3725.0, "The end, after an hour."],
        ],
    }
    assert recorded.asked[0] == WATCH
    assert recorded.asked[1].startswith(PLAYER)
    assert recorded.asked[2] == f"{TIMEDTEXT}?v={VIDEO}&lang=en"  # the manual track, never the asr one


def test_manual_captions_in_the_videos_own_language_come_first() -> None:
    # the auto-generated track tells the spoken language: Dutch; the English manual track is a translation
    _, out, recorded = run(youtube(track("en"), track("nl"), track("nl", asr=True)))
    assert (out["language"], out["generated"]) == ("nl", False)
    assert recorded.asked[-1].endswith("lang=nl")


def test_the_own_language_matches_a_regional_code() -> None:
    _, out, _ = run(youtube(track("fr"), track("en-GB"), track("en", asr=True)))
    assert out["language"] == "en-GB"


def test_manual_captions_beat_auto_generated_ones_in_another_language() -> None:
    _, out, recorded = run(youtube(track("en"), track("nl", asr=True)))
    assert (out["language"], out["generated"]) == ("en", False)
    assert "kind=asr" not in recorded.asked[-1]


def test_auto_generated_captions_only_when_there_are_no_manual_ones() -> None:
    _, out, recorded = run(youtube(track("el", asr=True)))
    assert (out["language"], out["generated"]) == ("el", True)
    assert "kind=asr" in recorded.asked[-1]


def test_without_auto_generated_captions_youtubes_default_track_tells_the_language() -> None:
    # live, 14 Oct 2026: an English lecture with hand-written tracks in many languages and none auto-generated came
    # back in Arabic (YouTube lists the tracks by language name); YouTube's default track names the video's own
    _, out, recorded = run(youtube(track("ar"), track("en"), track("nl"), default=1))
    assert (out["language"], out["generated"]) == ("en", False)
    assert recorded.asked[-1].endswith("lang=en")


def test_the_auto_generated_tracks_language_beats_youtubes_default() -> None:
    _, out, _ = run(youtube(track("en"), track("nl"), track("nl", asr=True), default=0))
    assert out["language"] == "nl"


def test_with_no_sign_of_the_videos_language_english_comes_first_then_youtubes_order() -> None:
    assert run(youtube(track("ar"), track("en"), track("fr")))[1]["language"] == "en"
    assert run(youtube(track("ar"), track("fr")))[1]["language"] == "ar"
    odd = youtube(track("ar"), track("en-US"), default=7)  # an index that names no track: not believed
    assert run(odd)[1]["language"] == "en-US"


def test_until_leaves_out_later_captions_and_says_so() -> None:
    _, out, _ = run(youtube(track("en")), "--until", "3600")
    assert [segment[0] for segment in out["segments"]] == [0.0, 65.5]
    assert out["cut"] is True and out["cut_by"] == "time"
    assert out["length"] == 3730.0  # the whole video's captions, for "the first 2 hours of a 3 h video"


def test_the_character_cap_leaves_out_later_captions_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "MAX_CHARS", 70)
    _, out, _ = run(youtube(track("en")), "--until", "7200")
    assert [segment[0] for segment in out["segments"]] == [0.0, 65.5]
    assert out["cut"] is True and out["cut_by"] == "size" and out["length"] == 3730.0


def test_whole_captions_name_no_cut() -> None:
    _, out, _ = run(youtube(track("en")))
    assert out["cut"] is False and "cut_by" not in out


def test_the_cap_is_the_bridges() -> None:
    assert helper.MAX_CHARS == 120_000  # local_assistant.video.CAP_CHARS (8 parts of 15,000)


def test_a_video_without_captions() -> None:
    assert run(youtube())[1] == {"ok": False, "error": "no_captions"}


def test_captions_with_no_words_count_as_none() -> None:
    _, out, _ = run(youtube(track("en"), captions=timedtext((0.0, 1.0, "  "))))
    assert out == {"ok": False, "error": "no_captions"}


@pytest.mark.parametrize(
    ("status", "reason", "error"),
    [
        ("ERROR", "This video is unavailable", "unavailable"),
        ("LOGIN_REQUIRED", "This video may be inappropriate for some users.", "age_restricted"),
        ("LOGIN_REQUIRED", "Sign in to confirm you’re not a bot", "blocked"),
        ("UNPLAYABLE", "Video unavailable in your country", "unavailable"),
    ],
)
def test_youtubes_refusals_have_their_own_codes(status: str, reason: str, error: str) -> None:
    code, out, _ = run(youtube(track("en"), status=status, reason=reason))
    assert (code, out) == (1, {"ok": False, "error": error})


def test_too_many_requests_is_blocked() -> None:
    routes = youtube(track("en"))
    routes[WATCH] = (429, "slow down", {})
    assert run(routes)[1] == {"ok": False, "error": "blocked"}


def test_a_page_without_the_key_cannot_be_read() -> None:
    routes = youtube(track("en"))
    routes[WATCH] = ok("<html>changed</html>")
    assert run(routes)[1] == {"ok": False, "error": "failed"}


def test_a_track_that_asks_for_a_token_cannot_be_read() -> None:
    routes = youtube(track("en", url=f"{TIMEDTEXT}?v={VIDEO}&lang=en&exp=xpe"))
    assert run(routes)[1] == {"ok": False, "error": "failed"}


def test_a_caption_address_on_another_host_is_never_asked() -> None:
    routes = youtube(track("en", url="https://captions.example.com/api/timedtext?v=1"))
    routes["https://captions.example.com"] = ok(LECTURE)
    code, out, recorded = run(routes)
    assert (code, out) == (1, {"ok": False, "error": "refused"})
    assert not any("example.com" in url for url in recorded.asked)


@pytest.mark.parametrize(
    "target",
    [
        "https://consent.youtube.com/m?continue=x",
        "https://youtube.com/watch?v=x",
        "http://www.youtube.com/watch?v=x",
        "https://www.youtube.com:8443/watch?v=x",
        "https://user@www.youtube.com/watch?v=x",  # privacy-ok: a made-up login in an address
        "https://www.youtube.com.example.com/watch",
    ],
)
def test_a_redirect_anywhere_but_youtubes_own_host_is_refused(target: str) -> None:
    routes = youtube(track("en"))
    routes[WATCH] = (302, "", {"Location": target})
    code, out, recorded = run(routes)
    assert (code, out) == (1, {"ok": False, "error": "refused"})
    assert recorded.asked == [WATCH]


def test_at_most_three_redirects() -> None:
    hops: list[str] = []

    def again(request: requests.PreparedRequest) -> Answer:
        hops.append(str(request.url))
        return 302, "", {"Location": f"https://www.youtube.com/hop{len(hops)}"}

    routes = youtube(track("en"))
    routes[WATCH] = again
    routes["https://www.youtube.com/hop"] = again
    code, out, recorded = run(routes)
    assert (code, out) == (1, {"ok": False, "error": "refused"})
    assert len(recorded.asked) == 4  # the first request and three redirects


def test_proxy_settings_in_the_environment_are_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example.com:3128")
    _, out, recorded = run(youtube(track("en")))
    assert out["ok"] is True
    assert all(not proxies for proxies in recorded.proxies)


def test_the_session_keeps_no_cookies_and_no_account() -> None:
    session = helper.session(helper.GuardedAdapter(time.monotonic() + 5))
    assert session.trust_env is False  # no proxy variables, no .netrc login, no other certificate bundle
    assert not session.proxies
    assert len(session.cookies) == 0
    assert session.max_redirects == 3
    assert "Authorization" not in session.headers


def test_no_network_is_an_unreachable_youtube() -> None:
    routes = youtube(track("en"))
    del routes[WATCH]
    assert run(routes)[1] == {"ok": False, "error": "unreachable"}


def test_a_server_error_is_an_unreachable_youtube() -> None:
    routes = youtube(track("en"))
    routes[PLAYER] = (503, "busy", {})
    assert run(routes)[1] == {"ok": False, "error": "unreachable"}


def test_every_request_gets_a_time_limit_within_the_helpers_own() -> None:
    seen: list[Any] = []

    class Timed(Recorded):
        def transport(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
            seen.append(kwargs.get("timeout"))
            return super().transport(request, **kwargs)

    helper.run(["captions", VIDEO], adapter=lambda deadline: Timed(youtube(track("en")), deadline))
    assert len(seen) == 3
    for connect, read in seen:
        assert 0 < connect <= helper.SECONDS and 0 < read <= helper.SECONDS


def test_the_helper_stops_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "SECONDS", 0.3)

    def slow(request: requests.PreparedRequest) -> Answer:
        time.sleep(2)
        return ok(WATCH_HTML)

    routes = youtube(track("en"))
    routes[WATCH] = slow
    started = time.monotonic()
    code, out, _ = run(routes)
    assert (code, out) == (1, {"ok": False, "error": "timeout"})
    assert time.monotonic() - started < 1.5


def test_the_bridges_argv_reads_an_id_that_starts_with_a_dash() -> None:
    # the bridge runs `captions --until 7200 -- <ID>`: about 1 in 64 IDs starts with "-", which only "--" keeps an ID
    dashed = "-0Ao4t_fe0I"
    routes = youtube(track("en"))
    routes[f"https://www.youtube.com/watch?v={dashed}"] = ok(WATCH_HTML)
    made: list[Recorded] = []

    def adapter(deadline: float) -> Recorded:
        made.append(Recorded(routes, deadline))
        return made[-1]

    code, out = helper.run(["captions", "--until", "7200", "--", dashed], adapter=adapter)
    assert code == 0 and out["segments"][0] == [0.0, "Welcome to the lecture on & bridges."]
    assert made[0].asked[0] == f"https://www.youtube.com/watch?v={dashed}"
    assert helper.run(["captions", dashed, "--until", "7200"], adapter=adapter)[1] == {"ok": False, "error": "invalid"}


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["captions"],
        ["captions", "short"],
        ["captions", "AbCdEfGhI_-x"],
        ["captions", "AbCdEfGhI/-"],
        ["captions", "https://www.youtube.com/watch?v=AbCdEfGhI_-"],
        ["captions", VIDEO, "--until", "0"],
        ["captions", VIDEO, "--until", "lots"],
        ["captions", VIDEO, "--proxy", "x"],
        ["other", VIDEO],
    ],
)
def test_bad_arguments_ask_nothing(argv: list[str]) -> None:
    made: list[Recorded] = []

    def adapter(deadline: float) -> Recorded:
        made.append(Recorded({}, deadline))
        return made[-1]

    code, out = helper.run(argv, adapter=adapter)
    assert (code, out) == (1, {"ok": False, "error": "invalid"})
    assert not made


def test_anything_unexpected_is_failed_without_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("captionTracks of a secret video")

    monkeypatch.setattr(helper, "choose", broken)
    assert run(youtube(track("en")))[1] == {"ok": False, "error": "failed"}


def test_main_prints_one_json_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.argv", ["helper.py", "captions", "short"])
    with pytest.raises(SystemExit) as stopped:
        helper.main()
    assert stopped.value.code == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == {"ok": False, "error": "invalid"}
