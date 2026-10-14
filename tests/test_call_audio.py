import array
import asyncio
import io
import itertools
import logging
import math
import random
import re
import socket
import wave
from collections.abc import Awaitable, Callable

import pytest

from local_assistant import call_audio
from local_assistant.call_audio import (
    FRAME_BYTES,
    RATE,
    Barge,
    CallAudio,
    Chunks,
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
    assert barge.summary() == (
        "calls: barge-in speech: speech stopped her 1 time, would have 0; loudness stopped her 0 times, would have 4;"
        " her own voice 1; detector 0 chunks, slowest 0 ms, 0 failed"
    )
    assert Barge("loudness", detector).summary() == (
        "calls: barge-in loudness: loudness stopped her 0 times, would have 0; speech stopped her 0 times, would have 0;"
        " her own voice 0; detector 0 chunks, slowest 0 ms, 0 failed"
    )
    assert Barge("speech", None).summary() == (
        "calls: barge-in speech (loudness: no speech detector): loudness stopped her 0 times, would have 0;"
        " her own voice 0"
    )
    assert Barge("loudness", None).summary() == (
        "calls: barge-in loudness (no speech detector): loudness stopped her 0 times, would have 0; her own voice 0"
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
        " her own voice 0; detector 4 chunks, slowest 350 ms, 2 failed (http 1, timeout 1)"
    )
    pump.cancel()


async def test_a_fall_back_says_why_in_the_end_line() -> None:
    barge = Barge("speech", broken, clock=lambda: 0.0)  # its slowest answer by a fixed clock, never wall time
    _tunnel, audio, pump = await call(noise(0.5, 30) + noise(8.0, 3000), barge)
    assert (await audio.say(tone(6.0), barge=True)).barged  # loudness, after the third failed chunk
    assert barge.summary() == (
        "calls: barge-in speech (loudness after 3 failed answers in a row): speech stopped her 0 times, would have 0;"
        " loudness stopped her 1 time, would have 0; her own voice 0; detector 3 chunks, slowest 0 ms, 3 failed"
        " (http 3)"
    )
    pump.cancel()
