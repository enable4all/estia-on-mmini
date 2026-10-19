"""Voice notes: what audio a file holds, the local voice service (tools/voice), the spoken text, AAC.

The voice service listens (WhisperKit) and speaks (Kokoro for English, Supertonic-3 for the other languages) on the
Neural Engine, and on a call tells how likely the caller's last 256 ms are speech (Silero VAD through FluidAudio) and
how sure it is that the caller's sentence is finished (Smart Turn v3.2, on the CPU);
the bridge only talks HTTP to it on 127.0.0.1, in one language at a time, and only over a connection whose other end a
program of its own account holds (`peer_is_ours`): in her own user a program of the owner's could take the port while
her service is not bound, and get the audio and send back words that count as the user's. Spoken answers are cleaned
deterministically (spec 3.3) and converted to AAC in M4A with macOS's own `afconvert`, so Signal shows a real
voice-note bubble.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import logging
import os
import re
import subprocess
import time
import unicodedata
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_assistant import signal_text, theory
from local_assistant.language import ENGLISH, STAGE_DIRECTIONS, STAGE_SOFT, STAGE_STARRED, made_of
from local_assistant.part_status import PartStatus

log = logging.getLogger(__name__)

AFCONVERT = "/usr/bin/afconvert"
LSOF = "/usr/sbin/lsof"
CHECK_SECONDS = 2.0  # at most, for the check of whose program took a connection (a call's detector: its own limit)
TAKEN_WAIT = 0.02  # between two looks while a listener of ours has not taken the connection yet
CONVERT_TIMEOUT = 60.0
MAX_RESPONSE = 50 * 1024 * 1024
MIN_SPOKEN = 10
REST = " The rest is in the text."
VAD_TIMEOUT = 0.3  # a call never waits for the speech detector: a slower answer is no answer


class VoiceError(Exception):
    """The voice service or the conversion failed."""


class VoiceUnavailableError(VoiceError):
    """The voice service is not running or still loading its models."""


class VoiceNotHersError(VoiceUnavailableError):
    """The program at the voice service's address is not one of her own account's: nothing is sent to it (part_status
    `not_hers`). One that could not be checked in time is a VoiceUnavailableError: nothing is sent either."""


def peer_is_ours(port: int, local: int, seconds: float = CHECK_SECONDS) -> bool | None:
    """Whether a program of this account took this account's connection from 127.0.0.1:`local` to 127.0.0.1:`port`
    (None: lsof could not run in `seconds`). lsof lists only the processes of the account that runs it (never root
    here), so another account's program (the owner's, in her own user) is never in its list: a connection it does not
    show taken is not hers. The connection itself is checked, not only who listens: a program that took it and then left
    the port to her service is still not hers. Without a listener of ours the answer is no at once; with one, the
    taking is waited for within `seconds` (a connection waits for its accept)."""
    taken, listening = f"n127.0.0.1:{port}->127.0.0.1:{local}", f"n127.0.0.1:{port}"
    deadline, looked = time.monotonic() + seconds, False
    while True:
        try:
            done = subprocess.run(
                [LSOF, "-nP", f"-iTCP@127.0.0.1:{port}", "-Fn"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=max(deadline - time.monotonic(), 0.01),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False if looked else None  # after a look that found it not taken: not taken in time
        looked = True
        names = set(done.stdout.decode("ascii", "replace").splitlines())
        if taken in names:
            return True
        if listening not in names or time.monotonic() + TAKEN_WAIT >= deadline:
            return False
        time.sleep(TAKEN_WAIT)


class VoiceTooLongError(VoiceError):
    """Over 10 MB or 3 minutes."""


class VoiceUnreadableError(VoiceError):
    """The service could not read the audio."""


@dataclass(frozen=True)
class Transcript:
    text: str  # "" when nothing was recognised
    duration: float  # seconds of audio


def sniff(data: bytes) -> str | None:
    """The audio format from the first bytes, never from the claimed type (iOS sends M4A, Android ADTS AAC)."""
    if data[4:8] == b"ftyp":
        return "m4a"
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xF6 == 0xF0:  # ADTS sync word, layer 00
        return "aac"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    return None


class VoiceClient:
    """HTTP to voice-service on 127.0.0.1 (no proxies), only once the connection is made and `peer` finds a program of
    this account at its other end: else nothing is sent. Every request carries the language; English requests carry
    none (exactly as before the languages), so an older voice service keeps working in English."""

    def __init__(
        self,
        base_url: str,
        *,
        voice: str,
        speed: float,
        timeout: float = 120.0,
        status: PartStatus | None = None,
        language: str = ENGLISH,
        peer: Callable[[int, int, float], bool | None] = peer_is_ours,
    ) -> None:
        parts = urllib.parse.urlsplit(base_url)
        # her voice service listens on 127.0.0.1 only: "localhost" is reached there, never at another address
        self._host = "127.0.0.1" if parts.hostname in (None, "localhost") else parts.hostname
        self._port = parts.port or 80
        self._peer = peer
        self._not_hers: bool | None = False  # the last check's verdict when it was not "hers" (logged once a change)
        self._voice = voice
        self._speed = speed
        self._timeout = timeout
        self._status = status or PartStatus(None)  # what the last real request did (doctor, /status)
        self._language = language

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, code: str) -> None:
        """Another language from the next request on (/language)."""
        self._language = code

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> Transcript:
        """What was said, in the current language (or `language`: a call keeps its own); Whisper is told it, no
        detection."""
        heard = language or self._language
        path = "/v1/audio/transcriptions"
        if heard != ENGLISH:
            path += f"?language={heard}"
        status, body = await self._real("POST", path, audio, "application/octet-stream")
        if status != 200:
            self._record(status)
            raise _refusal(status)
        try:
            data: Any = json.loads(body)
            transcript = Transcript(str(data["text"]).strip(), float(data.get("duration", 0)))
        except (ValueError, KeyError, TypeError) as exc:
            self._status.failed("voice", "bad_answer")
            raise VoiceError("the voice service sent no transcript") from exc
        self._status.ok("voice")
        return transcript

    @property
    def voice(self) -> str:
        return self._voice

    def set_voice(self, name: str) -> None:
        """Another Kokoro voice from the next answer on (/voice name)."""
        self._voice = name

    async def speak(self, text: str, *, language: str | None = None) -> bytes:
        """WAV audio of `text`, in the configured voice and speed and the current language (or `language`: code's
        own words on a call are said in English)."""
        fields: dict[str, Any] = {"input": text, "voice": self._voice, "speed": self._speed}
        spoken = language or self._language
        if spoken != ENGLISH:
            fields["language"] = spoken
        status, body = await self._real("POST", "/v1/audio/speech", json.dumps(fields).encode(), "application/json")
        if status != 200 or not body:
            if status == 200:
                self._status.failed("voice", "bad_answer")  # no audio
            else:
                self._record(status)
            raise _refusal(status)
        self._status.ok("voice")
        return body

    async def health(self) -> dict[str, str | tuple[str, ...]] | None:
        """The service's state ({"stt": ..., "tts": ...}, and "languages" once it can do them), or None when it does
        not answer."""
        try:
            status, body = await self._request("GET", "/health", None, None, seconds=5.0)
            data = json.loads(body) if status == 200 else None
        except (VoiceError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return {
            str(key): tuple(str(item) for item in value) if isinstance(value, list) else str(value)
            for key, value in data.items()
        }

    async def vad(self, chunk: bytes, *, stream: str) -> float | str:
        """How likely `chunk` (4096 samples of the caller's 16 kHz audio: 256 ms) is speech, from 0 to 1; the service
        keeps one detector state per `stream` (one call). On a failure, its kind (call_audio.Barge counts them, metadata
        only): "timeout" (no answer within VAD_TIMEOUT) or "http" (no service, an older one, a model still loading, a
        refusal or an answer that is not a probability). Never raises into the call and never marks the voice as
        failing (part_status)."""
        return await self._probability(f"/vad?stream={stream}", chunk)

    async def turn(self, window: bytes) -> float | str:
        """How sure Smart Turn is that the caller's sentence is finished, from 0 to 1: `window` is the turn's last 8 s
        at most (16 kHz mono s16le, the most recent sample last; no query, no state). On a failure its kind, as `vad`'s
        (call_audio.TurnEnd counts them): "timeout" (no answer within VAD_TIMEOUT) or "http" (no service, an older one,
        a model loading or unavailable, a refused window or an answer that is not a probability). Never raises into
        the call and never marks the voice as failing."""
        return await self._probability("/turn", window)

    async def _probability(self, path: str, body: bytes) -> float | str:
        """A detector's answer on a call: {"p": 0-1} within VAD_TIMEOUT, else "timeout" or "http"."""
        try:
            status, answer = await self._request("POST", path, body, "application/octet-stream", seconds=VAD_TIMEOUT)
            p = json.loads(answer).get("p") if status == 200 else None
        except VoiceUnavailableError as exc:
            return "timeout" if _timed_out(exc) else "http"
        except Exception:
            return "http"
        if isinstance(p, bool) or not isinstance(p, int | float) or not 0.0 <= p <= 1.0:
            return "http"
        return float(p)

    async def languages(self) -> tuple[str, ...] | None:
        """The languages the installed service has: () when it is older than the language feature, None when it does
        not answer."""
        health = await self.health()
        if health is None:
            return None
        listed = health.get("languages")
        return listed if isinstance(listed, tuple) else ()

    async def _real(self, method: str, path: str, body: bytes, content_type: str) -> tuple[int, bytes]:
        """A transcription or speech request: a service that does not answer is recorded (part_status)."""
        try:
            return await self._request(method, path, body, content_type)
        except VoiceNotHersError:  # another account's program at her address: doctor and /status name it
            self._status.failed("voice", "not_hers")
            raise
        except VoiceUnavailableError:
            self._status.failed("voice", "no_answer")
            raise

    def _record(self, status: int) -> None:
        """What a refusal says about the service: too long or unreadable audio is the service working as it should."""
        if status == 503:
            self._status.failed("voice", "loading", status)
        elif status in (413, 415, 422):
            self._status.ok("voice")
        else:
            self._status.failed("voice", "refused", status)

    async def _request(
        self, method: str, path: str, body: bytes | None, content_type: str | None, *, seconds: float | None = None
    ) -> tuple[int, bytes]:
        def call() -> tuple[int, bytes]:
            headers = {"Content-Type": content_type} if content_type else {}
            limit = seconds or self._timeout
            started = time.monotonic()
            connection = http.client.HTTPConnection(self._host, self._port, timeout=limit)
            try:
                connection.connect()  # first the connection, then whose program took it: then the request
                assert connection.sock is not None
                verdict = self._peer(self._port, connection.sock.getsockname()[1], min(limit, CHECK_SECONDS))
                self._say(verdict)
                if verdict is False:
                    raise VoiceNotHersError("the program at the voice service's address is not hers: nothing sent")
                if verdict is None:
                    raise VoiceUnavailableError("whose program is at the voice service's address: not checked in time")
                # the check counts within the request's own time (a call's detector: VAD_TIMEOUT in all)
                connection.sock.settimeout(max(limit - (time.monotonic() - started), 0.01))
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                return response.status, response.read(MAX_RESPONSE)
            except (OSError, http.client.HTTPException) as exc:
                raise VoiceUnavailableError(f"the voice service is not answering ({type(exc).__name__})") from exc
            finally:
                connection.close()

        return await asyncio.to_thread(call)

    def _say(self, verdict: bool | None) -> None:
        """The log line when the program at the port is not hers or could not be checked, once while it lasts, and
        when her own service holds it again (the port only: no address, no text)."""
        now = None if verdict is None else not verdict
        if now != self._not_hers:
            if now is None:
                log.warning("voice: whose program is at port %d could not be checked: nothing sent", self._port)
            elif now:
                log.warning("voice: the program at port %d is not hers (not her account's): nothing sent", self._port)
            else:
                log.info("voice: her own voice service holds port %d again", self._port)
        self._not_hers = now


DETECTOR_STATES = ("ready", "loading", "unavailable")  # what the voice service's health says as "vad" and "turn"


def detector_state(health: Mapping[str, object] | None, part: str = "vad") -> str:
    """A detector's state from the voice service's health (`part`: "vad", the speech detector, or "turn", Smart Turn):
    "ready", "loading" (the model loads or downloads at start), "unavailable" (its model did not download or failed its
    check: it needs downloading again), "old voice service" (built before that detector) or "not ready" (no answer, or
    a state this bridge does not know). Anything but "ready" means, on calls meanwhile, loudness for the speech detector
    (it stops her, and keeps a turn open) and the full pause for the turn detector."""
    if health is None:
        return "not ready"
    if part not in health:
        return "old voice service"
    state = health[part]
    return state if isinstance(state, str) and state in DETECTOR_STATES else "not ready"


def _timed_out(exc: BaseException) -> bool:
    """Whether a request failed for lack of an answer in time (a read or a connect that timed out)."""
    return isinstance(exc.__cause__, TimeoutError)


def _refusal(status: int) -> VoiceError:
    if status == 413:
        return VoiceTooLongError("over 10 MB or 3 minutes")
    if status in (415, 422):
        return VoiceUnreadableError("the voice service could not read the audio")
    if status == 503:
        return VoiceUnavailableError("the voice service is still loading")
    return VoiceError(f"the voice service answered {status}")


_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_LINK = re.compile(r"\[([^\]\n]*)\]\([^)\s]*\)")
_INLINE = re.compile(r"`([^`\n]*)`")
_URL = re.compile(r"(?:https?://|www\.)[^\s)\]>]*[^\s)\]>.,;:!?]")
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?/(?:[\w.-]+/)*[\w.-]*[\w-]")
# the user's commands (commands.py; /help names them): said as "slash reminders", never left out as a path ("/reminders"
# alone looks like one); a command with more path after it ("/new/x", "/help.txt") is a path
COMMANDS = (
    "new",
    "profile",
    "forget",
    "fix",
    "status",
    "learn",
    "voice",
    "voices",
    "soul",
    "docs",
    "language",
    "babble",
    "remind",
    "reminders",
    "quiet",
    "initiative",
    "evening",
    "devices",
    "lock",
    "unlock",
    "help",
)
_COMMAND = re.compile(rf"(?<![\w/~.])/({'|'.join(COMMANDS)})(?![\w-]|/|\.\w)", re.IGNORECASE)
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-♬♰-➿⬀-⯿️‍]")  # never ♭ ♮ ♯: said as flat, natural, sharp
_EMPHASIS = re.compile(r"\*\*|__|~~")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}")
_HEADING = re.compile(r"^#{1,6}\s+")
_BULLET = re.compile(r"^(?:[-*•+]|\d{1,2}[.)])\s+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[。！？]")  # Japanese: no space after its sentence ends


# a stage direction in her answer: a whole group in brackets, full-width brackets or single stars ("(pause)", "[beat]",
# "（間）", "*smiles*", "*(laughs)*"); never a link's text ("[pause](…)") or bold ("**Pause**"), and code (a fence or
# `inline`) is matched first and kept as written
_STAGE = re.compile(
    r"(?P<code>```.*?(?:```|\Z)|`[^`\n]*`)"
    r"|(?P<pre>[ \t]*)"
    r"(?:\*{1,2}\((?P<starred>[^()\n]{1,40})\)\*{1,2}"
    r"|\((?P<round>[^()\n]{1,40})\)"
    r"|\[(?P<square>[^\[\]\n]{1,40})\](?!\()"
    r"|（(?P<wide>[^（）\n]{1,20})）"
    r"|(?<![*\w])\*(?P<stars>[^*\s][^*\n]{0,39}?)\*(?![*\w]))"
    r"(?P<post>[ \t]*)",
    re.DOTALL,
)
_STAGE_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_STAGE_BETWEEN = re.compile(r"[\s,;.!…&'’-]+")  # all a stage direction may hold besides its words
_NO_SPACES = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")  # Japanese: by characters
_REPEATED_DOTS = re.compile(r"(\.\.\.|…)(?:[ \t]*(?:\.\.\.|…))+")  # "Em... ... C" once its "(pause)" is out
_ENDS = ".!?…。！？"  # a sentence's end marks (an ellipsis too)
# "..." right after a word (at a space, or right after another "...": "C...G...Am"), with the next word and the one after
# it (a lookahead: "C... G... Am..." is matched word by word)
_DOTS_BETWEEN = re.compile(
    r"(?:(?<!\S)|(?<=\.\.\.))([^\s.,;:!?]+)\.{3,}"  # a word, at a space or right after "...", and its "..."
    r"(?=[ \t]*([^\s.,;:!?]+)(?:[ \t]+([^\s.,;:!?]+))?)"  # the next two words
)
# The words that join two chords ("C... then G"): and, or and then in English and the eight languages that spell chords
_JOINS = frozenset(
    {
        *("and", "or", "then", "to"),
        *("y", "e", "o", "u", "luego", "después"),  # es
        *("ed", "poi"),  # it
        *("ou", "depois"),  # pt
        *("et", "puis", "ensuite"),  # fr
        *("en", "of", "dan"),  # nl
        *("und", "oder", "dann"),  # de
        *("i", "oraz", "lub", "albo", "potem"),  # pl
        *("и", "или", "потом", "затем"),  # ru
    }
)
_WRAPS = "([\"'“‘)]\"'”’"  # brackets and quotes around a word, never part of a chord
_MARKS_RUN = re.compile(r"([.,;:!?])[.,;:!?]+")  # a run of marks, said as its first
_DOTTED = re.compile(r"[^\W\d_]\.[^\W\d_]\Z")  # "N.C", "e.g", "a.m": a dotted abbreviation up to its last dot


def _stage_units(text: str) -> tuple[str, ...] | None:
    """A bracket's words as compared: casefolded, Latin letters without accents, Japanese by characters; None when it
    holds anything but words and the marks between them (a digit, a sign: "(x2)", "(G7)")."""
    if _STAGE_BETWEEN.sub("", _STAGE_WORD.sub("", text)):
        return None
    words = []
    for word in _STAGE_WORD.findall(text.replace("’", "'")):
        word = word.casefold()
        if all(ord(char) < 0x250 for char in word):
            word = "".join(char for char in unicodedata.normalize("NFKD", word) if not unicodedata.combining(char))
        words.append(unicodedata.normalize("NFC", word))
    return tuple("".join(words)) if _NO_SPACES.search(text) else tuple(words)


def _stage_phrases(lists: Mapping[str, tuple[str, ...]]) -> frozenset[tuple[str, ...]]:
    return frozenset(units for words in lists.values() for word in words if (units := _stage_units(word)))


_STAGE_WORDS = _stage_phrases(STAGE_DIRECTIONS)
_STAGE_STARRED_WORDS = _stage_phrases(STAGE_STARRED)
_STAGE_SOFT_WORDS = _stage_phrases(STAGE_SOFT)


def _cased(char: str) -> bool:
    """A letter with an upper and a lower case (Latin, Cyrillic); Japanese and Korean have none."""
    return char.lower() != char.upper()


def _attaches(char: str) -> bool:
    """A character a bracket right beside it belongs to: a cased letter, a digit or "_" ("time.sleep(pause)")."""
    return char == "_" or char.isdigit() or _cased(char)


def _between_sentences(before: str, after: str) -> bool:
    """Whether a group stands between sentences: after the start of a line or a sentence's end, and before the end of
    a line, a sentence's end or a sentence's start (a capital, a letter without case, or "¡", "¿", a quote first)."""
    left = before.rstrip(" \t")
    left_edge = not left or left.endswith("\n") or left.rstrip("\"'”’)]").endswith(tuple(_ENDS))
    right = after.lstrip(" \t")
    starts = right.lstrip("¡¿\"“«'‘(")
    right_edge = (
        not right
        or right[0] == "\n"
        or right[0] in _ENDS
        or bool(starts and starts[0].isalpha() and (starts[0].isupper() or not _cased(starts[0])))
    )
    return left_edge and right_edge


def without_stage_directions(text: str) -> str:
    """Her answer without its stage directions ("(pause)", "(short pause)", "[beat]", "*smiles*", "(laughs)", "(pausa)",
    "(пауза)", "（間）"): a whole bracketed group of only the words of language.STAGE_DIRECTIONS and STAGE_SOFT, or a
    starred one of STAGE_STARRED's (third-person and -ing forms: "*smiles*", never "*pause*" or "*smile*"), in any of
    the 11 languages (owner, 18 Oct: her "(pause)" between chords was read aloud). One written in lower case goes
    wherever it stands ("Okay (pause) the next one is G"); a capitalised one only between sentences ("Great! (Laughs)
    Let me think."), as a title in brackets stays ("(Applause) by Lady Gaga", "Play (Smile) next"). Never bold
    ("**Beat** the eggs"), code (a fence or `inline`: "time.sleep(pause)"), a bracket right beside a letter or digit,
    or a link's text (review 18 Oct). The rest stays as written: the punctuation around it ("Em... (pause) ... C..." is
    "Em... C...", so a call still pauses there), a space before "!" (French), and every bracket that holds anything
    else ("C (live version)", "(G shapes)", "(x2)", "(if you want)"). Spoken (spoken_text) and sent (brain) alike: she
    never needs one."""
    removed = False

    def drop(match: re.Match[str]) -> str:
        nonlocal removed
        if match.group("code") is not None:
            return match.group(0)
        stars = match.group("stars")
        inner = stars or next(group for group in match.group("starred", "round", "square", "wide") if group)
        units = _stage_units(inner)
        words = _STAGE_STARRED_WORDS if stars else _STAGE_WORDS
        pre, post = match.group("pre"), match.group("post")
        before, after = text[: match.start() + len(pre)], text[match.end() - len(post) :]
        attached = (not pre and before and _attaches(before[-1])) or (not post and after and _attaches(after[0]))
        first = next((char for char in inner if char.isalpha()), "")
        capitalised = _cased(first) and first.isupper()
        if (
            not units
            or not made_of(units, words, _STAGE_SOFT_WORDS)
            or attached
            or (capitalised and not _between_sentences(before, after))
        ):
            return match.group(0)
        removed = True
        at_line_start = match.start() == 0 or text[match.start() - 1] == "\n"
        return "" if at_line_start else post

    cleaned = _STAGE.sub(drop, text)
    if not removed:
        return text
    return _REPEATED_DOTS.sub(r"\1", cleaned).strip()


def _dots_between_chords(text: str) -> str:
    """Three dots between two chords as a comma ("C... G... Am..." is "C, G, Am..."): as a full stop each chord was a
    sentence of its own, and the voice service read "G." as "Jay" and "Am." as "M" (the chord speech audit, 18 Oct).
    A chord is what theory's parser reads in some naming ("G", "Am7", "Sol", "Fis"); so is a joining word before one
    (`_JOINS`: "G... then C" is "G, then C"; the review, 18 Oct). A "..." after or before a word stays as it is
    ("Wait... A moment.", "G... then the chorus.")."""

    def chord(word: str | None) -> bool:  # as written ("Cm7(b5)"), or without the brackets or quotes around it ("(G")
        return word is not None and (theory._parses(word) or theory._parses(word.strip(_WRAPS)))

    def comma(found: re.Match[str]) -> str:
        first, after, then = found.groups()
        goes_on = chord(after) or (after.casefold() in _JOINS and chord(then))
        return first + "," if chord(first) and goes_on else found.group(0)

    return _DOTS_BETWEEN.sub(comma, text)


def _one_mark(found: re.Match[str]) -> str:
    """A run of marks as its first ("Wait.. what?!" is "Wait. what?"), but a comma or a semicolon after a dotted
    abbreviation's last dot stays, a pause ("N.C., then G", "e.g., apples"; the chord speech audit, 18 Oct; "| N.C. | G
    |" is "N.C.; G.": the review, the same day)."""
    if found.group(0) in (".,", ".;") and _DOTTED.search(found.string, 0, found.start()):
        return found.group(0)
    return found.group(1)


def spoken_text(answer: str, max_chars: int = 1500, *, min_chars: int = MIN_SPOKEN, rest: str = REST) -> str:
    """Gemma's answer as something to say aloud, or "" when it should not be spoken (spec 3.3).

    A call speaks even a one-word answer (min_chars=1) and has no text to point to (rest="")."""
    code = sum(len(block) for block in _FENCE.findall(answer))
    if code * 2 >= len(answer.strip()):
        return ""
    text = without_stage_directions(_FENCE.sub("\n", answer))
    text = _INLINE.sub(r"\1", _LINK.sub(r"\1", text))
    text = _COMMAND.sub(lambda found: f"slash {found.group(1).lower()}", _URL.sub("", text))  # before paths
    text = _EMOJI.sub("", _PATH.sub("", text))
    text = _EMPHASIS.sub("", signal_text.spoken(text))  # the text styles' reading; then any unpaired ** __ ~~
    blocks: list[str] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        structural = False
        if line and _TABLE_RULE.match(line):
            continue
        if line.count("|") >= 2:
            line = "; ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
            structural = True
        elif _HEADING.match(line) or _BULLET.match(line):
            line = _BULLET.sub("", _HEADING.sub("", line))
            structural = True
        if structural or not line:  # a heading, list item or table row is a sentence; a blank line ends one
            if current:
                blocks.append(" ".join(current))
                current = []
            if line:
                blocks.append(line)
            continue
        current.append(line)
    if current:
        blocks.append(" ".join(current))
    sentences = [block if block[-1] in ".!?:;。！？" else block + "." for block in (b.strip() for b in blocks) if block]
    spoken = re.sub(r"\(\s*\)", "", " ".join(sentences))  # brackets that only held a link
    spoken = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", spoken)).strip()
    spoken = _MARKS_RUN.sub(_one_mark, _dots_between_chords(spoken)).strip()
    if len(spoken.rstrip(".")) < min_chars:
        return ""
    if len(spoken) <= max_chars:
        return spoken
    head = spoken[: max_chars - len(rest)]
    ends = [match.end() for match in _SENTENCE_END.finditer(head)]
    cut = head[: ends[-1]] if ends else head.rsplit(" ", 1)[0]
    return cut.rstrip() + rest


_SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")
# Never a sentence end: a title, "vs.", "e.g." or "i.e." before the next word (a name: "Dr. Smith", "PSV vs. Ajax",
# "e.g. Spotify"; the voice service would end a part that stops at "e.g." with a full stop); the others only before a
# lower-case letter or a digit ("8 a.m. now", "No. 5"), so "At 8 a.m. Then we go." still ends after "a.m.". A
# lower-case "no." is the word ("I said no. 5 people agreed."; the voice service reads only "no.7" as a number).
# "N.C." (no chord) too: "Intro: N.C. then G." stays one part, so the voice service reads its chords as one run; and
# before chords (`_chords_follow`: "N.C. G C D"; the review, 18 Oct).
_BEFORE_A_NAME = frozenset({"Dr.", "Mr.", "Mrs.", "Ms.", "St.", "vs.", "e.g.", "E.g.", "i.e.", "I.e."})
_BEFORE_LOWER_CASE = frozenset({"No.", "approx.", "Approx.", "a.m.", "p.m.", "A.M.", "P.M.", "N.C."})
_STRIP_MARKS = ".,;:!?"


def _chords_follow(rest: str) -> bool:
    """Whether `rest` starts with a chord theory's parser reads (in some naming) and then another one or the sentence's
    end ("G C D", "G.", "Sol Re Mim"), never a word ("A good start", "La casa es grande")."""
    words = rest.split(None, 2)[:2]
    if not words or not theory._parses(words[0].strip(_STRIP_MARKS)):
        return False
    return len(words) == 1 or words[0][-1] in ".!?" or theory._parses(words[1].strip(_STRIP_MARKS))


def sentences(spoken: str) -> list[str]:
    """Spoken text split at sentence ends, so a call can start speaking the first one while the rest is made; never
    after a title or an abbreviation inside a sentence (`_BEFORE_A_NAME`, `_BEFORE_LOWER_CASE`), in every language."""
    text = spoken.strip()
    parts: list[str] = []
    start = 0
    for gap in _SENTENCE_GAP.finditer(text):
        words = text[: gap.start()].split()
        last = words[-1].lstrip("(\"'“‘[") if words else ""
        after = text[gap.end() : gap.end() + 1]
        if last in _BEFORE_A_NAME or (last in _BEFORE_LOWER_CASE and (after.islower() or after.isdigit())):
            continue
        if last == "N.C." and _chords_follow(text[gap.end() :]):
            continue
        parts.append(text[start : gap.start()])
        start = gap.end()
    parts.append(text[start:])
    return [part for part in parts if part]


def _private_wav(wav: bytes, folder: Path) -> tuple[Path, Path]:
    """Write the WAV as a private file in a private folder; return it and the M4A path to convert to."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    name = uuid.uuid4().hex
    source, target = folder / f"{name}.wav", folder / f"{name}.m4a"
    fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(wav)
    return source, target


async def to_m4a(wav: bytes, folder: Path) -> Path:
    """WAV → AAC in M4A (a Signal voice note), as a private file in `folder`; the caller deletes it."""
    source, target = await asyncio.to_thread(_private_wav, wav, folder)
    try:
        proc = await asyncio.create_subprocess_exec(
            AFCONVERT,
            "-f",
            "m4af",
            "-d",
            "aac",
            str(source),
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), CONVERT_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise VoiceError("afconvert took too long") from None
        if proc.returncode != 0 or not target.is_file():
            raise VoiceError(f"afconvert failed (exit {proc.returncode})")
        target.chmod(0o600)
        return target
    except BaseException:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        raise
    finally:
        source.unlink(missing_ok=True)
