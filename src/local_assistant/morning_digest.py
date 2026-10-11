"""The morning digest (09:15 by default, every day): the calendar on top, then the mail digest.

It owns the schedule, the catch-up until noon after downtime and the once-a-day key (the key the mail
digest used before, so the day of the update never gets a second digest). The calendar part has 30
seconds in all; whatever goes wrong there gives one "not available" line and the mail part goes out as
usual. Without [mail] the digest is calendar-only; without the calendar block it is the mail digest.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from local_assistant.calendar_tools import UNAVAILABLE, CalendarTools
from local_assistant.config import Config
from local_assistant.mail_digest import LAST_DIGEST_KEY, MailDigest
from local_assistant.store import Store

log = logging.getLogger(__name__)

DIGEST_DAY_KEY = "mail:digest_day"  # kept from the mail digest: the day the digest went out
MISSED_UNTIL_HOUR = 12


class MorningDigest:
    def __init__(
        self,
        store: Store,
        config: Config,
        *,
        mail: MailDigest | None,
        calendar: CalendarTools | None,
        send: Callable[[str], Awaitable[None]],
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_seconds: float = 60.0,
        mail_ready: Callable[[], bool] = lambda: True,
        calendar_seconds: float = 30.0,
    ) -> None:
        self._store = store
        self._tz = config.timezone
        hour, minute = config.digest_time.split(":")
        self._time = (int(hour), int(minute))
        self._mail = mail
        self._calendar = calendar
        self._send = send
        self._clock = clock
        self._sleep = sleep
        self._poll = poll_seconds
        self._mail_ready = mail_ready  # the mail sync finished a round since the start (else it misses mail)
        self._calendar_seconds = calendar_seconds

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("morning digest failed; trying again in a minute")
            await self._sleep(self._poll)

    async def tick(self) -> None:
        """Send today's digest once, at its time; a digest missed by downtime is sent until noon."""
        now = self._clock()
        local = datetime.fromtimestamp(now, self._tz)
        today = local.date().isoformat()
        if self._store.get_state(DIGEST_DAY_KEY) == today or (local.hour, local.minute) < self._time:
            return
        if self._mail is not None and not self._mail_ready():
            return  # after a start: wait for the first mail sync round (still until noon)
        if local.hour >= MISSED_UNTIL_HOUR:
            self._store.set_state(DIGEST_DAY_KEY, today)
            log.info("morning digest of %s skipped: the bridge was down until after noon", today)
            return
        text = await self.compose()  # a failure here is tried again next minute (until noon)
        self._store.set_state(DIGEST_DAY_KEY, today)
        if self._mail is not None:
            self._store.set_state(LAST_DIGEST_KEY, f"{now:.0f}")
        await self._send(text)
        log.info("morning digest sent")

    async def compose(self) -> str:
        if self._calendar is None:
            return self._mail.compose() if self._mail is not None else ""
        mail = self._mail.compose(header="Mail:") if self._mail is not None else None  # first: its actions end now
        local = datetime.fromtimestamp(self._clock(), self._tz)
        parts = [f"{local:%a} {local.day} {local:%b}", await self._calendar_block()]
        return "\n".join([*parts, mail] if mail is not None else parts)

    async def _calendar_block(self) -> str:
        assert self._calendar is not None
        try:
            return await asyncio.wait_for(self._calendar.digest_block(), self._calendar_seconds)
        except TimeoutError:
            log.warning("morning digest: the calendar took longer than %.0f s", self._calendar_seconds)
        except Exception:
            log.exception("morning digest: the calendar block failed")
        return UNAVAILABLE
