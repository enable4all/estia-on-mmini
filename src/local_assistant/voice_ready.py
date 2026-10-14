"""The note "My voice is ready: you can call me now." (with [calls] only).

Her voice can take up to about 15 minutes to load: after a first install, a move into her own user or a macOS update
the Neural Engine compiles it, and calls are not answered meanwhile. Code then texts each account that needs to know,
in its own words (`prompts.VOICE_READY`), as a note: never in Gemma's history.

The note is owed
- to a caller whose call rang while the voice was not ready (the "not ready" note says she texts when it is): sent as
  soon as the voice is ready, also during quiet hours (the caller is awake);
- to every allowed account when a read 2 minutes or more after the bridge started still said not ready (a long load;
  a voice that was never seen not ready, or ready before then, is none): held until the end of quiet hours (the
  initiative's, 22:00-08:00 when it has none), never waking anyone.
At most once per bridge start for each account, except that a "not ready" note sent later in the same run (its promise)
is always followed by one more. Nothing goes out while /lock is on: it waits for /unlock. A call that got through
(answered and connected, or placed and picked up) told that account already: no note then. The voice is read again
right before a note goes out, so a held note never says "ready" while it is not.

The voice's health is read from the start until it is ready, and again after a missed call until it is ready: never
otherwise. Logs hold the reason only, never an account.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.initiative import in_quiet_hours

log = logging.getLogger(__name__)

LONG_LOAD_SECONDS = 120.0  # still not ready this long after the start: a long load (a compile), everyone is told
POLL_SECONDS = 15.0
QUIET_HOURS = (22 * 60, 8 * 60)  # when the initiative has none: the long-load note never wakes anyone


class ReadyNote:
    def __init__(
        self,
        ready: Callable[[], Awaitable[bool]],
        send: Callable[[str, str], Awaitable[None]],
        acis: Iterable[str],
        *,
        tz: ZoneInfo,
        quiet_hours: tuple[int, int] | None,
        locked: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ready = ready  # whether the voice service can take a call now (both engines ready)
        self._send = send  # (account, text): stored and sent as a note
        self._acis = frozenset(aci.lower() for aci in acis)
        self._tz = tz
        self._quiet_hours = quiet_hours
        self._locked = locked
        self._clock = clock
        self._sleep = sleep
        self._started = clock()  # the bridge's start
        self._starting = True  # not seen ready since the start yet
        self._not_ready_at: float | None = None  # the last read since the start that said not ready
        self._watching = True  # read the voice: from the start, and after a missed call, until it is ready
        self._missed: set[str] = set()  # callers whose call rang while the voice was not ready
        self._owed: dict[str, bool] = {}  # account -> urgent (a missed call: at once, also in quiet hours)
        self._told: set[str] = set()  # sent (or tried) since the start: at most once each, unless promised again
        self._heard: set[str] = set()  # a call got through: no long-load note for them

    def missed(self, aci: str, promised: bool = False) -> None:
        """A call from `aci` rang while the voice was not ready. `promised`: this ring got the "not ready" note, which
        says she texts when ready, so the note is owed again even after one went out in this run."""
        aci = aci.lower()
        if aci not in self._acis or (aci in self._told and not promised):
            return
        self._told.discard(aci)
        self._missed.add(aci)
        self._watching = True

    def reached(self, aci: str) -> None:
        """A call with `aci` got through (connected): that account heard her voice, no note is owed to it."""
        aci = aci.lower()
        self._missed.discard(aci)
        self._owed.pop(aci, None)
        self._heard.add(aci)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:  # a TaskGroup task that raises would take the bridge down
                log.exception("voice ready: the check failed")
            await self._sleep(POLL_SECONDS)

    async def check(self) -> None:
        """One look: the voice's state while it is watched, then the notes that are due now."""
        now = self._clock()
        read: bool | None = None
        if self._watching:
            read = await self._is_ready()
            if not read:
                if self._starting:
                    self._not_ready_at = now
                return
            self._turned_ready()
        due = self._due(now)
        if not due:
            return
        if read is None and not await self._is_ready():  # held until now: is it still ready?
            self._watching = True
            return
        for aci in due:
            if self._locked():  # /lock landed during the read or a send: the rest wait for /unlock
                return
            urgent = self._owed.pop(aci)
            self._told.add(aci)  # at most once: a failed send is not tried again
            log.info("voice ready: note sent (%s)", "a call rang meanwhile" if urgent else "after a long load")
            await self._send(aci, prompts.VOICE_READY)

    async def _is_ready(self) -> bool:
        try:
            return await self._ready()
        except Exception:
            return False

    def _turned_ready(self) -> None:
        loading = None if self._not_ready_at is None else self._not_ready_at - self._started
        if self._starting and loading is not None and loading >= LONG_LOAD_SECONDS:
            log.info("voice ready: still loading %.0f s after the start (a long load)", loading)
            for aci in self._acis - self._told - self._heard:
                self._owed.setdefault(aci, False)
        self._starting = False
        for aci in self._missed - self._told:
            self._owed[aci] = True
        self._missed.clear()
        self._watching = False

    def _due(self, now: float) -> list[str]:
        if self._locked():  # /lock: nothing goes out until /unlock
            return []
        quiet = in_quiet_hours(datetime.fromtimestamp(now, self._tz), self._quiet_hours)
        return sorted(aci for aci, urgent in self._owed.items() if urgent or not quiet)
