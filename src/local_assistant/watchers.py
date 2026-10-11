"""Watchers: checks that run on a schedule without Gemma (Hermes "no-agent" cron pattern).

A watcher only reports the problems it sees. The scheduler turns that into messages: a new problem
is re-checked after a short wait (a passing hiccup stays silent), confirmed problems are alerted
once, the rhythm speeds up while a problem lasts, and the recovery is reported once. The state is
kept in the database, so a restart of the bridge neither repeats an alert nor loses a recovery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from local_assistant.config import DEFAULT_HOST_LABEL
from local_assistant.store import Store
from local_assistant.timefmt import local_hhmm

log = logging.getLogger(__name__)

Send = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class Problem:
    key: str  # stable id, e.g. "disk"
    label: str  # what is affected, e.g. "Disk"
    detail: str  # what is wrong, e.g. "7% free"


class Watcher(Protocol):
    name: str

    async def check(self) -> list[Problem]: ...


@dataclass
class _Entry:
    watcher: Watcher
    every: float
    confirm_after: float
    next_run: float


class Scheduler:
    def __init__(
        self,
        store: Store,
        send: Send,
        tz: ZoneInfo,
        *,
        clock: Callable[[], float] = time.time,
        poll_seconds: float = 30.0,
        host: str = DEFAULT_HOST_LABEL,
    ) -> None:
        self._host = host  # alerts name the machine as the config does ("the Mac", "the Mini")
        self._store = store
        self._send = send
        self._tz = tz
        self._clock = clock
        self._poll = poll_seconds
        self._entries: list[_Entry] = []

    def add(self, watcher: Watcher, *, every: float, confirm_after: float, first_after: float = 120.0) -> None:
        self._entries.append(_Entry(watcher, every, confirm_after, self._clock() + first_after))

    async def run_forever(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._poll)

    async def tick(self) -> None:
        """Run every watcher that is due and send what it has to say."""
        for entry in self._entries:
            now = self._clock()
            if now < entry.next_run:
                continue
            try:
                await self._run(entry, now)
            except Exception:  # never take the bridge down; try again on the short rhythm
                log.exception("watcher %s: tick failed", entry.watcher.name)
                entry.next_run = now + entry.confirm_after

    async def _run(self, entry: _Entry, now: float) -> None:
        name = entry.watcher.name
        try:
            problems = await entry.watcher.check()
        except Exception as exc:
            log.exception("watcher %s failed", name)
            problems = [Problem(f"{name}:check", f"Check {name}", f"failed ({type(exc).__name__})")]
        messages = self._update(entry, problems, now)
        active = bool(self._load(name))
        entry.next_run = now + (entry.confirm_after if active else entry.every)
        for message in messages:
            await self._send(message)

    def _update(self, entry: _Entry, problems: list[Problem], now: float) -> list[str]:
        name = entry.watcher.name
        state = self._load(name)
        current = {problem.key: problem for problem in problems}
        alerts: list[str] = []
        recovered: list[str] = []
        for key, problem in current.items():
            known = state.get(key)
            if known is None:
                state[key] = {"since": now, "alerted": False, "label": problem.label, "detail": problem.detail}
                continue
            known["label"], known["detail"] = problem.label, problem.detail
            if not known["alerted"] and now - float(known["since"]) >= entry.confirm_after:
                known["alerted"] = True
                since = local_hhmm(float(known["since"]), self._tz)
                alerts.append(f"- {problem.label}: {problem.detail} (since {since})")
        for key in [k for k in state if k not in current]:
            gone = state.pop(key)
            if gone["alerted"]:
                since = local_hhmm(float(gone["since"]), self._tz)
                recovered.append(f"- {gone['label']} (was down {since}–{local_hhmm(now, self._tz)})")
        self._store.set_state(f"watch:{name}", json.dumps(state))
        messages = []
        if alerts:
            messages.append(f"⚠ Problem on {self._host}:\n" + "\n".join(alerts))
            log.warning("watcher %s: alert for %d problem(s)", name, len(alerts))
        if recovered:
            messages.append(f"✓ Back to normal on {self._host}:\n" + "\n".join(recovered))
            log.info("watcher %s: %d problem(s) recovered", name, len(recovered))
        return messages

    def _load(self, name: str) -> dict[str, dict[str, Any]]:
        raw = self._store.get_state(f"watch:{name}")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
