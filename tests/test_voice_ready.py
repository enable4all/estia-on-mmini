"""The "voice ready" note (voice_ready.ReadyNote): fake clocks and a fake voice health, nothing sent for real."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from local_assistant import prompts
from local_assistant.voice_ready import LONG_LOAD_SECONDS, ReadyNote

TZ = ZoneInfo("Europe/Athens")
ALEX = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SAM = "cccccccc-dddd-4eee-8fff-000000000000"
NIGHT = (22 * 60, 8 * 60)


def at(hour: int, minute: int = 0, second: int = 0) -> float:
    return datetime(2026, 10, 13, hour, minute, second, tzinfo=TZ).timestamp()


class Voice:
    """A fake voice health: ready or not, and how often it was read."""

    def __init__(self, ready: bool = False) -> None:
        self.ready = ready
        self.reads = 0
        self.broken = False

    async def __call__(self) -> bool:
        self.reads += 1
        if self.broken:
            raise OSError("no answer")
        return self.ready


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Harness:
    def __init__(self, start: float, *, ready: bool = False, quiet: tuple[int, int] | None = NIGHT) -> None:
        self.clock = Clock(start)
        self.voice = Voice(ready)
        self.sent: list[tuple[str, str]] = []
        self.locked = False
        self.note = ReadyNote(
            self.voice,
            self.send,
            frozenset({ALEX, SAM}),
            tz=TZ,
            quiet_hours=quiet,
            locked=lambda: self.locked,
            clock=self.clock,
        )

    async def send(self, aci: str, text: str) -> None:
        self.sent.append((aci, text))

    async def after(self, seconds: float) -> None:
        self.clock.now += seconds
        await self.note.check()

    def to(self) -> list[str]:
        return sorted(aci for aci, _text in self.sent)


async def test_after_a_long_load_every_allowed_account_gets_the_note_once() -> None:
    """A first install, a move or a macOS update: not ready 2 minutes after the start, then ready."""
    h = Harness(at(10))
    await h.after(0)
    await h.after(LONG_LOAD_SECONDS + 5)
    assert h.sent == []
    h.voice.ready = True
    await h.after(15)
    assert h.to() == [ALEX, SAM]
    assert {text for _aci, text in h.sent} == {prompts.VOICE_READY}
    assert prompts.VOICE_READY == "My voice is ready: you can call me now."
    await h.after(15)
    await h.after(3600)
    assert len(h.sent) == 2  # at most once per bridge start


async def test_a_short_load_sends_nothing_and_stops_reading_the_voice() -> None:
    h = Harness(at(10))
    await h.after(0)
    h.voice.ready = True
    await h.after(60)  # ready within 2 minutes: an ordinary restart
    reads = h.voice.reads
    await h.after(15)
    await h.after(3600)
    assert h.sent == [] and h.voice.reads == reads  # nothing owed: the health is not read any more


async def test_ready_at_the_start_sends_nothing() -> None:
    h = Harness(at(10), ready=True)
    await h.after(0)
    await h.after(600)
    assert h.sent == [] and h.voice.reads == 1


async def test_a_call_that_rang_while_not_ready_gets_the_note_at_once_and_only_the_caller() -> None:
    """The 'not ready' note says she texts when ready: the caller gets it as soon as the voice is ready."""
    h = Harness(at(10), ready=True)
    await h.after(0)  # ready at the start: no long load
    h.voice.ready = False  # the voice service restarted
    h.note.missed(ALEX)
    await h.after(15)
    assert h.sent == []
    h.voice.ready = True
    await h.after(15)
    assert h.sent == [(ALEX, prompts.VOICE_READY)]  # the other account did not call: nothing for it


async def test_in_quiet_hours_the_long_load_note_waits_until_morning_but_a_caller_gets_it_at_once() -> None:
    h = Harness(at(23, 0))
    await h.after(0)
    h.note.missed(SAM)
    await h.after(LONG_LOAD_SECONDS + 5)  # still loading: a long load
    h.voice.ready = True
    await h.after(5)
    assert h.sent == [(SAM, prompts.VOICE_READY)]  # a missed call overrides the quiet hours
    await h.after(3 * 3600)  # 02:02
    assert h.to() == [SAM]  # never waking anyone
    h.clock.now = at(7, 59, 50)
    await h.note.check()
    assert h.to() == [SAM]
    await h.after(15)  # 08:00:05
    assert h.to() == [ALEX, SAM]


async def test_without_quiet_hours_nothing_waits() -> None:
    h = Harness(at(23, 0), quiet=None)
    await h.after(LONG_LOAD_SECONDS + 5)
    h.voice.ready = True
    await h.after(5)
    assert h.to() == [ALEX, SAM]


async def test_while_locked_even_a_caller_waits_until_unlock() -> None:
    h = Harness(at(10))
    await h.after(0)
    h.note.missed(ALEX)
    h.locked = True
    await h.after(LONG_LOAD_SECONDS + 5)
    h.voice.ready = True
    await h.after(5)
    await h.after(3600)
    assert h.sent == []
    h.locked = False
    await h.after(15)
    assert h.to() == [ALEX, SAM]


async def test_a_held_note_is_sent_only_while_the_voice_is_still_ready() -> None:
    """Held until 08:00, then the voice is not ready again: no claim that it is; sent once it is."""
    h = Harness(at(23, 0))
    await h.after(LONG_LOAD_SECONDS + 5)
    h.voice.ready = True
    await h.after(5)
    h.voice.ready = False
    h.clock.now = at(8, 0, 5)
    await h.note.check()
    assert h.sent == []
    h.voice.ready = True
    await h.after(15)
    assert h.to() == [ALEX, SAM]


async def test_a_call_that_got_through_needs_no_note() -> None:
    """She answered (or a call she placed was picked up): that account heard her voice."""
    h = Harness(at(23, 0))
    await h.after(LONG_LOAD_SECONDS + 5)
    h.voice.ready = True
    await h.after(5)
    h.note.reached(ALEX)
    h.clock.now = at(8, 0, 5)
    await h.note.check()
    assert h.to() == [SAM]


async def test_at_most_once_per_bridge_start_unless_promised_again() -> None:
    """The owner's rule: one note per account per bridge start. A ring later in the run whose 'not ready' note was held
    back (no promise to keep) gets no second one."""
    h = Harness(at(10))
    await h.after(0)
    h.note.missed(ALEX)
    h.voice.ready = True
    await h.after(30)
    assert h.sent == [(ALEX, prompts.VOICE_READY)]
    h.voice.ready = False
    h.note.missed(ALEX)
    await h.after(15)
    h.voice.ready = True
    await h.after(15)
    assert h.sent == [(ALEX, prompts.VOICE_READY)]


async def test_a_voice_health_that_cannot_be_read_counts_as_not_ready() -> None:
    h = Harness(at(10))
    h.voice.broken = True
    await h.after(0)
    await h.after(LONG_LOAD_SECONDS + 10)
    assert h.sent == []
    h.voice.broken = False
    h.voice.ready = True
    await h.after(15)
    assert h.to() == [ALEX, SAM]


async def test_run_forever_checks_on_its_own_and_survives_a_failing_send(caplog: pytest.LogCaptureFixture) -> None:
    h = Harness(at(10))
    h.voice.ready = True
    h.note.missed(ALEX)
    calls: list[str] = []

    async def failing(aci: str, text: str) -> None:
        calls.append(aci)
        raise RuntimeError("signal-cli gone")

    h.note._send = failing
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 3:
            raise asyncio.CancelledError

    h.note._sleep = sleep
    with pytest.raises(asyncio.CancelledError):
        await h.note.run_forever()
    assert calls == [ALEX] and len(slept) == 3  # tried once (at most once), the loop went on
    assert ALEX not in caplog.text


# ---------------------------------------------------------------- review fixes (13 Oct)


async def test_ready_at_the_first_check_long_after_the_start_is_no_long_load() -> None:
    """A slow start-up (a cold JVM, no network at boot) with a voice that was ready all along: nothing sent."""
    h = Harness(at(10), ready=True)
    await h.after(LONG_LOAD_SECONDS + 10)  # the first check comes 130 s after the start
    await h.after(15)
    assert h.sent == []


async def test_not_ready_only_before_the_two_minutes_is_no_long_load() -> None:
    """Not ready at 105 s, ready at 120 s (first seen then): a short load, nothing sent."""
    h = Harness(at(10))
    await h.after(105)
    h.voice.ready = True
    await h.after(15)
    await h.after(15)
    assert h.sent == []


async def test_not_ready_at_125_seconds_is_a_long_load() -> None:
    h = Harness(at(10))
    await h.after(125)
    h.voice.ready = True
    await h.after(15)
    assert h.to() == [ALEX, SAM]


async def test_a_promise_after_the_note_is_kept_with_one_more_note() -> None:
    """The 'not ready' note says "I'll text you when it's ready": a later outage in the same run whose ring got that
    note gets one more ready note; a ring whose note was held back (no promise) gets none."""
    h = Harness(at(10), ready=True)
    await h.after(0)
    h.voice.ready = False
    h.note.missed(ALEX, True)
    h.voice.ready = True
    await h.after(15)
    assert h.sent == [(ALEX, prompts.VOICE_READY)]
    h.voice.ready = False  # the voice service restarts later in the same run
    h.note.missed(ALEX, False)  # the not-ready note was held back: no promise
    await h.after(15)
    h.voice.ready = True
    await h.after(15)
    assert h.sent == [(ALEX, prompts.VOICE_READY)]
    h.voice.ready = False
    h.note.missed(ALEX, True)  # the not-ready note went out again: its promise is kept
    await h.after(15)
    assert len(h.sent) == 1
    h.voice.ready = True
    await h.after(15)
    assert h.sent == [(ALEX, prompts.VOICE_READY), (ALEX, prompts.VOICE_READY)]


async def test_a_lock_during_the_health_read_holds_the_note_until_unlock() -> None:
    h = Harness(at(23, 0))
    await h.after(LONG_LOAD_SECONDS + 5)
    h.voice.ready = True
    await h.after(15)  # ready; held for the night
    assert h.sent == []

    real = h.note._ready

    async def locking() -> bool:
        h.locked = True  # /lock lands while the voice is read again before the held note goes out
        return await real()

    h.note._ready = locking
    h.clock.now = at(8, 0, 5)
    await h.note.check()
    assert h.sent == []
    h.note._ready = real
    h.locked = False
    await h.after(15)
    assert h.to() == [ALEX, SAM]


async def test_a_call_answered_before_the_poll_sees_ready_gets_no_long_load_note() -> None:
    h = Harness(at(10))
    await h.after(LONG_LOAD_SECONDS + 5)
    h.voice.ready = True
    h.note.reached(ALEX)  # the call manager read the voice itself and answered ALEX
    await h.after(15)
    assert h.to() == [SAM]
