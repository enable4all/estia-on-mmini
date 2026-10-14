"""YouTube helper for local-assistant: the captions of one video per run, from www.youtube.com only.

The bridge runs it with fixed arguments (no shell, a clean environment) and reads one JSON object from stdout:

    helper.py captions [--until <seconds>] -- <11-character video ID>

(the ID last, after "--": about 1 in 64 IDs starts with "-", which would otherwise be read as an option)

exit 0: {"ok": true, "language": "en", "generated": false, "length": <seconds>, "cut": false,
         "segments": [[<start in seconds>, "<text>"], …]}
        (when captions were left out: "cut": true, "cut_by": "time" (past --until) or "size" (past MAX_CHARS))
exit 1: {"ok": false, "error": "invalid" | "no_captions" | "unavailable" | "age_restricted" | "blocked" |
         "unreachable" | "refused" | "failed" | "timeout"}

Only the video's ID comes in, never a link. youtube-transcript-api reads the watch page, YouTube's player answer and
the caption track; every request goes through `GuardedAdapter`, which refuses any address but https://www.youtube.com
(redirects too, at most 3), and gives each request a time limit within the helper's own (`SECONDS`: the helper stops
itself; the bridge kills it at 30 s). The session uses no proxy settings, no .netrc and no cookie file (trust_env off),
no account and no key of its own (the library uses the public key YouTube puts in the watch page); it lives for this
one run, so cookies YouTube sets are never kept. Manual captions come before
auto-generated ones, each in the video's own language first (the language of YouTube's auto-generated track, which
follows the speech; without one, the track YouTube shows by default; with neither, English, then YouTube's order); a
machine translation YouTube would make is never asked for. An error is one of the codes above, never
YouTube's text or the library's.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
import urllib.parse
from collections.abc import Callable, Sequence
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from youtube_transcript_api._errors import (
    AgeRestricted,
    InvalidVideoId,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
)
from youtube_transcript_api._transcripts import Transcript, TranscriptList, TranscriptListFetcher

HOST = "www.youtube.com"
SECONDS = 20.0  # the helper stops itself after this; the bridge waits 30 s
CONNECT_SECONDS = 5.0
MAX_REDIRECTS = 3
MAX_CHARS = 120_000  # captions passed on, at most (more than 2 hours of fast speech; the bridge's CAP_CHARS)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class Refused(Exception):
    """A request to an address other than https://www.youtube.com: never sent."""


class _Alarm(BaseException):
    """The helper's own time is up (a BaseException: no library handler swallows it)."""


def _stop(signum: int, frame: object) -> None:
    raise _Alarm()


def allowed(url: str) -> bool:
    """Only https://www.youtube.com/…: no other host or port, no login in the address."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname == HOST
        and parts.netloc.lower() == HOST
        and port is None
        and parts.username is None
    )


class GuardedAdapter(HTTPAdapter):
    """Every request of the session, redirects included, passes here: other hosts are refused, and each request gets
    a time limit within what is left of the helper's own."""

    def __init__(self, deadline: float) -> None:
        super().__init__(max_retries=0)
        self.deadline = deadline

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        if not allowed(str(request.url or "")):
            raise Refused()
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise _Alarm()
        kwargs["timeout"] = (min(CONNECT_SECONDS, left), left)
        kwargs["proxies"] = {}
        return self.transport(request, **kwargs)

    def transport(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        """The network (tests put recorded answers here)."""
        return super().send(request, **kwargs)


def session(adapter: GuardedAdapter) -> requests.Session:
    """A session for this run only: no proxies, no .netrc, no cookie file, no account; YouTube's host only."""
    client = requests.Session()
    client.trust_env = False  # no proxy variables, no .netrc login, no other certificate bundle
    client.proxies = {}
    client.max_redirects = MAX_REDIRECTS
    client.adapters.clear()
    client.mount("https://", adapter)
    client.mount("http://", adapter)  # refused there, never sent
    client.headers["User-Agent"] = "local-assistant youtube helper"
    return client


def _base(code: str) -> str:
    return code.split("-")[0].lower()


def default_language(listing: dict[str, Any]) -> str:
    """The language of the caption track YouTube shows by default for the video's default audio, when it is a
    hand-written one; "" when YouTube names none (or an index that is no track)."""
    tracks, audio = listing.get("captionTracks") or [], listing.get("audioTracks") or []
    index = listing.get("defaultAudioTrackIndex", 0)
    if not isinstance(index, int) or not 0 <= index < len(audio) or not isinstance(audio[index], dict):
        return ""
    caption = audio[index].get("defaultCaptionTrackIndex")
    if not isinstance(caption, int) or not 0 <= caption < len(tracks) or not isinstance(tracks[caption], dict):
        return ""
    return "" if tracks[caption].get("kind") == "asr" else str(tracks[caption].get("languageCode") or "")


def choose(transcripts: Sequence[Transcript], default: str = "") -> Transcript | None:
    """Manual captions before auto-generated ones; within each, the video's own language first: the auto-generated
    track's language (it follows the speech), else the track YouTube shows by default (`default`); with no sign of it,
    English, then YouTube's order (it lists tracks by language name: "Arabic" before "English")."""
    own = next((_base(t.language_code) for t in transcripts if t.is_generated), "") or _base(default)
    for generated in (False, True):
        kind = [t for t in transcripts if t.is_generated == generated]
        wanted = own or "en"
        found = next((t for t in kind if _base(t.language_code) == wanted), None) or (kind[0] if kind else None)
        if found is not None:
            return found
    return None


def captions(video_id: str, until: float | None, client: requests.Session) -> dict[str, Any]:
    """The chosen track's captions as the bridge reads them; an error code from `_code` otherwise."""
    client.headers["Accept-Language"] = "en-US"  # as the library's own YouTubeTranscriptApi sets it
    fetcher = TranscriptListFetcher(client, proxy_config=None)
    listing = fetcher._fetch_captions_json(video_id)  # the library's own step (1.2.4, pinned), kept for its default
    chosen = choose(list(TranscriptList.build(client, video_id, listing)), default_language(listing))
    if chosen is None:
        raise TranscriptsDisabled(video_id)
    fetched = chosen.fetch()
    length = max((snippet.start + snippet.duration for snippet in fetched), default=0.0)
    segments: list[list[Any]] = []
    chars = 0
    cut_by = ""
    for snippet in fetched:
        text = " ".join(snippet.text.split())
        if not text:
            continue
        if until is not None and snippet.start >= until:
            cut_by = "time"
            break
        if chars + len(text) > MAX_CHARS:
            cut_by = "size"
            break
        segments.append([round(snippet.start, 2), text])
        chars += len(text)
    if not segments:
        raise NoTranscriptFound(video_id, [], None)
    return {
        "ok": True,
        "language": chosen.language_code,
        "generated": chosen.is_generated,
        "length": round(length, 2),
        "cut": bool(cut_by),
        **({"cut_by": cut_by} if cut_by else {}),
        "segments": segments,
    }


def _code(exc: BaseException) -> str:
    """The error code of a failure: never YouTube's text or the library's."""
    if isinstance(exc, _Alarm):
        return "timeout"
    if isinstance(exc, Refused | requests.TooManyRedirects):
        return "refused"
    if isinstance(exc, TranscriptsDisabled | NoTranscriptFound):
        return "no_captions"
    if isinstance(exc, AgeRestricted):
        return "age_restricted"
    if isinstance(exc, VideoUnavailable | VideoUnplayable | InvalidVideoId):
        return "unavailable"
    if isinstance(exc, RequestBlocked):  # a bot check, or too many requests (IpBlocked)
        return "blocked"
    if isinstance(exc, YouTubeRequestFailed | requests.ConnectionError | requests.Timeout):
        return "unreachable"
    return "failed"  # YouTube changed (no key on the page, a token asked for) or anything else


def _video_id(value: str) -> str:
    if not _VIDEO_ID.match(value):
        raise argparse.ArgumentTypeError("not a video ID")
    return value


def _seconds(value: str) -> float:
    number = float(value)
    if not 0 < number <= 86_400:
        raise argparse.ArgumentTypeError("must be 1 to 86400")
    return number


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # a usage error is the code "invalid", not text on stderr
        raise ValueError(message)


def parse(argv: list[str]) -> argparse.Namespace:
    parser = _Parser(prog="helper.py", add_help=False)
    actions = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    read = actions.add_parser("captions", add_help=False)
    read.add_argument("video_id", type=_video_id)
    read.add_argument("--until", type=_seconds, default=None)
    return parser.parse_args(argv)


Adapter = Callable[[float], GuardedAdapter]


def run(argv: list[str], *, adapter: Adapter = GuardedAdapter) -> tuple[int, dict[str, Any]]:
    """One video's captions; every outcome is one JSON-ready object. Stops itself after SECONDS."""
    try:
        args = parse(argv)
    except (ValueError, argparse.ArgumentTypeError):
        return 1, {"ok": False, "error": "invalid"}
    deadline = time.monotonic() + SECONDS
    previous = signal.signal(signal.SIGALRM, _stop)
    signal.setitimer(signal.ITIMER_REAL, SECONDS)
    try:
        return 0, captions(args.video_id, args.until, session(adapter(deadline)))
    except (Exception, _Alarm) as exc:
        return 1, {"ok": False, "error": _code(exc)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main() -> None:
    code, out = run(sys.argv[1:])
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
