"""The audio of one call: the call helper's Unix socket (48 kHz mono s16le, 960-byte frames each way), end-of-turn
detection and barge-in. Standard library only. Times are counted in frames of the call helper's 10 ms clock, not in
wall time, so a busy or throttled bridge cannot change what counts as a pause.

The call helper keeps the clock: it sends one frame of the caller every 10 ms and keeps only the newest 80 ms of
what we send. So we send exactly one frame for every frame we get (silence when she is quiet), after a 30 ms
cushion, and never a burst. Tuning comes from the call spike of 2026-10-09 (offline tests and live calls).
"""

from __future__ import annotations

import array
import asyncio
import collections
import io
import logging
import math
import random
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

RATE = 48_000
FRAME_BYTES = 960  # 10 ms of 48 kHz mono 16-bit
FRAME_S = 0.010
CUSHION_FRAMES = 3

# Barge-in: after the first 300 ms of her speech (whose level is the line's floor: echo and noise), 12 loud frames
# within the last 25 (about 120 ms of sound in 250 ms) stop her. Loudness alone cannot tell a cough from a word:
# the caller checks the words (calls.py) and lets her carry on after a cough.
BARGE_FLOOR_FRAMES = 30
BARGE_WINDOW = 25
BARGE_NEEDED = 12
BARGE_MIN_RMS = 400.0
BARGE_FLOOR_FACTOR = 3.5

# Barge-in by speech ([calls] barge = "speech"; spike of 2026-10-13, rule D5): the voice service's speech detector
# (Silero VAD through FluidAudio) rates each 256 ms chunk of the caller's audio (4096 samples at 16 kHz, 25.6
# frames); one chunk with p >= 0.99 that is also louder than max(3x the floor, 150) stops her. In 100 minutes of
# recorded car noise it stopped her 0 times (loudness 42.5 per 10 minutes) and still caught 99% of interruptions, about
# 0.1 s later than loudness. A chunk's level is the power mean of the 10 ms frames inside it. The detector is asked
# without holding up the frames; an answer for audio older than 600 ms is dropped, and 3 failed or late answers in a
# row hand over to loudness for the rest of the call. Whichever rule is not in charge runs beside it as a shadow, never
# acted on (Barge counts it), so every call shows what the other one would have done.
VAD_CHUNK = 4096
SPEECH_P = 0.99
SPEECH_MIN_RMS = 150.0
SPEECH_FLOOR_FACTOR = 3.0
VAD_MAX_AGE_FRAMES = 60
VAD_FAILURES = 3
VAD_CHUNK_FRAMES = math.ceil(VAD_CHUNK / 160)  # 26 (160 samples at 16 kHz a frame): how far back a chunk begins
# The caller's last frames before the stop, the first words of the interruption: enough to reach back to the first
# frame of a chunk whose answer came VAD_MAX_AGE_FRAMES late (870 ms; loudness needs about 800)
PREROLL_FRAMES = VAD_MAX_AGE_FRAMES + VAD_CHUNK_FRAMES + 1
# Her own voice (a loud speaker next to the caller's microphone): after a stop on it, a stop needs this much more than
# the level that stopped her, for the rest of the call (calls.py tells; spike: own-voice stops at -10 dB from 100% to
# 23-27%)
OWN_VOICE_FACTOR = 1.5
RULES = ("loudness", "speech")

# Listening: the first 200 ms measure the floor (those frames are kept and judged once it is known: a caller who
# answers the moment she stops is heard from the start); 10 loud frames in a row start an utterance, and the 200 ms
# before them go with it (a soft start: the "s" of "stop"); it ends after end_silence_s of quiet (the trailing quiet
# is cut to 200 ms). Live calls 2026-10-11: laptop typing near the phone became "*sad music*" and "thank" (Whisper's
# words for noise). Typing can be as loud as speech, but a key click lasts 40-100 ms and speech is sustained: 100 ms
# of sound in a row, above typing level and 4x the line's quiet level (OpenWhisperer's factor). The same rule
# sorts voice from clicks inside an utterance: only loud frames in runs of LISTEN_START_FRAMES or more are sustained
# voice (typing over her answer is loud often, never for long). Each utterance logs its levels (metadata) to tune this.
LISTEN_FLOOR_FRAMES = 20
LISTEN_START_FRAMES = 10
LISTEN_PREROLL_FRAMES = 20
LISTEN_MIN_RMS = 450.0
LISTEN_FLOOR_FACTOR = 4.0
TAIL_FRAMES = 20
ECHO_TAIL_FRAMES = 15  # after she stops, the caller's next 150 ms may still carry her voice
# The floor is the line's quiet level: the quietest frames (10th percentile), capped, so a caller who is already
# talking when the measuring starts does not raise it above their own voice. Phones suppress noise: on live calls
# the floor was 1-3 RMS.
FLOOR_CAP = 150.0


def floor_of(levels: list[float]) -> float:
    ordered = sorted(levels)
    return min(ordered[len(ordered) // 10], FLOOR_CAP) if ordered else 0.0


class _Voice:
    """Counts an utterance's loud frames (voiced) and those in runs of LISTEN_START_FRAMES or more (sustained: a key
    click lasts 40-100 ms, a syllable longer). A run goes on across the pre-roll and the frames after it."""

    def __init__(self) -> None:
        self.voiced = self.sustained = self.run = 0

    def add(self, loud: bool) -> None:
        if not loud:
            self.run = 0
            return
        self.voiced += 1
        self.run += 1
        if self.run == LISTEN_START_FRAMES:
            self.sustained += LISTEN_START_FRAMES
        elif self.run > LISTEN_START_FRAMES:
            self.sustained += 1


@dataclass(frozen=True)
class Said:
    """How a spoken clip went: its length, how much of it went out, and whether the caller cut in (then the line's
    floor, the level of the sound that stopped her and the shadow rules that fired too)."""

    total_s: float
    played_s: float
    barged: bool
    preroll: tuple[bytes, ...] = field(default=(), repr=False)
    floor: float = 0.0
    level: float = 0.0
    shadows: tuple[str, ...] = ()  # the other rules that fired in the clip before she was stopped (Barge.false_stop)


def rms(pcm: bytes) -> float:
    samples = array.array("h", pcm)
    return math.sqrt(sum(x * x for x in samples) / len(samples)) if samples else 0.0


def silence(ms: int) -> bytes:
    return bytes(2 * (RATE * ms // 1000))


def faint_noise(ms: int, level: int = 40) -> bytes:
    """Barely audible noise (about -63 dBFS): a lead-in that wakes the call's audio before the first word, where
    digital silence can let the line fall quiet and clip the start of a sentence."""
    rnd = random.Random(1)
    return array.array("h", (rnd.randint(-level, level) for _ in range(RATE * ms // 1000))).tobytes()


def wav_to_pcm48(data: bytes) -> bytes:
    """A 16-bit WAV (the voice service speaks at 24 kHz) as 48 kHz mono samples for the call."""
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected a 16-bit WAV")
        rate, channels = w.getframerate(), w.getnchannels()
        raw = array.array("h", w.readframes(w.getnframes()))
    mono = (
        raw
        if channels == 1
        else array.array("h", (sum(raw[i : i + channels]) // channels for i in range(0, len(raw), channels)))
    )
    if rate == RATE or not mono:
        return mono.tobytes()
    if rate * 2 == RATE:  # exact 2x: every sample, then the midpoint to the next
        out = array.array("h", bytes(4 * len(mono)))
        out[0::2] = mono
        nxt = mono[1:] + array.array("h", [mono[-1]])
        out[1::2] = array.array("h", ((a + b) // 2 for a, b in zip(mono, nxt, strict=True)))
        return out.tobytes()
    count = len(mono) * RATE // rate
    step = rate / RATE
    last = len(mono) - 1
    result = array.array("h", bytes(2 * count))
    for i in range(count):
        pos = i * step
        j = int(pos)
        a, b = mono[min(j, last)], mono[min(j + 1, last)]
        result[i] = int(a + (b - a) * (pos - j))
    return result.tobytes()


def to16k(pcm: bytes) -> array.array[int]:
    """48 kHz samples as 16 kHz ones: the average of every three."""
    samples = array.array("h", pcm)
    usable = len(samples) - len(samples) % 3
    return array.array("h", ((samples[i] + samples[i + 1] + samples[i + 2]) // 3 for i in range(0, usable, 3)))


def pcm48_to_wav16k(pcm: bytes) -> bytes:
    """The caller's audio as a 16 kHz WAV for transcription (Whisper's rate; a third of the upload)."""
    low = to16k(pcm)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(low.tobytes())
    return buf.getvalue()


def power_mean(levels: list[float]) -> float:
    return math.sqrt(sum(x * x for x in levels) / len(levels)) if levels else 0.0


class Chunks:
    """The caller's frames as the speech detector's chunks: 16 kHz (the same average transcription uses), VAD_CHUNK
    samples each; a chunk's level is the power mean of the 48 kHz frame levels fully inside it (a frame split between
    two chunks counts for neither)."""

    def __init__(self) -> None:
        self._samples: array.array[int] = array.array("h")
        self._levels: list[float] = []

    def add(self, frame: bytes, level: float) -> tuple[bytes, float] | None:
        """Add one frame; a chunk and its level once one is full."""
        low = to16k(frame)
        room = VAD_CHUNK - len(self._samples)
        self._samples.extend(low[:room])
        if len(low) <= room:
            self._levels.append(level)
        if len(self._samples) < VAD_CHUNK:
            return None
        chunk = (self._samples.tobytes(), power_mean(self._levels))
        self._samples, self._levels = low[room:], []
        return chunk


# one chunk -> its speech probability, or why there is none: "timeout" (no answer in time) or "http" (no service, a
# refusal, an answer that is not a probability)
Detect = Callable[[bytes], Awaitable[float | str]]


def _taken(task: asyncio.Task[float | str]) -> None:
    """An answer's error, if any, is taken even when nobody waits for it any more (asyncio would log it)."""
    if not task.cancelled():
        task.exception()


def _log_gated(gated: list[tuple[float, float]], floor: float) -> None:
    """One line a clip, to tune the level gate: the loudest chunk it turned away that the detector heard as speech
    (numbers only, never audio or words)."""
    if gated:
        p, level = gated[0]
        log.info("calls: speech gated (p %.2f, level %.0f, floor %.0f)", p, level, floor)


def _times(count: int) -> str:
    return f"{count} time{'' if count == 1 else 's'}"


@dataclass
class _Asked:
    """A chunk sent to the speech detector: its answer, the frame it ended on (its age) and its level."""

    answer: asyncio.Task[float | str]
    frame: int
    level: float


class Barge:
    """One call's rules for the caller cutting in (see the constants): "loudness" (today's rule) or "speech" (the
    speech detector and a level gate). The rule in charge stops her (`stopped`); the other one is a shadow, never
    acted on: `would` counts the clips where it fired and the rule in charge did not stop her, and the ones where it
    fired with a stop that calls.py found to be no words or her own voice (`false_stop`); each rule at most once a
    clip. Without a detector, or after VAD_FAILURES failed or late answers in a row, loudness is in charge and nothing
    is measured by speech for the rest of the call."""

    def __init__(
        self, rule: str = "loudness", detect: Detect | None = None, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.rule = rule
        self._detect = detect
        self._clock = clock
        self.measured = detect is not None  # the call had a detector (it may have failed since)
        self.gave_up = False  # the detector failed VAD_FAILURES times in a row during the call
        self.failures = 0  # failed or late answers in a row (a good answer starts again at 0)
        self.failed: collections.Counter[str] = (
            collections.Counter()
        )  # every failed answer by kind: timeout, http, late
        self.chunks = 0  # chunks the detector was asked about
        self.slowest = 0.0  # seconds from a chunk's end to its answer, the longest in the call
        self.least = 0.0  # her own voice was heard: a stop needs more than this (both rules)
        self.stopped = dict.fromkeys(RULES, 0)  # stops by each rule while it was in charge
        self.would = dict.fromkeys(RULES, 0)  # stops each rule would have made as the shadow
        self.own_voice = 0
        self._last: asyncio.Task[float | str] | None = None
        self._asking: set[asyncio.Task[float | str]] = set()

    @property
    def detecting(self) -> bool:
        return self._detect is not None

    @property
    def active(self) -> str:
        return "speech" if self.rule == "speech" and self.detecting else "loudness"

    def ask(self, chunk: bytes) -> asyncio.Task[float | str]:
        """The detector's answer for `chunk`, without waiting for it; chunks go out one after the other, in order
        (the service keeps the call's detector state)."""
        detect, before, asked = self._detect, self._last, self._clock()
        assert detect is not None

        async def answer() -> float | str:
            if before is not None and not before.done():
                await asyncio.wait({before})
            try:
                result = await detect(chunk)
            except Exception:  # a detector never raises into the call
                result = "error"
            self.slowest = max(self.slowest, self._clock() - asked)
            return result

        self.chunks += 1
        task = asyncio.create_task(answer())
        self._last = task
        self._asking.add(task)
        task.add_done_callback(self._asking.discard)
        task.add_done_callback(_taken)
        return task

    def answered(self) -> None:
        """A good answer: the failures in a row start again."""
        self.failures = 0

    def fail(self, kind: str) -> None:
        """A failed answer ("timeout", "http") or a late one ("late"); VAD_FAILURES in a row end the detector."""
        self.failures += 1
        self.failed[kind] += 1
        if self.failures >= VAD_FAILURES and self._detect is not None:
            self._detect = None
            self.gave_up = True
            then = "loudness for the rest of the call" if self.rule == "speech" else "no speech shadow from now on"
            log.info("calls: the speech detector failed %d times in a row; %s", self.failures, then)

    def false_stop(self, said: Said) -> None:
        """The stop `said` was no words or her own voice (calls.py): the shadows that fired in that clip would have
        made it too."""
        for rule in said.shadows:
            self.would[rule] += 1

    def her_own_voice(self, level: float) -> None:
        """Her own voice stopped her at `level` (calls.py heard her sentence in the transcript): the first time in a
        call, a stop needs OWN_VOICE_FACTOR times that from now on; later times are only counted (raised once, never
        compounding, so a caller can always still stop her)."""
        if not self.own_voice:
            self.least = max(self.least, OWN_VOICE_FACTOR * level)
        self.own_voice += 1

    def summary(self) -> str:
        """The call's end line (metadata): the configured rule and, when loudness took over, why; for each rule
        measured, how often it stopped her in charge and would have as the shadow; her own voice; the detector's
        chunks, slowest answer and failures by kind."""
        head = f"calls: barge-in {self.rule}"
        if not self.measured:
            head += " (no speech detector)" if self.rule == "loudness" else " (loudness: no speech detector)"
        elif self.gave_up:
            then = "loudness" if self.rule == "speech" else "no speech shadow"
            head += f" ({then} after {VAD_FAILURES} failed answers in a row)"
        rules = [self.rule, *(rule for rule in RULES if rule != self.rule)]
        counts = "; ".join(
            f"{rule} stopped her {_times(self.stopped[rule])}, would have {self.would[rule]}"
            for rule in rules
            if rule == "loudness" or self.measured
        )
        line = f"{head}: {counts}; her own voice {self.own_voice}"
        if not self.measured:
            return line
        failed = sum(self.failed.values())
        line += f"; detector {self.chunks} chunks, slowest {round(self.slowest * 1000)} ms, {failed} failed"
        if failed:
            line += " (" + ", ".join(f"{kind} {count}" for kind, count in sorted(self.failed.items())) + ")"
        return line

    def close(self) -> None:
        for task in list(self._asking):
            task.cancel()


class CallAudio:
    """One connection to the call helper's socket: speak (with or without barge-in) and listen."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, barge: Barge | None = None) -> None:
        self._reader, self._writer = reader, writer
        self.barge = barge or Barge()
        self._outgoing = bytearray()
        self._sent = 0  # bytes of the current clip already sent
        self.speaking = asyncio.Event()
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = asyncio.Event()
        self.started = asyncio.Event()  # the helper's first frame: the call's audio runs
        self.frames_in = 0
        self.frames_out = 0

    async def pump(self) -> None:
        """Frame in, frame out, until the helper closes the socket."""
        try:
            self._writer.write(bytes(CUSHION_FRAMES * FRAME_BYTES))
            await self._writer.drain()
            while True:
                frame = await self._reader.readexactly(FRAME_BYTES)
                self.frames_in += 1
                self.started.set()
                self.incoming.put_nowait(frame)
                chunk = bytes(self._outgoing[:FRAME_BYTES])
                del self._outgoing[:FRAME_BYTES]
                self._sent += len(chunk)
                if not self._outgoing:
                    self.speaking.clear()
                self._writer.write(chunk.ljust(FRAME_BYTES, b"\0"))
                await self._writer.drain()
                self.frames_out += 1
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.closed.set()
            self.speaking.clear()
            self.started.set()

    def close(self) -> None:
        self._writer.close()

    def drain_incoming(self) -> None:
        while not self.incoming.empty():
            self.incoming.get_nowait()

    def stop_speaking(self) -> None:
        self._outgoing.clear()
        self.speaking.clear()

    async def say(self, pcm: bytes, *, barge: bool = False, fresh: bool = True) -> Said:
        """Speak `pcm` and return when it went out. With barge=True the caller can cut in (see the constants and
        `Barge`). fresh: a new answer starts, so what came in meanwhile (while she was thinking) is dropped; the next
        sentence of the same answer keeps it (the caller may have started to talk between two sentences)."""
        if fresh:
            self.drain_incoming()
        total = len(pcm) / (2 * RATE)
        self._outgoing = bytearray(pcm)
        self._sent = 0
        self.speaking.set()
        rules = self.barge
        recent: collections.deque[bytes] = collections.deque(maxlen=PREROLL_FRAMES)
        loud: collections.deque[tuple[bool, float]] = collections.deque(maxlen=BARGE_WINDOW)
        floor_levels: list[float] = []
        chunks = Chunks()
        asked: list[_Asked] = []
        fired: set[str] = set()  # the rules that fired in this clip (each counts once a clip)
        gated: list[tuple[float, float]] = []  # the loudest speech chunk the level gate turned away: (p, level)
        floor = 0.0
        frames = 0
        while self.speaking.is_set() and not self.closed.is_set():
            try:
                frame = await asyncio.wait_for(self.incoming.get(), 0.5)
            except TimeoutError:
                continue
            if not barge:
                continue
            frames += 1
            level = rms(frame)
            recent.append(frame)
            if rules.detecting and (chunk := chunks.add(frame, level)) is not None:
                asked.append(_Asked(rules.ask(chunk[0]), frames, chunk[1]))
            if len(floor_levels) < BARGE_FLOOR_FRAMES:
                floor_levels.append(level)
                continue
            floor = floor_of(floor_levels)
            loud.append((level > max(BARGE_FLOOR_FACTOR * floor, BARGE_MIN_RMS, rules.least), level))
            stops: dict[str, tuple[float, str]] = {}  # rule -> the level that stopped her, how (the log)
            loud_levels = [lvl for is_loud, lvl in loud if is_loud]
            if len(loud_levels) >= BARGE_NEEDED:
                stops["loudness"] = (power_mean(loud_levels), f"{len(loud_levels)} loud frames")
            heard = self._speech(asked, frames, floor, gated)
            if heard is not None:
                stops["speech"] = (heard[1], f"p {heard[0]:.2f}")
            fired |= stops.keys()
            active = rules.active
            if active in stops:
                played = self._sent / (2 * RATE)
                self.stop_speaking()
                rules.stopped[active] += 1
                stopper, how = stops[active]
                log.info("calls: stopped by %s (%s, level %.0f, floor %.0f)", active, how, stopper, floor)
                _log_gated(gated, floor)
                shadows = tuple(sorted(fired - {active}))  # counted only if the stop was no words (false_stop)
                return Said(round(total, 2), round(played, 2), True, tuple(recent), floor, stopper, shadows)
        for rule in fired:  # she was not stopped: each rule that fired was the shadow
            rules.would[rule] += 1
        _log_gated(gated, floor)
        await self._skip(ECHO_TAIL_FRAMES)
        return Said(round(total, 2), round(self._sent / (2 * RATE), 2), False)

    def _speech(
        self, asked: list[_Asked], frames: int, floor: float, gated: list[tuple[float, float]]
    ) -> tuple[float, float] | None:
        """The detector's answers that came in, in order: (p, level) of a chunk that is speech and loud enough, else
        None; the loudest speech chunk that was not loud enough goes in `gated`. A failed answer, or one still missing
        for audio over VAD_MAX_AGE_FRAMES old ("late"), is a failure."""
        rules = self.barge
        heard: tuple[float, float] | None = None
        for item in list(asked):
            if not rules.detecting:
                asked.clear()
                break
            if frames - item.frame > VAD_MAX_AGE_FRAMES:
                asked.remove(item)
                rules.fail("late")
                continue
            if not item.answer.done():
                continue
            asked.remove(item)
            p = "error" if item.answer.cancelled() or item.answer.exception() else item.answer.result()
            if isinstance(p, str):
                rules.fail(p)
                continue
            rules.answered()
            if p < SPEECH_P:
                continue
            if item.level > max(SPEECH_FLOOR_FACTOR * floor, SPEECH_MIN_RMS, rules.least):
                heard = (p, item.level)
            elif not gated or item.level > gated[0][1]:
                gated[:] = [(p, item.level)]
        return heard

    async def _skip(self, frames: int) -> None:
        """Drop exactly the next `frames` frames (her echo tail); later frames are the caller's and stay."""
        for _ in range(frames):
            if self.closed.is_set() and self.incoming.empty():
                return
            try:
                await asyncio.wait_for(self.incoming.get(), 0.5)
            except TimeoutError:
                return

    async def listen(
        self,
        *,
        preroll: tuple[bytes, ...] = (),
        floor: float = 0.0,
        end_silence_s: float = 1.8,
        no_speech_s: float | None = None,
        max_s: float = 120.0,
    ) -> tuple[bytes, dict[str, Any]]:
        """The caller's next utterance, or b"" when nobody spoke within no_speech_s (frames of call time).
        After a barge-in, start inside the caller's speech with the pre-roll. ConnectionError when the call's
        audio ends."""
        got = bytearray()
        floor_levels: list[float] = []
        measuring: list[tuple[bytes, float]] = []  # the floor's frames, judged once the floor is known
        onset: collections.deque[bytes] = collections.deque(maxlen=LISTEN_PREROLL_FRAMES + LISTEN_START_FRAMES)
        started = bool(preroll)
        quiet_run = waited = 0
        voice = _Voice()
        if preroll:
            got += b"".join(preroll)
            floor_levels = [floor] * LISTEN_FLOOR_FRAMES
            # words said over her are judged by their voiced time, and much of it is in the pre-roll
            for f in preroll:
                voice.add(rms(f) > max(LISTEN_FLOOR_FACTOR * floor, LISTEN_MIN_RMS))
        peak = 0.0
        while True:
            if self.closed.is_set() and self.incoming.empty():
                raise ConnectionError("the call's audio ended")
            try:
                frame = await asyncio.wait_for(self.incoming.get(), 0.5)
            except TimeoutError:
                continue
            waited += 1
            level = rms(frame)
            peak = max(peak, level)
            if len(floor_levels) < LISTEN_FLOOR_FRAMES:
                floor_levels.append(level)
                measuring.append((frame, level))
                if len(floor_levels) < LISTEN_FLOOR_FRAMES:
                    continue
            batch, measuring = measuring or [(frame, level)], []
            floor_now = floor_of(floor_levels)
            threshold = max(LISTEN_FLOOR_FACTOR * floor_now, LISTEN_MIN_RMS)
            for frame_now, level_now in batch:
                loud = level_now > threshold
                if not started:
                    onset.append(frame_now)
                    if not loud:
                        voice = _Voice()  # only the run that starts the utterance counts
                    voice.add(loud)
                    if voice.run >= LISTEN_START_FRAMES:
                        started = True
                        got += b"".join(onset)
                    continue
                got += frame_now
                voice.add(loud)
                quiet_run = 0 if loud else quiet_run + 1
                too_long = len(got) / (2 * RATE) >= max_s
                if quiet_run * FRAME_S >= end_silence_s or too_long:
                    keep = len(got) - max(0, quiet_run - TAIL_FRAMES) * FRAME_BYTES
                    clip = bytes(got[:keep])
                    return clip, {
                        "speech": True,
                        "speech_s": round(len(clip) / (2 * RATE), 2),
                        "voiced_s": round(voice.voiced * FRAME_S, 2),
                        "sustained_s": round(voice.sustained * FRAME_S, 2),
                        "floor": round(floor_now),
                        "peak": round(peak),
                        "cut_by_max": too_long,
                    }
            if not started and no_speech_s is not None and waited * FRAME_S >= no_speech_s:
                return b"", {"speech": False, "floor": round(floor_now), "peak": round(peak)}
