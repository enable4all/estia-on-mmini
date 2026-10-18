"""The audio of one call: the call helper's Unix socket (48 kHz mono s16le, 960-byte frames each way), end-of-turn
detection (the full pause, Smart Turn's early end and the pause by speech: `TurnEnd`) and barge-in. Standard library
only. Times are counted in frames of the call helper's 10 ms clock, not in
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
import statistics
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

# Early end of turn (owner, 17 Oct; spike A): at a short pause (`early_pause_s`, 0.6 s) Smart Turn, a small model in the
# voice service, hears the turn's last 8 s and says how sure it is that the sentence is finished, once a pause:
# p >= SURE_P ends the turn at once, p >= PROBABLY_P at PROBABLY_END_S of pause (never later than the full pause),
# anything else waits the full pause, the room to think. The window ends TAIL_FRAMES after the last sound, as the clip
# does (spike A: unfinished turns called sure fell from 6.0% to 4.5%). An answer not in by ANSWER_FRAMES after the ask
# is late; a late or failed answer means the full pause, and TURN_FAILURES of them in a row the full pause for the rest
# of the call. Sound after the pause began drops the ask; the next pause asks again.
SURE_P = 0.8
PROBABLY_P = 0.5
PROBABLY_END_S = 1.5
ANSWER_FRAMES = 30
# Frames already queued (the listen after Whisper) are taken without the event loop running, so no answer could come
# in time: with a backlog the listen waits for an answer outstanding, at most as long as the client does (0.3 s)
ANSWER_WAIT_S = ANSWER_FRAMES * FRAME_S
TURN_FAILURES = 3
TURN_WINDOW_FRAMES = 800  # 8 s: Smart Turn's whole window (128,000 samples at 16 kHz)
LOW_FRAME_BYTES = 320  # a frame at 16 kHz: 160 samples
EARLY_KINDS = ("sure", "probably", "speech")  # the ends before the full pause
# The pause by speech (owner, 17 Oct; spike B, rule R4): the speech detector rates the listen's audio chunk by chunk
# (VAD_CHUNK samples from its first frame, in order, one request out at a time, on the call's barge-in stream). A chunk
# is noise when p < PAUSE_NOISE_P and the next chunk's too, or at once when the chunk before was loud and below it as
# well (noise that goes on). A loud frame counts as pause once every chunk it falls in is noise; until then it is sound,
# as by loudness, so a turn never ends later than today. A failed or missing answer is never noise. In 200 made turns no
# speech chunk was taken for noise, whispers included; noise after the last word ended 37% of those turns sooner (mean
# 0.26 s); other voices still keep a pause open. PAUSE_FAILURES failed answers in a row: loudness for the rest of the call.
PAUSE_NOISE_P = 0.1
PAUSE_FAILURES = 3
EARLY_BARGE_S = 1.5  # a takeover this soon into her answer counts against the end before it (calls.py)
HOLDS = ("goes on", "ends the call")  # why calls.py held an early end to the full pause (`TurnEnd.held`)


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
        return self.add_low(to16k(frame), level)

    def add_low(self, low: array.array[int], level: float) -> tuple[bytes, float] | None:
        """`add` for a frame already at 16 kHz (`to16k`)."""
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


def _taken(task: asyncio.Task[Any]) -> None:
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
        self.acknowledged = 0  # a listener's "okay" over her: she carried on (calls.is_acknowledgement)
        self._last: asyncio.Task[float | str] | None = None
        self._asking: set[asyncio.Task[float | str]] = set()

    @property
    def detecting(self) -> bool:
        return self._detect is not None

    @property
    def active(self) -> str:
        return "speech" if self.rule == "speech" and self.detecting else "loudness"

    def ask(self, chunk: bytes, *, counted: bool = True) -> asyncio.Task[float | str]:
        """The detector's answer for `chunk`, without waiting for it; chunks go out one after the other, in order
        (the service keeps the call's detector state). `counted`: barge-in's own chunk, in its end line (the watch
        after an early end asks on the same chain, uncounted)."""
        detect, before, asked = self._detect, self._last, self._clock()
        assert detect is not None

        async def answer() -> float | str:
            if before is not None and not before.done():
                await asyncio.wait({before})
            try:
                result = await detect(chunk)
            except Exception:  # a detector never raises into the call
                result = "error"
            if counted:
                self.slowest = max(self.slowest, self._clock() - asked)
            return result

        self.chunks += counted
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

    def acknowledgement(self) -> None:
        """A stop was a listener's acknowledgement alone ("okay", "mm-hm"; calls.py): she carried on. Only counted."""
        self.acknowledged += 1

    def summary(self) -> str:
        """The call's end line (metadata): the configured rule and, when loudness took over, why; for each rule
        measured, how often it stopped her in charge and would have as the shadow; her own voice; the acknowledgements
        she carried on after; the detector's chunks, slowest answer and failures by kind."""
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
        line = f"{head}: {counts}; her own voice {self.own_voice}; acknowledgements {self.acknowledged}"
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


# one 16 kHz window (at most TURN_WINDOW_FRAMES, the most recent sample last) -> how sure Smart Turn is that the
# sentence is finished, or why there is no answer: "timeout" or "http" (as Detect)
Judge = Callable[[bytes], Awaitable[float | str]]


@dataclass
class _End:
    """How one listen ended (metadata only), and what calls.py found out after it."""

    kind: str  # "sure", "probably" (Smart Turn), "speech" (the pause by speech), "full" (the full pause), "max"
    quiet_s: float  # the quiet run at the end (loudness)
    pause_s: float  # the pause at the end
    p: float | None  # Smart Turn's answer for this pause, if any
    saved_s: float  # the full pause less the quiet run: how much sooner than loudness at least (0 for "max")
    noise_frames: int = 0  # loud frames counted as pause (the pause by speech)
    noise: bool = False  # no words in it (calls.py): not a turn
    held: str | None = None  # waited out the full pause after the words were read: "goes on" or "ends the call"
    caught: bool = False  # the caller went on in that hold: joined into the next listen's turn
    cut_off: bool = False  # the caller went on after an early end, before her answer, and no hold caught it
    barged_early: bool = False  # the caller took over in her first EARLY_BARGE_S of the answer


@dataclass
class _Ask:
    """Smart Turn asked about the pause that began on frame `start` (asked on frame `frame`)."""

    start: int
    frame: int
    task: asyncio.Task[tuple[float | str, float]]
    tier: str | None = None  # "sure", "probably" or "not" once answered; "failed"
    p: float | None = None
    ms: int | None = None


class TurnEnd:
    """One call's rules for the end of a turn (see the constants), like `Barge`: Smart Turn's answer at a short pause
    (`judge`, part A) and the pause by speech (`detect`, part B, the call's barge-in stream). Without either a listen is
    today's, byte for byte. Each listen's end is kept as an `_End` (`ends`), which calls.py edits with what it learns
    after (`noise`, `held`, `joined`, `went_on`, `barged_early`), for the call's end lines (`summary`). `early_end`
    and `pause` are the settings, for those lines; without them, what was given."""

    def __init__(
        self,
        judge: Judge | None = None,
        detect: Detect | None = None,
        *,
        early_pause_s: float = 0.6,
        early_end: bool | None = None,
        pause: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._judge = judge
        self._detect = detect
        self.early_pause_s = early_pause_s
        self.early_end = judge is not None if early_end is None else early_end
        self.pause = ("speech" if detect is not None else "loudness") if pause is None else pause
        self._clock = clock
        self.judged = judge is not None  # the call had a judge (it may have failed since)
        self.judge_gave_up = False
        self.judge_failures = 0  # failed or late answers in a row
        self.judge_failed: collections.Counter[str] = collections.Counter()  # timeout, http, error, late
        self.asks: collections.Counter[str] = collections.Counter()  # sure, probably, not, dropped
        self.answer_ms: list[int] = []
        self.detected = detect is not None  # the call had a speech detector for the pause
        self.detect_gave_up = False
        self.chunk_failures = 0  # failed answers in a row
        self.chunk_failed: collections.Counter[str] = collections.Counter()  # timeout, http, error
        self.chunks = 0  # chunks asked about while listening
        self.slowest = 0.0  # seconds from a chunk's ask to its answer, the longest in the call
        self.full_s: float | None = None  # the full pause of the latest listen
        self.ends: list[_End] = []
        self._last: asyncio.Task[float | str] | None = None
        self._asking: set[asyncio.Task[Any]] = set()

    @property
    def judging(self) -> bool:
        return self._judge is not None

    @property
    def detecting(self) -> bool:
        return self._detect is not None

    @property
    def measured(self) -> bool:
        """The call had a judge or a detector: its listens' infos carry the end's keys."""
        return self.judged or self.detected

    def _ask_chunk(self, chunk: bytes) -> asyncio.Task[float | str]:
        """The detector's answer for `chunk`, without waiting for it; one after the other, in order (as `Barge.ask`)."""
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
        self._track(task)
        return task

    def _chunk_answered(self) -> None:
        self.chunk_failures = 0

    def _chunk_failed(self, kind: str) -> None:
        self.chunk_failures += 1
        self.chunk_failed[kind] += 1
        if self.chunk_failures >= PAUSE_FAILURES and self._detect is not None:
            self._detect = None
            self.detect_gave_up = True
            log.info(
                "calls: the speech detector failed %d times in a row while listening; loudness holds the pause for"
                " the rest of the call",
                self.chunk_failures,
            )

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._asking.add(task)
        task.add_done_callback(self._asking.discard)
        task.add_done_callback(_taken)

    def _ask(self, window: bytes) -> asyncio.Task[tuple[float | str, float]]:
        judge, asked = self._judge, self._clock()
        assert judge is not None

        async def answer() -> tuple[float | str, float]:
            try:
                result = await judge(window)
            except Exception:  # a judge never raises into the call
                result = "error"
            return result, self._clock() - asked

        task = asyncio.create_task(answer())
        self._track(task)
        return task

    def _answered(self, ask: _Ask, p: float, seconds: float) -> None:
        self.judge_failures = 0
        ask.p, ask.ms = p, round(seconds * 1000)
        ask.tier = "sure" if p >= SURE_P else "probably" if p >= PROBABLY_P else "not"
        self.asks[ask.tier] += 1
        self.answer_ms.append(ask.ms)

    def _failed(self, ask: _Ask, kind: str) -> None:
        ask.tier = "failed"
        self.judge_failures += 1
        self.judge_failed[kind] += 1
        if self.judge_failures >= TURN_FAILURES and self._judge is not None:
            self._judge = None
            self.judge_gave_up = True
            log.info(
                "calls: the turn detector failed %d times in a row; the full pause for the rest of the call",
                self.judge_failures,
            )

    def _dropped(self, ask: _Ask) -> None:
        """Sound after the pause began, before the answer: the ask is dropped (no failure)."""
        if ask.tier is None:
            self.asks["dropped"] += 1
        ask.task.cancel()

    def _ended(self, kind: str, pause: int, quiet: int, full_s: float, ask: _Ask | None, noise: int) -> dict[str, Any]:
        """Keep the listen's end; its info keys (only for a call with a judge)."""
        pause_s, quiet_s = round(pause * FRAME_S, 2), round(quiet * FRAME_S, 2)
        p = ask.p if ask is not None else None
        ms = ask.ms if ask is not None else None
        saved = 0.0 if kind == "max" else round(max(0.0, full_s - quiet_s), 2)
        self.ends.append(_End(kind, quiet_s, pause_s, p, saved, noise))
        if kind in ("sure", "probably"):
            log.info("calls: turn ended early (%s, p %.2f, pause %.2f s, answer %d ms)", kind, p, pause_s, ms)
        elif kind == "speech":
            log.info(
                "calls: the speech pause ended the turn %.2f s sooner (%d loud frames counted as pause)", saved, noise
            )
        if not self.measured:
            return {}
        return {"ended": kind, "pause_s": pause_s, "quiet_s": quiet_s, "p": p, "answer_ms": ms}

    def noise(self) -> None:
        """The latest end held no words (calls.py): it is no turn."""
        if self.ends:
            self.ends[-1].noise = True

    def held(self, reason: str) -> None:
        """The latest end's words go on ("goes on") or end the call ("ends the call"): calls.py waits out the rest of
        the full pause after all, so it saved nothing."""
        if self.ends and self.ends[-1].held is None:
            self.ends[-1].held = reason
            self.ends[-1].saved_s = 0.0

    def joined(self) -> None:
        """The caller went on in the hold: the held end (one of the latest two; the hold's own listen ends after it) is
        no turn but caught by the hold, and the next listen's end is the turn's."""
        for end in reversed(self.ends[-2:]):
            if end.held is not None:
                end.caught, end.cut_off = True, False
                return

    def went_on(self) -> None:
        """The caller went on after the latest end, an early one, before her answer (`CallAudio.watch`): cut off,
        unless a hold catches it."""
        end = self.ends[-1] if self.ends else None
        if end is not None and end.kind in EARLY_KINDS and not (end.cut_off or end.caught or end.noise):
            end.cut_off = True
            log.info("calls: the caller went on after an early end (%s)", end.kind)

    def barged_early(self, at_s: float) -> None:
        """The caller took over `at_s` into her answer to the latest end, within EARLY_BARGE_S (calls.py)."""
        end = self.ends[-1] if self.ends else None
        if end is not None and not end.barged_early:
            end.barged_early = True
            after = f"an early end ({end.kind})" if end.kind in EARLY_KINDS else "a full pause"
            log.info("calls: taken over %.1f s into her answer, after %s", at_s, after)

    def summary(self) -> tuple[str, str]:
        """The call's two end lines (metadata, numbers only): the early end (the turns, how many ended early and the
        time saved, the ones the caller went on after, caught by the hold or cut off, the holds, the takeovers in her
        first EARLY_BARGE_S, Smart Turn's asks, answer times and failures by kind, and each turn's saving, for the
        owner's test: cut off at most 1 turn in 20, at least 1 s saved on median) and the pause by speech (the turns
        it ended sooner than loudness and by how much, the loud frames it counted as pause, the detector's chunks,
        slowest answer and failures by kind)."""
        turns = [end for end in self.ends if not end.noise and not end.caught]
        kinds = collections.Counter(end.kind for end in turns)
        saved = [end.saved_s for end in turns]
        went = [end for end in self.ends if not end.noise and (end.caught or end.cut_off)]
        cut = [end for end in went if end.cut_off]
        caught = sum(1 for end in went if end.caught)
        held = collections.Counter(end.held for end in self.ends if end.held is not None and not end.noise)
        early = sum(1 for end in turns if end.barged_early and end.kind in EARLY_KINDS)
        late = sum(1 for end in turns if end.barged_early and end.kind not in EARLY_KINDS)
        if not self.early_end:
            head = "calls: early end off"
        elif not self.judged:
            head = "calls: early end on (no turn detector: the full pause decided)"
        else:
            full = f", full {self.full_s:.2f} s" if self.full_s is not None else ""
            why = f"; the full pause after {TURN_FAILURES} failed answers in a row" if self.judge_gave_up else ""
            head = f"calls: early end on (short pause {self.early_pause_s:.2f} s{full}{why})"
        reasons = ", ".join(f"{reason} {held[reason]}" for reason in (*HOLDS, *sorted(set(held) - set(HOLDS))))
        line = (
            f"{head}: {_count(len(turns), 'turn')}, {kinds['sure'] + kinds['probably']} ended early (sure"
            f" {kinds['sure']}, probably {kinds['probably']}); saved {sum(saved):.1f} s, median {_median(saved):.2f} s"
            f" a turn; went on {len(went)} ({_by_kind(went)}), caught by the hold {caught}, cut off"
            f" {len(cut)} ({_by_kind(cut)}); held {sum(held.values())} ({reasons}); taken over in her first"
            f" {EARLY_BARGE_S:.1f} s: {early} after an early end, {late} after a full pause"
        )
        if self.judged:
            asked = sum(self.asks.values()) + sum(self.judge_failed.values())
            line += (
                f"; {_count(asked, 'ask')} (sure {self.asks['sure']}, probably {self.asks['probably']}, not"
                f" {self.asks['not']}, dropped {self.asks['dropped']}), answer median {round(_median(self.answer_ms))}"
                f" ms, slowest {max(self.answer_ms, default=0)} ms, {_failures(self.judge_failed)}"
            )
        if saved:
            line += "; saved per turn " + " ".join(f"{value:.2f}" for value in saved)
        if self.pause != "speech":
            head = "calls: pause by loudness"
        elif not self.detected:
            head = "calls: pause by speech (loudness: no speech detector)"
        elif self.detect_gave_up:
            head = f"calls: pause by speech (loudness after {PAUSE_FAILURES} failed answers in a row)"
        else:
            head = "calls: pause by speech"
        pause = f"{head}: {_count(len(turns), 'turn')}"
        if self.detected:
            sooner = [end.saved_s for end in turns if end.kind == "speech"]
            pause += (
                f", {len(sooner)} ended sooner than by loudness ({sum(sooner):.1f} s in all, median"
                f" {_median(sooner):.2f} s), {sum(end.noise_frames for end in turns)} loud frames counted as pause;"
                f" detector {self.chunks} chunks, slowest {round(self.slowest * 1000)} ms, {_failures(self.chunk_failed)}"
            )
        return line, pause

    def close(self) -> None:
        for task in list(self._asking):
            task.cancel()


def _count(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _median(values: list[float] | list[int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _by_kind(ends: list[_End]) -> str:
    return ", ".join(f"{kind} {sum(1 for end in ends if end.kind == kind)}" for kind in EARLY_KINDS)


def _failures(failed: collections.Counter[str]) -> str:
    count = sum(failed.values())
    kinds = " (" + ", ".join(f"{kind} {n}" for kind, n in sorted(failed.items())) + ")" if count else ""
    return f"{count} failed{kinds}"


class _Listening:
    """One listen's end of turn, frame by frame (frames counted from the listen's first). The pause is the frames since
    the last sound (the utterance's start counts as sound); by speech, a loud frame is sound until its chunks are
    settled (`_chunk_state`), and noise then. The full pause ends the turn, or sooner Smart Turn's answer, asked once a
    pause at the short pause, acting while the pause it was asked about goes on."""

    def __init__(self, turn: TurnEnd, end_silence_s: float) -> None:
        self.turn = turn
        self.full_s = end_silence_s
        turn.full_s = end_silence_s
        self.ask_frames = max(1, round(turn.early_pause_s / FRAME_S))
        self.probably_frames = round(min(PROBABLY_END_S, end_silence_s) / FRAME_S)
        self.judging = turn.judging  # the clip's 16 kHz frames are kept for the window
        self.converting = turn.judging or turn.detecting
        self.onset: collections.deque[bytes] = collections.deque(maxlen=LISTEN_PREROLL_FRAMES + LISTEN_START_FRAMES)
        self.lows = bytearray()  # the clip's frames at 16 kHz, one conversion a frame
        self.first = 0  # the clip's first frame
        self.started = False
        self.last_sound = -1
        self.ask: _Ask | None = None
        self.chunks = Chunks()
        self.asked: collections.deque[asyncio.Task[float | str]] = collections.deque()
        self.answers: list[float | None] = []  # chunk by chunk: p, or None (failed: never noise)
        self.fresh = False  # answers came since the pending frames were last looked at
        self.loud_chunks: set[int] = set()
        self.pending: list[int] = []  # loud frames whose chunks are not all settled: sound until they are
        self.noise_frames = 0

    def heard(self, index: int, frame: bytes, loud: bool) -> None:
        """Every frame, in order, once its loudness is known (before the utterance starts too)."""
        if not self.converting:
            return
        low = to16k(frame)
        if self.judging:
            if self.started:
                self.lows += low.tobytes()
            else:
                self.onset.append(low.tobytes())
        if not self.turn.detecting:
            return
        if loud:
            self.loud_chunks.update(_chunks_of(index))
        chunk = self.chunks.add_low(low, 0.0)
        if chunk is not None:
            self.asked.append(self.turn._ask_chunk(chunk[0]))
        while self.asked and self.asked[0].done():
            task = self.asked.popleft()
            result = "error" if task.cancelled() or task.exception() else task.result()
            if isinstance(result, str):
                self.answers.append(None)
                self.turn._chunk_failed(result)
            else:
                self.answers.append(float(result))
                self.turn._chunk_answered()
            self.fresh = True

    def start(self, index: int, onset: int) -> None:
        """The utterance started on frame `index`; its `onset` frames before it go with the clip, which begins there."""
        self.started = True
        self.first = index - onset + 1
        self.lows = bytearray(b"".join(self.onset))
        self.last_sound = index

    def _sound(self, index: int) -> None:
        """Frame `index` is sound: the pause begins after it, and an ask for an earlier pause is dropped."""
        self.last_sound = max(self.last_sound, index)
        ask = self.ask
        if ask is not None and index > ask.start:
            self.turn._dropped(ask)
            self.ask = None

    def _chunk_state(self, n: int) -> str | None:
        """Chunk n: "noise" (p < PAUSE_NOISE_P and the next chunk's too, or the chunk before was loud and low too),
        "sound", or None while its answers are not all in."""
        if n >= len(self.answers):
            return None
        p = self.answers[n]
        if p is None or p >= PAUSE_NOISE_P:
            return "sound"
        before = self.answers[n - 1] if n >= 1 else None
        if before is not None and before < PAUSE_NOISE_P and n - 1 in self.loud_chunks:
            return "noise"  # noise that goes on: at once
        if n + 1 >= len(self.answers):
            return None
        after = self.answers[n + 1]
        return "noise" if after is not None and after < PAUSE_NOISE_P else "sound"

    def _settle(self) -> None:
        """Loud frames whose chunks are all settled: noise only if every one is noise."""
        if not self.turn.detecting:  # loudness from now on: what was not settled is sound
            for index in self.pending:
                self._sound(index)
            self.pending = []
            return
        if not self.fresh:
            return
        self.fresh = False
        still: list[int] = []
        for index in self.pending:
            states = [self._chunk_state(n) for n in _chunks_of(index)]
            if None in states:
                still.append(index)
            elif all(state == "noise" for state in states):
                self.noise_frames += 1
            else:
                self._sound(index)
        self.pending = still

    def _look(self, index: int) -> None:
        """Smart Turn's answer, if it came; none by ANSWER_FRAMES after the ask is late."""
        ask = self.ask
        if ask is None or ask.tier is not None:
            return
        if ask.task.done():
            result, seconds = ("error", 0.0) if ask.task.cancelled() or ask.task.exception() else ask.task.result()
            if isinstance(result, str):
                self.turn._failed(ask, result)
            else:
                self.turn._answered(ask, float(result), seconds)
        elif index - ask.frame >= ANSWER_FRAMES:
            ask.task.cancel()
            self.turn._failed(ask, "late")

    def step(self, index: int, loud: bool, quiet: int, too_long: bool) -> tuple[str, int] | None:
        """A frame of the utterance: how the turn ends on it (the end's kind and the pause), or None."""
        self._look(index)
        self._settle()
        turn = self.turn
        if loud and turn.detecting:
            self.pending.append(index)
        elif loud:
            self._sound(index)
        start = max(self.last_sound, self.pending[-1]) if self.pending else self.last_sound
        pause = index - start  # never shorter than the quiet run: a loud frame is sound until settled as noise
        if self.ask is not None and start < self.ask.start:
            # asked about a pause that began on a loud frame since settled as noise: the pause is the earlier one
            # again, so that ask is about no pause; drop it and ask about this one (its window ends as on time)
            turn._dropped(self.ask)
            self.ask = None
        if turn.judging and self.ask is None and pause >= self.ask_frames:
            end = start + 1 + TAIL_FRAMES - self.first
            frames = self.lows[max(0, end - TURN_WINDOW_FRAMES) * LOW_FRAME_BYTES : end * LOW_FRAME_BYTES]
            self.ask = _Ask(start, index, turn._ask(bytes(frames)))
        ask = self.ask
        if pause * FRAME_S >= self.full_s:  # first: an answer acting on the full pause's own frame saved nothing
            return ("full" if quiet * FRAME_S >= self.full_s else "speech"), pause
        if (
            ask is not None
            and ask.start == start  # the pause it was asked about
            and (ask.tier == "sure" or (ask.tier == "probably" and pause >= self.probably_frames))
        ):
            return ask.tier, pause
        if too_long:
            return "max", pause
        return None

    def waiting(self) -> asyncio.Task[tuple[float | str, float]] | None:
        """Smart Turn's answer still to come for the ask out, or None."""
        ask = self.ask
        return ask.task if ask is not None and ask.tier is None and not ask.task.done() else None

    def ended(self, kind: str, pause: int, quiet: int) -> dict[str, Any]:
        start = self.last_sound if not self.pending else max(self.last_sound, self.pending[-1])
        ask = self.ask if self.ask is not None and self.ask.start == start else None
        return self.turn._ended(kind, pause, quiet, self.full_s, ask, self.noise_frames)

    def close(self) -> None:
        """The listen is over: an ask still out is dropped (a chunk's answer is left to come: one at a time)."""
        if self.ask is not None and self.ask.tier is None:
            self.turn._dropped(self.ask)
        self.ask = None


def _chunks_of(index: int) -> range:
    """The chunks frame `index`'s 16 kHz samples fall in: one, or two for a frame split between chunks."""
    first = 160 * index
    return range(first // VAD_CHUNK, (first + 159) // VAD_CHUNK + 1)


# The watch's speech check, from spike B's chunks (live Silero): 340 of 342 spoken phrase starts had their first chunk or
# the next at p >= SPEECH_P; of the noise chunks 0.6 s or more after the last word, 7 of 288 were (other voices aside:
# 11 of 41 café chunks), and of the 188 chunks of noise that went on for 5.5 s (8 kinds, café too), none
@dataclass
class _Watch:
    """`CallAudio.watch`: frames of the window still to come, the level, what to call, the loud frames in a row so
    far; with a speech detector, the watched frames as chunks (`made`), each chunk's answer and the frame it was asked
    on, and the chunks the latest sustained run is judged by (every chunk it lasts in the window, and the one after
    it), None once judged noise."""

    left: int
    threshold: float
    seen: Callable[[], None]
    run: int = 0
    frame: int = 0  # frames watched so far
    chunks: Chunks = field(default_factory=lambda: Chunks())
    made: int = 0
    asked: dict[int, tuple[asyncio.Task[float | str], int]] = field(default_factory=dict)
    judged: tuple[int, int] | None = None


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
        self._watch: _Watch | None = None
        self._echo = 0  # frames of her voice still on the line: while she speaks, and ECHO_TAIL_FRAMES after

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
                self._echo = ECHO_TAIL_FRAMES if self.speaking.is_set() else max(0, self._echo - 1)
                if self._watch is not None:
                    self._watched(frame)
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

    def watch(self, frames: int, threshold: float, seen: Callable[[], None]) -> None:
        """Within the next `frames` frames that come in, call `seen` once for the caller going on: LISTEN_START_FRAMES
        frames in a row louder than `threshold` (sustained sound, never a click) that the speech detector hears as
        speech: a chunk it lasts, or the one after it, at p >= SPEECH_P, as for barge-in (review I2: a door or dishes
        are no going on). A run whose answers do not come (failed, or none VAD_MAX_AGE_FRAMES after its chunk) or that she
        speaks over counts, as by loudness; without a detector loudness alone decides. A run in the window is judged
        after it too. While her voice is on the line (her lines said with `watch=True` and their echo's tail) a frame
        also needs more than the level her own voice stopped her at (`Barge.least`), as for barge-in. After an early
        end, while her answer is worked out. The frames still queue for the next listen. Over when she speaks (`say`,
        unless `watch=True`) or `unwatch`."""
        self._watch = _Watch(frames, threshold, seen) if frames > 0 else None

    def unwatch(self) -> None:
        self._watch = None

    def _watched(self, frame: bytes) -> None:
        watch = self._watch
        assert watch is not None
        index = watch.frame
        watch.frame += 1
        rules = self.barge
        if watch.left > 0:
            watch.left -= 1
            threshold = max(watch.threshold, rules.least) if self._echo else watch.threshold
            watch.run = watch.run + 1 if rms(frame) > threshold else 0
            if watch.run >= LISTEN_START_FRAMES:  # judged by every chunk it lasts, and the one after it
                last = _chunks_of(index)[-1] + 1
                if watch.judged is None:
                    first = _chunks_of(index - min(watch.run, LISTEN_START_FRAMES) + 1)[0]
                    watch.judged = (first, last)
                else:
                    watch.judged = (watch.judged[0], max(watch.judged[1], last))
        if rules.detecting:
            chunk = watch.chunks.add_low(to16k(frame), 0.0)
            if chunk is not None:
                watch.asked[watch.made] = (rules.ask(chunk[0], counted=False), index)
                watch.made += 1
        verdict = self._went_on(watch, index)
        if verdict is False:
            watch.judged = None  # noise: a later run in the window may still be the caller
        if verdict:
            self._end_watch()
        elif watch.left <= 0 and watch.judged is None:
            self._watch = None

    def _went_on(self, watch: _Watch, index: int) -> bool | None:
        """Whether the run being judged is the caller going on: True (speech, or no answer to go by), False (every
        chunk it is judged by was rated below SPEECH_P), None (not yet: an answer is still to come)."""
        if watch.judged is None:
            return None
        if not self.barge.detecting:
            return True  # loudness alone
        first, last = watch.judged
        waiting = False
        for n in range(first, last + 1):
            if n not in watch.asked:
                waiting = True
                continue
            task, asked_on = watch.asked[n]
            if not task.done():
                if index - asked_on > VAD_MAX_AGE_FRAMES:
                    return True  # no answer in time: as by loudness
                waiting = True
                continue
            p = "error" if task.cancelled() or task.exception() else task.result()
            if isinstance(p, str) or p >= SPEECH_P:
                return True  # speech, or a failed answer: as by loudness
        return None if waiting else False

    def _end_watch(self) -> None:
        """The watch is over: a run still being judged counts (no answer to go by: as by loudness)."""
        watch, self._watch = self._watch, None
        if watch is None or watch.judged is None:
            return
        try:
            watch.seen()
        except Exception:  # what calls.py does with it never stops the call's audio
            log.exception("calls: the watch after an early end failed")

    def drain_incoming(self) -> None:
        while not self.incoming.empty():
            self.incoming.get_nowait()

    def stop_speaking(self) -> None:
        self._outgoing.clear()
        self.speaking.clear()

    async def say(self, pcm: bytes, *, barge: bool = False, fresh: bool = True, watch: bool = False) -> Said:
        """Speak `pcm` and return when it went out. With barge=True the caller can cut in (see the constants and
        `Barge`). fresh: a new answer starts, so what came in meanwhile (while she was thinking) is dropped; the next
        sentence of the same answer keeps it (the caller may have started to talk between two sentences). watch: a
        line while her answer is worked out ("Got it.", a thinking line): the watch after an early end goes on."""
        if not watch:
            self._end_watch()  # she answers: whatever the caller says now is barge-in's
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
        turn: TurnEnd | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """The caller's next utterance, or b"" when nobody spoke within no_speech_s (frames of call time).
        After a barge-in, start inside the caller's speech with the pre-roll (`turn` is then left out: what the caller
        says over her ends by end_silence_s alone). With `turn`, the call's rules for the end of a turn (`TurnEnd`):
        the end is kept there, and the info says how the turn ended when the call has a judge. ConnectionError when
        the call's audio ends."""
        rules = _Listening(turn, end_silence_s) if turn is not None and not preroll else None
        try:
            return await self._listen(preroll, floor, end_silence_s, no_speech_s, max_s, rules)
        finally:
            if rules is not None:
                rules.close()

    async def _listen(
        self,
        preroll: tuple[bytes, ...],
        floor: float,
        end_silence_s: float,
        no_speech_s: float | None,
        max_s: float,
        rules: _Listening | None,
    ) -> tuple[bytes, dict[str, Any]]:
        got = bytearray()
        floor_levels: list[float] = []
        measuring: list[tuple[bytes, float, int]] = []  # the floor's frames, judged once the floor is known
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
                measuring.append((frame, level, waited - 1))
                if len(floor_levels) < LISTEN_FLOOR_FRAMES:
                    continue
            batch, measuring = measuring or [(frame, level, waited - 1)], []
            floor_now = floor_of(floor_levels)
            threshold = max(LISTEN_FLOOR_FACTOR * floor_now, LISTEN_MIN_RMS)
            for frame_now, level_now, index in batch:
                loud = level_now > threshold
                if rules is not None:
                    rules.heard(index, frame_now, loud)
                if not started:
                    onset.append(frame_now)
                    if not loud:
                        voice = _Voice()  # only the run that starts the utterance counts
                    voice.add(loud)
                    if voice.run >= LISTEN_START_FRAMES:
                        started = True
                        got += b"".join(onset)
                        if rules is not None:
                            rules.start(index, len(onset))
                    continue
                got += frame_now
                voice.add(loud)
                quiet_run = 0 if loud else quiet_run + 1
                too_long = len(got) / (2 * RATE) >= max_s
                if rules is not None:
                    end = rules.step(index, loud, quiet_run, too_long)
                elif quiet_run * FRAME_S >= end_silence_s or too_long:
                    end = ("full", quiet_run)
                else:
                    end = None
                if end is None and rules is not None and not self.incoming.empty():
                    answer = rules.waiting()  # a backlog: let the answer come, as it would in real time
                    if answer is not None:
                        await asyncio.wait({answer}, timeout=ANSWER_WAIT_S)
                if end is not None:
                    kind, pause = end
                    keep = len(got) - max(0, pause - TAIL_FRAMES) * FRAME_BYTES
                    clip = bytes(got[:keep])
                    info: dict[str, Any] = {
                        "speech": True,
                        "speech_s": round(len(clip) / (2 * RATE), 2),
                        "voiced_s": round(voice.voiced * FRAME_S, 2),
                        "sustained_s": round(voice.sustained * FRAME_S, 2),
                        "floor": round(floor_now),
                        "peak": round(peak),
                        "cut_by_max": too_long,
                    }
                    if rules is not None:
                        info |= rules.ended(kind, pause, quiet_run)
                    return clip, info
            if not started and no_speech_s is not None and waited * FRAME_S >= no_speech_s:
                return b"", {"speech": False, "floor": round(floor_now), "peak": round(peak)}
