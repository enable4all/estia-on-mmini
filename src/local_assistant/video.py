"""YouTube videos: the strict link check, the captions through the YouTube helper, a long video read in parts, and the
last video kept in memory for follow-up questions.

Only a link in the user's current message is read (tools.read_link), and only the 11-character video ID taken out of
it reaches the helper (tools/youtube), never the link. The bridge runs the helper as it runs the calendar helper: fixed
arguments, no shell, a clean environment, a time limit (the helper stops itself after 20 s; the bridge kills it at
`youtube_helper_timeout`, 30 s), one JSON line back, and only the helper's own error codes are believed. The captions
are outside text and never stored: no document, a stub in the tool log, and the last video's captions in memory only,
per account and conversation, for `KEEP_SECONDS` (10 minutes; /new drops them). A video longer than `CAP_SECONDS`
(2 hours) is read up to there. Captions longer than `PART_CHARS` are split into even parts: Gemma makes notes on each
part in a call of its own (no tools; the user's message in each), and the answer joins the notes; before that, code's
note says how long it will take. Logs hold outcome codes, character counts and times, never the video ID or a title.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from local_assistant import prompts
from local_assistant.claims import sentences
from local_assistant.gemma import GemmaClient
from local_assistant.part_status import PartStatus, Reason

log = logging.getLogger(__name__)

PART = "youtube"  # part_status
CAP_SECONDS = 7_200  # 2 hours of video at most: the first 2 hours of a longer one
PART_CHARS = 15_000  # one part of a long video's captions, at most
CAP_CHARS = 8 * PART_CHARS  # captions read, at most (more than 2 hours of fast speech); the helper keeps the same cap
PART_TOKENS = 400  # one part's notes
PART_SECONDS = 8  # a part's notes, for the note's estimate: about 5 s measured on an M6 Mac mini (evals), with margin
LINE_SECONDS = 30.0  # a time stamp about every 30 s of captions
LINE_CHARS = 2_000  # ... or sooner: a line's words, at most (hand-written captions can pack much into a few seconds)
SEGMENT_CHARS = 1_000  # one caption segment's text, at most
KEEP_SECONDS = 600.0  # the last video's captions stay in memory this long (follow-up questions)
_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_MUSIC = "music.youtube.com"  # YouTube Music: watch links only
_SHORT = "youtu.be"
_PATHS = frozenset({"shorts", "live", "embed"})
_TRAILING = ".,;:!?)]}'\"»"  # a sentence's punctuation after a link (never part of an ID)
_OPENING = "(<[\"'«"  # and before one

Notify = Callable[[str], Awaitable[None]]


def video_id(link: str) -> str | None:
    """The 11-character video ID of a YouTube video link, or None for anything else: https or http (or none) on
    youtube.com, www., m. (watch, shorts, live, embed), music. (watch) or youtu.be; no login, no port, no other host;
    exactly one v= whose raw value is the ID; nothing after the ID in the path."""
    link = link.strip().rstrip(_TRAILING)
    if not link or any(char.isspace() or ord(char) < 32 for char in link):
        return None
    if "://" not in link:
        link = f"https://{link}"
    try:
        parts = urllib.parse.urlsplit(link)
        host, port = parts.hostname or "", parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("https", "http") or port is not None or parts.netloc.lower() != host:
        return None
    path = parts.path
    if host == _SHORT:
        found = path.removeprefix("/").removesuffix("/")
        return found if _ID.fullmatch(found) else None
    if host not in _HOSTS and host != _MUSIC:
        return None
    if path == "/watch":
        values = [value for key, _, value in (pair.partition("=") for pair in parts.query.split("&")) if key == "v"]
        return values[0] if len(values) == 1 and _ID.fullmatch(values[0]) else None
    pieces = path.removesuffix("/").split("/")
    if host != _MUSIC and len(pieces) == 3 and pieces[0] == "" and pieces[1] in _PATHS and _ID.fullmatch(pieces[2]):
        return pieces[2]
    return None


def _bare(word: str) -> str:
    """A word of the message without the punctuation around a link."""
    return word.lstrip(_OPENING).strip().rstrip(_TRAILING)


def link_in(text: str, video: str) -> str | None:
    """The user's own link to this video in their message (its first form there, without the punctuation around it),
    or None. This is the link kept and left tappable, never the one Gemma passed: a planted line could make her add
    another link to the video's."""
    for word in text.split():
        link = _bare(word)
        if video_id(link) == video:
            return link
    return None


def first_link(text: str) -> str | None:
    """The first YouTube video link in the user's message, as link_in finds it, or None (code's line for her prompt:
    tools.video_hint)."""
    for word in text.split():
        found = video_id(_bare(word))
        if found is not None:
            return link_in(text, found)
    return None


@dataclass(frozen=True)
class Captions:
    segments: tuple[tuple[float, str], ...]  # (start in seconds, text), in order
    language: str  # the track's language code ("en", "nl", "en-GB")
    generated: bool  # generated automatically by YouTube (else written by hand)
    length: float  # seconds of the whole video's captions (also past the cap)
    cut: bool  # past CAP_SECONDS or CAP_CHARS: only the first part was read
    size_cut: bool = False  # cut by CAP_CHARS (the bridge's or the helper's), before 2 hours

    @property
    def chars(self) -> int:
        return sum(len(text) for _, text in self.segments)


class VideoError(Exception):
    """A video could not be read: `code` is the helper's (or the bridge's) code, the message code's own words."""

    def __init__(self, code: str) -> None:
        self.code = code if code in prompts.VIDEO_ERRORS else "failed"
        super().__init__(prompts.VIDEO_ERRORS[self.code])


class CaptionSource(Protocol):
    async def captions(self, video: str) -> Captions: ...


# the helper's codes as part_status reasons: None when YouTube answered (the part works, the video has nothing)
_REASONS: dict[str, Reason | None] = {
    "no_captions": None,
    "unavailable": None,
    "age_restricted": None,
    "blocked": "refused",
    "unreachable": "no_answer",
    "timeout": "no_answer",
    "invalid": "helper_failed",
}

_CODES = frozenset({*_REASONS, "refused", "failed"})  # the helper's codes, as logged (anything else: "other")


def _helper_env() -> dict[str, str]:
    """A clean environment: no API keys, proxy settings or other secrets of the bridge's shell."""
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG") if key in os.environ}
    return env | {"PATH": "/usr/bin:/bin"}


class CaptionHelper:
    """The YouTube helper (tools/youtube), run per video with fixed arguments."""

    def __init__(self, command: Sequence[str], timeout: float, *, status: PartStatus | None = None) -> None:
        self._command = tuple(command)
        self._timeout = timeout
        self._status = status or PartStatus(None)

    async def captions(self, video: str) -> Captions:
        """The video's captions up to CAP_SECONDS; VideoError otherwise."""
        if not _ID.fullmatch(video):
            raise VideoError("failed")  # never reached: only a checked ID comes here
        started = time.monotonic()
        try:  # the ID last, after "--": about 1 in 64 IDs starts with "-", which the helper would read as an option
            data = await self._run(["captions", "--until", str(CAP_SECONDS), "--", video])
        except VideoError as exc:
            log.info("youtube helper: %s in %d ms", exc.code, (time.monotonic() - started) * 1000)
            raise
        error = None if data.get("ok") is True else str(data.get("error") or "failed")
        captions = None if error else _captions(data)
        if error is None and captions is None:
            error = "no_captions"
        elapsed = (time.monotonic() - started) * 1000
        if error is not None or captions is None:
            log.info("youtube helper: %s in %d ms", error if error in _CODES else "other", elapsed)
            reason = _REASONS.get(str(error), "bad_answer")
            if reason is None:
                self._status.ok(PART)
            else:
                self._status.failed(PART, reason)
            raise VideoError(str(error))
        log.info(
            "youtube helper: ok, %d chars, %s, %s in %d ms",
            captions.chars,
            "auto-generated" if captions.generated else "manual",
            "cut" if captions.cut else "whole",
            elapsed,
        )
        self._status.ok(PART)
        return captions

    async def _run(self, args: list[str]) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=_helper_env(),
            )
        except OSError:  # not set up yet (tools/youtube/.venv missing)
            self._status.failed(PART, "helper_missing")
            raise VideoError("helper_missing") from None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self._timeout)
        except TimeoutError:
            await _stop(proc)
            self._status.failed(PART, "no_answer")
            raise VideoError("timeout") from None
        except BaseException:  # an outer limit or a shutdown: no helper is left behind
            await asyncio.shield(_stop(proc))
            raise
        try:
            data = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            data = None
        if not isinstance(data, dict):
            self._status.failed(PART, "helper_failed")
            raise VideoError("failed")
        return data


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.kill()
        await proc.wait()


def _captions(data: dict[str, Any]) -> Captions | None:
    """The helper's answer as Captions (odd segments left out, both caps kept here too: CAP_SECONDS and CAP_CHARS);
    None when no words are left."""
    segments: list[tuple[float, str]] = []
    cut = bool(data.get("cut"))
    size_cut = cut and data.get("cut_by") == "size"
    chars = 0
    for item in data.get("segments") or []:
        if not isinstance(item, list) or len(item) != 2:
            continue
        start, words = item
        if isinstance(start, bool) or not isinstance(start, int | float) or not isinstance(words, str):
            continue
        if start < 0 or not words.strip():
            continue
        if start >= CAP_SECONDS:
            cut, size_cut = True, False
            break
        text = " ".join(words.split())[:SEGMENT_CHARS]
        if chars + len(text) > CAP_CHARS:
            cut = size_cut = True
            break
        segments.append((float(start), text))
        chars += len(text)
    if not segments:
        return None
    length = data.get("length")
    seconds = float(length) if isinstance(length, int | float) and not isinstance(length, bool) else segments[-1][0]
    language = str(data.get("language") or "")
    return Captions(
        segments=tuple(segments),
        language=language if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*", language) else "unknown",
        generated=bool(data.get("generated")),
        length=max(seconds, segments[-1][0]),
        cut=cut,
        size_cut=size_cut,
    )


def stamp(seconds: float) -> str:
    """A time in the video: 1:05, 12:30, 1:02:05."""
    whole = int(seconds)
    hours, rest = divmod(whole, 3_600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def length_words(seconds: float) -> str:
    """A video's length in words: 25 min, 1 h 40 min, 2 h."""
    minutes = max(1, round(seconds / 60))
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h" + (f" {rest} min" if rest else "")


def caption_lines(captions: Captions) -> list[str]:
    """The captions as lines, each starting with its time, a new line about every LINE_SECONDS, or sooner when its words
    would pass LINE_CHARS."""
    lines: list[str] = []
    start: float | None = None
    words: list[str] = []
    size = 0
    for at, text in captions.segments:
        if start is not None and (at - start >= LINE_SECONDS or size + 1 + len(text) > LINE_CHARS):
            lines.append(f"[{stamp(start)}] {' '.join(words)}")
            start, words, size = None, [], 0
        if start is None:
            start = at
        size += len(text) + (1 if words else 0)
        words.append(text)
    if start is not None:
        lines.append(f"[{stamp(start)}] {' '.join(words)}")
    return lines


@dataclass(frozen=True)
class Part:
    start: float  # its first line's time
    end: float  # the next part's start (the captions' end for the last)
    text: str


def _line_start(line: str) -> float:
    clock = line[1 : line.index("]")].split(":")
    return float(sum(int(value) * 60**power for power, value in enumerate(reversed(clock))))


def _pieces(line: str, size: int) -> list[str]:
    """A line no longer than `size`, else cut into pieces that are, each with the line's time."""
    if len(line) <= size:
        return [line]
    head = line[: line.index("] ") + 2]
    body, room = line[len(head) :], max(1, size - len(head))
    return [head + body[at : at + room] for at in range(0, len(body), room)]


def split(captions: Captions, size: int = PART_CHARS) -> list[Part]:
    """The captions in even parts of whole lines, each at most `size` characters (a longer line is cut, its time on
    each piece); a part closes early rather than pass `size`, so there may be one more part than the even count."""
    lines = [piece for line in caption_lines(captions) for piece in _pieces(line, size)]
    total = len("\n".join(lines))
    count = max(1, math.ceil(total / size))
    target = total / count
    groups: list[list[str]] = [[]]
    filled = 0  # the group's characters, each line's line break counted
    for line in lines:
        if groups[-1] and ((len(groups) < count and filled + len(line) > target) or filled + len(line) > size):
            groups.append([])
            filled = 0
        groups[-1].append(line)
        filled += len(line) + 1
    starts = [_line_start(group[0]) for group in groups]
    ends = [*starts[1:], max(captions.length if not captions.cut else captions.segments[-1][0], starts[-1])]
    return [Part(start, end, "\n".join(group)) for start, end, group in zip(starts, ends, groups, strict=True)]


def reading_note(captions: Captions, parts: int) -> str:
    """Code's note before a long video is read in parts: its length and the wait, estimated from the part count."""
    minutes = max(1, round((parts + 1) * PART_SECONDS / 60))
    wait = f"{minutes} minute{'' if minutes == 1 else 's'}"
    if captions.size_cut:
        template = prompts.VIDEO_READING_SIZE
    else:
        template = prompts.VIDEO_READING_CUT if captions.cut else prompts.VIDEO_READING
    return template.format(length=length_words(captions.length), read=read_words(captions), minutes=wait)


def read_words(captions: Captions) -> str:
    """How much of a cut video was read, in words: up to its last caption read."""
    return length_words(captions.segments[-1][0])


COPY_WORDS = 13  # this many caption words in a row in her answer is a copy (more than 12): the sentence goes
SHARE_WORDS = 6  # shorter runs from this long count too, when together they hold more than COPY_SHARE of the captions
COPY_SHARE = 0.3
# scripts written without spaces (Chinese, Japanese, Thai): each character counts as a word
_NO_SPACES = "฀-๿぀-ヿ㐀-䶿一-鿿豈-﫿"
_TOKEN = re.compile(rf"[{_NO_SPACES}]|[^\W{_NO_SPACES}]+")


def _tokens(text: str) -> list[str]:
    """The words of a text for comparing with captions: no case, no punctuation, no time stamps' colons."""
    return _TOKEN.findall(text.casefold())


def _runs(words: Sequence[str], grams: set[tuple[str, ...]], size: int) -> list[bool]:
    """Which of `words` lie in a run of at least `size` words that the captions have in that order."""
    marked = [False] * len(words)
    for at in range(len(words) - size + 1):
        if tuple(words[at : at + size]) in grams:
            marked[at : at + size] = [True] * size
    return marked


def _grams(words: Sequence[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(words[at : at + size]) for at in range(len(words) - size + 1)}


def without_copies(text: str, captions: Captions) -> tuple[str, bool]:
    """Her answer without the sentences that write the captions out, and whether any went: a sentence holding
    COPY_WORDS caption words in a row (case and punctuation aside, also across sentences), and, when runs of
    SHARE_WORDS or more hold more than COPY_SHARE of the captions' words together, every sentence holding one. Her
    other sentences and line breaks stay; "" when nothing is left."""
    said = _tokens(" ".join(words for _, words in captions.segments))
    lines = [sentences(line) for line in text.splitlines()]
    words: list[str] = []
    owner: list[tuple[int, int]] = []  # each word's (line, sentence)
    for row, line in enumerate(lines):
        for number, sentence in enumerate(line):
            found = _tokens(sentence)
            words += found
            owner += [(row, number)] * len(found)
    marked = _runs(words, _grams(said, COPY_WORDS), COPY_WORDS)
    shared = _runs(words, _grams(said, SHARE_WORDS), SHARE_WORDS)
    if sum(shared) > COPY_SHARE * len(said):
        marked = [one or other for one, other in zip(marked, shared, strict=True)]
    gone = {owner[at] for at, copied in enumerate(marked) if copied}
    if not gone:
        return text, False
    kept_lines: list[str] = []
    for row, line in enumerate(lines):
        kept = [sentence for number, sentence in enumerate(line) if (row, number) not in gone]
        if kept or not line:
            kept_lines.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip(), True


@dataclass(frozen=True)
class Kept:
    """The last video read for an account: its captions, in memory only."""

    video: str
    link: str  # as the user sent it (in an answer it stays tappable)
    captions: Captions
    conversation_id: int
    at: float


class KeptVideos:
    """The last video of each account, for follow-up questions: in memory only, never on disk, for `seconds` from
    when it was read, in the conversation it was read in (/new starts without it). A timer drops it when time is up,
    so it never waits for the next question to go."""

    def __init__(self, *, seconds: float = KEEP_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._seconds = seconds
        self._clock = clock
        self._videos: dict[str, Kept] = {}

    def keep(self, aci: str, conversation_id: int, video: str, link: str, captions: Captions) -> Kept:
        kept = Kept(video, link, captions, conversation_id, self._clock())
        self._videos[aci.lower()] = kept
        with contextlib.suppress(RuntimeError):  # no loop (a test): the next read drops it
            asyncio.get_running_loop().call_later(self._seconds + 0.01, self.expire)
        return kept

    def get(self, aci: str, conversation_id: int, video: str) -> Kept | None:
        """The kept video when it is this one, of this account and conversation, within the time; else None (an
        expired or other conversation's video is dropped)."""
        kept = self._videos.get(aci.lower())
        if kept is None:
            return None
        if kept.conversation_id != conversation_id or self._clock() - kept.at > self._seconds:
            del self._videos[aci.lower()]
            return None
        return kept if kept.video == video else None

    def last(self, aci: str, conversation_id: int) -> Kept | None:
        """This account's kept video in this conversation, whichever it is, within the time (None otherwise); only
        looks, never drops."""
        kept = self._videos.get(aci.lower())
        if kept is None or kept.conversation_id != conversation_id or self._clock() - kept.at > self._seconds:
            return None
        return kept

    def left(self, kept: Kept) -> float:
        """Seconds until it goes."""
        return max(0.0, self._seconds - (self._clock() - kept.at))

    def expire(self) -> None:
        now = self._clock()
        for aci in [aci for aci, kept in self._videos.items() if now - kept.at > self._seconds]:
            del self._videos[aci]

    def clear(self) -> None:
        self._videos.clear()

    def held(self) -> int:
        return len(self._videos)


class VideoReader:
    """Reads a video for an answer: its captions through the helper (kept for follow-ups), then the material Gemma
    answers from: the captions themselves, or notes made part by part."""

    def __init__(
        self,
        helper: CaptionSource,
        gemma: GemmaClient,
        *,
        kept: KeptVideos | None = None,
        part_chars: int = PART_CHARS,
    ) -> None:
        self._helper = helper
        self._gemma = gemma
        self._kept = kept or KeptVideos()
        self._part_chars = part_chars

    def kept(self, aci: str, conversation_id: int, video: str) -> Kept | None:
        return self._kept.get(aci, conversation_id, video)

    def last(self, aci: str, conversation_id: int) -> tuple[Kept, int] | None:
        """This account's kept video in this conversation and the whole minutes it stays (rounded up), if any."""
        kept = self._kept.last(aci, conversation_id)
        return (kept, max(1, math.ceil(self._kept.left(kept) / 60))) if kept is not None else None

    def clear(self) -> None:
        """/new: every kept video goes."""
        self._kept.clear()

    def held(self) -> int:
        """How many videos are kept (one at most per account)."""
        return self._kept.held()

    async def fetch(self, aci: str, conversation_id: int, video: str, link: str) -> Kept:
        """The video's captions from the helper, kept as this account's last video; VideoError otherwise."""
        captions = await self._helper.captions(video)
        return self._kept.keep(aci, conversation_id, video, link, captions)

    async def material(self, kept: Kept, question: str, notify: Notify | None = None) -> str:
        """What Gemma answers from (a read_link result): the captions with their times when they fit in one part;
        else, after code's note (`notify`), notes on each part, made in a call of their own with the user's message."""
        captions = kept.captions
        extra = prompts.VIDEO_GENERATED if captions.generated else ""
        if captions.size_cut:
            extra += prompts.VIDEO_CUT_SIZE.format(read=read_words(captions), length=length_words(captions.length))
        elif captions.cut:
            extra += prompts.VIDEO_CUT.format(length=length_words(captions.length))
        fields = {
            "kind": prompts.VIDEO_KINDS[captions.generated],
            "language": captions.language,
            "extra": extra,
            "link": kept.link,
        }
        parts = split(captions, self._part_chars)
        if len(parts) == 1:
            return prompts.VIDEO_CAPTIONS.format(**fields, captions=parts[0].text)
        if notify is not None:
            await notify(reading_note(captions, len(parts)))
        started = time.monotonic()
        notes: list[str] = []
        for number, part in enumerate(parts, 1):
            head = prompts.VIDEO_PART_HEAD.format(
                number=number, count=len(parts), start=stamp(part.start), end=stamp(part.end)
            )
            notes.append(f"{head}\n{await self._notes(question, part, number, len(parts))}")
        log.info("youtube: %d parts, %d chars, notes in %.0f s", len(parts), captions.chars, time.monotonic() - started)
        return prompts.VIDEO_NOTES.format(**fields, notes="\n\n".join(notes))

    async def _notes(self, question: str, part: Part, number: int, count: int) -> str:
        text = prompts.VIDEO_PART_INPUT.format(
            question=question, number=number, count=count, start=stamp(part.start), end=stamp(part.end), text=part.text
        )
        messages = [{"role": "system", "content": prompts.VIDEO_PART_RULES}, {"role": "user", "content": text}]
        completion = await self._gemma.chat(messages, max_tokens=PART_TOKENS, tools=None)
        return completion.content.strip() or prompts.VIDEO_NO_NOTES
