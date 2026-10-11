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
import math
import random
import wave
from dataclasses import dataclass, field
from typing import Any

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
PREROLL_FRAMES = 80  # the caller's last 800 ms before the stop: the first words of the interruption

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
    """How a spoken clip went: its length, how much of it went out, and whether the caller cut in."""

    total_s: float
    played_s: float
    barged: bool
    preroll: tuple[bytes, ...] = field(default=(), repr=False)
    floor: float = 0.0


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


def pcm48_to_wav16k(pcm: bytes) -> bytes:
    """The caller's audio as a 16 kHz WAV for transcription (Whisper's rate; a third of the upload)."""
    samples = array.array("h", pcm)
    usable = len(samples) - len(samples) % 3
    low = array.array("h", ((samples[i] + samples[i + 1] + samples[i + 2]) // 3 for i in range(0, usable, 3)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(low.tobytes())
    return buf.getvalue()


class CallAudio:
    """One connection to the call helper's socket: speak (with or without barge-in) and listen."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader, self._writer = reader, writer
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
        """Speak `pcm` and return when it went out. With barge=True the caller can cut in (see the constants).
        fresh: a new answer starts, so what came in meanwhile (while she was thinking) is dropped; the next
        sentence of the same answer keeps it (the caller may have started to talk between two sentences)."""
        if fresh:
            self.drain_incoming()
        total = len(pcm) / (2 * RATE)
        self._outgoing = bytearray(pcm)
        self._sent = 0
        self.speaking.set()
        recent: collections.deque[bytes] = collections.deque(maxlen=PREROLL_FRAMES)
        loud: collections.deque[bool] = collections.deque(maxlen=BARGE_WINDOW)
        floor_levels: list[float] = []
        while self.speaking.is_set() and not self.closed.is_set():
            try:
                frame = await asyncio.wait_for(self.incoming.get(), 0.5)
            except TimeoutError:
                continue
            if not barge:
                continue
            level = rms(frame)
            recent.append(frame)
            if len(floor_levels) < BARGE_FLOOR_FRAMES:
                floor_levels.append(level)
                continue
            floor = floor_of(floor_levels)
            loud.append(level > max(BARGE_FLOOR_FACTOR * floor, BARGE_MIN_RMS))
            if sum(loud) >= BARGE_NEEDED:
                played = self._sent / (2 * RATE)
                self.stop_speaking()
                return Said(round(total, 2), round(played, 2), True, tuple(recent), floor)
        await self._skip(ECHO_TAIL_FRAMES)
        return Said(round(total, 2), round(self._sent / (2 * RATE), 2), False)

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
