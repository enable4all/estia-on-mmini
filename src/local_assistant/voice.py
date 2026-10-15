"""Voice notes: what audio a file holds, the local voice service (tools/voice), the spoken text, AAC.

The voice service listens (WhisperKit) and speaks (Kokoro for English, Supertonic-3 for the other languages) on the
Neural Engine, and on a call tells how likely the caller's last 256 ms are speech (Silero VAD through FluidAudio);
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
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_assistant import signal_text
from local_assistant.language import ENGLISH
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
        try:
            status, body = await self._request(
                "POST", f"/vad?stream={stream}", chunk, "application/octet-stream", seconds=VAD_TIMEOUT
            )
            p = json.loads(body).get("p") if status == 200 else None
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


DETECTOR_STATES = ("ready", "loading", "unavailable")  # what the voice service's health says as "vad"


def detector_state(health: Mapping[str, object] | None) -> str:
    """The speech detector's state from the voice service's health: "ready", "loading" (the model loads or downloads
    at start), "unavailable" (its model did not download or failed its check: it needs downloading again), "old voice
    service" (built before the detector) or "not ready" (no answer, or a state this bridge does not know). Anything
    but "ready" means loudness stops her on calls meanwhile."""
    if health is None:
        return "not ready"
    if "vad" not in health:
        return "old voice service"
    state = health["vad"]
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
    "devices",
    "lock",
    "unlock",
    "help",
)
_COMMAND = re.compile(rf"(?<![\w/~.])/({'|'.join(COMMANDS)})(?![\w-]|/|\.\w)", re.IGNORECASE)
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿️‍]")
_EMPHASIS = re.compile(r"\*\*|__|~~")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}")
_HEADING = re.compile(r"^#{1,6}\s+")
_BULLET = re.compile(r"^(?:[-*•+]|\d{1,2}[.)])\s+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|[。！？]")  # Japanese: no space after its sentence ends


def spoken_text(answer: str, max_chars: int = 1500, *, min_chars: int = MIN_SPOKEN, rest: str = REST) -> str:
    """Gemma's answer as something to say aloud, or "" when it should not be spoken (spec 3.3).

    A call speaks even a one-word answer (min_chars=1) and has no text to point to (rest="")."""
    code = sum(len(block) for block in _FENCE.findall(answer))
    if code * 2 >= len(answer.strip()):
        return ""
    text = _FENCE.sub("\n", answer)
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
    spoken = re.sub(r"([.,;:!?])[.,;:!?]+", r"\1", spoken).strip()
    if len(spoken.rstrip(".")) < min_chars:
        return ""
    if len(spoken) <= max_chars:
        return spoken
    head = spoken[: max_chars - len(rest)]
    ends = [match.end() for match in _SENTENCE_END.finditer(head)]
    cut = head[: ends[-1]] if ends else head.rsplit(" ", 1)[0]
    return cut.rstrip() + rest


def sentences(spoken: str) -> list[str]:
    """Spoken text split at sentence ends, so a call can start speaking the first one while the rest is made."""
    return [part for part in re.split(r"(?<=[.!?])\s+|(?<=[。！？])\s*", spoken.strip()) if part]


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
