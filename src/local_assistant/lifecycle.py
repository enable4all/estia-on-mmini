"""Start-up and upkeep: the Signal profile's name and picture, recovery after a restart, the welcome, the offline note,
the start-up checks of the reminders and the initiative, the Spotify sign-in reminder, the sweeper, retention and the
heartbeat. Built by App, which keeps the delegates the tests and `process` call; every other part of the app is read
through `self.app`."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import stat
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from local_assistant import prompts
from local_assistant.config import AvatarError, read_avatar
from local_assistant.initiative import ANNOUNCED_KEY
from local_assistant.signal_io import SignalError
from local_assistant.store import Message
from local_assistant.timefmt import iso_to_epoch, local_hhmm

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name in bridge.log

# after signal-cli subscribed (and recovery), then every minute: the next start's offline note, and estia-deploy's health
# check (read-only, as _estia) both read it; its name and timing stay
ALIVE_KEY = "last_alive"
ALIVE_SECONDS = 60
PRUNED_KEY = "privacy:pruned_at"  # when the history was last pruned ([privacy] keep_days)
PRUNE_HOURS = (3, 5)  # pruned once a night, in this local window, when nothing else runs
PRUNE_CHECK_SECONDS = 900.0
MUSIC_REMINDED_KEY = "music:reminded_week"
PROFILE_NAME_KEY = "signal_profile_name"
PROFILE_AVATAR_KEY = "signal_profile_avatar"  # the sha256 of the picture last set
WELCOME_KEY = "welcome_sent"  # set once a fresh install's welcome reached everyone it was owed to
WELCOME_OWED_KEY = "welcome_owed"  # the accounts it has not reached yet (comma-separated ACIs)
# signal-cli also downloads files no photo message refers to (link previews, quote thumbnails, avatars,
# edited messages): every 10 minutes, files older than that which no waiting photo needs are deleted.
SWEEP_SECONDS = 600.0


class Lifecycle:
    """The bridge's start-up and upkeep."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.started_at = 0.0
        self.offline_since: str | None = None  # last heartbeat of the previous run
        self.offline_noted = False

    async def name_profile(self) -> None:
        """The bot's Signal profile shows the assistant's name, set once per name (e.g. after a rename)."""
        name = prompts.signal_profile_name(self.app.cfg.name)
        if self.app.store.get_state(PROFILE_NAME_KEY) == name:
            return
        try:
            await self.app.signal.update_profile(name)
        except SignalError as exc:  # tried again at the next start
            log.warning("Signal profile name not set: %s", exc)
            return
        self.app.store.set_state(PROFILE_NAME_KEY, name)
        log.info("Signal profile name set")

    async def profile_picture(self) -> None:
        """The bot's Signal profile picture (the config's `avatar`), sent once per picture: again only after the file
        changes. The file is checked here, not when the config is loaded: one that is missing, unreadable, too large
        or not a PNG or JPEG is skipped (Signal keeps the picture it has), so a picture never stops the bridge. A
        skip or a failure is logged (never the path) and tried again at the next start."""
        path = self.app.cfg.avatar
        if path is None:
            return
        try:
            data = await asyncio.to_thread(read_avatar, path)
        except AvatarError as exc:
            log.warning("Signal profile picture not set: %s", exc)
            return
        digest = hashlib.sha256(data).hexdigest()
        if self.app.store.get_state(PROFILE_AVATAR_KEY) == digest:
            return
        try:
            await self.app.signal.update_avatar(path)
        except SignalError as exc:  # tried again at the next start; signal-cli's message may name the file
            log.warning("Signal profile picture not set: %s (code %s)", type(exc).__name__, exc.code)
            return
        self.app.store.set_state(PROFILE_AVATAR_KEY, digest)
        log.info("Signal profile picture set")

    def welcome_text(self) -> str:
        cfg = self.app.cfg
        return prompts.welcome(
            name=cfg.name,
            mail=cfg.mail_enabled,
            calendar=cfg.calendar_enabled,
            voice=cfg.voice_enabled,
            calls=cfg.calls_enabled,
            device=cfg.tapo_devices[0].name if cfg.tapo_devices else "",
            web=cfg.web_enabled,
            maps=cfg.maps_enabled,
            music=cfg.spotify_enabled,
            reminders=cfg.reminders_enabled,
            wake_calls=cfg.reminders_enabled and cfg.wake_calls,
            initiative=self.app.initiative.settings.line()
            if self.app.initiative and self.app.initiative.enabled
            else "",
        )

    async def welcome(self) -> None:
        """A fresh install (nothing in the store) greets each allowed user once, in code's words. It is owed to the
        accounts allowed at that first start; one it could not reach is tried again at the next start, even when the
        others chat by then. Only a delivered welcome is stored, as a sent note: never in Gemma's history, nothing for
        recovery to send again. An install that already has messages never gets it. Locked: the next start."""
        if self.app.store.get_state(WELCOME_KEY) or self.app.guard.locked:
            return
        owed = self.app.store.get_state(WELCOME_OWED_KEY)
        if owed is None:
            if self.app.store.count_messages():
                return
            owed = ",".join(sorted(self.app.cfg.allowed_acis))
            self.app.store.set_state(WELCOME_OWED_KEY, owed)
            if (
                self.app.initiative is not None and self.app.initiative.enabled
            ):  # the welcome tells: no announcement later
                self.app.store.set_state(ANNOUNCED_KEY, f"{self.app._clock():.0f} welcome")
        left = [aci for aci in owed.split(",") if aci in self.app.cfg.allowed_acis]  # one no longer allowed is not owed
        text = self.welcome_text()
        conversation = self.app.store.current_conversation()
        for aci in list(left):
            timestamp = await self.app._deliver(aci, text, "the welcome")
            if timestamp is None:
                continue
            left.remove(aci)
            with self.app.store.tx():
                out_id = self.app.store.add_outgoing(conversation.id, aci, "note", text, None)
                self.app.store.mark_sent(out_id, timestamp)
                self.app.store.set_state(WELCOME_OWED_KEY, ",".join(left))
            log.info("welcome sent: %d chars", len(text))
        if not left:
            self.app.store.set_state(WELCOME_KEY, f"{self.app._clock():.0f}")

    async def recover(self) -> None:
        """After a restart: answer recent messages, one offline note for older ones, resend recent replies."""
        now = self.app._clock()
        window = self.app.cfg.recover_window_seconds
        stale: list[Message] = []
        for message in self.app.store.unanswered_incoming():
            if now - (message.signal_ts or 0) / 1000 <= window:
                await self.app.queue.put(message.id)
            else:
                stale.append(message)
        for out in self.app.store.unsent_outgoing():
            if now - iso_to_epoch(out.created_at) <= window:
                await self.app._send(out.id)
        if not stale:
            return
        for message in stale:
            self.app.store.set_status(message.id, "failed")
            self.app._drop_attachments(message.id)
        await self.offline_note(stale[-1])
        log.info("recovery: %d older messages got the offline note", len(stale))

    async def offline_note(self, message: Message) -> None:
        """One note per start: 'I was offline HH:MM–HH:MM, ask again if you still need something.'"""
        if self.offline_noted:
            return
        self.offline_noted = True
        last = self.offline_since
        since = local_hhmm(float(last), self.app.cfg.timezone) if last else "?"
        note = prompts.OFFLINE.format(since=since, until=local_hhmm(self.started_at, self.app.cfg.timezone))
        await self.app._send(
            self.app.store.add_outgoing(message.conversation_id, message.peer_aci, "note", note, message.id)
        )

    def is_stale(self, message: Message) -> bool:
        """Sent before this start and older than the recovery window: missed while the bridge was down."""
        sent_at = (message.signal_ts or 0) / 1000
        return 0 < sent_at < self.started_at and self.app._clock() - sent_at > self.app.cfg.recover_window_seconds

    async def reminders_start_up(self) -> None:
        """Reminders missed while the bridge was down: one late note per user; one that was being sent is marked
        "(may be a repeat)" and its old note is kept from recovery. A failure here never stops the bridge."""
        if self.app.reminder_scheduler is None:
            return
        since = self.offline_since
        try:
            await self.app.reminder_scheduler.start_up(
                offline_since=float(since) if since else None, started_at=self.started_at
            )
        except Exception:
            log.exception("reminders: the start-up check failed")

    def initiative_start_up(self) -> None:
        """A message she started that was being sent at the stop is never sent again (its row: unknown). A failure
        here never stops the bridge."""
        if self.app.initiative is None:
            return
        try:
            self.app.initiative.start_up()
        except Exception:
            log.exception("initiative: the start-up check failed")

    async def music_reminder(self) -> None:
        """Spotify ends the sign-in after 6 months: a note once a week in the last 30 days (daytime only)."""
        assert self.app.music is not None
        while True:
            try:
                note = self.app.music.reminder()
                now = datetime.fromtimestamp(self.app._clock(), self.app.cfg.timezone)
                week = f"{now.isocalendar().year}-{now.isocalendar().week}"
                if note and 9 <= now.hour < 21 and self.app.store.get_state(MUSIC_REMINDED_KEY) != week:
                    self.app.store.set_state(MUSIC_REMINDED_KEY, week)
                    await self.app._alert(note)
            except Exception:  # a TaskGroup task that raises would take the bridge down
                log.exception("music reminder failed")
            await asyncio.sleep(3600)

    def sweep_files(self) -> int:
        """Delete old files nothing waits for: signal-cli's downloads that no unanswered photo or voice note needs
        (with their `<id>.preview` sidecars kept alongside), and leftovers of interrupted photo conversions and
        spoken answers."""
        keep = self.app.store.open_attachment_ids()
        cutoff = self.app._clock() - SWEEP_SECONDS
        removed = 0
        folders: tuple[tuple[Path, set[str]], ...] = (
            (self.app.cfg.signal_attachments_dir, keep),
            (self.app.cfg.data_dir / "photos", set()),
            (self.app.cfg.voice_dir, set()),
        )
        for folder, needed in folders:
            try:
                entries = list(folder.iterdir())
            except FileNotFoundError:
                continue
            except OSError as exc:
                log.warning("could not list %s: %s", folder.name, type(exc).__name__)
                continue
            for path in entries:
                if path.name in needed or any(path.name.startswith(f"{name}.") for name in needed):
                    continue
                try:
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError as exc:
                    log.warning("could not sweep a file: %s", type(exc).__name__)
        if removed:
            log.info("swept %d old file(s) from the attachments and photo folders", removed)
        return removed

    async def sweeper(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            self.sweep_files()

    async def retention_loop(self) -> None:
        while True:
            await asyncio.sleep(PRUNE_CHECK_SECONDS)
            try:
                await self.retention_pass()
            except Exception:
                log.exception("retention: the pass failed")

    async def retention_pass(self) -> bool:
        """With [privacy] keep_days: once a night (03:00-05:00), when nothing else runs, delete the older history, then
        compact the database (it blocks for a moment). Whether it ran."""
        keep = self.app.cfg.keep_days
        if keep is None:
            return False
        now = self.app._clock()
        local = datetime.fromtimestamp(now, self.app.cfg.timezone)
        last = self.app.store.get_state(PRUNED_KEY)
        if last is not None and now - float(last) < 20 * 3600:
            return False
        if not PRUNE_HOURS[0] <= local.hour < PRUNE_HOURS[1] or self.app._is_busy():
            return False
        pruned = self.app.store.prune(keep, now=now)
        self.app.store.compact()
        self.app.store.set_state(PRUNED_KEY, f"{now:.0f}")
        log.info(
            "retention: %d messages, %d pages and %d tool calls older than %d days deleted",
            pruned.messages,
            pruned.documents,
            pruned.tool_calls,
            keep,
        )
        return True

    def mark_alive(self) -> None:
        self.app.store.set_state(ALIVE_KEY, f"{self.app._clock():.0f}")

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(ALIVE_SECONDS)
            self.mark_alive()
