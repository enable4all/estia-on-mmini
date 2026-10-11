import array
import asyncio
import io
import math
import random
import socket
import wave

import pytest

from local_assistant import call_audio
from local_assistant.call_audio import (
    FRAME_BYTES,
    RATE,
    CallAudio,
    faint_noise,
    pcm48_to_wav16k,
    rms,
    silence,
    wav_to_pcm48,
)

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

    def __init__(self, caller: bytes) -> None:
        self.caller = caller
        self.heard: list[bytes] = []  # her frames, in order
        self.ours, self.theirs = socket.socketpair()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> CallAudio:
        reader, writer = await asyncio.open_unix_connection(sock=self.ours)
        audio = CallAudio(reader, writer)
        self.task = asyncio.create_task(self._run())
        return audio

    async def _run(self) -> None:
        reader, writer = await asyncio.open_unix_connection(sock=self.theirs)
        position = 0
        while position < len(self.caller):
            writer.write(self.caller[position : position + FRAME_BYTES].ljust(FRAME_BYTES, b"\0"))
            await writer.drain()
            position += FRAME_BYTES
            self.heard.append(await reader.readexactly(FRAME_BYTES))
        writer.close()

    def spoke_s(self) -> float:
        return sum(1 for frame in self.heard if rms(frame) > 300) / FRAMES_PER_S


async def call(caller: bytes) -> tuple[FakeTunnel, CallAudio, asyncio.Task[None]]:
    tunnel = FakeTunnel(caller)
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
