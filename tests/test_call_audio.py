import array
import asyncio
import hashlib
import io
import itertools
import logging
import math
import random
import re
import socket
import wave
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from local_assistant import call_audio
from local_assistant.call_audio import (
    FRAME_BYTES,
    RATE,
    Barge,
    CallAudio,
    Chunks,
    TurnEnd,
    _End,
    faint_noise,
    pcm48_to_wav16k,
    rms,
    silence,
    to16k,
    wav_to_pcm48,
)
from tests.fakes.fake_voice import speech_probability

FRAMES_PER_S = 100


def tone(seconds: float, level: int = 3000, hz: float = 220.0) -> bytes:
    n = int(RATE * seconds)
    return array.array("h", (int(level * math.sin(2 * math.pi * hz * i / RATE)) for i in range(n))).tobytes()


def speech(seconds: float, level: int = 3000) -> bytes:
    """Syllables: 150 ms of sound, 50 ms of near-silence."""
    out = bytearray()
    syllable = 0
    while len(out) < 2 * RATE * seconds:
        out += tone(0.15, level, hz=200.0 + 37 * syllable) + noise(0.05, 30)  # every syllable differs
        syllable += 1
    return bytes(out[: int(2 * RATE * seconds)])


def noise(seconds: float, level: int) -> bytes:
    rnd = random.Random(7)
    return array.array("h", (rnd.randint(-level, level) for _ in range(int(RATE * seconds)))).tobytes()


class FakeTunnel:
    """The call helper's side of the socket, in lock step: one caller frame out, one of her frames back."""

    def __init__(self, caller: bytes, barge: Barge | None = None) -> None:
        self.caller = caller
        self.barge = barge
        self.heard: list[bytes] = []  # her frames, in order
        self.ours, self.theirs = socket.socketpair()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> CallAudio:
        reader, writer = await asyncio.open_unix_connection(sock=self.ours)
        audio = CallAudio(reader, writer, self.barge)
        self.task = asyncio.create_task(self._run())
        return audio

    async def _run(self) -> None:
        reader, writer = await asyncio.open_unix_connection(sock=self.theirs)
        position = 0
        try:
            while position < len(self.caller):
                writer.write(self.caller[position : position + FRAME_BYTES].ljust(FRAME_BYTES, b"\0"))
                await writer.drain()
                position += FRAME_BYTES
                self.heard.append(await reader.readexactly(FRAME_BYTES))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass  # the test hung up (a second call in the same test)
        writer.close()

    def spoke_s(self) -> float:
        return sum(1 for frame in self.heard if rms(frame) > 300) / FRAMES_PER_S


async def call(caller: bytes, barge: Barge | None = None) -> tuple[FakeTunnel, CallAudio, asyncio.Task[None]]:
    tunnel = FakeTunnel(caller, barge)
    audio = await tunnel.start()
    pump = asyncio.create_task(audio.pump())
    await asyncio.wait_for(audio.started.wait(), 5)
    return tunnel, audio, pump


def test_tts_is_upsampled_exactly_and_stt_gets_16k() -> None:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(array.array("h", [0, 1000, 2000, 1000] * 600).tobytes())
    pcm = wav_to_pcm48(buf.getvalue())
    samples = array.array("h", pcm)
    assert len(samples) == 4800 and samples[:4].tolist() == [0, 500, 1000, 1500]
    with wave.open(io.BytesIO(pcm48_to_wav16k(pcm))) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (16_000, 1, 2, 1600)


def test_faint_noise_is_quiet_but_not_digital_silence() -> None:
    lead = faint_noise(300)
    assert len(lead) == 2 * RATE * 300 // 1000
    assert 5 < rms(lead) < 60
    assert rms(silence(100)) == 0


async def test_speech_goes_out_whole_and_in_order() -> None:
    clip = tone(1.0)
    tunnel, audio, pump = await call(noise(3.0, 20))
    said = await audio.say(clip)
    assert not said.barged and said.total_s == pytest.approx(1.0)
    await asyncio.sleep(0.05)
    out = b"".join(tunnel.heard)
    assert clip in out  # nothing dropped, nothing reordered
    pump.cancel()


async def test_one_utterance_survives_a_thinking_pause_with_a_laid_back_end_of_turn() -> None:
    caller = noise(0.3, 20) + speech(1.0) + noise(1.2, 20) + speech(1.0) + noise(3.0, 20)
    _tunnel, audio, pump = await call(caller)
    _clip, info = await audio.listen(end_silence_s=1.8)
    assert info["speech"] and 2.0 <= info["speech_s"] <= 4.0  # both halves, one turn
    pump.cancel()


async def test_a_short_end_of_turn_splits_at_the_pause() -> None:
    caller = noise(0.3, 20) + speech(1.0) + noise(1.2, 20) + speech(1.0) + noise(3.0, 20)
    _tunnel, audio, pump = await call(caller)
    _clip, info = await audio.listen(end_silence_s=0.9)
    assert info["speech"] and info["speech_s"] < 2.0
    pump.cancel()


async def test_no_speech_returns_nothing_after_the_wait() -> None:
    _tunnel, audio, pump = await call(noise(3.0, 40))
    clip, info = await audio.listen(no_speech_s=1.0)
    assert clip == b"" and not info["speech"]
    pump.cancel()


async def test_talking_over_her_stops_her_fast_and_keeps_the_first_words() -> None:
    caller = noise(1.5, 30) + speech(1.5) + noise(3.0, 30)
    tunnel, audio, pump = await call(caller)
    said = await audio.say(tone(8.0), barge=True)
    assert said.barged and 1.5 < said.played_s < 1.9  # ~0.15 s after the caller starts
    clip, info = await audio.listen(preroll=said.preroll, floor=said.floor, end_silence_s=0.9)
    assert info["speech"] and speech(1.5)[: 10 * FRAME_BYTES] in clip  # the pre-roll kept the first syllable
    await asyncio.sleep(0.05)
    assert tunnel.spoke_s() < 2.0  # she went quiet at once (8 s were queued)
    pump.cancel()


@pytest.mark.parametrize("caller", [noise(10, 60), noise(1.5, 30) + noise(0.09, 6000) + noise(8, 30)])
async def test_line_noise_and_a_click_do_not_stop_her(caller: bytes) -> None:
    _tunnel, audio, pump = await call(caller)
    said = await audio.say(tone(3.0), barge=True)
    assert not said.barged
    pump.cancel()


async def test_steady_background_sound_below_speech_level_does_not_stop_her() -> None:
    _tunnel, audio, pump = await call(noise(0.5, 30) + noise(8, 300))  # a radio in the room, after a quiet start
    said = await audio.say(tone(3.0), barge=True)
    assert not said.barged
    pump.cancel()


async def test_the_pump_starts_with_a_30_ms_cushion() -> None:
    ours, theirs = socket.socketpair()
    reader, writer = await asyncio.open_unix_connection(sock=ours)
    pump = asyncio.create_task(CallAudio(reader, writer).pump())
    helper_reader, _ = await asyncio.open_unix_connection(sock=theirs)
    cushion = await asyncio.wait_for(helper_reader.readexactly(3 * FRAME_BYTES), 2)  # before any frame came in
    assert cushion == bytes(3 * FRAME_BYTES)
    pump.cancel()


async def test_without_barge_in_she_finishes_even_when_talked_over() -> None:
    _tunnel, audio, pump = await call(noise(0.5, 30) + speech(4.0) + noise(2, 30))
    said = await audio.say(tone(2.0))
    assert not said.barged and said.played_s >= 2.0
    pump.cancel()


async def test_a_closed_socket_ends_listening() -> None:
    _tunnel, audio, pump = await call(noise(0.5, 20))
    with pytest.raises(ConnectionError):
        await audio.listen(no_speech_s=30)
    assert audio.closed.is_set()
    pump.cancel()


async def test_a_caller_who_talks_at_once_is_still_heard() -> None:
    _tunnel, audio, pump = await call(speech(1.0) + noise(2.0, 20))  # no quiet start to measure the floor on
    _clip, info = await audio.listen(end_silence_s=0.6)
    assert info["speech"] and info["speech_s"] >= 0.7
    pump.cancel()


async def test_a_caller_already_talking_when_she_starts_can_still_stop_her() -> None:
    _tunnel, audio, pump = await call(speech(3.0) + noise(3.0, 20))
    said = await audio.say(tone(5.0), barge=True)
    assert said.barged
    pump.cancel()


def typing(seconds: float, level: int = 2500) -> bytes:
    """Laptop typing near the phone: 60 ms key clicks with 100 ms between them (keystrokes last 40-100 ms)."""
    out = bytearray()
    while len(out) < 2 * call_audio.RATE * seconds:
        out += noise(0.06, level) + noise(0.10, 20)
    return bytes(out[: int(2 * call_audio.RATE * seconds)])


async def test_laptop_typing_never_starts_an_utterance() -> None:
    """Live calls 2026-10-11: typing was heard as '*sad music*' and 'thank'; a key click is short, speech is not."""
    _tunnel, audio, pump = await call(noise(0.3, 20) + typing(3.0) + noise(2.0, 20))
    clip, info = await audio.listen(no_speech_s=3.0)
    assert clip == b"" and not info["speech"]
    pump.cancel()


async def test_an_utterance_says_how_much_of_it_was_voiced() -> None:
    _tunnel, audio, pump = await call(noise(0.3, 20) + speech(1.0) + noise(2.5, 20))
    _clip, info = await audio.listen(end_silence_s=0.9)
    assert info["speech"] and 0.5 <= info["voiced_s"] <= 1.0
    pump.cancel()


async def listening_to(caller: bytes) -> CallAudio:
    """A CallAudio whose incoming frames are exactly `caller`, already queued (no pump): frame-exact listening."""
    ours, theirs = socket.socketpair()
    theirs.close()
    reader, writer = await asyncio.open_unix_connection(sock=ours)
    audio = CallAudio(reader, writer)
    for start in range(0, len(caller), FRAME_BYTES):
        audio.incoming.put_nowait(caller[start : start + FRAME_BYTES].ljust(FRAME_BYTES, b"\0"))
    audio.closed.set()
    return audio


async def test_a_reply_that_starts_at_once_is_kept_from_its_first_frame() -> None:
    # review 2026-10-11: the floor's 200 ms were thrown away, and a short reply said at once with them
    word = speech(0.35)
    audio = await listening_to(word + noise(1.0, 20))
    clip, info = await audio.listen(end_silence_s=0.6)
    audio.close()
    assert info["speech"] and clip.startswith(word) and info["voiced_s"] >= 0.25


async def test_a_soft_start_before_the_loud_part_goes_with_the_clip() -> None:
    # review 2026-10-11: the clip started at the loud run, so the "s" of "stop" never reached speech recognition
    soft = noise(0.15, 600)  # a breathy "s": below the start level
    audio = await listening_to(noise(0.3, 20) + soft + speech(0.5) + noise(1.0, 20))
    clip, info = await audio.listen(end_silence_s=0.6)
    audio.close()
    assert info["speech"] and soft in clip


async def test_after_a_barge_in_the_pre_rolls_voice_counts_as_voiced() -> None:
    # review 2026-10-11: words said over her are judged by their voiced time, most of which is in the pre-roll
    word = speech(0.3)
    preroll = tuple(word[start : start + FRAME_BYTES] for start in range(0, len(word), FRAME_BYTES))
    audio = await listening_to(noise(1.0, 20))
    _clip, info = await audio.listen(preroll=preroll, floor=10.0, end_silence_s=0.6)
    audio.close()
    assert info["voiced_s"] >= 0.2


def frames_of(pcm: bytes) -> tuple[bytes, ...]:
    return tuple(pcm[start : start + FRAME_BYTES] for start in range(0, len(pcm), FRAME_BYTES))


async def test_key_clicks_after_a_barge_in_are_never_sustained_voice() -> None:
    # review 2026-10-11: after a barge-in every loud key click counted as voiced, so 1.5 s of typing over her answer
    # that Whisper heard as "Thank you." passed as words
    clicks = typing(2.0)
    cut = 2 * RATE * 8 // 10  # her last 800 ms: the pre-roll
    audio = await listening_to(clicks[cut:] + noise(1.0, 20))
    _clip, info = await audio.listen(preroll=frames_of(clicks[:cut]), floor=10.0, end_silence_s=0.6)
    audio.close()
    assert info["voiced_s"] >= 0.6 and info["sustained_s"] == 0.0


async def test_speech_split_by_a_barge_in_is_sustained_voice() -> None:
    # a syllable that starts in the pre-roll and goes on after it is one run of sound
    word = speech(0.6)  # syllables of 150 ms sound and 50 ms quiet
    cut = 2 * RATE * 26 // 100  # 60 ms into the second syllable
    audio = await listening_to(word[cut:] + noise(1.0, 20))
    _clip, info = await audio.listen(preroll=frames_of(word[:cut]), floor=10.0, end_silence_s=0.6)
    audio.close()
    assert info["sustained_s"] == info["voiced_s"] == 0.45


async def test_typing_after_a_bump_is_not_sustained_voice() -> None:
    # a bump starts an utterance; the typing after it adds loud clicks, not sustained voice
    audio = await listening_to(noise(0.3, 20) + noise(0.15, 3000) + noise(0.1, 20) + typing(2.0) + noise(1.0, 20))
    _clip, info = await audio.listen(end_silence_s=0.6)
    audio.close()
    assert info["speech"] and info["voiced_s"] >= 0.6 and info["sustained_s"] == 0.15


def test_listening_needs_sustained_sound_well_above_the_line() -> None:
    # 100 ms of sound in a row (key clicks are 40-100 ms), louder than typing near the phone, and 4x the line's
    # quiet level (OpenWhisperer's rule)
    assert (call_audio.LISTEN_MIN_RMS, call_audio.LISTEN_START_FRAMES, call_audio.LISTEN_FLOOR_FACTOR) == (
        450.0,
        10,
        4.0,
    )


# The speech detector (barge = "speech"): the voice service's Silero VAD stands in as `speech_probability` (the tests'
# speech is tones, their road noise random), answered at once: the answer lands on the next frame, as a localhost
# request of a few milliseconds does on a real call.


async def detector(chunk: bytes) -> float | str:
    return speech_probability(chunk)


def recording(into: list[bytes]) -> Callable[[bytes], Awaitable[float | str]]:
    async def detect(chunk: bytes) -> float | str:
        into.append(chunk)
        return speech_probability(chunk)

    return detect


async def broken(chunk: bytes) -> float | str:
    return "http"


def test_the_detectors_audio_is_the_same_16k_audio_speech_recognition_gets() -> None:
    caller = speech(0.5) + noise(0.5, 300)
    with wave.open(io.BytesIO(pcm48_to_wav16k(caller))) as w:
        assert to16k(caller).tobytes() == w.readframes(w.getnframes())


def test_the_callers_frames_become_chunks_of_4096_samples_with_the_level_of_the_frames_inside() -> None:
    # 4096 samples at 16 kHz are 25.6 frames: frames 0-24 are inside the first chunk, frame 25 is split between the
    # first two, frames 26-50 are inside the second, frame 51 is split ...
    chunks = Chunks()
    frames = [tone(0.01, level=100 * (n + 1)) for n in range(52)]
    made = [(n, chunk) for n, frame in enumerate(frames) if (chunk := chunks.add(frame, rms(frame))) is not None]
    assert [n for n, _ in made] == [25, 51]
    (_, (first, first_level)), (_, (second, second_level)) = made
    assert len(first) == len(second) == 8192
    assert first == to16k(b"".join(frames))[:4096].tobytes()
    assert second == to16k(b"".join(frames))[4096:8192].tobytes()
    levels = [rms(frame) for frame in frames]
    assert first_level == pytest.approx(math.sqrt(sum(x * x for x in levels[:25]) / 25))  # a power mean
    assert second_level == pytest.approx(math.sqrt(sum(x * x for x in levels[26:51]) / 25))


async def test_road_noise_stops_her_by_loudness_but_not_by_speech() -> None:
    # the owner's car (13 Oct): road noise kept stopping her; the speech rule hears no speech in it
    road = noise(0.5, 30) + noise(6.0, 3000)
    _tunnel, audio, pump = await call(road)
    assert (await audio.say(tone(5.0), barge=True)).barged  # today's rule: stopped by the noise
    pump.cancel()
    barge = Barge("speech", detector)
    _tunnel, audio, pump = await call(road, barge)
    said = await audio.say(tone(5.0), barge=True)
    assert not said.barged
    assert barge.would == {"loudness": 1, "speech": 0}  # loudness ran beside it: counted once, never acted on
    assert barge.stopped == {"loudness": 0, "speech": 0}
    pump.cancel()


async def test_talking_over_her_stops_her_by_the_speech_rule_and_keeps_the_first_words() -> None:
    caller = noise(1.5, 30) + speech(1.5) + noise(3.0, 30)
    barge = Barge("speech", detector)
    tunnel, audio, pump = await call(caller, barge)
    said = await audio.say(tone(8.0), barge=True)
    assert said.barged and 1.5 < said.played_s < 2.1  # one 256 ms chunk of speech, decided when it is full
    assert said.level > 1000  # the chunk's level: what a later stop must beat after her own voice (1.5x)
    assert barge.stopped == {"loudness": 0, "speech": 1}
    # loudness fired first, in the same clip: no "would have" while the stop was the caller's turn (calls.py decides)
    assert said.shadows == ("loudness",) and barge.would == {"loudness": 0, "speech": 0}
    clip, info = await audio.listen(preroll=said.preroll, floor=said.floor, end_silence_s=0.9)
    assert info["speech"] and speech(1.5)[: 10 * FRAME_BYTES] in clip  # the pre-roll kept the first syllable
    await asyncio.sleep(0.05)
    assert tunnel.spoke_s() < 2.5
    pump.cancel()


async def test_a_far_voice_below_the_level_gate_does_not_stop_the_speech_rule() -> None:
    # a radio or a passenger far from the phone is speech to the detector: the chunk must also be loud enough
    chunks: list[bytes] = []
    _tunnel, audio, pump = await call(
        noise(1.0, 30) + speech(3.0, level=180) + noise(1.0, 30), Barge("speech", recording(chunks))
    )
    said = await audio.say(tone(4.5), barge=True)
    assert not said.barged
    assert any(speech_probability(chunk) >= 0.99 for chunk in chunks)  # heard as speech, but too quiet
    pump.cancel()


async def test_speech_the_level_gate_turned_away_is_logged_once_a_clip_in_numbers_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # to tune the gate: the loudest chunk it turned away with p >= 0.99, one line a clip, never words or audio
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    barge = Barge("speech", detector)
    _tunnel, audio, pump = await call(noise(1.0, 30) + speech(3.0, level=180) + noise(1.0, 30), barge)
    assert not (await audio.say(tone(4.5), barge=True)).barged
    lines = [record.getMessage() for record in caplog.records if "gated" in record.getMessage()]
    assert len(lines) == 1
    assert re.fullmatch(r"calls: speech gated \(p 1\.00, level \d+, floor \d+\)", lines[0])
    level = int(lines[0].split("level ")[1].split(",")[0])
    assert 100 < level <= 150  # below the gate (150 here: the line is quiet)
    pump.cancel()


async def test_loudness_in_charge_counts_what_the_speech_rule_would_have_stopped() -> None:
    # speech between the gates: above the speech rule's (150), below loudness's (400): only the shadow fires
    barge = Barge("loudness", detector)
    _tunnel, audio, pump = await call(noise(1.0, 30) + speech(2.0, level=450) + noise(2.0, 30), barge)
    said = await audio.say(tone(4.0), barge=True)
    assert not said.barged and barge.would == {"loudness": 0, "speech": 1}
    assert barge.stopped == {"loudness": 0, "speech": 0}
    assert barge.active == "loudness"
    pump.cancel()


async def test_without_a_detector_loudness_decides() -> None:
    barge = Barge("speech", None)  # the voice service has no detector (an older build, or still loading)
    assert barge.active == "loudness"
    _tunnel, audio, pump = await call(noise(0.5, 30) + noise(6.0, 3000), barge)
    assert (await audio.say(tone(5.0), barge=True)).barged
    assert barge.stopped == {"loudness": 1, "speech": 0} and barge.would == {"loudness": 0, "speech": 0}
    pump.cancel()


async def test_three_failed_chunks_hand_over_to_loudness_for_the_rest_of_the_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    barge = Barge("speech", broken)
    _tunnel, audio, pump = await call(noise(0.5, 30) + noise(8.0, 3000), barge)
    said = await audio.say(tone(6.0), barge=True)
    assert said.barged and barge.active == "loudness" and barge.failures == 3
    assert said.played_s > 0.75  # not before the third chunk (25.6 frames each) failed
    said = await audio.say(tone(6.0), barge=True)  # the next sentence: loudness at once, no requests
    assert said.barged and barge.failures == 3
    lines = [record.getMessage() for record in caplog.records if "detector" in record.getMessage()]
    assert lines == ["calls: the speech detector failed 3 times in a row; loudness for the rest of the call"]  # once
    assert barge.failed == {"http": 3}
    pump.cancel()


async def test_a_detectors_answer_older_than_600_ms_is_dropped() -> None:
    # the frames go on while a request is slow: an answer for audio 0.7 s old never stops her
    holder: list[CallAudio] = []

    async def slow(chunk: bytes) -> float | str:
        start = holder[0].frames_in
        while holder[0].frames_in < start + 70:
            await asyncio.sleep(0)
        return 1.0

    barge = Barge("speech", slow)
    _tunnel, audio, pump = await call(noise(0.5, 30) + noise(3.0, 3000), barge)
    holder.append(audio)
    said = await audio.say(tone(3.0), barge=True)
    assert barge.stopped["speech"] == 0 and barge.failures == 3  # late answers count as failed ones
    assert barge.failed == {"late": 3}
    assert said.barged and barge.active == "loudness"
    pump.cancel()


def dithered(pcm: bytes, seed: int) -> bytes:
    """`pcm` with a faint dither of its own: every frame differs, so a chunk can be found in the caller's audio."""
    rnd = random.Random(seed)
    return array.array("h", (max(-32768, min(32767, s + rnd.randint(-8, 8))) for s in array.array("h", pcm))).tobytes()


def test_the_pre_roll_reaches_the_first_frame_of_the_latest_answered_chunk() -> None:
    # a chunk (25.6 frames) begins at most 26 frames before the frame that completes it; its answer may come
    # VAD_MAX_AGE_FRAMES later, and the pre-roll must still hold that first frame (the first words)
    assert call_audio.PREROLL_FRAMES >= call_audio.VAD_MAX_AGE_FRAMES + 26 + 1


async def test_a_speech_stop_58_frames_late_keeps_the_onset_chunk_in_the_pre_roll() -> None:
    caller = dithered(noise(1.0, 30) + speech(2.0) + noise(2.0, 30), seed=3)
    low = to16k(caller).tobytes()
    first: list[int] = []  # the first frame of the chunk that stops her
    holder: list[CallAudio] = []

    async def late(chunk: bytes) -> float | str:
        p = speech_probability(chunk)
        if p >= 0.99 and not first:
            at = low.find(chunk)
            assert at >= 0 and at % 2 == 0
            first.append(at // 2 // 160)  # 160 samples at 16 kHz a frame
            start = holder[0].frames_in
            while holder[0].frames_in < start + 58:
                await asyncio.sleep(0)
        return p

    barge = Barge("speech", late)
    _tunnel, audio, pump = await call(caller, barge)
    holder.append(audio)
    said = await audio.say(tone(4.0), barge=True)
    assert said.barged and barge.stopped["speech"] == 1 and barge.failures == 0
    onset = caller[first[0] * FRAME_BYTES : (first[0] + 1) * FRAME_BYTES]
    assert onset in said.preroll  # the chunk's first frame: the first words go to speech recognition
    pump.cancel()


@pytest.mark.parametrize("rule", ["loudness", "speech"])
async def test_after_her_own_voice_a_stop_needs_half_again_its_level(rule: str) -> None:
    barge = Barge(rule, detector)
    barge.her_own_voice(2000.0)  # her echo stopped her at this level: 3000 from now on
    _tunnel, audio, pump = await call(noise(1.0, 30) + speech(2.0, level=3000) + noise(2.0, 30), barge)
    assert not (await audio.say(tone(4.0), barge=True)).barged  # as loud as her echo was: carries on
    pump.cancel()
    _tunnel, audio, pump = await call(noise(1.0, 30) + speech(2.0, level=8000) + noise(2.0, 30), barge)
    assert (await audio.say(tone(4.0), barge=True)).barged  # a caller well above it still stops her
    pump.cancel()
    assert barge.own_voice == 1


def test_her_own_voice_raises_the_level_once_per_call_never_again() -> None:
    barge = Barge("loudness")
    barge.her_own_voice(2000.0)  # a stop needs 3000 from now on
    barge.her_own_voice(4000.0)  # her echo again, louder still: the level stays (never 6000, never compounding)
    assert barge.least == 3000.0 and barge.own_voice == 2


def test_the_calls_end_line_counts_each_rule_in_charge_and_as_the_shadow() -> None:
    barge = Barge("speech", detector)
    barge.stopped.update(speech=1)
    barge.would.update(loudness=4)
    barge.her_own_voice(500.0)
    barge.acknowledgement()  # a listener's "okay" over her: she carried on (calls.is_acknowledgement)
    barge.acknowledgement()
    assert barge.summary() == (
        "calls: barge-in speech: speech stopped her 1 time, would have 0; loudness stopped her 0 times, would have 4;"
        " her own voice 1; acknowledgements 2; detector 0 chunks, slowest 0 ms, 0 failed"
    )
    assert Barge("loudness", detector).summary() == (
        "calls: barge-in loudness: loudness stopped her 0 times, would have 0; speech stopped her 0 times, would have 0;"
        " her own voice 0; acknowledgements 0; detector 0 chunks, slowest 0 ms, 0 failed"
    )
    assert Barge("speech", None).summary() == (
        "calls: barge-in speech (loudness: no speech detector): loudness stopped her 0 times, would have 0;"
        " her own voice 0; acknowledgements 0"
    )
    assert Barge("loudness", None).summary() == (
        "calls: barge-in loudness (no speech detector): loudness stopped her 0 times, would have 0; her own voice 0;"
        " acknowledgements 0"
    )


async def test_a_stop_that_was_no_words_counts_the_shadow_that_fired_with_it() -> None:
    # calls.py heard no words (or her own voice) in what stopped her: the shadow that fired in the same clip would
    # have made that false stop too
    barge = Barge("speech", detector)
    _tunnel, audio, pump = await call(noise(1.5, 30) + speech(1.5) + noise(3.0, 30), barge)
    said = await audio.say(tone(8.0), barge=True)
    assert said.barged and said.shadows == ("loudness",)
    barge.false_stop(said)
    assert barge.would == {"loudness": 1, "speech": 0} and barge.stopped == {"loudness": 0, "speech": 1}
    pump.cancel()


async def test_only_failures_in_a_row_hand_over_to_loudness() -> None:
    # a good answer between them starts the count again
    sequence: list[float | str] = ["http", "http", 0.02]
    answers = itertools.cycle(sequence)

    async def flaky(chunk: bytes) -> float | str:
        return next(answers)

    barge = Barge("speech", flaky)
    _tunnel, audio, pump = await call(noise(3.0, 30), barge)
    said = await audio.say(tone(2.0), barge=True)  # 7 chunks: 5 of them failed, never 3 in a row
    assert not said.barged and barge.active == "speech" and barge.failures < 3
    assert barge.failed == {"http": 5} and barge.chunks == 7
    pump.cancel()


async def test_the_end_line_has_the_detectors_failures_by_kind_and_its_slowest_answer() -> None:
    now = [0.0]
    timings: list[tuple[float, float | str]] = [(0.012, 0.02), (0.35, "timeout"), (0.004, "http"), (0.02, 0.02)]
    answers = iter(timings)

    async def timed(chunk: bytes) -> float | str:
        seconds, answer = next(answers)
        now[0] += seconds
        return answer

    barge = Barge("speech", timed, clock=lambda: now[0])
    _tunnel, audio, pump = await call(noise(3.0, 30), barge)
    await audio.say(tone(1.2), barge=True)  # 4 chunks
    assert barge.summary() == (
        "calls: barge-in speech: speech stopped her 0 times, would have 0; loudness stopped her 0 times, would have 0;"
        " her own voice 0; acknowledgements 0; detector 4 chunks, slowest 350 ms, 2 failed (http 1, timeout 1)"
    )
    pump.cancel()


async def test_a_fall_back_says_why_in_the_end_line() -> None:
    barge = Barge("speech", broken, clock=lambda: 0.0)  # its slowest answer by a fixed clock, never wall time
    _tunnel, audio, pump = await call(noise(0.5, 30) + noise(8.0, 3000), barge)
    assert (await audio.say(tone(6.0), barge=True)).barged  # loudness, after the third failed chunk
    assert barge.summary() == (
        "calls: barge-in speech (loudness after 3 failed answers in a row): speech stopped her 0 times, would have 0;"
        " loudness stopped her 1 time, would have 0; her own voice 0; acknowledgements 0; detector 3 chunks, slowest"
        " 0 ms, 3 failed (http 3)"
    )
    pump.cancel()


# Early end of turn (part A: Smart Turn's "finished?" at a short pause) and the pause by speech (part B: Silero's
# chunks below PAUSE_NOISE_P count as pause). Off is today, byte for byte: these clips and infos were recorded with the
# code before either part existed (1cc0902).

GOLDEN_LISTENS: dict[str, tuple[Callable[[], bytes], dict[str, object], int, str, dict[str, object]]] = {
    "thinking_pause": (
        lambda: noise(0.3, 20) + speech(1.0) + noise(1.2, 20) + speech(1.0) + noise(3.0, 20),
        {"end_silence_s": 1.8},
        340800,
        "2f98388aba1bae495a5a68a416a2304213e5c6693da388fa56bd67937243fe41",
        {"speech_s": 3.55, "voiced_s": 1.5, "sustained_s": 1.5, "floor": 12, "peak": 2181, "cut_by_max": False},
    ),
    "reply_at_once": (
        lambda: speech(0.35) + noise(1.0, 20),
        {"end_silence_s": 0.6},
        52800,
        "0890c06c8997beaf570adc1f2fde1f4a68654e6d363f2a0b4751b5d243472a81",
        {"speech_s": 0.55, "voiced_s": 0.3, "sustained_s": 0.3, "floor": 18, "peak": 2172, "cut_by_max": False},
    ),
    "soft_start": (
        lambda: noise(0.3, 20) + noise(0.15, 600) + speech(0.5) + noise(1.0, 20),
        {"end_silence_s": 0.6},
        86400,
        "c8540b938459e84e02eea52a56ec93b71aab319a1a22aec4ca7cf50b16757c9a",
        {"speech_s": 0.9, "voiced_s": 0.4, "sustained_s": 0.4, "floor": 12, "peak": 2178, "cut_by_max": False},
    ),
    "no_speech": (
        lambda: noise(2.0, 20),
        {"end_silence_s": 2.3, "no_speech_s": 1.0},
        0,
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        {"speech": False, "floor": 12, "peak": 13},
    ),
    "typing_after_bump": (
        lambda: noise(0.3, 20) + noise(0.15, 3000) + noise(0.1, 20) + typing(2.0) + noise(1.0, 20),
        {"end_silence_s": 0.6},
        252480,
        "82c8d061c8b3c11adc8b0e29242461eab245ddc3efe7d4ccf7f9eb8405aa9850",
        {"speech_s": 2.63, "voiced_s": 0.93, "sustained_s": 0.15, "floor": 12, "peak": 1809, "cut_by_max": False},
    ),
    "max_cap": (
        lambda: noise(0.3, 20) + speech(3.0) + noise(1.0, 20),
        {"end_silence_s": 2.3, "max_s": 1.0},
        96000,
        "e2ef6f2d111f92dc58cd16ac484f70bc6ad7bbdb4ef4a614874893211ca2895e",
        {"speech_s": 1.0, "voiced_s": 0.6, "sustained_s": 0.6, "floor": 12, "peak": 2181, "cut_by_max": True},
    ),
    "burst_in_pause": (
        lambda: noise(0.3, 20) + speech(1.0) + noise(0.5, 20) + noise(0.2, 3000) + noise(3.0, 20),
        {"end_silence_s": 2.3},
        201600,
        "ebd6765c55a72c21269f05b03b6521e61ca16e43f7e1c8b1de27f62def955c03",
        {"speech_s": 2.1, "voiced_s": 0.95, "sustained_s": 0.95, "floor": 12, "peak": 2181, "cut_by_max": False},
    ),
    "after_barge_in": (
        lambda: noise(1.0, 20),
        {"end_silence_s": 0.6, "floor": 10.0, "preroll": frames_of(speech(0.3))},
        48000,
        "a392a16e4fe183cda063880d75a6d0de8e6697b3fbfd248e92827a9ff0a04fc1",
        {"speech_s": 0.5, "voiced_s": 0.25, "sustained_s": 0.25, "floor": 10, "peak": 13, "cut_by_max": False},
    ),
}


@pytest.mark.parametrize("turn", [False, True])
@pytest.mark.parametrize("name", list(GOLDEN_LISTENS))
async def test_without_judge_or_detector_clip_and_info_are_todays(name: str, turn: bool) -> None:
    make, kwargs, length, digest, expected = GOLDEN_LISTENS[name]
    audio = await listening_to(make())
    clip, info = await audio.listen(**kwargs, turn=TurnEnd() if turn else None)  # type: ignore[arg-type]
    audio.close()
    assert (len(clip), hashlib.sha256(clip).hexdigest()) == (length, digest)
    assert info == ({"speech": True, **expected} if length else expected)


QUIET_FRAME = noise(0.01, 20)  # the line's floor: about 12
TONE_FRAME = tone(0.01)  # a voice: about 2100, far over the 450 line
NOISE_FRAME = noise(0.01, 3000)  # a clatter: loud, but no voice
SOFT_FRAME = tone(0.01, level=400)  # a voice below the 450 line


def scripted(*parts: tuple[str, int]) -> bytes:
    """The caller's audio frame by frame: ("q", n) quiet, ("t", n) a voice, ("n", n) a loud noise, ("s", n) a soft
    voice. Frame numbers in the tests count from the listen's first frame: 30 quiet frames, then a voice from frame
    30 starts the utterance at frame 39 (10 loud in a row), and the clip begins 30 frames before that, at frame 10."""
    kinds = {"q": QUIET_FRAME, "t": TONE_FRAME, "n": NOISE_FRAME, "s": SOFT_FRAME}
    return b"".join(kinds[kind] * count for kind, count in parts)


# A turn: a voice in frames 30 to 127, then quiet. Its pause starts at frame 127, the last sound; the short pause
# (60 frames) is reached on frame 187, the full pause (230) on frame 357.
TURN = (("q", 30), ("t", 98), ("q", 400))
LAST_SOUND = 127
FIRST_KEPT = 10


def kept(caller: bytes, last: int) -> bytes:
    """The clip of a turn whose last sound is frame `last`: from its first frame to TAIL_FRAMES after that sound."""
    return caller[FIRST_KEPT * FRAME_BYTES : (last + 1 + call_audio.TAIL_FRAMES) * FRAME_BYTES]


class Feeder:
    """A listen's frames one at a time: the next goes in once the listen took and judged the last one, with a few turns
    of the event loop between, so the fakes answer on a chosen frame ("on frame n": seen while frame n is judged)."""

    def __init__(self, caller: bytes) -> None:
        self.frames = [frame.ljust(FRAME_BYTES, b"\0") for frame in frames_of(caller)]
        self.taken = 0  # frames the listen took and judged

    async def until(self, frame: int) -> None:
        """Wait until every frame before `frame` was judged: an answer then is seen on `frame`."""
        while self.taken < frame:
            await asyncio.sleep(0)

    async def listen(self, **kwargs: Any) -> tuple[bytes, dict[str, Any], int]:
        """The listen's clip and info, and the frame it ended on."""
        ours, theirs = socket.socketpair()
        theirs.close()
        reader, writer = await asyncio.open_unix_connection(sock=ours)
        audio = CallAudio(reader, writer)
        put = 0

        async def feed() -> None:
            nonlocal put
            for frame in self.frames:
                audio.incoming.put_nowait(frame)
                put += 1
                while not audio.incoming.empty():
                    await asyncio.sleep(0)
                self.taken += 1
                for _ in range(3):
                    await asyncio.sleep(0)
            audio.closed.set()

        feeding = asyncio.create_task(feed())
        try:
            clip, info = await audio.listen(**kwargs)
        finally:
            feeding.cancel()
            audio.close()
        return clip, info, put - 1 - audio.incoming.qsize()


class FakeJudge:
    """Smart Turn's stand-in: one answer per ask, in order, each seen on its frame of the listen `feeder` feeds (None:
    on the frame after the ask; "raise": the judge raises). An ask past the list is answered 0.0 at once."""

    def __init__(self, *answers: tuple[float | str, int | None]) -> None:
        self.answers = list(answers)
        self.windows: list[bytes] = []
        self.feeder: Feeder | None = None

    async def __call__(self, window: bytes) -> float | str:
        answer, frame = self.answers[len(self.windows)] if len(self.windows) < len(self.answers) else (0.0, None)
        self.windows.append(window)
        if frame is not None:
            assert self.feeder is not None
            await self.feeder.until(frame)
        if answer == "raise":
            raise RuntimeError("the judge broke")
        return answer


async def judged(
    parts: tuple[tuple[str, int], ...],
    *answers: tuple[float | str, int | None],
    judge: FakeJudge | None = None,
    turn: TurnEnd | None = None,
    **kw: Any,
) -> tuple[bytes, dict[str, Any], int, FakeJudge, TurnEnd]:
    """A listen to `parts` with Smart Turn's stand-in giving `answers` (or `judge`'s, a call's judge across listens):
    clip, info, the end frame, the judge and the call's rules."""
    feeder = Feeder(scripted(*parts))
    judge = judge or FakeJudge(*answers)
    judge.feeder = feeder
    turn = turn or TurnEnd(judge, clock=lambda: 0.0)
    clip, info, end = await feeder.listen(end_silence_s=2.3, turn=turn, **kw)
    return clip, info, end, judge, turn


async def test_a_sure_answer_ends_at_the_short_pause(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    clip, info, end, judge, _turn = await judged(TURN, (0.92, None))
    assert end == LAST_SOUND + 60 + 1  # asked on the 60th frame of the pause, answered on the next
    assert clip == kept(scripted(*TURN), LAST_SOUND) and len(judge.windows) == 1
    assert {key: info[key] for key in ("ended", "pause_s", "quiet_s", "p", "answer_ms")} == {
        "ended": "sure",
        "pause_s": 0.61,
        "quiet_s": 0.61,
        "p": 0.92,
        "answer_ms": 0,
    }
    assert info["speech"] and info["speech_s"] == round(len(clip) / (2 * RATE), 2) and not info["cut_by_max"]
    assert "calls: turn ended early (sure, p 0.92, pause 0.61 s, answer 0 ms)" in caplog.messages
    _clip, _info, end, _judge, _turn = await judged(TURN, (0.92, LAST_SOUND + 60 + 9))
    assert end == LAST_SOUND + 60 + 9  # an answer 9 frames after the ask: the turn ends then


async def test_the_window_is_at_most_8_s_ending_0_2_s_after_the_last_sound() -> None:
    caller = scripted(*TURN)
    _clip, _info, _end, judge, _turn = await judged(TURN, (0.2, None))
    assert judge.windows == [to16k(kept(caller, LAST_SOUND)).tobytes()]  # the whole clip: shorter than 8 s
    long_turn = (("q", 30), ("t", 1000), ("q", 400))
    caller = dithered(scripted(*long_turn), seed=5)  # every frame differs
    feeder = Feeder(caller)
    judge = FakeJudge((0.2, None))
    judge.feeder = feeder
    await feeder.listen(end_silence_s=2.3, turn=TurnEnd(judge))
    last = 30 + 1000 - 1
    end = last + 1 + call_audio.TAIL_FRAMES
    assert judge.windows == [to16k(caller[(end - 800) * FRAME_BYTES : end * FRAME_BYTES]).tobytes()]
    assert len(judge.windows[0]) == 256_000  # 8 s at 16 kHz, the model's whole window


async def test_probably_ends_at_1_5_s() -> None:
    clip, info, end, _judge, _turn = await judged(TURN, (0.6, None))
    assert end == LAST_SOUND + 150 and info["ended"] == "probably" and info["p"] == 0.6
    assert clip == kept(scripted(*TURN), LAST_SOUND)


async def test_with_a_full_pause_of_1_5_s_or_less_probably_ends_as_the_full_pause() -> None:
    # review M4: end_of_turn_seconds set by hand to 1.0 s: "probably" would wait 1.0 s, the full pause itself; that end
    # is the full pause (nothing saved), never counted as an early one (and never held by calls.py)
    feeder = Feeder(scripted(*TURN))
    judge = FakeJudge((0.6, None))
    judge.feeder = feeder
    turn = TurnEnd(judge, clock=lambda: 0.0)
    _clip, info, end = await feeder.listen(end_silence_s=1.0, turn=turn)
    assert end == LAST_SOUND + 100 and info["ended"] == "full" and info["p"] == 0.6
    assert turn.ends[-1].kind == "full" and turn.ends[-1].saved_s == 0.0


async def test_unsure_waits_the_full_pause() -> None:
    clip, info, end, _judge, _turn = await judged(TURN, (0.3, None))
    assert end == LAST_SOUND + 230 and info["ended"] == "full" and info["p"] == 0.3
    assert clip == kept(scripted(*TURN), LAST_SOUND) and info["quiet_s"] == info["pause_s"] == 2.3


async def test_speech_before_the_answer_drops_it_and_the_next_pause_asks() -> None:
    parts = (("q", 30), ("t", 98), ("q", 72), ("t", 50), ("q", 400))  # she goes on at frame 200, until 249
    clip, info, end, judge, turn = await judged(parts, (0.95, 205), (0.95, None))
    assert end == 249 + 61 and info["ended"] == "sure" and len(judge.windows) == 2
    assert clip == kept(scripted(*parts), 249)  # one clip: both parts
    assert turn.asks == {"sure": 1, "dropped": 1} and turn.judge_failures == 0


async def test_speech_after_probably_cancels_it() -> None:
    parts = (("q", 30), ("t", 98), ("q", 100), ("t", 50), ("q", 400))  # she goes on at frame 228, before 1.5 s
    _clip, info, end, judge, turn = await judged(parts, (0.6, None), (0.2, None))
    assert end == 277 + 230 and info["ended"] == "full" and len(judge.windows) == 2
    assert turn.asks == {"probably": 1, "not": 1}


@pytest.mark.parametrize("answer", [0.2, "timeout"])
async def test_never_a_second_ask_in_one_pause(answer: float | str) -> None:
    _clip, info, end, judge, _turn = await judged(TURN, (answer, None))
    assert end == LAST_SOUND + 230 and info["ended"] == "full" and len(judge.windows) == 1


async def test_an_answer_after_30_frames_is_late_never_used() -> None:
    asked = LAST_SOUND + 60
    _clip, info, end, _judge, turn = await judged(TURN, (0.95, asked + 30))
    assert end == asked + 30 and info["ended"] == "sure"  # the 30th frame after the ask: still in time
    _clip, info, end, _judge, turn = await judged(TURN, (0.95, asked + 31))
    assert end == LAST_SOUND + 230 and info["ended"] == "full" and info["p"] is None
    assert turn.judge_failed == {"late": 1} and turn.judge_failures == 1


async def test_a_backlog_of_frames_waits_for_smart_turns_answer() -> None:
    # review M1: frames queued while Whisper ran (the hold's listen) are taken without the event loop running, so an
    # answer that comes at once was counted late (3 of them: the full pause for the call); the listen now waits for it
    # in wall time, at most as long as the client does, and the turn ends on the same frame as in real time
    ours, theirs = socket.socketpair()
    theirs.close()
    reader, writer = await asyncio.open_unix_connection(sock=ours)
    audio = CallAudio(reader, writer)
    for frame in frames_of(scripted(*TURN)):
        audio.incoming.put_nowait(frame.ljust(FRAME_BYTES, b"\0"))  # all of it queued: a backlog
    total = audio.incoming.qsize()
    judge = FakeJudge((0.95, None))  # answers as soon as it runs
    turn = TurnEnd(judge, clock=lambda: 0.0)
    _clip, info = await asyncio.wait_for(audio.listen(end_silence_s=2.3, turn=turn), 5)
    audio.close()
    assert total - 1 - audio.incoming.qsize() == LAST_SOUND + 60 + 1 and info["ended"] == "sure"
    assert turn.judge_failed == {} and turn.asks == {"sure": 1}


async def test_timeout_http_late_raising_count_and_3_mean_the_full_pause_for_the_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    judge = FakeJudge(("timeout", None), ("http", None), ("raise", None), (0.95, None))
    turn = TurnEnd(judge)
    for _ in range(3):
        _clip, info, end, _judge, _turn = await judged(TURN, judge=judge, turn=turn)
        assert end == LAST_SOUND + 230 and info["ended"] == "full"
    assert turn.judge_failed == {"timeout": 1, "http": 1, "error": 1} and not turn.judging
    _clip, info, end, _judge, _turn = await judged(TURN, judge=judge, turn=turn)
    assert len(judge.windows) == 3 and end == LAST_SOUND + 230  # no judge for the rest of the call
    judge = FakeJudge(*[(0.95, 10_000)] * 3)  # never in time
    turn = TurnEnd(judge)
    for _ in range(3):
        _clip, info, end, _judge, _turn = await judged(TURN, judge=judge, turn=turn)
        assert end == LAST_SOUND + 230
    assert turn.judge_failed == {"late": 3} and not turn.judging
    lines = [line for line in caplog.messages if "turn detector" in line]
    assert lines == ["calls: the turn detector failed 3 times in a row; the full pause for the rest of the call"] * 2


async def test_a_good_answer_resets_failures() -> None:
    answers: list[float | str] = ["timeout", "timeout", 0.3, "timeout", "timeout", 0.9]
    judge = FakeJudge(*[(answer, None) for answer in answers])
    turn = TurnEnd(judge)
    for _ in range(5):
        await judged(TURN, judge=judge, turn=turn)
    assert turn.judging and turn.judge_failures == 2 and turn.judge_failed == {"timeout": 4}
    _clip, info, _end, _judge, _turn = await judged(TURN, judge=judge, turn=turn)
    assert info["ended"] == "sure" and turn.judge_failures == 0


async def test_the_120_s_cap_still_ends() -> None:
    parts = (("q", 30), ("t", 300), ("q", 300))
    clip, info, end, judge, turn = await judged(parts, (0.95, None), max_s=1.0)
    assert end == FIRST_KEPT + 99 and info["ended"] == "max" and info["cut_by_max"] and judge.windows == []
    assert len(clip) == 100 * FRAME_BYTES and turn.ends[-1].saved_s == 0.0


# The pause by speech (part B, rule R4 at PAUSE_NOISE_P): chunk n holds frames 25.6 n to 25.6 (n + 1), and is full
# (asked) on frame chunk_full(n). The turn above: chunks 0 to 4 hold the voice (frames 30 to 127), chunk 5 (frames 128
# to 153) is the first quiet one, chunk 7 is frames 180 to 204, chunk 8 frames 205 to 229.


def chunk_full(n: int) -> int:
    return math.ceil((n + 1) * call_audio.VAD_CHUNK / 160) - 1


def voice_then(rated: dict[int, float | str]) -> Callable[[int], float | str]:
    """Chunk n's answer: `rated`'s, else 0.9 for the voice's chunks (0 to 4) and 0.05 for the rest."""
    return lambda n: rated.get(n, 0.9 if n <= 4 else 0.05)


class FakeDetector:
    """Silero's stand-in for the pause: chunk n's answer is p(n) (a number, a failure kind, or "hang": none ever), seen
    on the frame after the chunk is full or on frame at(n). Keeps the chunks as asked and how many were out at once."""

    def __init__(self, p: Callable[[int], float | str], at: Callable[[int], int | None] = lambda n: None) -> None:
        self.p, self.at = p, at
        self.chunks: list[bytes] = []
        self.out = self.most_out = 0
        self.feeder: Feeder | None = None

    async def __call__(self, chunk: bytes) -> float | str:
        n = len(self.chunks)
        self.chunks.append(chunk)
        self.out += 1
        self.most_out = max(self.most_out, self.out)
        try:
            frame = self.at(n)
            if frame is not None:
                assert self.feeder is not None
                await self.feeder.until(frame)
            answer = self.p(n)
            if answer == "hang":
                await asyncio.Event().wait()
            return answer
        finally:
            self.out -= 1


async def paused(
    parts: tuple[tuple[str, int], ...] | bytes,
    detector: FakeDetector,
    *,
    judge: FakeJudge | None = None,
    turn: TurnEnd | None = None,
    **kw: Any,
) -> tuple[bytes, dict[str, Any], int, TurnEnd]:
    """A listen to `parts` with the pause by speech (and Smart Turn's stand-in, if given): clip, info, end frame, rules."""
    feeder = Feeder(parts if isinstance(parts, bytes) else scripted(*parts))
    detector.feeder = feeder
    if judge is not None:
        judge.feeder = feeder
    turn = turn or TurnEnd(judge, detector, clock=lambda: 0.0)
    clip, info, end = await feeder.listen(end_silence_s=2.3, turn=turn, **kw)
    return clip, info, end, turn


BURST_AFTER = (("q", 30), ("t", 98), ("q", 52), ("n", 10), ("q", 400))  # a clatter in frames 180 to 189: chunk 7


async def test_noise_below_0_1_with_its_next_chunk_counts_as_pause(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    clip, info, end, turn = await paused(BURST_AFTER, FakeDetector(voice_then({})))
    assert end == LAST_SOUND + 230 and info["ended"] == "speech"  # by loudness: 189 + 230
    assert clip == kept(scripted(*BURST_AFTER), LAST_SOUND)  # the clatter is cut off with the trailing quiet
    assert (info["pause_s"], info["quiet_s"], info["p"]) == (2.3, 1.68, None)
    assert turn.ends[-1].noise_frames == 10 and turn.ends[-1].saved_s == 0.62
    assert "calls: the speech pause ended the turn 0.62 s sooner (10 loud frames counted as pause)" in caplog.messages
    clip, info, end, turn = await paused(BURST_AFTER, FakeDetector(voice_then({8: 0.5})))  # the next chunk: not low
    assert end == 189 + 230 and info["ended"] == "full" and clip == kept(scripted(*BURST_AFTER), 189)


async def test_noise_that_goes_on_settles_at_once() -> None:
    # a clatter in frames 160 to 199 (chunks 6 and 7): chunk 7 is low and so was the loud chunk 6 before it, so it is
    # noise at once, without chunk 8's answer (which never comes here)
    parts = (("q", 30), ("t", 98), ("q", 32), ("n", 40), ("q", 400))
    detector = FakeDetector(voice_then({8: "hang"}))
    _clip, info, end, turn = await paused(parts, detector)
    turn.close()
    assert end == LAST_SOUND + 230 and info["ended"] == "speech" and turn.ends[-1].noise_frames == 40


async def test_a_low_word_start_before_speech_is_sound() -> None:
    # a word's first loud frames (215 to 224, chunk 8) rated low, then the word goes on softly (chunk 9, rated 0.9):
    # the start is sound, as the next chunk is speech
    parts = (("q", 30), ("t", 98), ("q", 87), ("t", 10), ("s", 36), ("q", 400))
    _clip, info, end, _turn = await paused(parts, FakeDetector(voice_then({9: 0.9})))
    assert end == 224 + 230 and info["ended"] == "full"


@pytest.mark.parametrize(("chunk_7", "last", "kind"), [(0.9, 179, "full"), (0.05, LAST_SOUND, "speech")])
async def test_a_split_frame_needs_both_chunks(chunk_7: float, last: int, kind: str) -> None:
    # a click on frame 140 (chunk 5, noise) and one on frame 179, half in chunk 6 (noise), half in chunk 7
    parts = (("q", 30), ("t", 98), ("q", 12), ("n", 1), ("q", 38), ("n", 1), ("q", 400))
    _clip, info, end, _turn = await paused(parts, FakeDetector(voice_then({7: chunk_7})))
    assert end == last + 230 and info["ended"] == kind


@pytest.mark.parametrize("rated", [{7: "http"}, {8: "timeout"}, {8: "hang"}, {7: "hang"}])
async def test_failed_or_missing_answers_are_sound(rated: dict[int, float | str]) -> None:
    _clip, info, end, turn = await paused(BURST_AFTER, FakeDetector(voice_then(rated)))
    turn.close()
    assert end == 189 + 230 and info["ended"] == "full"
    assert turn.chunk_failures <= 1 and turn.detecting


async def test_3_detector_failures_mean_loudness_for_the_call(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    detector = FakeDetector(lambda n: "http")
    _clip, info, end, turn = await paused(BURST_AFTER, detector)
    assert end == 189 + 230 and info["ended"] == "full" and not turn.detecting
    assert turn.chunk_failed == {"http": 3} and len(detector.chunks) == 3
    _clip, info, end, turn = await paused(BURST_AFTER, detector, turn=turn)
    assert end == 189 + 230 and len(detector.chunks) == 3  # loudness for the rest of the call: nothing asked
    lines = [line for line in caplog.messages if "speech detector" in line]
    assert lines == [
        "calls: the speech detector failed 3 times in a row while listening; loudness holds the pause for the rest"
        " of the call"
    ]


async def test_a_burst_settled_as_noise_brings_back_the_answer() -> None:
    # Smart Turn is sure about the pause from frame 127 (asked on 187, answered on 195), but a clatter in frames 190
    # to 199 counts as sound until chunk 8's answer (frame 231) settles it as noise: then the answer stands
    parts = (("q", 30), ("t", 98), ("q", 62), ("n", 10), ("q", 400))
    judge = FakeJudge((0.95, 195))
    clip, info, end, turn = await paused(parts, FakeDetector(voice_then({})), judge=judge)
    assert end == chunk_full(8) + 1 and info["ended"] == "sure" and info["p"] == 0.95
    assert clip == kept(scripted(*parts), LAST_SOUND) and turn.asks == {"sure": 1}
    feeder = Feeder(scripted(*parts))  # by loudness alone the clatter drops the ask
    judge = FakeJudge((0.95, 195), (0.2, None))
    judge.feeder = feeder
    _clip, info, end = await feeder.listen(end_silence_s=2.3, turn=TurnEnd(judge))
    assert end == 199 + 230 and info["ended"] == "full"


async def test_an_ask_about_a_pause_that_settled_back_is_asked_again() -> None:
    # review M3: a clatter in frames 180 to 189 whose chunks (7 and 8) are answered only on frame 264: Smart Turn is
    # asked on frame 249 about the pause from 189, then the clatter settles as noise and the pause is the one from 127
    # again; that ask is dropped and the right one asked (its window ends 0.2 s after frame 127, as on time), so the
    # early end is not lost: probably, at 1.5 s of that pause
    detector = FakeDetector(voice_then({}), at=lambda n: 264 if n in (7, 8) else None)
    judge = FakeJudge((0.6, None), (0.6, None))
    clip, info, end, turn = await paused(BURST_AFTER, detector, judge=judge)
    assert end == LAST_SOUND + 150 and info["ended"] == "probably" and info["p"] == 0.6
    assert turn.asks == {"probably": 2}  # the first answer counts too: it was asked and answered
    assert len(judge.windows) == 2 and judge.windows[1] == to16k(kept(scripted(*BURST_AFTER), LAST_SOUND)).tobytes()
    assert clip == kept(scripted(*BURST_AFTER), LAST_SOUND)


async def test_chunks_go_out_in_order_one_at_a_time() -> None:
    caller = dithered(scripted(*BURST_AFTER), seed=9)
    detector = FakeDetector(voice_then({}), at=lambda n: chunk_full(n) + 40)  # slower than a chunk
    _clip, _info, end, turn = await paused(caller, detector)
    turn.close()
    low = to16k(caller).tobytes()
    expected = [low[n * 8192 : (n + 1) * 8192] for n in range(len(low) // 8192)]
    assert detector.most_out == 1 and len(detector.chunks) >= 10
    assert detector.chunks == expected[: len(detector.chunks)]  # from the listen's first frame, in order
    assert sum(1 for n in range(len(expected)) if chunk_full(n) <= end) - len(detector.chunks) <= 2


def _random_parts(rnd: random.Random) -> tuple[tuple[str, int], ...]:
    parts: list[tuple[str, int]] = [("q", 30), ("t", rnd.randint(20, 60))]
    for _ in range(rnd.randint(1, 5)):
        parts.append((rnd.choice("qqnnts"), rnd.randint(1, 80)))
    return (*parts, ("q", 240))


def _random_detector(rnd: random.Random) -> FakeDetector:
    answers: dict[int, float | str] = {}
    delays: dict[int, int] = {}

    def p(n: int) -> float | str:
        if n not in answers:
            choices: list[float | str] = [
                rnd.uniform(0.0, 0.15),
                rnd.uniform(0.0, 0.15),
                rnd.random(),
                "http",
                "timeout",
            ]
            answers[n] = rnd.choice(choices)
        return answers[n]

    def at(n: int) -> int:
        delays.setdefault(n, rnd.choice([0, 0, 1, 5, 30]))
        return chunk_full(n) + 1 + delays[n]

    return FakeDetector(p, at)


async def test_the_speech_pause_never_ends_later_than_loudness(monkeypatch: pytest.MonkeyPatch) -> None:
    # the scripts reuse four frames: their levels and 16 kHz samples are worked out once (the same numbers)
    rms_of: dict[bytes, float] = {}
    low_of: dict[bytes, array.array[int]] = {}
    real_rms, real_low = call_audio.rms, call_audio.to16k
    monkeypatch.setattr(
        call_audio, "rms", lambda pcm: rms_of[pcm] if pcm in rms_of else rms_of.setdefault(pcm, real_rms(pcm))
    )
    monkeypatch.setattr(
        call_audio, "to16k", lambda pcm: low_of[pcm] if pcm in low_of else low_of.setdefault(pcm, real_low(pcm))
    )
    rnd = random.Random(2026)
    sooner = 0
    for _ in range(200):
        parts = _random_parts(rnd)
        caller = scripted(*parts)
        audio = await listening_to(caller)  # by loudness: no task to wait for
        today, _info = await audio.listen(end_silence_s=2.3)
        by_loudness = len(caller) // FRAME_BYTES - 1 - audio.incoming.qsize()
        audio.close()
        detector = _random_detector(rnd)
        clip, info, end, turn = await paused(parts, detector)
        turn.close()
        assert end <= by_loudness and today.startswith(clip)
        assert (info["ended"] == "full") == (end == by_loudness)
        sooner += end < by_loudness
    assert sooner >= 10  # the scripts do have noise in the pause


async def test_the_watch_sees_sustained_speech_not_clicks_and_stops_when_she_speaks() -> None:
    # after an early end, while her answer is worked out: the caller going on is seen once, after 10 loud frames in a
    # row (the first syllable starts on frame 126); key clicks (6 loud frames) never count
    seen: list[int] = []
    _tunnel, audio, pump = await call(noise(0.3, 20) + typing(0.96) + speech(1.0) + noise(1.0, 20))
    audio.watch(400, 450.0, lambda: seen.append(audio.frames_in))
    await asyncio.wait_for(audio.closed.wait(), 10)
    assert seen == [126 + 10]
    pump.cancel()
    seen.clear()
    _tunnel, audio, pump = await call(noise(0.3, 20) + typing(1.0) + speech(1.0) + noise(1.0, 20))
    audio.watch(100, 450.0, lambda: seen.append(audio.frames_in))  # over before the caller speaks
    await asyncio.wait_for(audio.closed.wait(), 10)
    assert seen == []
    pump.cancel()
    _tunnel, audio, pump = await call(noise(0.3, 20) + speech(2.0) + noise(1.0, 20))
    audio.watch(400, 450.0, lambda: seen.append(audio.frames_in))
    await audio.say(tone(0.1))  # she speaks: the watch is over
    await asyncio.wait_for(audio.closed.wait(), 10)
    assert seen == []
    pump.cancel()
    _tunnel, audio, pump = await call(noise(0.3, 20) + speech(2.0) + noise(1.0, 20))
    audio.watch(400, 450.0, lambda: seen.append(audio.frames_in))
    audio.unwatch()  # calls.py holds the turn: its own listen hears what comes
    await asyncio.wait_for(audio.closed.wait(), 10)
    assert seen == []
    pump.cancel()


async def test_her_lines_while_the_answer_is_worked_out_leave_the_watch_on() -> None:
    # "Got it." and a thinking line (watch=True) come inside the rest of the full pause: the caller going on over them
    # (from frame 30) is seen, after 10 loud frames in a row, as without them
    seen: list[int] = []
    _tunnel, audio, pump = await call(noise(0.3, 20) + speech(1.0) + noise(1.0, 20))
    audio.watch(400, 450.0, lambda: seen.append(audio.frames_in))
    await audio.say(tone(0.5), watch=True)
    await asyncio.wait_for(audio.closed.wait(), 10)
    assert seen == [30 + 10]
    pump.cancel()


async def test_over_her_lines_the_watch_needs_more_than_her_own_voices_level() -> None:
    # her own voice stopped her before in the call (a loud speaker next to the phone): while she speaks and in her
    # echo's tail, a frame needs more than that level, as for barge-in; after it, the watch's own level again
    seen: list[int] = []
    barge = Barge()
    barge.her_own_voice(2000.0)  # a stop needs more than 3000 from now on
    _tunnel, audio, pump = await call(noise(0.3, 20) + speech(2.0) + noise(1.0, 20), barge)
    audio.watch(400, 450.0, lambda: seen.append(audio.frames_in))
    await audio.say(tone(0.5), watch=True)  # frames 1 to 50 go out, then her echo's tail: 15 frames more
    await asyncio.wait_for(audio.closed.wait(), 10)
    assert len(seen) == 1 and 50 + call_audio.ECHO_TAIL_FRAMES + 10 <= seen[0] <= 50 + call_audio.ECHO_TAIL_FRAMES + 30
    pump.cancel()


BANG = noise(0.15, 3000)  # a door or a cup: 15 loud frames, no voice


async def watched(caller: bytes, barge: Barge, *, frames: int = 400) -> tuple[list[int], CallAudio]:
    """The frames on which the watch (from the call's first frame) saw the caller go on, to the end of `caller`."""
    seen: list[int] = []
    _tunnel, audio, pump = await call(caller, barge)
    audio.watch(frames, 450.0, lambda: seen.append(audio.frames_in))
    await asyncio.wait_for(audio.closed.wait(), 10)
    pump.cancel()
    return seen, audio


async def test_with_a_speech_detector_the_watch_needs_speech_not_just_loud_sound() -> None:
    # review I2: a bang, then dishes, after an early end are no going on; the caller's voice is, once its chunk is
    # rated speech (p >= 0.99, as for barge-in); without a detector loudness alone decides, as before
    quiet = noise(0.3, 20)
    bangs = quiet + BANG + noise(0.45, 20) + BANG + noise(1.0, 20)
    seen, audio = await watched(bangs, Barge("speech", detector))
    assert seen == [] and audio.barge.chunks == 0  # never counted in barge-in's own line
    seen, _audio = await watched(bangs, Barge())
    assert seen == [30 + 10]  # loudness alone: the bang
    seen, _audio = await watched(bangs + speech(1.0) + noise(1.0, 20), Barge("speech", detector), frames=1000)
    assert len(seen) == 1 and 205 + 10 <= seen[0] <= 205 + 26 + 8  # the voice from frame 205, once its chunk is in
    seen, _audio = await watched(quiet + speech(1.0) + noise(1.0, 20), Barge("speech", broken))
    assert len(seen) == 1 and 30 + 10 <= seen[0] <= 40 + 26 + 8  # a failed answer: as by loudness, once it is in


async def test_a_run_that_goes_on_is_judged_by_every_chunk_it_lasts() -> None:
    # noise that goes on (a tap, dishes in a row) and the caller's voice right in it, with no quiet frame between: one
    # loud run; its first chunks are noise, its later ones the voice (from frame 110): counted once that chunk is in
    quiet = noise(0.3, 20)
    seen, _audio = await watched(quiet + noise(0.8, 3000) + tone(1.0) + noise(1.0, 20), Barge("speech", detector))
    assert len(seen) == 1 and 110 <= seen[0] <= 110 + 2 * 26 + 8
    seen, _audio = await watched(quiet + noise(1.8, 3000) + noise(1.0, 20), Barge("speech", detector))
    assert seen == []  # the noise alone, however long: no going on


async def test_a_run_inside_the_window_is_judged_after_it_and_an_unanswered_one_counts() -> None:
    async def hang(chunk: bytes) -> float | str:
        await asyncio.Event().wait()
        return 0.0

    quiet = noise(0.3, 20)
    # the voice starts on frame 30, the window ends on frame 40: its chunks are still judged after the window
    seen, _audio = await watched(quiet + speech(1.0) + noise(1.0, 20), Barge("speech", detector), frames=40)
    assert len(seen) == 1 and seen[0] <= 40 + 26 + 8
    seen, _audio = await watched(quiet + BANG + noise(1.0, 20), Barge("speech", detector), frames=40)
    assert seen == []
    # no answer within VAD_MAX_AGE_FRAMES of its chunk: the run counts, as by loudness
    seen, audio = await watched(quiet + BANG + noise(2.0, 20), Barge("speech", hang))
    assert len(seen) == 1 and seen[0] <= 30 + 10 + 2 * 26 + call_audio.VAD_MAX_AGE_FRAMES + 2
    audio.barge.close()
    # she answers while a run is still being judged: it counts, as by loudness
    seen = []
    _tunnel, audio, pump = await call(quiet + BANG + noise(2.0, 20), Barge("speech", hang))
    audio.watch(400, 450.0, lambda: seen.append(audio.frames_in))
    while audio.frames_in < 50:
        await asyncio.sleep(0)
    await audio.say(tone(0.1), barge=True)
    assert len(seen) == 1 and 50 <= seen[0] <= 60
    pump.cancel()
    audio.barge.close()


async def test_a_watch_that_raises_never_stops_the_audio() -> None:
    def broken() -> None:
        raise RuntimeError("calls.py broke")

    caller = noise(0.3, 20) + speech(1.0) + noise(1.0, 20)
    _tunnel, audio, pump = await call(caller)
    await asyncio.wait_for(audio.closed.wait(), 10)
    unwatched = audio.frames_in
    pump.cancel()
    _tunnel, audio, pump = await call(caller)
    audio.watch(400, 450.0, broken)
    await asyncio.wait_for(audio.closed.wait(), 10)
    assert audio.frames_in == audio.incoming.qsize() == unwatched  # every frame went in, as without the watch
    pump.cancel()


def turn_end(kind: str, quiet_s: float, noise_frames: int = 0) -> _End:
    """A listen's end as TurnEnd keeps it, with the full pause at 2.3 s."""
    return _End(
        kind, quiet_s, quiet_s if kind != "speech" else 2.3, None, round(max(0.0, 2.3 - quiet_s), 2), noise_frames
    )


def test_record_edits(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    turn = TurnEnd()
    turn.ends.append(turn_end("sure", 0.62))
    turn.noise()  # no words in it: no turn
    turn.noise()
    assert turn.ends[-1].noise
    turn.ends.append(turn_end("probably", 1.5))
    turn.held("goes on")  # the full pause is waited out after all: nothing saved
    turn.held("ends the call")
    assert (turn.ends[-1].held, turn.ends[-1].saved_s) == ("goes on", 0.0)
    turn.went_on()  # the watch heard the caller during Whisper
    assert turn.ends[-1].cut_off
    turn.ends.append(turn_end("full", 2.3))  # the hold's listen heard the caller go on
    turn.joined()
    turn.joined()
    held, hold = turn.ends[-2:]
    assert held.caught and not held.cut_off  # caught by the hold, not cut off
    assert not hold.caught and hold.held is None
    turn.went_on()  # a full pause: nothing to go on after
    assert not hold.cut_off
    turn.barged_early(0.8)
    turn.barged_early(0.9)
    assert hold.barged_early
    turn.ends.append(turn_end("sure", 0.61))
    turn.went_on()
    turn.went_on()
    turn.barged_early(0.4)
    turn.ends.append(turn_end("full", 2.3))
    turn.joined()  # nothing held just before: nothing joined
    assert not any(end.caught for end in turn.ends[-2:])
    assert [line for line in caplog.messages if "calls:" in line] == [
        "calls: the caller went on after an early end (probably)",
        "calls: taken over 0.8 s into her answer, after a full pause",
        "calls: the caller went on after an early end (sure)",
        "calls: taken over 0.4 s into her answer, after an early end (sure)",
    ]


async def _never(window: bytes) -> float | str:
    return 0.0


def test_summary_lines_exact_and_numbers_only() -> None:
    turn = TurnEnd(_never, _never)
    turn.full_s = 2.3
    ends = [
        turn_end("sure", 0.62),
        turn_end("sure", 0.65),  # cut off
        turn_end("full", 2.3),
        turn_end("probably", 1.5),  # held, the caller went on: joined into the next
        turn_end("full", 2.3),
        turn_end("speech", 1.1, noise_frames=37),
        turn_end("sure", 0.3),  # no words: no turn
        turn_end("sure", 0.61),  # held: ends the call
        turn_end("full", 2.3),  # taken over in her first 1.5 s
        turn_end("sure", 0.62),  # taken over in her first 1.5 s
    ]
    for index, end in enumerate(ends):
        turn.ends.append(end)
        if index == 1:
            turn.went_on()
        elif index == 3:
            turn.held("goes on")
        elif index == 4:
            turn.joined()
        elif index == 6:
            turn.noise()
        elif index == 7:
            turn.held("ends the call")
        elif index in (8, 9):
            turn.barged_early(1.2)
    turn.asks.update({"sure": 5, "probably": 1, "not": 2, "dropped": 1})
    turn.answer_ms = [4, 3, 18, 5, 4, 2, 6, 4]
    turn.judge_failed.update(late=1, timeout=1)
    turn.chunks, turn.slowest = 412, 0.007
    assert turn.summary() == (
        "calls: early end on (short pause 0.60 s, full 2.30 s): 8 turns, 4 ended early (sure 4, probably 0); saved"
        " 6.2 s, median 0.60 s a turn; went on 2 (sure 1, probably 1, speech 0), caught by the hold 1, cut off 1 (sure"
        " 1, probably 0, speech 0); held 2 (goes on 1, ends the call 1); taken over in her first 1.5 s: 1 after an"
        " early end, 1 after a full pause; 11 asks (sure 5, probably 1, not 2, dropped 1), answer median 4 ms,"
        " slowest 18 ms, 2 failed (late 1, timeout 1); saved per turn 1.68 1.65 0.00 0.00 1.20 0.00 0.00 1.68",
        "calls: pause by speech: 8 turns, 1 ended sooner than by loudness (1.2 s in all, median 1.20 s), 37 loud"
        " frames counted as pause; detector 412 chunks, slowest 7 ms, 0 failed",
    )


def test_summary_heads_name_the_settings_and_why_a_part_did_not_decide() -> None:
    off = TurnEnd(None, _never, early_end=False)
    assert off.summary() == (
        "calls: early end off: 0 turns, 0 ended early (sure 0, probably 0); saved 0.0 s, median 0.00 s a turn; went"
        " on 0 (sure 0, probably 0, speech 0), caught by the hold 0, cut off 0 (sure 0, probably 0, speech 0); held 0"
        " (goes on 0, ends the call 0); taken over in her first 1.5 s: 0 after an early end, 0 after a full pause",
        "calls: pause by speech: 0 turns, 0 ended sooner than by loudness (0.0 s in all, median 0.00 s), 0 loud"
        " frames counted as pause; detector 0 chunks, slowest 0 ms, 0 failed",
    )
    waiting = TurnEnd(None, None, early_end=True, pause="speech")
    waiting.ends.append(turn_end("full", 2.3))
    first, second = waiting.summary()
    assert first.startswith("calls: early end on (no turn detector: the full pause decided): 1 turn, 0 ended early")
    assert first.endswith("after a full pause; saved per turn 0.00")
    assert second == "calls: pause by speech (loudness: no speech detector): 1 turn"
    assert TurnEnd(_never).summary()[1] == "calls: pause by loudness: 0 turns"
    gave_up = TurnEnd(_never, _never)
    gave_up.full_s = 2.3
    gave_up._judge = gave_up._detect = None
    gave_up.judge_gave_up = gave_up.detect_gave_up = True
    gave_up.chunk_failed.update(http=3)
    first, second = gave_up.summary()
    assert first.startswith("calls: early end on (short pause 0.60 s, full 2.30 s; the full pause after 3 failed")
    assert " answers in a row): 0 turns," in first
    assert second == (
        "calls: pause by speech (loudness after 3 failed answers in a row): 0 turns, 0 ended sooner than by loudness"
        " (0.0 s in all, median 0.00 s), 0 loud frames counted as pause; detector 0 chunks, slowest 0 ms, 3 failed"
        " (http 3)"
    )


async def test_after_a_barge_in_the_listen_never_uses_the_turn_rules() -> None:
    # what the caller says over her ends by its own short pause: no ask, no chunk, no end kept, today's clip and info
    make, kwargs, length, digest, expected = GOLDEN_LISTENS["after_barge_in"]
    judge, detector = FakeJudge((0.95, None)), FakeDetector(voice_then({}))
    turn = TurnEnd(judge, detector)
    audio = await listening_to(make())
    clip, info = await audio.listen(**kwargs, turn=turn)  # type: ignore[arg-type]
    audio.close()
    assert (len(clip), hashlib.sha256(clip).hexdigest()) == (length, digest) and info == {"speech": True, **expected}
    assert judge.windows == [] and detector.chunks == [] and turn.ends == []
