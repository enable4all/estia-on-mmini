import array
import asyncio
import io
import itertools
import logging
import math
import random
import re
import shutil
import tempfile
import time
import wave
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.call_audio import FRAME_BYTES, RATE, rms
from local_assistant.calls import (
    CallManager,
    CallSettings,
    Turn,
    ends_with_goodbye,
    is_own_voice,
    is_wrap_up,
    played_part,
    wants_hang_up,
)
from local_assistant.config import DEFAULT_NAME
from local_assistant.voice import Transcript, VoiceUnavailableError
from tests.conftest import ME, STRANGER
from tests.fakes.fake_voice import speech_probability
from tests.test_call_audio import typing

CALL = -7  # signal-cli's call ids are signed
NAME = DEFAULT_NAME  # her name in these calls
GREETING = prompts.call_greetings(NAME)[0]  # the one known greeting the harness gives her


def tone(seconds: float, level: int = 3000, hz: float = 220.0) -> bytes:
    n = int(RATE * seconds)
    return array.array("h", (int(level * math.sin(2 * math.pi * hz * i / RATE)) for i in range(n))).tobytes()


def noise(seconds: float, level: int = 20) -> bytes:
    rnd = random.Random(3)
    return array.array("h", (rnd.randint(-level, level) for _ in range(int(RATE * seconds)))).tobytes()


def speech(seconds: float, level: int = 3000) -> bytes:
    out = bytearray()
    syllable = 0
    while len(out) < 2 * RATE * seconds:
        out += tone(0.15, level, hz=200.0 + 37 * syllable) + noise(0.05)
        syllable += 1
    return bytes(out[: int(2 * RATE * seconds)])


def wav24(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(array.array("h", (int(3000 * math.sin(i / 7)) for i in range(int(24_000 * seconds)))).tobytes())
    return buf.getvalue()


class FakeVoice:
    def __init__(self, transcripts: list[str], ready: bool = True) -> None:
        self.transcripts = transcripts
        self.ready = ready
        self.detector: str | None = None  # the speech detector's state in the health; None: an older service
        self.chunks: list[tuple[str, int]] = []  # (stream, bytes) of each chunk the detector was asked about
        self.spoken: list[str] = []
        self.languages: list[str | None] = []  # the language of each spoken text
        self.heard_in: list[str | None] = []  # the language of each transcription
        self.heard = 0
        self.fail_transcribe = False
        self.broken: set[str] = set()

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> Transcript:
        self.heard += 1
        self.heard_in.append(language)
        if self.fail_transcribe:
            raise VoiceUnavailableError("down")
        return Transcript(self.transcripts.pop(0) if self.transcripts else "", 1.0)

    async def speak(self, text: str, *, language: str | None = None) -> bytes:
        self.spoken.append(text)
        self.languages.append(language)
        await asyncio.sleep(0)  # a request to the voice service: other tasks (the answer) run meanwhile
        if text in self.broken:
            return b"not a wav at all"
        return wav24(min(0.03 * len(text), 8.0))

    async def health(self) -> dict[str, str] | None:
        health = {"stt": "ready", "tts": "ready"} if self.ready else {"stt": "loading", "tts": "loading"}
        return health | ({"vad": self.detector} if self.detector is not None else {})

    async def vad(self, chunk: bytes, *, stream: str) -> float | str:
        self.chunks.append((stream, len(chunk)))
        return speech_probability(chunk) if self.detector == "ready" else "http"


class FakeHost:
    def __init__(self, turns: list[Turn], delay: float = 0.0, use_tools: bool = False) -> None:
        self.answers = turns
        self.delay = delay
        self.use_tools = use_tools
        self.turns: list[str] = []
        self.notes: list[str] = []
        self.summaries: list[tuple[list[str], int]] = []
        self.interruptions: list[tuple[int, str]] = []
        self.started: list[str] = []
        self.started_practice: list[bool] = []  # per call: whether it started as a practice call
        self.ended = 0
        self.dropped_waiting = 0
        self.cancelled_turns = 0
        self.gate: asyncio.Event | None = None  # when set, an answer also waits for it
        self.practice_ended: list[str] = []
        self.practice_turns: list[bool] = []  # per turn: whether the call said it is a practice call

    def call_started(self, aci: str, *, practice: bool = False) -> None:
        self.started.append(aci)
        self.started_practice.append(practice)

    def call_ended(self) -> None:
        self.ended += 1

    async def call_turn(self, aci: str, text: str, on_tools: Callable[[], None], *, practice: bool = False) -> Turn:
        self.turns.append(text)
        self.practice_turns.append(practice)
        if self.use_tools:
            on_tools()
        try:
            await asyncio.sleep(self.delay)
            if self.gate is not None:
                await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled_turns += 1
            raise
        return self.answers.pop(0) if self.answers else Turn("Okay.")

    async def call_drop_waiting(self, aci: str) -> None:
        self.dropped_waiting += 1

    def call_interrupted(self, reply_id: int, heard: str) -> None:
        self.interruptions.append((reply_id, heard))

    async def call_note(self, aci: str, text: str) -> None:
        self.notes.append(text)

    async def call_summary(self, aci: str, lines: list[str], minutes: int) -> None:
        self.summaries.append((lines, minutes))

    async def practice_call_ended(self, aci: str) -> None:
        self.practice_ended.append(aci)
        self.notes.append("(practice ended)")  # in order with the notes


class FakeHelper:
    """The call helper's socket in lock step, faster than real time: one caller frame out, one of her frames
    back (timers that count call time run fast; the idle tests rely on it). The caller is quiet until a test queues
    speech."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.queued = bytearray()
        self.heard: list[bytes] = []
        self.server: asyncio.Server | None = None
        self.stop = False
        self.triggers: list[tuple[Callable[[], bool], bytes]] = []

    async def start(self) -> None:
        self.server = await asyncio.start_unix_server(self._serve, path=str(self.path))

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        quiet = noise(0.01)
        try:
            while not self.stop:
                for trigger in list(self.triggers):
                    if trigger[0]():
                        self.triggers.remove(trigger)
                        self.queued += trigger[1]
                frame = bytes(self.queued[:FRAME_BYTES]) if self.queued else quiet
                del self.queued[:FRAME_BYTES]
                writer.write(frame.ljust(FRAME_BYTES, b"\0"))
                await writer.drain()
                self.heard.append(await reader.readexactly(FRAME_BYTES))
                await asyncio.sleep(0)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        writer.close()

    def caller_says(self, pcm: bytes) -> None:
        self.queued += pcm

    def caller_says_when(self, condition: Callable[[], bool], pcm: bytes) -> None:
        """Frame-exact: the caller starts `pcm` at the first frame where `condition` holds."""
        self.triggers.append((condition, pcm))

    def caller_says_after(self, condition: Callable[[], bool], frames: int, pcm: bytes) -> None:
        """Frame-exact: the caller starts `pcm` `frames` frames after the first frame where `condition` holds."""
        mark: list[int] = []

        def due() -> bool:
            if not mark and condition():
                mark.append(len(self.heard))
            return bool(mark) and len(self.heard) >= mark[0] + frames

        self.triggers.append((due, pcm))

    def her_frames(self) -> int:
        return sum(1 for frame in self.heard if rms(frame) > 300)


OUT = 77  # the id signal-cli gives a call she places


class FakeSignal:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.accepted: list[tuple[str, int]] = []
        self.hung_up: list[tuple[str, int]] = []
        self.started: list[str] = []
        self.start_error: Exception | None = None
        self.helper: FakeHelper | None = None

    async def start_call(self, aci: str) -> dict[str, Any]:
        """As signal-cli 0.14.9 does: the ringing event comes before startCall's reply (the call id is not known yet
        when it is read)."""
        self.started.append(aci)
        if self.start_error is not None:
            raise self.start_error
        self.events.put_nowait({"callId": OUT, "state": "RINGING_OUTGOING", "uuid": aci, "isOutgoing": True})
        await asyncio.sleep(0.05)
        return {"callId": OUT, "state": "RINGING_OUTGOING"}

    async def pick_up(self, call_id: int = OUT) -> None:
        """The user answers the call she placed."""
        path = self.folder / f"signal-call-{call_id & 0xFFFFFFFFFFFFFFFF}.sock"
        self.helper = FakeHelper(path)
        await self.helper.start()
        self.events.put_nowait({"callId": call_id, "state": "CONNECTING", "isOutgoing": True})
        self.events.put_nowait(
            {"callId": call_id, "state": "CONNECTED", "isOutgoing": True, "inputDeviceName": f"unix:{path}"}
        )

    def end_ringing(self, reason: str, call_id: int | None = OUT) -> None:
        event: dict[str, Any] = {"state": "ENDED", "reason": reason}
        if call_id is not None:
            event |= {"callId": call_id, "isOutgoing": True}
        self.events.put_nowait(event)

    async def call_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self.events.get()

    async def accept_call(self, aci: str, call_id: int) -> dict[str, Any]:
        self.accepted.append((aci, call_id))
        path = self.folder / f"signal-call-{call_id & 0xFFFFFFFFFFFFFFFF}.sock"
        self.helper = FakeHelper(path)
        await self.helper.start()
        self.events.put_nowait({"callId": call_id, "state": "CONNECTED", "inputDeviceName": f"unix:{path}"})
        return {"callId": call_id, "state": "CONNECTING", "inputDeviceName": f"unix:{path}"}

    async def hangup_call(self, aci: str, call_id: int) -> None:
        self.hung_up.append((aci, call_id))
        self.events.put_nowait({"callId": call_id, "state": "ENDED", "reason": "local_hangup"})
        if self.helper:
            self.helper.stop = True

    def ring(self, aci: str = ME, call_id: int = CALL) -> None:
        self.events.put_nowait({"callId": call_id, "state": "RINGING_INCOMING", "uuid": aci, "isOutgoing": False})

    def remote_hangup(self, call_id: int = CALL) -> None:
        self.events.put_nowait({"callId": call_id, "state": "ENDED", "reason": "remote_hangup"})
        if self.helper:
            self.helper.stop = True


class Harness:
    def __init__(
        self,
        transcripts: list[str] | None = None,
        turns: list[Turn] | None = None,
        *,
        delay: float = 0.0,
        use_tools: bool = False,
        ready: bool = True,
        language: str = "en",
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        name: str = NAME,
        practice: Callable[[str], str | None] | None = None,
        locked: Callable[[], bool] = lambda: False,
        missed: Callable[[str, bool], None] | None = None,
        reached: Callable[[str], None] | None = None,
        **settings: Any,
    ) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="c", dir="/tmp"))  # short: socket paths are at most 104 bytes
        self.signal = FakeSignal(self.folder)
        self.voice = FakeVoice(transcripts or [], ready)
        self.host = FakeHost(turns or [], delay, use_tools)
        # idle timers and the call's length cap get long defaults here, except in the tests about them: call time
        # is counted in frames, and the lock-step helper exchanges frames as fast as it can while Gemma "thinks"
        # (an hour of call time can pass in a few real seconds on a fast machine)
        options: dict[str, Any] = {
            "end_of_turn_s": 0.6,
            "thinking_after_s": 0.5,
            "idle_prompt_s": 3000.0,
            "idle_hangup_s": 6000.0,
            "max_s": 1_000_000.0,
            "answer_after_s": 0.0,  # answered at once, except in the test about ringing
            "fillers": True,  # the tests about thinking lines; off in the real default
            "greetings": (GREETING,),  # one known greeting (the real default varies)
            "acknowledge": False,  # the tests about the short "okay" turn it on; on in the real default
        }
        options.update(settings)
        self.manager = CallManager(
            self.signal,
            self.voice,
            self.host,
            frozenset({ME}),
            CallSettings(socket_dir=self.folder, **options),
            language=language,
            rng=rng,
            clock=clock,
            name=name,
            practice=practice or (lambda aci: None),
            locked=locked,
            missed=missed or (lambda aci, promised: None),
            reached=reached or (lambda aci: None),
        )
        spoken_in = (practice(ME) if practice is not None else None) or language
        self.greeting = GREETING if spoken_in == "en" else prompts.spoken(spoken_in, name=name).greeting
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Harness":
        self.task = asyncio.create_task(self.manager.run_forever())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.task:
            self.task.cancel()
        shutil.rmtree(self.folder, ignore_errors=True)

    @property
    def helper(self) -> FakeHelper:
        assert self.signal.helper is not None
        return self.signal.helper

    async def until(self, condition: Callable[[], bool], limit: float = 20.0) -> None:
        async with asyncio.timeout(limit):
            while not condition():
                await asyncio.sleep(0.01)

    async def connected(self) -> None:
        self.signal.ring()
        await self.until(lambda: self.signal.helper is not None and len(self.helper.heard) > 0)
        await self.until(lambda: self.greeting in self.voice.spoken)  # synthesised (maybe still playing)

    async def quiet_after(self, text: str) -> None:
        """Wait until she finished saying `text` (a whole line or one sentence of an answer)."""
        await self.until(lambda: text in self.manager.said)


async def test_the_owners_call_is_answered_greeted_and_a_turn_is_spoken() -> None:
    async with Harness(["what's on tomorrow"], [Turn("Two meetings tomorrow.", reply_id=7)]) as h:
        await h.connected()
        assert h.signal.accepted == [(ME, CALL)] and h.host.started == [ME]
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Two meetings tomorrow.")
        assert h.host.turns == ["what's on tomorrow"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        assert h.host.summaries == []  # one turn: nothing to summarise
        assert not h.manager.in_call


async def test_after_a_longer_call_the_user_gets_a_summary() -> None:
    async with Harness(["first idea", "second idea"], [Turn("Nice."), Turn("Good one.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Nice.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Good one.")
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.summaries))
        lines, minutes = h.host.summaries[0]
        assert lines == ["User: first idea", "Estia: Nice.", "User: second idea", "Estia: Good one."]
        assert minutes >= 1


async def test_a_strangers_call_is_never_answered() -> None:
    async with Harness() as h:
        h.signal.ring(STRANGER)
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [] and h.signal.hung_up == [] and h.host.started == []


async def test_while_locked_the_owners_call_is_ignored_and_none_is_placed() -> None:
    """/lock: a call from the allowed account rings out (ignored, never rejected: a reject is a reply), and no reminder
    call is placed; after /unlock calls work again."""
    locked = [True]
    async with Harness(locked=lambda: locked[0]) as h:
        h.signal.ring()
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [] and h.signal.hung_up == [] and h.host.started == [] and h.host.notes == []
        assert await h.manager.can_place(ME) == "locked"
        assert await h.manager.place(ME, wake=True, time="7:00", text="x") == "call_failed"
        assert h.signal.started == []
        locked[0] = False
        assert await h.manager.can_place(ME) == ""
        await h.connected()


async def test_a_second_call_during_a_call_is_ignored() -> None:
    async with Harness() as h:
        await h.connected()
        h.signal.ring(ME, call_id=99)
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [(ME, CALL)]


async def test_without_the_voice_service_the_call_rings_out_with_one_note() -> None:
    async with Harness(ready=False) as h:
        h.signal.ring()
        h.signal.ring(call_id=8)
        await h.until(lambda: bool(h.host.notes))
        await asyncio.sleep(0.2)
        assert h.signal.accepted == [] and h.host.notes == [prompts.CALL_NOT_READY]  # once, not per ring


def test_the_not_ready_note_says_why_and_that_she_texts_when_ready() -> None:
    assert prompts.CALL_NOT_READY == (
        "I can't take calls yet: my voice is still loading (up to 15 minutes after an install, a move or a macOS "
        "update). I'll text you when it's ready."
    )


async def test_every_ring_while_the_voice_is_not_ready_is_a_missed_call() -> None:
    """The voice-ready note keeps the promise: each ring while not ready counts, also one whose note is held back;
    only a ring that got the note (its "I'll text you") is a promise."""
    missed: list[tuple[str, bool]] = []
    async with Harness(ready=False, missed=lambda aci, promised: missed.append((aci, promised))) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.host.notes))
        h.signal.ring(call_id=8)
        await h.until(lambda: len(missed) == 2)
    assert missed == [(ME, True), (ME, False)]


async def test_a_ring_that_is_answered_or_ignored_is_no_missed_call() -> None:
    missed: list[tuple[str, bool]] = []
    async with Harness(missed=lambda aci, promised: missed.append((aci, promised))) as h:
        await h.connected()
    async with Harness(
        ready=False, missed=lambda aci, promised: missed.append((aci, promised)), locked=lambda: True
    ) as h:
        h.signal.ring()
        await asyncio.sleep(0.2)
    assert missed == []


async def test_a_call_counts_as_reached_only_once_connected() -> None:
    """The voice-ready note is dropped only for a caller who heard her: never for one who hung up while it rang."""
    reached: list[str] = []
    async with Harness(answer_after_s=0.4, reached=reached.append) as h:
        h.signal.ring()
        await asyncio.sleep(0.1)
        h.signal.remote_hangup()
        await asyncio.sleep(0.6)
        assert reached == [] and h.host.started == [ME]
    async with Harness(reached=reached.append) as h:
        await h.connected()
        assert reached == [ME]


async def test_a_placed_call_counts_as_reached_only_when_picked_up() -> None:
    reached: list[str] = []
    async with Harness(reached=reached.append) as h:
        task = await placing(h)
        await asyncio.sleep(0.2)
        assert reached == []
        await h.signal.pick_up()
        assert await task == "answered"
        assert reached == [ME]


async def test_every_ring_while_the_voice_is_not_ready_leaves_a_log_line(caplog: pytest.LogCaptureFixture) -> None:
    """The note is held back for 10 minutes; the log is not (live 13 Oct: a second call left no trace)."""
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(ready=False) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.host.notes))
        h.signal.ring(call_id=8)
        await asyncio.sleep(0.2)
    lines = [record.getMessage() for record in caplog.records if "not ready" in record.getMessage()]
    assert len(lines) == 2, lines
    assert "note sent" in lines[0] and "note held back" in lines[1]


async def test_a_sound_without_words_gets_no_sorry_he_just_listens() -> None:
    # live test 2026-10-10: "I didn't get that" over and over while the user was simply quiet
    async with Harness(["-", "what time is it"], [Turn("Ten past four.")]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, speech(0.5))  # at once
        h.helper.caller_says_when(lambda: h.voice.heard >= 1, speech(0.6))
        await h.quiet_after("Ten past four.")
        assert prompts.CALL_UNHEARD not in h.manager.said and len(h.host.turns) == 1


async def test_a_lone_stop_makes_him_quiet_without_gemma() -> None:
    async with Harness(["Stop.", "go on then"], [Turn("Sure.")]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, speech(0.5))  # at once
        await h.until(lambda: h.voice.heard == 1)
        await asyncio.sleep(0.2)
        assert h.host.turns == [] and h.manager.said == [GREETING]
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Sure.")
        assert h.host.turns == ["go on then"]


async def test_silence_gets_a_gentle_prompt_then_a_goodbye() -> None:
    async with Harness(idle_prompt_s=0.5, idle_hangup_s=1.0) as h:
        await h.connected()
        await h.quiet_after(prompts.CALL_IDLE)
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_IDLE_BYE in h.voice.spoken


async def test_silence_logs_the_lines_levels_to_tune_listening(caplog: pytest.LogCaptureFixture) -> None:
    # review 2026-10-11: a quiet caller who never starts an utterance left no trace to tune the start level with
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(idle_prompt_s=0.5, idle_hangup_s=1.0) as h:
        await h.connected()
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
    lines = [record.getMessage() for record in caplog.records]
    assert any(re.search(r"no speech .*floor \d+, peak \d+.*checking in", line) for line in lines)
    assert any(re.search(r"no speech .*floor \d+, peak \d+.*goodbye", line) for line in lines)


async def test_the_call_length_cap_says_goodbye() -> None:
    async with Harness(max_s=0.5) as h:
        await h.connected()
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_MAX_BYE in h.voice.spoken


async def test_talking_over_her_interrupts_and_becomes_the_next_turn() -> None:
    long_answer = "This is a long answer. " * 8
    async with Harness(
        ["tell me more", "wait, what about tomorrow"], [Turn(long_answer, reply_id=5), Turn("Okay.")]
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(1.2))  # 1.5 s in
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} wait, what about tomorrow"]
        [(reply_id, heard)] = h.host.interruptions
        assert reply_id == 5 and heard.startswith("This is a long answer.") and len(heard) < len(long_answer.strip())


async def test_a_cough_only_pauses_her() -> None:
    answer = "This is a long answer. " * 6
    async with Harness(["tell me more", ""], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, noise(0.4, 6000))  # a cough
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to the cough
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 6)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]
        assert h.helper.her_frames() - greeting > 6 * 60  # all six sentences went out


@pytest.mark.parametrize("heard", ["*sad music*", "Thank you."])
async def test_noise_heard_as_words_over_her_answer_only_pauses_her(heard: str) -> None:
    # review 2026-10-11: typing over her answer stopped her, and Gemma answered "(cutting in) *sad music*"
    answer = "This is a long answer. " * 6
    async with Harness(["tell me more", heard], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, noise(0.4, 6000))
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to the noise
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 6)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]


async def test_long_typing_over_her_answer_heard_as_thank_you_only_pauses_her() -> None:
    # review 2026-10-11: every loud key click counted as voice, so 1.5 s of typing over her answer that Whisper heard
    # as "Thank you." stopped her, and Gemma got "(cutting in) Thank you."
    answer = "This is a long answer. " * 8
    async with Harness(["tell me more", "Thank you."], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, typing(2.0))
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to the typing
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 8)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]


async def test_a_spoken_thank_you_over_her_answer_still_stops_her() -> None:
    answer = "This is a long answer. " * 8
    async with Harness(["tell me more", "Thank you."], [Turn(answer, reply_id=5), Turn("You're welcome.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, speech(1.0))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("You're welcome.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} Thank you."]


async def test_typing_after_a_bump_heard_as_thank_you_never_reaches_gemma() -> None:
    # a bump starts an utterance, the typing after it keeps it going: its clicks are no voice
    async with Harness(["Thank you.", "what time is it"], [Turn("Ten past four.")]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, noise(0.15, 3000) + noise(0.1) + typing(2.0))
        h.helper.caller_says_when(lambda: h.voice.heard >= 1, speech(0.8))
        await h.quiet_after("Ten past four.")
        assert h.host.turns == ["what time is it"]


@pytest.mark.parametrize("said", ["*sad music*", "(keyboard clicking)", "[Music] [Applause]"])
def test_sound_labels_are_not_words(said: str) -> None:
    from local_assistant.calls import is_interruption, real_words

    assert real_words(said) == [] and not is_interruption(said, name=NAME)


async def test_a_slow_answer_gets_a_thinking_line_and_tools_a_checking_line() -> None:
    async with Harness(["give me an idea", "and my calendar?"], [Turn("Fine."), Turn("Free.")], delay=1.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Fine.")
        assert any(line in h.manager.said for line in prompts.CALL_THINKING_LINES)
        h.host.use_tools = True
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Free.")
        assert any(line in h.manager.said for line in prompts.CALL_CHECKING_LINES)


async def test_a_device_question_is_spoken_whole() -> None:
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    async with Harness(["switch the heater off", "yes"], [Turn(question, reply_id=3, asks_yes=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        last = "Say yes if you want me to switch it off now."
        h.helper.caller_says_when(lambda: h.manager.saying == last, speech(0.8))  # over the last sentence
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(last)
        await asyncio.sleep(0.5)  # (call time runs fast: plenty for anything to happen)
        assert h.host.interruptions == []  # the question went out whole
        assert h.voice.heard == 1  # a yes counts only once she has finished asking, never one said over it


async def test_a_short_yes_to_a_device_question_reaches_the_turn() -> None:
    # review 2026-10-11: a brisk "yes" has well under 0.3 s of voiced sound and was dropped before speech recognition
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    async with Harness(["switch the heater off", "Yes."], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        last = "Say yes if you want me to switch it off now."
        h.helper.caller_says_after(lambda: last in h.manager.said, 30, speech(0.25))  # one syllable, 0.3 s later
        await h.quiet_after("Done: heater off.")
        assert h.host.turns == ["switch the heater off", "Yes."]


async def test_a_yes_said_the_moment_she_stops_is_heard() -> None:
    # review 2026-10-11: the first 200 ms of listening only measured the line's floor and were thrown away
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    async with Harness(["switch the heater off", "Yes."], turns) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        last = "Say yes if you want me to switch it off now."
        h.helper.caller_says_when(lambda: last in h.manager.said, speech(0.25))  # at once, after her echo
        await h.quiet_after("Done: heater off.")
        assert h.host.turns == ["switch the heater off", "Yes."]


async def test_hearing_trouble_is_said_once_then_the_call_ends() -> None:
    async with Harness() as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.voice.fail_transcribe = True
        h.helper.caller_says(speech(0.6))
        await h.quiet_after(prompts.CALL_HEARING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_DROPPED in h.host.notes


async def test_a_signal_cli_restart_ends_the_call() -> None:
    async with Harness() as h:
        await h.connected()
        h.signal.events.put_nowait({"state": "ENDED", "reason": "signal_cli_restarted"})
        await h.until(lambda: h.host.ended == 1)
        assert not h.manager.in_call


async def test_status_line() -> None:
    async with Harness() as h:
        assert h.manager.status_line() == "Calls: ready"
        await h.connected()
        assert h.manager.status_line().startswith("Calls: in a call since ")
        h.signal.remote_hangup()
        await h.until(lambda: not h.manager.in_call)
        assert h.manager.status_line().startswith("Calls: ready (last call ")


@pytest.mark.parametrize("path", ["unix:/elsewhere/signal-call-1.sock", "unix:relative.sock"])
def test_a_socket_outside_the_private_folder_is_refused(path: str) -> None:
    from local_assistant.calls import socket_path

    folder = Path("/tmp/x")
    with pytest.raises(ValueError):
        socket_path({"inputDeviceName": path}, {}, folder, 1)
    assert socket_path({}, {}, folder, -1) == folder / "signal-call-18446744073709551615.sock"


async def test_speech_that_cannot_be_played_ends_the_call_instead_of_hanging() -> None:
    async with Harness(["tell me"], [Turn("First part. Second part.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.voice.broken = {"Second part."}  # the voice service sent something that is not a WAV
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.ended == 1, limit=10)
        assert prompts.CALL_DROPPED in h.host.notes and not h.manager.in_call


async def test_a_turn_that_takes_too_long_is_cancelled_and_its_device_question_dropped() -> None:
    async with Harness(["switch the heater off"], delay=30.0, turn_timeout_s=1.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(prompts.CALL_LOST.split(". ")[-1])  # spoken sentence by sentence
        assert h.host.cancelled_turns == 1 and h.host.dropped_waiting == 1


async def test_hanging_up_during_a_slow_answer_ends_the_call_at_once() -> None:
    async with Harness(["think hard"], delay=30.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.turns == ["think hard"])
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1, limit=3)
        assert h.host.cancelled_turns == 1 and h.host.dropped_waiting == 1


async def test_a_lone_stop_drops_a_waiting_device_question() -> None:
    async with Harness(["Wait."]) as h:
        await h.connected()
        h.helper.caller_says_when(lambda: GREETING in h.manager.said, speech(0.5))  # at once
        await h.until(lambda: h.host.dropped_waiting == 1)


async def test_a_device_question_cut_off_before_its_question_is_dropped() -> None:
    question = "This will take a moment to explain. " * 3 + "Say yes if you want me to switch it off now."
    async with Harness(["switch the heater off", "no wait"], [Turn(question, reply_id=3, asks_yes=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 60, speech(1.0))
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.dropped_waiting >= 1)
        assert h.host.interruptions  # she was cut off before asking


async def test_waiting_for_a_slow_tool_does_not_spin(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = 0
    real_wait = asyncio.wait

    async def counting_wait(*args: Any, **kwargs: Any) -> Any:
        nonlocal waits
        waits += 1
        return await real_wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", counting_wait)
    async with Harness(["and my calendar?"], [Turn("Free.")], delay=1.0, use_tools=True) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Free.")
        assert any(line in h.manager.said for line in prompts.CALL_CHECKING_LINES) and waits < 20


async def test_thinking_lines_vary_come_first_and_never_repeat_back_to_back() -> None:
    answers = [Turn(f"Answer {n}.") for n in range(5)]
    async with Harness([f"question {n}" for n in range(5)], answers, delay=1.0, thinking_after_s=0.3) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        for n in range(5):
            h.helper.caller_says(speech(0.6))
            await h.quiet_after(f"Answer {n}.")
        fillers = [line for line in h.manager.said if line in prompts.CALL_THINKING_LINES]
        assert len(fillers) == 5 and len(set(fillers)) >= 2
        assert all(a != b for a, b in itertools.pairwise(fillers))
        order = h.manager.said[h.manager.said.index("Answer 0.") - 1]
        assert order in prompts.CALL_THINKING_LINES  # the thinking line came first, then the answer


async def test_a_plain_goodbye_ends_the_call_without_gemma() -> None:
    async with Harness(["okay bye bye"]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns == []
        assert any(line in h.manager.said for line in prompts.CALL_BYE_LINES)


async def test_she_can_end_the_call_after_her_goodbye() -> None:
    async with Harness(["that's everything for now, thanks"], [Turn("Glad I could help. Bye!", end_call=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert "Bye!" in h.manager.said  # she said it before hanging up


async def test_her_end_call_alone_ends_the_call_after_her_answer() -> None:
    async with Harness(["I think I'll go to sleep now"], [Turn("Sleep well!", end_call=True)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert "Sleep well!" in h.manager.said and prompts.CALL_IDLE not in h.manager.said


async def test_a_goodbye_that_is_talked_over_does_not_end_the_call() -> None:
    long_bye = "It was really nice to talk about all of this with you today. " * 3 + "Bye!"
    async with Harness(
        ["thanks, that's all", "oh wait, one more thing"], [Turn(long_bye, end_call=True), Turn("Sure.")]
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 80, speech(1.0))
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Sure.")
        assert h.signal.hung_up == []


async def test_a_wrap_up_ends_the_call_after_his_answer() -> None:
    # live test 2026-10-10: "No, that will do for now." got a goodbye from Gemma but no end_call
    async with Harness(["No, that will do for now."], [Turn("Alright, enjoy your afternoon!")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert "Alright, enjoy your afternoon!" in h.manager.said
        assert prompts.CALL_IDLE not in h.manager.said  # hung up for the wrap-up, not after silence


async def test_a_wrap_up_answered_with_a_question_keeps_the_call() -> None:
    async with Harness(["that's all", "yes please"], [Turn("Shall I text you the list?"), Turn("Done.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Shall I text you the list?")
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Done.")
        assert h.signal.hung_up == []


async def test_a_wrap_up_answered_with_a_device_question_keeps_the_call() -> None:
    asked = Turn("Okay.", reply_id=4, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness(["that's all, heater off", "yes"], [asked, Turn("", code_text="Done: heater off.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Shall I switch heater off?")
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Done: heater off.")
        assert h.signal.hung_up == []


@pytest.mark.parametrize(
    ("said", "wraps_up"),
    [
        ("No, that will do for now.", True),
        ("that's all for today, thanks", True),
        ("Okay, that's it.", True),
        ("nothing else, thanks", True),
        ("I'm done", True),
        ("I'm good, thanks", False),  # also the answer to "how are you?"
        ("that's all I know about it, what do you think?", False),
        ("tell me more about that", False),
    ],
)
def test_what_counts_as_wrapping_up(said: str, wraps_up: bool) -> None:
    assert is_wrap_up(said) is wraps_up


@pytest.mark.parametrize(
    ("said", "ends"),
    [
        # live 2026-10-12: both got a goodbye from Gemma but no end_call, and the call stayed open
        ("Yeah, it's all good. Thank you. Talk to you later.", True),
        ("No, that's good. Okay, thank you. Bye-bye.", True),
        ("Great talk. See you soon, Tiffany.", True),
        # the same with commas (Whisper punctuates fast speech so), and the usual short closings
        ("Yeah, it's all good, thank you, talk to you later.", True),
        ("No, that's good, okay, thank you, bye-bye.", True),
        ("Yeah, bye.", True),
        ("Great, thanks. Take care.", True),
        ("Okay, I am off to bed. Good night.", True),
        ("Perfect, thanks, catch you later.", True),
        ("Okay, thanks. Speak to you soon.", True),
        ("Bye-bye. Wait, what's the weather tomorrow?", False),  # the goodbye is not the last thing said
        ("I'll see you.", False),
        ("I said bye to him. Then we left.", False),
        ("I said bye, then we left.", False),
        ("Take care of the plants, please.", False),
        ("okay bye bye", False),  # a plain goodbye alone: code says bye without Gemma (is_goodbye)
    ],
)
def test_a_turn_that_ends_with_a_goodbye(said: str, ends: bool) -> None:
    assert ends_with_goodbye(said, name="Tiffany") is ends


@pytest.mark.parametrize(
    "said",
    [
        "No, bye.",
        "Nope, bye-bye.",
        "No thanks, bye.",
        "Okay, thanks Tiffany, bye.",
        "Thank you very much, bye.",
        "Thanks a lot, bye bye.",
        "All right, bye.",
        "Take care.",
        "Good night, Tiffany.",
        "Okay, goodnight.",
        "See you tomorrow.",
        "Catch you later.",
    ],
)
def test_a_plain_goodbye_also_after_no_thanks_or_her_name(said: str) -> None:
    from local_assistant.calls import is_goodbye

    assert is_goodbye(said, name="Tiffany")


@pytest.mark.parametrize("said", ["Yes, bye.", "Yeah bye", "Have a good night's sleep.", "Take care of it."])
def test_a_yes_before_a_goodbye_still_goes_to_gemma(said: str) -> None:
    # a "yes" may answer a waiting device question: Gemma's turn handles it (then the call can end)
    from local_assistant.calls import is_goodbye

    assert not is_goodbye(said, name="Tiffany")


async def test_a_goodbye_after_other_words_ends_the_call_after_her_answer() -> None:
    said = "No, that's good. Okay, thank you. Bye-bye."
    async with Harness([said], [Turn("You're welcome. Have a good night! Bye for now.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert h.host.turns  # Gemma answered the rest
        assert "Bye for now." in h.manager.said  # and said it whole before hanging up
        assert prompts.CALL_IDLE not in h.manager.said  # hung up for the goodbye, not after silence


async def test_a_goodbye_after_other_words_answered_with_a_question_keeps_the_call() -> None:
    said = "Thanks, that helps. Talk to you later."
    async with Harness([said, "no, bye"], [Turn("Before you go, shall I text you the list?"), Turn("Okay.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after("Before you go, shall I text you the list?")
        assert h.signal.hung_up == []
        h.helper.caller_says(speech(0.6))  # "no, bye": a plain goodbye, so code says bye and hangs up
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_IDLE not in h.manager.said


async def test_it_rings_twice_before_he_answers() -> None:
    async with Harness(answer_after_s=0.4) as h:
        rang = time.monotonic()
        h.signal.ring()
        await h.until(lambda: h.signal.accepted != [])
        assert time.monotonic() - rang >= 0.4


async def test_a_caller_who_hangs_up_while_it_rings_is_not_answered() -> None:
    async with Harness(answer_after_s=0.4) as h:
        h.signal.ring()
        await asyncio.sleep(0.1)
        h.signal.remote_hangup()
        await asyncio.sleep(0.6)
        assert h.signal.accepted == [] and h.manager._call is None


async def test_no_thinking_lines_unless_they_are_switched_on() -> None:
    async with Harness(["and my calendar?"], [Turn("Free.")], delay=1.0, use_tools=True, fillers=False) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.quiet_after("Free.")
        fillers = prompts.CALL_THINKING_LINES + prompts.CALL_CHECKING_LINES
        assert not any(line in h.manager.said for line in fillers)


@pytest.mark.parametrize(
    "said",
    [
        "Shall we hang up? I think we're done for now. Thank you.",
        "you can hang up now",
        "let's hang up",
        "please hang up",
    ],
)
def test_asking_him_to_hang_up_ends_the_call(said: str) -> None:
    assert wants_hang_up(said)


@pytest.mark.parametrize("said", ["don't hang up yet", "how do I hang up a picture frame properly?"])
def test_hang_up_in_other_sentences_does_not(said: str) -> None:
    assert not wants_hang_up(said)


async def test_a_request_to_hang_up_ends_the_call_even_after_a_question() -> None:
    async with Harness(["Shall we hang up? I think we're done for now."], [Turn("Sure, anything else first?")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert prompts.CALL_IDLE not in h.manager.said  # hung up for the request, not after silence


@pytest.mark.parametrize("said", ["stop Tiffany", "Tiffany", "okay bye Tiffany", "goodbye tiffany"])
def test_her_name_counts_in_a_stop_or_a_goodbye(said: str) -> None:
    from local_assistant.calls import is_goodbye, is_interruption, is_lone_stop

    owner = "Tiffany"  # the owner's own Estia
    if "bye" in said.lower():
        assert is_goodbye(said, name=owner)
    elif said == "Tiffany":
        assert is_interruption(said, name=owner)
    else:
        assert is_lone_stop(said, name=owner)


@pytest.mark.parametrize(
    ("text", "voiced_s", "noise"),
    [
        ("*sad music*", 1.5, True),  # a sound label, never words
        ("[Music]", 1.0, True),
        ("(keyboard clicking)", 2.0, True),
        ("♪ ♪", 1.0, True),
        ("Thank you.", 0.3, True),  # Whisper's favourite word for noise, on a short sound
        ("thank", 0.4, True),
        ("So", 0.2, True),
        ("I", 0.3, True),
        ("Thanks for watching!", 2.0, True),  # never said on a call
        ("Subtitles by the Amara.org community", 2.0, True),
        ("Please, subscribe!", 1.0, True),  # punctuation inside a sign-off changes nothing
        ("Thank you, for watching!", 1.5, True),
        ("Thanks for watching the kids yesterday, it really helped.", 2.5, False),  # inside a real sentence
        ("Can you please subscribe me to the mailing list?", 2.0, False),
        ("Thank you.", 1.2, False),  # a real thank you is longer
        ("Yes.", 0.3, False),  # a short real answer counts (a device's yes)
        ("Stop.", 0.3, False),
        ("No, that's okay.", 0.8, False),
        ("What's on my calendar?", 1.5, False),
    ],
)
def test_noise_that_whisper_turns_into_words_is_silence(text: str, voiced_s: float, noise: bool) -> None:
    from local_assistant.calls import is_noise_transcript

    assert is_noise_transcript(text, voiced_s) is noise


async def test_the_owners_tiffany_greets_and_stops_by_her_name_on_a_dutch_call() -> None:
    turn = Turn("Een tuin op het balkon, leuk idee. Kruiden eerst, die zijn vergevingsgezind.", reply_id=3)
    async with Harness(["wat vind je van een balkontuin", "stop Tiffany"], [turn], language="nl", name="Tiffany") as h:
        await h.connected()
        assert h.voice.spoken[0] == "Hoi, met Tiffany. Wat kan ik voor je doen?"
        await h.quiet_after(h.greeting)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Een tuin op het balkon, leuk idee.")
        h.helper.caller_says(speech(0.6))  # "stop Tiffany": a lone stop by her name, Gemma is not asked
        await h.until(lambda: h.host.dropped_waiting == 1)
        assert h.host.turns == ["wat vind je van een balkontuin"]


async def test_without_greetings_set_english_calls_greet_by_her_name() -> None:
    async with Harness(greetings=(), name="Tiffany", rng=random.Random(1)) as h:
        h.signal.ring()
        await h.until(lambda: bool(h.voice.spoken))
        assert h.voice.spoken[0] in prompts.call_greetings("Tiffany")


async def test_the_greeting_comes_from_a_varied_set() -> None:
    greetings = ("Hey, Estia here. How are you doing?",)
    async with Harness(greetings=greetings) as h:
        h.signal.ring()
        await h.until(lambda: greetings[0] in h.voice.spoken)
    assert len(prompts.call_greetings(NAME)) >= 4 and len(set(prompts.call_greetings(NAME))) == len(
        prompts.call_greetings(NAME)
    )
    assert sum("how are you" in g.lower() or "how's" in g.lower() for g in prompts.call_greetings(NAME)) >= 2


async def test_the_greeting_is_never_the_same_twice_in_a_row_across_calls() -> None:
    # review 2026-10-11: greetings, the short okay and goodbyes shared one "last line", so by the next call the last
    # greeting could come back
    greetings = ("Hi, it's Estia.", "Hey, Estia here.")

    def greeted() -> list[str]:
        return [line for line in h.manager.said if line in greetings]

    async def one_call(number: int) -> None:
        h.signal.ring(call_id=CALL - number)
        await h.until(lambda: len(greeted()) == number)
        h.helper.caller_says(speech(0.6))  # "okay bye": a goodbye line, then she hangs up
        await h.until(lambda: h.host.ended == number)

    async with Harness(["okay bye"] * 4, greetings=greetings, rng=random.Random(0)) as h:
        for number in range(1, 5):
            await one_call(number)
    assert all(a != b for a, b in itertools.pairwise(greeted()))


async def test_a_short_got_it_shows_her_turn_was_heard_before_the_answer() -> None:
    said = "What's on my calendar for tomorrow?"
    async with Harness([said], [Turn("Two meetings tomorrow.")], acknowledge=True) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Two meetings tomorrow.")
    assert h.manager.said[1] in prompts.CALL_ACK_LINES and h.manager.said[2:] == ["Two meetings tomorrow."]
    assert all(len(line.split()) <= 3 for line in prompts.CALL_ACK_LINES)  # "Got it.": short, not a speech


@pytest.mark.parametrize(
    ("said", "acknowledged"),
    [
        ("How are you?", False),  # owner 2026-10-11: a "got it" before the answer to this makes no sense
        ("Yes.", False),
        ("Thanks a lot!", False),
        ("Um, uh, what is on tomorrow?", False),  # fillers are no words: four
        ("What is on my calendar?", True),  # five words: she took in a request
    ],
)
async def test_only_a_turn_of_five_words_or_more_gets_an_acknowledgement(said: str, acknowledged: bool) -> None:
    async with Harness([said], [Turn("Fine.")], acknowledge=True) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Fine.")
    assert h.host.turns == [said]
    assert (h.manager.said[1] in prompts.CALL_ACK_LINES) is acknowledged
    assert h.manager.said[-1] == "Fine." and len(h.manager.said) == (3 if acknowledged else 2)


@pytest.mark.parametrize(
    ("said", "words"),
    [
        ("How are you?", 3),
        ("Um, uh, what is on tomorrow?", 4),
        ("*keyboard clicking* Switch the lights off, please.", 5),
        ("Wat staat morgen op mijn agenda?", 6),
        ("Как дела?", 2),
        ("元気ですか", 2),  # Japanese is written without spaces: about two characters a word
        ("明日の予定を教えてください", 6),
    ],
)
def test_the_words_of_a_turn_in_any_language(said: str, words: int) -> None:
    from local_assistant.calls import turn_words

    assert turn_words(said) == words


def said_in(h: Harness) -> list[tuple[str, str | None]]:
    return list(zip(h.voice.spoken, h.voice.languages, strict=True))


async def test_a_call_in_another_language_uses_its_lines_and_says_codes_words_in_english() -> None:
    dutch = prompts.spoken("nl", name=NAME)
    result = "Done: lamp is now on."
    turn = Turn("De lamp is aan.", reply_id=3, code_text=result)
    said = "doe de lamp in de woonkamer aan"
    async with Harness([said], [turn], language="nl", acknowledge=True, greetings=("Hi!",)) as h:
        await h.connected()
        await h.quiet_after(dutch.greeting)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after(result)
        assert said_in(h)[0] == (dutch.greeting, "nl")  # the language's greeting (the English set is not used)
        assert said_in(h)[1] in {(ack, "nl") for ack in prompts.call_lines("nl", name=NAME).acknowledge}  # "Begrepen."
        assert said_in(h)[2:] == [
            ("De lamp is aan.", "nl"),
            (result, "en"),  # code's exact words, in English so they are always clear
        ]
        assert h.manager.said[-2:] == ["De lamp is aan.", result]
        assert h.voice.heard_in == ["nl"]  # the whole call listens in its language too
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)


async def test_a_device_question_on_a_call_in_another_language_is_only_codes_question() -> None:
    # real-model eval 2026-10-11: on a Dutch call Gemma said "Ik heb de heater uitgezet, maar je moet nog even 'ja'
    # zeggen" (it had not: the heater waited for the yes); English Gemma does not. She says only code's question.
    question = "Shall I switch heater off?"
    claim = "Ik heb de heater uitgezet, maar je moet nog even 'ja' zeggen."
    turns = [Turn(claim, reply_id=3, asks_yes=True, code_text=question), Turn("", code_text="Done: heater is now off.")]
    async with Harness(["doe de verwarming uit", "ja"], turns, language="nl") as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).greeting)
        h.helper.caller_says(speech(1.0))
        h.helper.caller_says_after(lambda: question in h.manager.said, 30, speech(0.4))
        await h.quiet_after("Done: heater is now off.")
    assert said_in(h)[1:] == [(question, "en"), ("Done: heater is now off.", "en")]
    assert claim not in h.voice.spoken and h.host.turns == ["doe de verwarming uit", "ja"]
    assert h.host.interruptions == []


async def test_english_calls_say_the_code_text_as_before() -> None:
    # English Gemma's words come before the device question, as before (only another language leaves them out)
    turn = Turn("The heater can go off.", reply_id=3, asks_yes=True, code_text="Shall I switch heater off?")
    async with Harness(["switch the heater off"], [turn]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Shall I switch heater off?")
        assert said_in(h)[1:] == [("The heater can go off.", "en"), ("Shall I switch heater off?", "en")]


async def test_a_quiet_call_in_another_language() -> None:
    german = prompts.spoken("de", name=NAME)
    async with Harness(language="de", idle_prompt_s=0.5, idle_hangup_s=1.0) as h:
        await h.connected()
        await h.quiet_after(german.take_your_time)
        await h.until(lambda: h.signal.hung_up == [(ME, CALL)])
        assert (german.take_your_time, "de") in said_in(h)
        assert (prompts.CALL_IDLE_BYE, "en") in said_in(h)  # a rare line: English


async def test_hearing_trouble_and_a_lost_answer_in_another_language() -> None:
    french = prompts.spoken("fr", name=NAME)
    async with Harness(["c'est quoi le programme?"], language="fr", turn_timeout_s=0.3, delay=5.0) as h:
        await h.connected()
        await h.quiet_after(french.greeting)
        h.helper.caller_says(speech(0.8))
        await h.quiet_after(french.again)  # the answer took too long
        assert (french.again, "fr") in said_in(h)
        h.voice.fail_transcribe = True
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: h.voice.spoken.count(french.again) == 2)  # hearing trouble


@pytest.mark.parametrize(
    ("text", "voiced_s", "noise"),
    [
        ("Привет, как дела?", 1.0, False),  # Cyrillic is words, not a sound
        ("今日の予定は？", 1.0, False),
        ("오늘 일정이 뭐야?", 1.0, False),
        ("Wat staat er vandaag op mijn agenda?", 1.5, False),
        ("Ja.", 0.3, False),
        ("Untertitel im Auftrag des ZDF, 2021", 2.0, True),  # Whisper's well-known noise in other languages
        ("Ondertiteld door de Amara.org gemeenschap", 2.0, True),
        ("ご視聴ありがとうございました", 2.0, True),
        ("Продолжение следует...", 2.0, True),
        # a subtitle credit is never speech, however long (the cap on never-said phrases is for sign-offs)
        ("Sous-titres réalisés par la communauté d'Amara.org", 2.5, True),
        ("Untertitel im Auftrag des ZDF für funk, 2017", 2.5, True),
        ("Sottotitoli e revisione a cura di QTSS", 2.5, True),
        ("Merci.", 0.3, True),
        ("Danke.", 0.3, True),
    ],
)
def test_words_in_other_languages_are_words(text: str, voiced_s: float, noise: bool) -> None:
    from local_assistant.calls import is_noise_transcript, real_words

    assert is_noise_transcript(text, voiced_s) is noise
    assert real_words(text) or noise


@pytest.mark.parametrize("said", ["Wacht!", "Ja.", "はい", "Подожди", "네", "Stopp", "attends", "これは何ですか"])
def test_talking_over_her_in_another_language_counts(said: str) -> None:
    from local_assistant.calls import is_interruption

    assert is_interruption(said, name=NAME)


@pytest.mark.parametrize("said", ["Ähm", "ん", "-", "euh"])
def test_a_sound_in_another_language_does_not_interrupt(said: str) -> None:
    from local_assistant.calls import is_interruption

    assert not is_interruption(said, name=NAME)


def test_the_acknowledgement_is_a_short_got_it() -> None:
    # owner 2026-10-11: "Got it." and a few variations. Kokoro garbled "Hmm.", "Mhm.", "Mm-hm." and "Uh-huh." (Whisper
    # heard its clips as "Emily.", "Me have.", "Eh hi."); these four came back exactly
    assert prompts.CALL_ACK_LINES == ("Got it.", "Gotcha.", "Okay, got it.", "Alright, got it.")


async def test_a_dutch_call_acknowledges_a_long_turn_in_dutch_and_a_short_one_not_at_all() -> None:
    turns = [Turn("Twee afspraken."), Turn("Goed, dank je.")]
    said = ["wat staat er morgen op mijn agenda", "hoe gaat het"]
    async with Harness(said, turns, language="nl", acknowledge=True) as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).greeting)
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Twee afspraken.")
        h.helper.caller_says(speech(1.2))
        await h.quiet_after("Goed, dank je.")
    acknowledged = h.manager.said[1]
    assert acknowledged in prompts.call_lines("nl", name=NAME).acknowledge and (acknowledged, "nl") in said_in(h)
    assert h.manager.said[2:] == ["Twee afspraken.", "Goed, dank je."]  # nothing before the short turn's answer


async def test_no_acknowledgement_before_the_answer_to_a_device_question() -> None:
    question = "The heater needs your yes. Say yes if you want me to switch it off now."
    last = "Say yes if you want me to switch it off now."
    turns = [Turn(question, reply_id=3, asks_yes=True), Turn("Done: heater off.")]
    said = ["please switch the heater off now", "Yes, please switch it off now."]  # both long enough for one
    async with Harness(said, turns, acknowledge=True, fillers=False) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        h.helper.caller_says_after(lambda: last in h.manager.said, 30, speech(0.4))
        await h.quiet_after("Done: heater off.")
    said = h.manager.said
    assert said[1] in prompts.CALL_ACK_LINES  # the request got one
    assert said[said.index(last) + 1] == "Done: heater off."  # the yes did not: the answer comes at once


async def test_no_thinking_line_right_after_the_acknowledgement() -> None:
    # review 2026-10-11: the thinking timer ran while she said the short "Hmm.", so a thinking line followed at once

    def call_time() -> float:  # moves only while she says the short line: a second each, past thinking_after_s
        return float(sum(line in prompts.CALL_ACK_LINES for line in h.manager.said))

    said = ["give me an idea for dinner"]
    h = Harness(said, [Turn("Fine.")], acknowledge=True, thinking_after_s=0.5, clock=call_time)
    h.host.gate = asyncio.Event()
    async with h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.6))
        await h.until(lambda: any(line in prompts.CALL_ACK_LINES for line in h.manager.said))
        h.host.gate.set()
        await h.quiet_after("Fine.")
    assert h.manager.said[1] in prompts.CALL_ACK_LINES and h.manager.said[2:] == ["Fine."]  # nothing in between


async def test_a_failing_acknowledgement_abandons_the_turn() -> None:
    # review 2026-10-11: the short line was said outside the turn's try: a voice failure left the answer running
    # after the call, with its device question
    async with Harness(["please switch the heater off now"], delay=30.0, acknowledge=True) as h:
        h.voice.broken = set(prompts.CALL_ACK_LINES)
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.ended == 1)
        assert h.host.cancelled_turns == 1 and h.host.dropped_waiting == 1


async def test_a_call_that_ends_during_an_answer_leaves_a_note_that_it_was_not_done() -> None:
    async with Harness(["switch the lights off"], delay=30.0) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.turns == ["switch the lights off"])
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await h.until(lambda: bool(h.host.notes))
        assert h.host.notes == [prompts.CALL_UNFINISHED]


def test_no_spoken_line_is_a_hum_the_voice_garbles() -> None:
    """Kokoro turns "Hmm.", "Mm, okay." and "Mm-hm." into a vague sound (Whisper heard "Emily."): no code-written
    line she says on a call may contain one."""
    hum = re.compile(r"\b(h?m+|mh?m|m+-hm+|uh-huh)\b", re.IGNORECASE)
    spoken = [
        *prompts.CALL_THINKING_LINES,
        *prompts.CALL_CHECKING_LINES,
        *prompts.CALL_ACK_LINES,
        *prompts.call_greetings(NAME),
    ]
    assert [line for line in spoken if hum.search(line)] == []


# calls she places (a wake-up call or a call reminder): the reminders' scheduler sends the text first


async def placing(h: "Harness", *, wake: bool = True) -> "asyncio.Task[str]":
    task = asyncio.create_task(h.manager.place(ME, wake=wake, time="7:00", text="wake up"))
    await h.until(lambda: h.signal.started == [ME])
    return task


async def test_a_placed_call_starts_only_when_picked_up_and_opens_with_the_wake_line() -> None:
    async with Harness(["thanks, I'm up"], [Turn("Great, have a good day!")]) as h:
        task = await placing(h)
        await asyncio.sleep(0.2)
        assert not task.done() and h.host.started == []  # ringing: texts are not held yet
        await h.signal.pick_up()
        assert await task == "answered"
        assert h.host.started == [ME]
        opening = "Good morning! It's 7:00. wake up"
        await h.quiet_after(opening)
        assert GREETING not in h.voice.spoken  # the opening instead of the greeting
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Great, have a good day!")
        assert h.host.turns == ["thanks, I'm up"]


async def test_a_call_reminder_opens_with_why_she_calls() -> None:
    async with Harness() as h:
        task = asyncio.create_task(h.manager.place(ME, wake=False, time="17:00", text="call the dentist"))
        await h.until(lambda: h.signal.started == [ME])
        await h.signal.pick_up()
        assert await task == "answered"
        await h.quiet_after("It's 17:00. You asked me to call you. call the dentist")


async def test_the_opening_is_said_in_the_calls_language() -> None:
    async with Harness(language="nl") as h:
        task = await placing(h)
        await h.signal.pick_up()
        await task
        await h.quiet_after("Goedemorgen! Het is 7:00. wake up")


@pytest.mark.parametrize(
    ("reason", "outcome"),
    [
        ("ring_timeout", "no_answer"),
        ("Timeout", "no_answer"),
        ("remote_hangup", "declined"),
        ("RemoteHangup", "declined"),
        ("remote_busy", "busy"),
        ("tunnel_exit", "call_failed"),
        ("tunnel_spawn_error", "call_failed"),
        ("shutdown", "call_failed"),
    ],
)
async def test_a_call_that_is_not_picked_up_ends_with_its_outcome(reason: str, outcome: str) -> None:
    async with Harness() as h:
        task = await placing(h)
        h.signal.end_ringing(reason)
        assert await task == outcome
        assert h.host.started == [] and h.host.ended == 0 and h.host.notes == []  # no call notes for no call
        assert not h.manager.in_call


async def test_a_signal_cli_restart_while_ringing_is_a_failed_call() -> None:
    async with Harness() as h:
        task = await placing(h)
        h.signal.end_ringing("signal_cli_restarted", call_id=None)
        assert await task == "call_failed"


async def test_no_end_in_time_hangs_up_and_is_a_failed_call() -> None:
    async with Harness(ring_wait_s=0.3) as h:
        task = await placing(h)
        assert await task == "call_failed"
        assert h.signal.hung_up == [(ME, OUT)]


async def test_a_startcall_error_is_a_failed_call() -> None:
    async with Harness() as h:
        h.signal.start_error = RuntimeError("signal-cli refused")
        assert await h.manager.place(ME, wake=True, time="7:00", text="wake up") == "call_failed"


async def test_she_never_dials_an_account_that_is_not_allowed() -> None:
    async with Harness() as h:
        assert await h.manager.place(STRANGER, wake=True, time="7:00", text="x") == "call_failed"
        assert h.signal.started == []


async def test_whether_a_call_can_be_placed() -> None:
    async with Harness(ready=False) as h:
        assert await h.manager.can_place(ME) == "not_ready"
        assert await h.manager.place(ME, wake=True, time="7:00", text="x") == "call_failed"
        assert h.signal.started == []
    async with Harness() as h:
        assert await h.manager.can_place(ME) == ""
        await h.connected()  # the user called her
        assert await h.manager.can_place(ME) == "in_call"


async def test_an_incoming_call_while_she_dials_is_ignored() -> None:
    async with Harness() as h:
        task = await placing(h)
        h.signal.ring()
        await asyncio.sleep(0.2)
        assert h.signal.accepted == []
        h.signal.end_ringing("ring_timeout")
        assert await task == "no_answer"


async def test_stopping_the_bridge_hangs_up_a_ringing_call() -> None:
    async with Harness() as h:
        task = await placing(h)
        await asyncio.sleep(0.1)  # the call id is known
        await h.manager.hangup()
        assert h.signal.hung_up == [(ME, OUT)]
        h.signal.end_ringing("local_hangup")
        assert await task == "call_failed"


async def test_a_minute_of_silence_after_the_wake_line_ends_the_call_with_a_goodbye() -> None:
    async with Harness(wake_silence_s=0.5) as h:
        task = await placing(h)
        await h.signal.pick_up()
        await task
        await h.until(lambda: h.signal.hung_up == [(ME, OUT)])
        assert any(line in h.voice.spoken for line in prompts.CALL_BYE_LINES)
        assert prompts.CALL_IDLE not in h.voice.spoken


async def test_the_status_line_shows_the_next_call() -> None:
    async with Harness() as h:
        assert h.manager.status_line(next_call="Tue 13 Oct 07:00") == "Calls: ready · next call Tue 13 Oct 07:00"


WEATHER = "The weather in Amsterdam stays sunny and warm until Friday."


@pytest.mark.parametrize(
    ("heard", "played", "own"),
    [
        # her echo: her sentence, or a long part of it, in her order
        (WEATHER, [WEATHER], True),
        ("weather in amsterdam stays sunny", [WEATHER], True),
        ("um, the weather in Amsterdam stays sunny", [WEATHER], True),  # fillers left out
        ("the weather in amsterdam stays sunny and warm till friday", [WEATHER], True),  # one word heard otherwise
        ("the train leaves from platform four", ["The train leaves from platform four.", "It is on time."], True),
        ("Don’t worry, the train is running late tonight", ["Don't worry, the train is running late tonight."], True),
        ("esta bien gracias por tu paciencia hoy", ["Está bien, gracias por tu paciencia hoy."], True),  # accents
        ("le train part à neuf heures ce soir", ["Le train part à neuf heures ce soir."], True),
        ("Привет как дела у тебя сегодня", ["Привет, как дела у тебя сегодня?"], True),
        ("今日は晴れです", ["今日は晴れですね。"], True),  # Japanese, without spaces: by characters
        # the caller (review of 13 Oct): a few of her words are no echo
        ("the dentist at three", ["You see the dentist at three tomorrow."], False),  # two real words of hers
        ("turn it off", ["Should I turn the heater off?"], False),
        ("no, not Amsterdam", ["We land at nine tonight in Amsterdam."], False),
        ("wait, it is at five", ["The meeting is at five, it is in the big room."], False),
        ("wait, the meeting is at five in the big room", ["The meeting is at five in the big room."], False),
        ("no the weather in amsterdam stays sunny", [WEATHER], False),  # a no she did not say
        ("yes the weather in amsterdam stays sunny", [WEATHER], False),
        ("no, está bien gracias por tu paciencia", ["Está bien, gracias por tu paciencia hoy."], False),
        ("non le train part à neuf heures", ["Le train part à neuf heures ce soir."], False),
        ("nein der Zug fährt um neun Uhr ab", ["Der Zug fährt um neun Uhr ab."], False),
        ("sunny Amsterdam weather", [WEATHER], False),  # her words, not in her order
        ("weather in amsterdam", [WEATHER], False),  # two words of hers
        ("the weather in amsterdam stays sunny but what about rome and paris next week", [WEATHER], False),  # half
        ("what about rain on friday afternoon", ["Tomorrow looks dry,"], False),  # words she has not said yet
        ("Answer.", [WEATHER], False),
        ("", [WEATHER], False),
        ("はい", ["はい、今日は晴れです。"], False),  # one word of two characters
        ("明日の予定を教えて", ["はい、今日は晴れです。"], False),
        ("待って、今日は晴れです", ["今日は晴れですね。"], False),  # a stop word she did not say
        ("今日は晴れです", ["今日は"], False),  # not said yet
    ],
)
def test_her_own_voice_is_a_long_part_of_what_she_played(heard: str, played: list[str], own: bool) -> None:
    assert is_own_voice(heard, played) is own


@pytest.mark.parametrize(
    ("hers", "fraction", "played"),
    [
        (("Ten past four.", "The weather in Amsterdam is sunny today."), 0.5, ("Ten past four.", "The weather in")),
        (("The weather is fine.",), 11 / 20, ("The weather",)),  # cut at a word's end: the word is said
        (("The weather is fine.",), 1.0, ("The weather is fine.",)),
        (("今日は晴れですね。",), 0.5, ("今日は晴",)),  # Japanese by characters
        ((), 0.5, ()),
    ],
)
def test_what_she_played_is_the_sentence_before_and_this_one_up_to_the_stop(
    hers: tuple[str, ...], fraction: float, played: tuple[str, ...]
) -> None:
    assert played_part(hers, fraction) == played


async def test_her_own_voice_over_her_answer_only_pauses_her(caplog: pytest.LogCaptureFixture) -> None:
    # a loud speaker next to the caller's microphone (13 Oct): her own words came back and stopped her, and she took
    # them for the caller's turn
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = f"{WEATHER} " * 6
    async with Harness(["tell me more", "weather in amsterdam stays sunny"], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # her echo
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)  # she stopped and listened to her own voice
        await h.until(lambda: h.manager.said.count(WEATHER) == 6)  # then carried on to the end
        assert h.host.interruptions == [] and h.host.turns == ["tell me more"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = [record.getMessage() for record in caplog.records]
    assert "calls: her own voice heard; carrying on" in lines
    assert any(line.startswith("calls: barge-in loudness") and "her own voice 1" in line for line in lines)


async def test_her_words_she_has_not_said_yet_are_the_callers() -> None:
    # the caller asks about the end of her sentence while she is at its start: no echo of hers
    sentence = "Tomorrow looks dry, but rain comes back on Friday afternoon, and it stays wet over the weekend."
    async with Harness(
        ["tell me more", "what about rain on friday afternoon"], [Turn(sentence, reply_id=5), Turn("Okay.")]
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 30, speech(1.0))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} what about rain on friday afternoon"]


async def test_after_her_own_voice_a_caller_as_loud_as_it_no_longer_stops_her() -> None:
    answer = f"{WEATHER} " * 8
    async with Harness(["tell me more", "weather in amsterdam stays sunny"], [Turn(answer, reply_id=5)]) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # her echo
        h.helper.caller_says_when(lambda: h.voice.heard == 2, noise(0.5) + speech(1.0))  # as loud, after it
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.manager.said.count(WEATHER) == 8)
        assert h.voice.heard == 2  # the second time it did not stop her: no third transcription


async def test_her_own_voice_never_reaches_gemma_even_after_three_false_alarms() -> None:
    # after MAX_FALSE_BARGE false alarms in one sentence the next stop is taken as the caller's: her own words never are
    answer = f"{WEATHER} " * 30  # her sentences, and an echo that stops her four times in a row
    echo = ["weather in amsterdam stays sunny"] * 4
    async with Harness(["tell me more", *echo, "what time is it"], [Turn(answer, reply_id=5), Turn("Four.")]) as h:
        await h.connected()
        await h.quiet_after(GREETING)

        greeting = h.helper.her_frames()

        def heard(times: int) -> Callable[[], bool]:
            return lambda: h.voice.heard == times

        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # in her 2nd sentence
        for times in range(2, 5):  # louder than the level raised after the first (raised once, never again)
            h.helper.caller_says_when(heard(times), noise(0.4) + speech(1.0, level=4800))
        h.helper.caller_says(speech(1.0))  # "tell me more"
        await h.until(lambda: h.voice.heard == 5)
        h.helper.caller_says(noise(0.5) + speech(1.0))
        await h.quiet_after("Four.")
        assert h.host.turns == ["tell me more", "what time is it"]


async def test_speech_rule_road_noise_over_her_answer_does_not_stop_her(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = "This is a long answer. " * 6
    async with Harness(["tell me more"], [Turn(answer, reply_id=5)], barge="speech") as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 100, noise(3.0, 3000))  # the road
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.manager.said.count("This is a long answer.") == 6)
        assert h.voice.heard == 1 and h.host.interruptions == []  # never stopped: nothing to transcribe
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    streams = {stream for stream, _ in h.voice.chunks}
    assert len(streams) == 1 and re.fullmatch(r"[0-9a-f]{32}", streams.pop())  # one detector state for the call
    assert {size for _, size in h.voice.chunks} == {8192}
    lines = [record.getMessage() for record in caplog.records]
    shadow = (
        r"calls: barge-in speech: speech stopped her 0 times, would have 0; loudness stopped her 0 times, would have"
        r" [1-9]\d*; her own voice 0; detector \d+ chunks, slowest \d+ ms, 0 failed"
    )
    assert any(re.fullmatch(shadow, line) for line in lines)  # once in each sentence the road was under


async def test_speech_rule_a_caller_talking_over_her_still_stops_her() -> None:
    long_answer = "This is a long answer. " * 8
    async with Harness(
        ["tell me more", "wait, what about tomorrow"], [Turn(long_answer, reply_id=5), Turn("Okay.")], barge="speech"
    ) as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(1.2))
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Okay.")
        assert h.host.turns == ["tell me more", f"{prompts.CUTTING_IN} wait, what about tomorrow"]


@pytest.mark.parametrize(
    ("detector", "state"), [(None, "old voice service"), ("loading", "loading"), ("unavailable", "unavailable")]
)
async def test_speech_rule_without_a_detector_loudness_decides_the_call(
    caplog: pytest.LogCaptureFixture, detector: str | None, state: str
) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    async with Harness(barge="speech") as h:
        h.voice.detector = detector
        await h.connected()
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    assert h.voice.chunks == []  # never asked
    lines = [record.getMessage() for record in caplog.records]
    assert f"calls: speech detector {state}; loudness decides this call" in lines
    end = (
        "calls: barge-in speech (loudness: no speech detector): loudness stopped her 0 times, would have 0;"
        " her own voice 0"
    )
    assert end in lines


async def test_speech_rule_a_stop_without_words_counts_what_loudness_did_beside_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # speech stopped her, loudness fired first in the same clip, and the words were none: a false stop both made
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    answer = f"{WEATHER} " * 4
    async with Harness(["tell me more", ""], [Turn(answer, reply_id=5)], barge="speech") as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 250, speech(1.0))  # no words in it
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.manager.said.count(WEATHER) == 4)
        assert h.voice.heard == 2 and h.host.turns == ["tell me more"]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    end = [record.getMessage() for record in caplog.records if record.getMessage().startswith("calls: barge-in")]
    assert len(end) == 1
    assert "speech stopped her 1 time, would have 0; loudness stopped her 0 times, would have 1;" in end[0]


async def test_a_call_that_falls_back_to_loudness_midway_counts_each_rule(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="local_assistant.calls")
    caplog.set_level(logging.INFO, logger="local_assistant.call_audio")
    long_answer = f"{WEATHER} " * 8
    async with Harness(
        ["tell me more", "wait, what about tomorrow", "and the day after"],
        [Turn(long_answer, reply_id=5), Turn(long_answer, reply_id=6), Turn("Okay.")],
        barge="speech",
    ) as h:
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        greeting = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > greeting + 150, speech(1.2))  # speech stops her
        h.helper.caller_says(speech(1.0))
        await h.until(lambda: h.voice.heard == 2)
        h.voice.detector = "unavailable"  # from here every chunk fails: loudness after the third
        second = h.helper.her_frames()
        h.helper.caller_says_when(lambda: h.helper.her_frames() > second + 150, speech(1.2))  # loudness stops her
        await h.quiet_after("Okay.")
        assert h.host.turns == [
            "tell me more",
            f"{prompts.CUTTING_IN} wait, what about tomorrow",
            f"{prompts.CUTTING_IN} and the day after",
        ]
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
    lines = [record.getMessage() for record in caplog.records]
    assert "calls: the speech detector failed 3 times in a row; loudness for the rest of the call" in lines
    end = (
        r"calls: barge-in speech \(loudness after 3 failed answers in a row\): speech stopped her 1 time, would have 0;"
        r" loudness stopped her 1 time, would have 0; her own voice 0; detector \d+ chunks, slowest \d+ ms, 3 failed"
        r" \(http 3\)"
    )
    assert any(re.fullmatch(end, line) for line in lines), [line for line in lines if "barge-in" in line]


async def test_loudness_rule_measures_speech_beside_it_when_the_service_has_a_detector() -> None:
    async with Harness() as h:  # barge = "loudness", the default
        h.voice.detector = "ready"
        await h.connected()
        await h.quiet_after(GREETING)
        assert h.voice.chunks  # the caller's audio under the greeting went to the detector: speech is the shadow


async def test_the_status_line_shows_the_speech_detector_when_speech_stops_her() -> None:
    async with Harness(barge="speech") as h:
        h.voice.detector = "ready"
        assert await h.manager.detector() == "ready"
        assert h.manager.status_line(detector="ready") == "Calls: ready · speech detector: ready"
        h.voice.detector = None
        assert await h.manager.detector() == "old voice service"
    async with Harness() as h:
        assert await h.manager.detector() == ""  # loudness: nothing to show


# /babble: a call during practice is a practice call


async def test_a_practice_call_is_in_the_practised_language_and_ends_practice_instead_of_a_summary() -> None:
    said = ["Ik woon in Utrecht.", "Ik werk bij een bank.", "Ik heb twee katten."]
    turns = [Turn("Waar werk je?"), Turn("Heb je huisdieren?"), Turn("Wat leuk!")]
    async with Harness(said, turns, practice=lambda aci: "nl" if aci == ME else None) as h:
        await h.connected()
        greeting = prompts.spoken("nl", name=NAME).greeting
        assert greeting in h.voice.spoken and h.voice.languages[0] == "nl"
        await h.quiet_after(greeting)
        for answer in ("Waar werk je?", "Heb je huisdieren?", "Wat leuk!"):
            h.helper.caller_says(speech(1.0))
            await h.quiet_after(answer)
        assert set(h.voice.heard_in) == {"nl"} and set(h.voice.languages) == {"nl"}
        assert h.host.practice_turns == [True, True, True]  # the app answers them as practice turns
        assert h.host.started_practice == [True]  # and its start says so (no glance of the day for practice)
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.practice_ended))
        await asyncio.sleep(0.1)
        assert h.host.practice_ended == [ME] and h.host.summaries == []  # the recap replaces the summary


async def test_a_practice_call_without_a_turn_leaves_practice_running() -> None:
    async with Harness(practice=lambda aci: "de") as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("de", name=NAME).greeting)
        h.signal.remote_hangup()
        await h.until(lambda: h.host.ended == 1)
        await asyncio.sleep(0.1)
        assert h.host.practice_ended == [] and h.host.summaries == []


async def test_a_practice_call_ends_practice_before_its_notes() -> None:
    async with Harness(["Ik ben moe."], delay=30.0, practice=lambda aci: "nl") as h:
        await h.connected()
        await h.quiet_after(prompts.spoken("nl", name=NAME).greeting)
        h.helper.caller_says(speech(0.8))
        await h.until(lambda: h.host.turns == ["Ik ben moe."])
        h.signal.remote_hangup()
        await h.until(lambda: len(h.host.notes) == 2)
        assert h.host.notes == ["(practice ended)", prompts.CALL_UNFINISHED]


async def test_without_practice_a_call_keeps_its_language_and_summary() -> None:
    async with Harness(
        ["first idea", "second idea"], [Turn("Nice."), Turn("Good one.")], practice=lambda aci: None
    ) as h:
        await h.connected()
        await h.quiet_after(GREETING)
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Nice.")
        h.helper.caller_says(speech(1.0))
        await h.quiet_after("Good one.")
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.summaries))
        assert h.host.practice_ended == [] and set(h.voice.heard_in) == {"en"}


async def test_a_call_she_places_during_practice_is_an_ordinary_call() -> None:
    # a wake-up call or a reminder call: the user's own language, ordinary turns (tools, reminders), its summary, and
    # practice keeps running (only a call the user makes during practice is a practice call)
    said = ["ok, remind me again in 10 minutes", "thanks"]
    async with Harness(said, [Turn("Done."), Turn("Bye!")], practice=lambda aci: "nl") as h:
        task = await placing(h)
        await h.signal.pick_up()
        assert await task == "answered"
        await h.quiet_after("Good morning! It's 7:00. wake up")  # English, as /language says, not Dutch
        for answer in ("Done.", "Bye!"):
            h.helper.caller_says(speech(1.0))
            await h.quiet_after(answer)
        assert h.host.practice_turns == [False, False] and set(h.voice.heard_in) == {"en"}
        assert h.host.started_practice == [False]
        h.signal.remote_hangup()
        await h.until(lambda: bool(h.host.summaries))
        assert h.host.practice_ended == []
