"""Watchers: checks that run on a schedule without Gemma (Hermes "no-agent" cron pattern).

A watcher only reports the problems it sees. The scheduler turns that into messages: a new problem
is re-checked after a short wait (a passing hiccup stays silent), confirmed problems are alerted
once, the rhythm speeds up while a problem lasts, and the recovery is reported once. The state is
kept in the database, so a restart of the bridge neither repeats an alert nor loses a recovery.

In quiet hours (`quiet_hours`: the initiative's, 22:00-08:00 by default) nothing is sent: an alert and a "back to normal"
wait for their end, and a problem confirmed and gone within them becomes one line in the morning ("Overnight on the
Mac, already back to normal"). The checks keep their rhythm. Security notes (a new linked device, a changed safety
number, the model paused or a cloud model) never come through here: they go at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from local_assistant.config import DEFAULT_HOST_LABEL
from local_assistant.initiative import in_quiet_hours
from local_assistant.store import Store
from local_assistant.timefmt import local_hhmm

log = logging.getLogger(__name__)

Send = Callable[[str], Awaitable[None]]
MORNING_KEY = "watch:morning"  # what quiet hours held back: recoveries, and problems that came and went


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
        quiet_hours: tuple[int, int] | None = None,
    ) -> None:
        self._host = host  # alerts name the machine as the config does ("the Mac", "the Mini")
        self._quiet_hours = quiet_hours  # minutes of the day (start, end): nothing is sent then
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

    def _quiet(self, now: float) -> bool:
        return in_quiet_hours(datetime.fromtimestamp(now, self._tz), self._quiet_hours)

    async def tick(self) -> None:
        """Run every watcher that is due and send what it has to say; after quiet hours, first what they held."""
        if not self._quiet(self._clock()):
            try:
                await self._morning()
            except Exception:
                log.exception("watchers: what the quiet hours held could not be sent; trying again")
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
        messages = self._update(entry, problems, now, quiet=self._quiet(now))
        active = bool(self._load(name))
        entry.next_run = now + (entry.confirm_after if active else entry.every)
        for message in messages:
            await self._send(message)

    def _update(self, entry: _Entry, problems: list[Problem], now: float, *, quiet: bool = False) -> list[str]:
        """The state after this check, and what to send now; in quiet hours nothing: a new alert is marked `held`
        (told at their end), a recovery waits in MORNING_KEY, and a held problem that is gone is one line there."""
        name = entry.watcher.name
        state = self._load(name)
        current = {problem.key: problem for problem in problems}
        alerts: list[str] = []
        recovered: list[str] = []
        morning = self._load_morning()
        for key, problem in current.items():
            known = state.get(key)
            if known is None:
                state[key] = {"since": now, "alerted": False, "label": problem.label, "detail": problem.detail}
                continue
            known["label"], known["detail"] = problem.label, problem.detail
            if not known["alerted"] and now - float(known["since"]) >= entry.confirm_after:
                known["alerted"] = True
                if quiet:
                    known["held"] = True  # told when the quiet hours end
                else:
                    alerts.append(self._alert_line(known))
        for key in [k for k in state if k not in current]:
            gone = state.pop(key)
            if not gone["alerted"]:
                continue
            since, until = local_hhmm(float(gone["since"]), self._tz), local_hhmm(now, self._tz)
            if gone.get("held"):  # never told: one line in the morning, with what it was
                morning["cleared"].append(f"- {gone['label']}: {gone['detail']} ({since}–{until})")
            elif quiet:
                morning["recovered"].append(f"- {gone['label']} (was down {since}–{until})")
            else:
                recovered.append(f"- {gone['label']} (was down {since}–{until})")
        self._store.set_state(f"watch:{name}", json.dumps(state))
        self._store.set_state(MORNING_KEY, json.dumps(morning))
        held, lines = sum(1 for known in state.values() if known.get("held")), sum(map(len, morning.values()))
        if quiet and (held or lines):
            log.info("watcher %s: quiet hours, %d alert(s) and %d line(s) held", name, held, lines)
        return self._messages(name, alerts, recovered)

    def _alert_line(self, known: dict[str, Any]) -> str:
        return f"- {known['label']}: {known['detail']} (since {local_hhmm(float(known['since']), self._tz)})"

    def _messages(
        self, name: str, alerts: Sequence[str], recovered: Sequence[str], cleared: Sequence[str] = ()
    ) -> list[str]:
        messages = []
        if alerts:
            messages.append(f"⚠ **Problem on {self._host}:**\n" + "\n".join(alerts))
            log.warning("watcher %s: alert for %d problem(s)", name, len(alerts))
        if recovered:
            messages.append(f"✓ **Back to normal on {self._host}:**\n" + "\n".join(recovered))
            log.info("watcher %s: %d problem(s) recovered", name, len(recovered))
        if cleared:
            messages.append(f"✓ **Overnight on {self._host}, already back to normal:**\n" + "\n".join(cleared))
            log.info("watchers: %d problem(s) came and went in quiet hours", len(cleared))
        return messages

    async def _morning(self) -> None:
        """After quiet hours: the alerts they held (problems still there), the recoveries and the problems that came
        and went, each kind in one message. Each kind is cleared only once its message went out: a send that raises
        leaves the rest for the next tick, and nothing sent is sent again."""
        names = [entry.watcher.name for entry in self._entries]
        alerts = [self._alert_line(known) for name in names for known in self._load(name).values() if known.get("held")]
        for message in self._messages("morning", alerts, ()):
            await self._send(message)
        for name in names:  # sent: nothing is held any more
            state = self._load(name)
            if [known for known in state.values() if known.pop("held", None)]:  # every flag popped
                self._store.set_state(f"watch:{name}", json.dumps(state))
        for kind in ("recovered", "cleared"):
            lines = self._load_morning()[kind]
            if not lines:
                continue
            recovered, cleared = (lines, []) if kind == "recovered" else ([], lines)
            for message in self._messages("morning", (), recovered, cleared):
                await self._send(message)
            morning = self._load_morning()
            morning[kind] = []
            self._store.set_state(MORNING_KEY, json.dumps(morning))

    def _load_morning(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self._store.get_state(MORNING_KEY) or "{}")
        except json.JSONDecodeError:
            data = {}
        data = data if isinstance(data, dict) else {}
        return {
            kind: [str(line) for line in data.get(kind, []) if isinstance(line, str)]
            if isinstance(data.get(kind), list)
            else []
            for kind in ("recovered", "cleared")
        }

    def _load(self, name: str) -> dict[str, dict[str, Any]]:
        raw = self._store.get_state(f"watch:{name}")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
