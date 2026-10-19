"""Status: what she can do in this install (the capability lines her rules carry and /help shows), the Mac's security
level, the extra status lines, /status, receive problems and her own Signal account's devices. Built by App, which keeps
the delegates the tests and the commands call; every other part of the app is read through `self.app`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from local_assistant import prompts
from local_assistant.commands import VOICE_MODE_KEY, StatusInfo
from local_assistant.config import INTERRUPT_NEW
from local_assistant.documents import shown_folder
from local_assistant.initiative import Settings, evening_of, is_enabled
from local_assistant.part_status import lines as part_lines
from local_assistant.part_status import security_lines
from local_assistant.profiler import LAST_RUN_KEY
from local_assistant.signal_io import ReceiveProblem, SignalError

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name in bridge.log

LINKED_DEVICES_SECONDS = 4 * 3600.0  # her own Signal account's devices are counted at the start and this often
SECURITY_LEVEL_SECONDS = 4 * 3600.0  # the Mac's security level (her list of what she can do) is read as often


class StatusHost:
    """What she says about herself: her list of what she can do, /status, and the checks behind them."""

    def __init__(self, app: App, read_level: Callable[[], str | None]) -> None:
        self.app = app
        self.read_level = read_level
        self.level: str | None = None  # None: not read yet, or off the live Mac

    def capability_lines(self) -> list[str]:
        """What she can do in this install (prompts.capabilities): the parts on, with each level's current value: the
        voice answers (/voice), what interrupts her on a call, the calendars' services (Google, Outlook, iCloud, …), the
        messages she starts (/initiative), the evening note's time while it is on (/evening), the lights' and plugs' brands (TP-Link, Philips Hue), the projects' levels,
        the documents folder and the Mac's security level. Her rules carry these lines and /help shows them word for
        word."""
        cfg = self.app.cfg
        others = [level for level, names in (("docs", cfg.projects_docs), ("code", cfg.projects_code)) if names]
        acis = sorted(cfg.allowed_acis)  # the gate's order: the config's evening goes to the only one
        return prompts.capabilities(
            host=cfg.host_label,
            voice=(self.app.store.get_state(VOICE_MODE_KEY) or "auto") if cfg.voice_enabled else "",
            calls=INTERRUPT_NEW[cfg.calls_barge] if cfg.calls_enabled else "",
            mail=cfg.mail_enabled,
            calendar=[source.service for source in cfg.calendars],
            digest=cfg.digest_time if cfg.mail_enabled or (cfg.calendar_enabled and cfg.calendar_digest) else "",
            digest_calendar=cfg.calendar_enabled and cfg.calendar_digest,
            reminders=cfg.reminders_enabled,
            wake_calls=cfg.reminders_enabled and cfg.wake_calls,
            initiative=is_enabled(self.app.store, cfg.initiative_enabled),
            evening=held.time if (held := evening_of(self.app.store, Settings.of(cfg).evening, acis)) else "",
            device=cfg.tapo_devices[0].name if cfg.tapo_devices else "",
            device_brands=[spec.service for spec in cfg.tapo_devices],
            music=cfg.spotify_enabled,
            web=cfg.web_enabled,
            maps=cfg.maps_enabled,
            projects=cfg.projects_default if cfg.projects_enabled else "",
            project_levels=[level for level in others if level != cfg.projects_default],
            documents=shown_folder(cfg.documents_folder) if cfg.documents_enabled else "",
            chords=cfg.chords_enabled,
            security=self.level,
        )

    async def security_level_loop(self) -> None:
        while True:
            try:
                level = await asyncio.to_thread(self.read_level)
            except Exception:
                log.exception("security level: not read")
            else:
                if level != self.level:
                    log.info("security level: %s", level if level is not None else "not read")
                    self.level = level
                    self.app._settings_changed()
            await asyncio.sleep(SECURITY_LEVEL_SECONDS)

    async def status_lines(self) -> list[str]:
        """Extra lines for mini_status: mail, the calendar, and which devices answer."""
        lines = [self.app.mail_sync.status_line(self.app.cfg.timezone)] if self.app.mail_sync else []
        if self.app.mail_sorter:
            lines.append(self.app.mail_sorter.status_line())
        if self.app.calendar:
            lines.append(await self.app.calendar.status_line())
        if self.app.devices:
            lines.append(await self.app.devices.reachability())
        if self.app.calls:
            next_call = self.app.reminders.next_call() if self.app.reminders else ""
            speech, turn = await self.app.calls.detectors()
            lines.append(self.app.calls.status_line(next_call=next_call, detector=speech, turn=turn))
        if self.app.music:
            lines.append(self.app.music.status_line())
        if self.app.web:
            lines.append(f"Web search: {self.app.web.usage()}")
        if self.app.maps:
            lines.append(f"Maps: {self.app.maps.usage()}")
        return lines

    async def status(self) -> StatusInfo:
        return StatusInfo(
            gemma=await self.app.gemma.status(),
            queue_length=self.app.queue.qsize(),
            last_learning_run=self.app.store.get_state(LAST_RUN_KEY),
            db_bytes=self.app.store.db_size_bytes(),
            messages=self.app.store.count_messages(),
            restarts=self.app.signal.restarts,
            parts=tuple(
                line.line() for line in part_lines(self.app.cfg, self.app.part_status.snapshot(), self.app._clock())
            ),
            security=tuple(
                line.line()
                for line in security_lines(
                    self.app.part_status.security(), self.app._clock(), self.app.cfg.timezone, model=self.app.cfg.model
                )
            ),
            reminders=self.app.reminders.status_line() if self.app.reminders else "",
            initiative=tuple(self.app.initiative.status_lines()) if self.app.initiative else (),
        )

    async def problems_loop(self) -> None:
        async for problem in self.app.signal.problems():
            try:
                self.on_problem(problem)
            except Exception:
                log.exception("a receive problem could not be handled")

    def on_problem(self, problem: ReceiveProblem) -> None:
        """A message signal-cli could not open. From an allowed account with a changed safety number: recorded (doctor
        and /status show it with the compare-then-trust steps), logged without the account."""
        if problem.untrusted_identity and problem.sender in self.app.cfg.allowed_acis:
            self.app._identity_changed.add(problem.sender)
            self.app.part_status.identity_changed()
            log.error(
                "a message from the user was dropped: their safety number changed. Compare it on the phone, then trust "
                "it with signal-cli (setup_assistant.md, 7. Operations, 'Safety number changed')"
            )
        elif problem.untrusted_identity:
            log.info("a message from an account that is not allowed could not be opened (changed safety number)")
        else:
            log.warning("signal-cli could not open a message (%s)", problem.kind)

    async def linked_devices_loop(self) -> None:
        while True:
            try:
                await self.check_linked_devices()
            except Exception:
                log.exception("linked devices: the check failed")
            await asyncio.sleep(LINKED_DEVICES_SECONDS)

    async def check_linked_devices(self) -> int | None:
        """Count her own Signal account's devices (only this Mac is expected): kept in status.json; one note when the
        count goes above this Mac (or up again)."""
        try:
            devices = await self.app.signal.list_devices()
        except SignalError as exc:
            log.warning("linked devices: not counted (%s)", type(exc).__name__)
            return None
        count = len(devices)
        before = self.app.part_status.security().linked_devices
        self.app.part_status.linked_devices(count)
        if count > 1:
            log.error("linked devices: %d devices on her Signal account; only this Mac is expected", count)
            if before is None or count > before:
                await self.app._alert(prompts.LINKED_ALERT.format(count=count))
        return count
