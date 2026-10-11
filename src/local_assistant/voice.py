"""Voice notes: what audio a file holds, the local voice service (tools/voice), the spoken text, AAC.

The voice service listens (WhisperKit) and speaks (Kokoro for English, Supertonic-3 for the other languages) on the
Neural Engine; the bridge only talks HTTP to it on 127.0.0.1, in one language at a time. Spoken answers are cleaned
deterministically (spec 3.3) and converted to AAC in M4A with macOS's own `afconvert`, so Signal shows a real
voice-note bubble.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_assistant.language import ENGLISH
from local_assistant.part_status import PartStatus

AFCONVERT = "/usr/bin/afconvert"
CONVERT_TIMEOUT = 60.0
MAX_RESPONSE = 50 * 1024 * 1024
MIN_SPOKEN = 10
REST = " The rest is in the text."


class VoiceError(Exception):
    """The voice service or the conversion failed."""


class VoiceUnavailableError(VoiceError):
    """The voice service is not running or still loading its models."""


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
    """HTTP to voice-service on 127.0.0.1 (no proxies). Every request carries the language; English requests carry
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
    ) -> None:
        self._base = base_url.rstrip("/")
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
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            headers = {"Content-Type": content_type} if content_type else {}
            request = urllib.request.Request(self._base + path, data=body, headers=headers, method=method)
            try:
                with opener.open(request, timeout=seconds or self._timeout) as response:
                    return response.status, response.read(MAX_RESPONSE)
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read(64 * 1024)
            except (urllib.error.URLError, OSError) as exc:
                raise VoiceUnavailableError(f"the voice service is not answering ({type(exc).__name__})") from exc

        return await asyncio.to_thread(call)


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
    text = _EMOJI.sub("", _PATH.sub("", _URL.sub("", text)))
    text = _EMPHASIS.sub("", text)
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
