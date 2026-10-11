"""The bridge: Signal in, Gemma out. Wires every part together and runs them (spec section 3).

Messages are handled one at a time (one Gemma slot). Logs carry metadata only, never message text or
image data. Step 3 adds photos (fetched, converted, shown to Gemma once, then deleted), the Mac
health watcher (alerts as notes) and the project tools; voice notes are heard and answered through the
local voice service (transcript in, spoken answer out, audio deleted).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import logging
import logging.handlers
import signal as os_signal
import stat
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from local_assistant import prompts
from local_assistant.brain import Brain
from local_assistant.calendar_feed import CalendarFeed
from local_assistant.calendar_graph import OutlookCalendars
from local_assistant.calendar_tools import CalendarTools
from local_assistant.calls import CallManager, CallSettings, Turn, speakable, whole_sentences
from local_assistant.commands import (
    VOICE_ECHO_KEY,
    VOICE_MODE_KEY,
    VOICE_NAME_KEY,
    VOICES,
    Commands,
    StatusInfo,
)
from local_assistant.config import (
    DEFAULT_CONFIG_PATH,
    AvatarError,
    Config,
    ConfigError,
    find_java_home,
    load_config,
    read_avatar,
)
from local_assistant.context import ContextBuilder
from local_assistant.devices import Devices
from local_assistant.fetcher import Fetcher, FetchError, Page
from local_assistant.gate import LONG_TEXT, AttachmentRef, Decision, Kind, classify, sender_aci
from local_assistant.gemma import GemmaClient, GemmaError, OllamaGemma
from local_assistant.graph import Graph
from local_assistant.health import Health, HealthCheck
from local_assistant.language import CODES, ENGLISH, LANGUAGE_KEY
from local_assistant.mail_auth import AccessTokens, MailAuthError, TokenStore, device_login
from local_assistant.mail_digest import MailDigest, MailNumbers
from local_assistant.mail_http import http
from local_assistant.mail_prefilter import Jev
from local_assistant.mail_sort import MailSorter
from local_assistant.mail_sync import MailSync
from local_assistant.mail_tools import MailTools
from local_assistant.maps import Maps, find_pin
from local_assistant.morning_digest import MorningDigest
from local_assistant.music_tools import MusicTools
from local_assistant.part_status import PartStatus
from local_assistant.part_status import lines as part_lines
from local_assistant.photos import PhotoError, data_uri, prepare
from local_assistant.profile import Profile
from local_assistant.profiler import LAST_RUN_KEY, UNANNOUNCED_KEY, LearnError, Profiler
from local_assistant.projects import Projects
from local_assistant.signal_io import (
    NotAllowedError,
    SignalClient,
    SignalError,
    UntrustedIdentityError,
    call_env,
    signal_cli_command,
)
from local_assistant.spotify import Spotify
from local_assistant.spotify_auth import SpotifyAuthError, SpotifyTokens, login
from local_assistant.store import Attachment, Message, MessageKind, MessageStatus, Store
from local_assistant.timefmt import iso_to_epoch, local_hhmm
from local_assistant.tools import Tools, tool_schemas
from local_assistant.voice import (
    VoiceClient,
    VoiceError,
    VoiceTooLongError,
    VoiceUnavailableError,
    sniff,
    spoken_text,
    to_m4a,
)
from local_assistant.watchers import Scheduler
from local_assistant.web_search import WebSearch

log = logging.getLogger("local_assistant")

ALIVE_KEY = "last_alive"
MUSIC_REMINDED_KEY = "music:reminded_week"
PROFILE_NAME_KEY = "signal_profile_name"
PROFILE_AVATAR_KEY = "signal_profile_avatar"  # the sha256 of the picture last set
WELCOME_KEY = "welcome_sent"  # set once a fresh install's welcome reached everyone it was owed to
WELCOME_OWED_KEY = "welcome_owed"  # the accounts it has not reached yet (comma-separated ACIs)
# during a call these wait: they change the conversation or the language, or use Gemma
WAITING_COMMANDS = frozenset({"/new", "/learn", "/language"})
CALL_MAX_TOKENS = 300  # spoken answers stay short (a few sentences); a cut-off one ends at its last whole sentence
# signal-cli also downloads files no photo message refers to (link previews, quote thumbnails, avatars,
# edited messages): every 10 minutes, files older than that which no waiting photo needs are deleted.
SWEEP_SECONDS = 600.0
# shorter than this is a slip of the thumb, not speech (OpenClaw's minimum)
MIN_AUDIO_BYTES = 1024
# a photo message is stored as text "[photo] caption", a voice message as "[voice] transcript";
# their files are in the attachments table
_KINDS: dict[Kind, MessageKind] = {
    Kind.TEXT: "text",
    Kind.PHOTO: "text",
    Kind.VOICE: "text",
    Kind.COMMAND: "command",
    Kind.UNSUPPORTED: "unsupported",
}


class App:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        signal: SignalClient,
        gemma: GemmaClient,
        fetcher: Fetcher,
        *,
        clock: Callable[[], float] = time.time,
        retry_delay: float = 2.0,
        fetch_timeout: float = 45.0,
        health: HealthCheck | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.signal = signal
        self.gemma = gemma
        self._fetcher = fetcher
        self._clock = clock
        self._retry_delay = retry_delay
        self._fetch_timeout = fetch_timeout
        self._processing = False
        self._started_at = 0.0
        self._offline_since: str | None = None  # last heartbeat of the previous run
        self._offline_noted = False
        # the last photo, in memory only (its files are deleted), for follow-up questions: (conversation, message, images, at)
        self._recent_photo: tuple[int, int, list[str], float] | None = None
        self.profile = Profile(store, max_chars=cfg.profile_max_chars, fact_max_chars=cfg.fact_max_chars)
        # one language at a time: the config's is the starting value, a typed /language (kept in the database) wins
        chosen_language = store.get_state(LANGUAGE_KEY)
        self.language = chosen_language if chosen_language in CODES else cfg.language
        self.context = ContextBuilder(
            store,
            self.profile,
            cfg.timezone,
            window_tokens=cfg.window_tokens,
            emergency_tokens=cfg.emergency_tokens,
            rules=self._rules(self.language),
            soul_path=cfg.soul_path,
            name=cfg.name,
        )
        # what each part's last real read or call did, for /status and doctor (<data_dir>/status.json)
        self.part_status = PartStatus(cfg.data_dir / "status.json", clock=clock)
        # mail (read-only Graph): sync, sorting at idle time, the digest's mail part, Gemma's mail tools
        self.mail_sync: MailSync | None = None
        self.mail_sorter: MailSorter | None = None
        self.mail_digest: MailDigest | None = None
        mail_tools: MailTools | None = None
        if cfg.mail_enabled:
            tokens = TokenStore(cfg.mail_token_path)
            graph = Graph(AccessTokens(cfg, tokens))
            numbers = MailNumbers(store, cfg.timezone, clock)
            self.mail_sync = MailSync(
                store, graph, cfg, tokens, notify=self._alert, clock=clock, status=self.part_status
            )
            jev = Jev(cfg.mail_jev_url) if cfg.mail_jev_url else None  # the pre-filter's second step
            self.mail_sorter = MailSorter(store, graph, gemma, cfg, busy=self._is_busy, jev=jev)
            self.mail_digest = MailDigest(store, cfg, numbers, clock=clock)
            mail_tools = MailTools(store, graph, cfg, numbers, self.mail_digest, clock=clock)
        # the calendars: Google iCal addresses (tools/ical) and Outlook calendars of the mailboxes (their own Graph,
        # Calendars.Read only); the digest's block and Gemma's calendar tools
        self.calendar: CalendarTools | None = None
        if cfg.calendar_enabled:
            outlook = (
                OutlookCalendars(cfg, status=self.part_status, clock=clock)
                if any(source.mailbox for source in cfg.calendars)
                else None
            )
            feed = CalendarFeed(cfg, status=self.part_status, outlook=outlook.reader if outlook else None)
            self.calendar = CalendarTools(feed, cfg, notify=self._alert, clock=clock)
        # the morning digest (09:15): calendar on top, then mail; none without either
        digest_calendar = self.calendar if cfg.calendar_digest else None
        self.morning_digest: MorningDigest | None = None
        if self.mail_digest or digest_calendar:
            self.morning_digest = MorningDigest(
                store,
                cfg,
                mail=self.mail_digest,
                calendar=digest_calendar,
                send=self._alert,
                clock=clock,
                mail_ready=self._mail_synced,
                calendar_seconds=cfg.calendar_helper_timeout + 5,
            )
        # Spotify by text message (the official Web API, the user's own app); notes and the sign-in reminder are code
        self.music: MusicTools | None = None
        if cfg.spotify_enabled:
            spotify_tokens = SpotifyTokens(cfg.spotify_token_path, cfg.spotify_client_id)
            self.music = MusicTools(Spotify(spotify_tokens, status=self.part_status), spotify_tokens, cfg)
        # web search (Brave, Ollama as backup); results are never stored, their links are a code-written note
        self.web: WebSearch | None = None
        if cfg.web_enabled:
            self.web = WebSearch(
                http,
                cfg.web_keys_path,
                store,
                country=cfg.web_country,
                brave_cap=cfg.web_brave_monthly_cap,
                fetch=self._fetch,  # Brave's top page, through the guarded fetcher
                status=self.part_status,
            )
        # places and routes (Google); "where the user is" comes from their last Signal location pin, else home
        self.maps: Maps | None = None
        if cfg.maps_enabled:
            self.maps = Maps(
                http,
                cfg.web_keys_path,
                store,
                home=cfg.maps_home,
                daily_cap=cfg.maps_daily_cap,
                tz=cfg.timezone,
                region=cfg.maps_region,
                status=self.part_status,
            )
        # lights and plugs (through the device helper); "ask first" confirmations and notes are code
        self.devices = Devices(cfg, status=self.part_status) if cfg.tapo_enabled else None
        self.voice = (
            VoiceClient(
                cfg.voice_url,
                voice=cfg.voice_name,
                speed=cfg.voice_speed,
                status=self.part_status,
                language=self.language,
            )
            if cfg.voice_enabled
            else None
        )
        # voice calls (the JVM signal-cli and our call helper): calls.py talks, this app is its Host (turns, notes)
        self._no_call = asyncio.Event()
        self._no_call.set()
        self._background: set[asyncio.Task[None]] = set()  # commands answered during a call
        self._worker_idle = asyncio.Event()  # no text being answered (a call turn waits for it)
        self._worker_idle.set()
        self.calls: CallManager | None = None
        self._call_voice: VoiceClient | None = None
        if cfg.calls_enabled:
            self._call_voice = VoiceClient(
                cfg.voice_url,
                voice=cfg.voice_name,
                speed=cfg.calls_speed,
                status=self.part_status,
                language=self.language,
            )
            self.calls = CallManager(
                signal,
                self._call_voice,
                self,
                cfg.allowed_acis,
                CallSettings(
                    socket_dir=cfg.call_socket_dir,
                    end_of_turn_s=cfg.calls_end_of_turn_seconds,
                    idle_prompt_s=cfg.calls_idle_prompt_seconds,
                    idle_hangup_s=cfg.calls_idle_hangup_seconds,
                    max_s=cfg.calls_max_minutes * 60,
                    summary=cfg.calls_summary,
                    fillers=cfg.calls_fillers,
                    acknowledge=cfg.calls_acknowledge,
                    answer_after_s=cfg.calls_answer_after_seconds,
                ),
                tz=cfg.timezone,
                language=self.language,
                name=cfg.name,
            )
        self.health = health or Health(
            store,
            cfg,
            signal_restarts=lambda: self.signal.restarts,
            clock=clock,
            extra_lines=self._status_lines,
        )
        self.tools = Tools(
            store,
            self.profile,
            cfg.timezone,
            self._fetch,
            status=self.health.report,
            projects=Projects(cfg),
            mail=mail_tools,
            devices=self.devices,
            calendar=self.calendar,
            music=self.music,
            calls=cfg.calls_enabled,
            web=self.web,
            maps=self.maps,
            base_schemas=tool_schemas(
                host_label=cfg.host_label,
                checks=[check.label for check in cfg.health_extra_checks],
                projects_root=cfg.projects_root,
            ),
            name=cfg.name,
        )
        # Hermes "no-agent" watchers: checks without Gemma, alerts as notes to the allowed user
        self.scheduler = Scheduler(store, self._alert, cfg.timezone, clock=clock, host=cfg.host_label)
        self.scheduler.add(
            self.health, every=cfg.health_interval_hours * 3600, confirm_after=cfg.health_confirm_minutes * 60
        )
        self.brain = Brain(
            gemma,
            self.context,
            self.tools,
            max_tool_calls=cfg.max_tool_calls,
            learn_tool_calls=cfg.learn_tool_calls,
            reply_max_tokens=cfg.reply_max_tokens,
        )
        self.profiler = Profiler(
            store,
            self.profile,
            self.context,
            gemma,
            cfg.timezone,
            idle_seconds=cfg.idle_learn_seconds,
            soon_seconds=cfg.soon_learn_seconds,
            window_tokens=cfg.window_tokens,
            summary_tokens=cfg.summary_tokens,
            busy=self._is_busy,
        )
        self.tools.set_consolidate(self.profiler.consolidate_for)
        self.tools.set_learn_page(self.profiler.learn_page)
        voice_client = self.voice or self._call_voice  # the same service: the one asked whether it has a language
        self.commands = Commands(
            store,
            self.profile,
            self.context,
            cfg.timezone,
            self.status,
            self.profiler.request_soon,
            learn=self._learn_command,
            voice=self.voice is not None,
            set_voice=self._set_voice,
            language=self.language,
            set_language=self._set_language,
            voice_languages=voice_client.languages if voice_client else None,
        )
        chosen = store.get_state(VOICE_NAME_KEY)
        if chosen in VOICES:
            self._set_voice(chosen)
        self.queue: asyncio.Queue[int] = asyncio.Queue()

    async def run(self) -> None:
        self._started_at = self._clock()
        self._offline_since = self.store.get_state(ALIVE_KEY)
        await self.signal.start()
        await self._name_profile()
        await self._profile_picture()
        self.context.refresh_snapshot()
        await self.recover()
        await self._welcome()
        self.sweep_files()
        self._mark_alive()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._receive_loop(), name="receive")
            group.create_task(self._worker(), name="worker")
            group.create_task(self.profiler.run_forever(), name="profiler")
            group.create_task(self._heartbeat(), name="heartbeat")
            group.create_task(self.scheduler.run_forever(), name="watchers")
            group.create_task(self._sweeper(), name="sweeper")
            group.create_task(self._warm_up(self.language), name="voice-warm-up")
            if self.mail_sync and self.mail_sorter:
                group.create_task(self.mail_sync.run_forever(), name="mail-sync")
                group.create_task(self.mail_sorter.run_forever(), name="mail-sort")
            if self.morning_digest:
                group.create_task(self.morning_digest.run_forever(), name="morning-digest")
            if self.calls:
                group.create_task(self._calls_forever(), name="calls")
            if self.music:
                group.create_task(self._music_reminder(), name="music-reminder")

    def _rules(self, language: str) -> str:
        cfg = self.cfg
        return prompts.rules(
            name=cfg.name,
            host=cfg.host_label,
            calendar=cfg.calendar_enabled,
            calls=cfg.calls_enabled,
            music=cfg.spotify_enabled,
            web=cfg.web_enabled,
            maps=cfg.maps_enabled,
            language=language,
        )

    def _set_language(self, code: str) -> None:
        """A language chosen with /language (typed, never during a call): Gemma's rules and the voice from the next
        answer; the new language's voice is loaded in the background."""
        self.language = code
        self.context.set_rules(self._rules(code))
        for client in (self.voice, self._call_voice):
            if client is not None:
                client.set_language(code)
        if self.calls is not None:
            self.calls.language = code  # from the next call (/language waits while one runs)
        log.info("language switched to %s", code)
        self._background.add(task := asyncio.create_task(self._warm_up(code)))
        task.add_done_callback(self._background.discard)

    async def _warm_up(self, language: str) -> None:
        """One short sentence in a language other than English, thrown away: its voice loads on first use (about a
        minute the very first time). Failures are only logged (metadata)."""
        client = self.voice or self._call_voice
        if client is None or language == ENGLISH:
            return
        try:
            installed = await client.languages()
            if installed is None:
                log.warning("voice warm-up (%s) skipped: the voice service is not answering", language)
                return
            if language not in installed:
                log.warning(
                    "voice warm-up (%s) skipped: the voice service is older than the language feature "
                    "(scripts/install-voice-daemon.sh)",
                    language,
                )
                return
            started = time.monotonic()
            await client.speak(prompts.CALL_SPOKEN[language].now_speaking, language=language)
            log.info("voice warmed up for %s in %.1f s", language, time.monotonic() - started)
        except Exception as exc:  # a TaskGroup task that raises would take the bridge down
            log.warning("voice warm-up (%s) failed: %s", language, type(exc).__name__)

    def _set_voice(self, name: str) -> None:
        """A voice chosen with /voice name: voice notes and calls use it from the next answer."""
        for client in (self.voice, self._call_voice):
            if client is not None:
                client.set_voice(name)

    async def _name_profile(self) -> None:
        """The bot's Signal profile shows the assistant's name, set once per name (e.g. after a rename)."""
        name = prompts.signal_profile_name(self.cfg.name)
        if self.store.get_state(PROFILE_NAME_KEY) == name:
            return
        try:
            await self.signal.update_profile(name)
        except SignalError as exc:  # tried again at the next start
            log.warning("Signal profile name not set: %s", exc)
            return
        self.store.set_state(PROFILE_NAME_KEY, name)
        log.info("Signal profile name set")

    async def _profile_picture(self) -> None:
        """The bot's Signal profile picture (the config's `avatar`), sent once per picture: again only after the file
        changes. The file is checked here, not when the config is loaded: one that is missing, unreadable, too large
        or not a PNG or JPEG is skipped (Signal keeps the picture it has), so a picture never stops the bridge. A
        skip or a failure is logged (never the path) and tried again at the next start."""
        path = self.cfg.avatar
        if path is None:
            return
        try:
            data = await asyncio.to_thread(read_avatar, path)
        except AvatarError as exc:
            log.warning("Signal profile picture not set: %s", exc)
            return
        digest = hashlib.sha256(data).hexdigest()
        if self.store.get_state(PROFILE_AVATAR_KEY) == digest:
            return
        try:
            await self.signal.update_avatar(path)
        except SignalError as exc:  # tried again at the next start; signal-cli's message may name the file
            log.warning("Signal profile picture not set: %s (code %s)", type(exc).__name__, exc.code)
            return
        self.store.set_state(PROFILE_AVATAR_KEY, digest)
        log.info("Signal profile picture set")

    def _welcome_text(self) -> str:
        cfg = self.cfg
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
        )

    async def _welcome(self) -> None:
        """A fresh install (nothing in the store) greets each allowed user once, in code's words. It is owed to the
        accounts allowed at that first start; one it could not reach is tried again at the next start, even when the
        others chat by then. Only a delivered welcome is stored, as a sent note: never in Gemma's history, nothing for
        recovery to send again. An install that already has messages never gets it."""
        if self.store.get_state(WELCOME_KEY):
            return
        owed = self.store.get_state(WELCOME_OWED_KEY)
        if owed is None:
            if self.store.count_messages():
                return
            owed = ",".join(sorted(self.cfg.allowed_acis))
            self.store.set_state(WELCOME_OWED_KEY, owed)
        left = [aci for aci in owed.split(",") if aci in self.cfg.allowed_acis]  # one no longer allowed is not owed
        text = self._welcome_text()
        conversation = self.store.current_conversation()
        for aci in list(left):
            timestamp = await self._deliver(aci, text, "the welcome")
            if timestamp is None:
                continue
            left.remove(aci)
            with self.store.tx():
                out_id = self.store.add_outgoing(conversation.id, aci, "note", text, None)
                self.store.mark_sent(out_id, timestamp)
                self.store.set_state(WELCOME_OWED_KEY, ",".join(left))
            log.info("welcome sent: %d chars", len(text))
        if not left:
            self.store.set_state(WELCOME_KEY, f"{self._clock():.0f}")

    async def recover(self) -> None:
        """After a restart: answer recent messages, one offline note for older ones, resend recent replies."""
        now = self._clock()
        window = self.cfg.recover_window_seconds
        stale: list[Message] = []
        for message in self.store.unanswered_incoming():
            if now - (message.signal_ts or 0) / 1000 <= window:
                await self.queue.put(message.id)
            else:
                stale.append(message)
        for out in self.store.unsent_outgoing():
            if now - iso_to_epoch(out.created_at) <= window:
                await self._send(out.id)
        if not stale:
            return
        for message in stale:
            self.store.set_status(message.id, "failed")
            self._drop_attachments(message.id)
        await self._offline_note(stale[-1])
        log.info("recovery: %d older messages got the offline note", len(stale))

    async def _offline_note(self, message: Message) -> None:
        """One note per start: 'I was offline HH:MM–HH:MM, ask again if you still need something.'"""
        if self._offline_noted:
            return
        self._offline_noted = True
        last = self._offline_since
        since = local_hhmm(float(last), self.cfg.timezone) if last else "?"
        note = prompts.OFFLINE.format(since=since, until=local_hhmm(self._started_at, self.cfg.timezone))
        await self._send(self.store.add_outgoing(message.conversation_id, message.peer_aci, "note", note, message.id))

    def _is_busy(self) -> bool:
        return self._processing or not self.queue.empty() or not self._no_call.is_set()

    # voice calls: the Host side of calls.CallManager

    async def _music_reminder(self) -> None:
        """Spotify ends the sign-in after 6 months: a note once a week in the last 30 days (daytime only)."""
        assert self.music is not None
        while True:
            try:
                note = self.music.reminder()
                now = datetime.fromtimestamp(self._clock(), self.cfg.timezone)
                week = f"{now.isocalendar().year}-{now.isocalendar().week}"
                if note and 9 <= now.hour < 21 and self.store.get_state(MUSIC_REMINDED_KEY) != week:
                    self.store.set_state(MUSIC_REMINDED_KEY, week)
                    await self._alert(note)
            except Exception:  # a TaskGroup task that raises would take the bridge down
                log.exception("music reminder failed")
            await asyncio.sleep(3600)

    async def _calls_forever(self) -> None:
        """The call loop never takes the bridge down (a TaskGroup task that raises would)."""
        assert self.calls is not None
        while True:
            try:
                await self.calls.run_forever()
            except Exception:
                log.exception("calls: the call loop failed; starting it again")
            await asyncio.sleep(5)

    def call_started(self, aci: str) -> None:
        """Texts wait until the call ends; idle learning and mail sorting keep away from Gemma."""
        self._no_call.clear()
        self.profiler.note_activity()
        if self.devices:
            self.devices.cancel()

    def call_ended(self) -> None:
        if self.devices:
            self.devices.cancel()  # a question asked on the call is not answered by a later text
        self._no_call.set()
        self.profiler.note_activity()

    async def call_turn(self, aci: str, text: str, on_tools: Callable[[], None]) -> Turn:
        """One thing the user said on a call: stored like a message (answered at once), then a waiting device's
        yes (code) or Gemma with the same tools as text; device notes and profile notes go out as texts."""
        self.profiler.note_activity()
        await self._worker_idle.wait()  # a text that was being answered when the call came in finishes first
        conversation_id = self.store.current_conversation().id
        said = f"{prompts.CALL_MARK} {text}"
        said_id = self.store.add_call_turn(conversation_id, aci, said)
        if self.devices is not None and self.devices.pending:
            confirmed = await self.devices.confirm(said, aci)
            if confirmed is not None:
                await self._call_notes(aci, conversation_id, said_id, [confirmed])
                return Turn("", code_text=speakable(confirmed))  # code's words (English)
        message = self.store.message(said_id)
        conversation = self.context.enforce_limit(self.store.conversation(conversation_id))
        try:
            reply = await self.brain.answer(conversation, message, max_tokens=CALL_MAX_TOKENS, on_tools=on_tools)
        except GemmaError as exc:
            log.error("call turn: Gemma failed: %s", exc)
            await self.call_drop_waiting(aci)  # the user never heard a question; a device that switched is reported
            return Turn(prompts.call_lines(self.language, name=self.cfg.name).lost)
        said_text = whole_sentences(reply.text)  # cut off at the token limit: never stop mid-sentence
        asks_yes = bool(self.devices and self.devices.pending)
        question = ""
        if asks_yes and self.devices is not None:  # the caller hears exactly what a yes will do (the note is a text)
            question = speakable(self.devices.question())
            if self.language != ENGLISH:  # Gemma may claim it is done already: only code's question is said and kept
                said_text = ""
        reply_id = self.store.add_call_reply(conversation_id, aci, f"{said_text} {question}".strip(), said_id)
        device_note = self.devices.take_note() if self.devices else ""
        notes = [note for note in (reply.sources, device_note, reply.notice) if note]
        await self._call_notes(aci, conversation_id, said_id, notes)
        log.info("call turn %d answered: %d tool calls", said_id, reply.tool_calls)
        return Turn(said_text, reply_id=reply_id, asks_yes=asks_yes, end_call=reply.end_call, code_text=question)

    async def call_drop_waiting(self, aci: str) -> None:
        """Drop a device action waiting for a yes; what did switch in the meantime is still a note (as in text)."""
        if self.devices is None:
            return
        self.devices.cancel()
        note = self.devices.take_note()
        if note:
            conversation_id = self.store.current_conversation().id
            await self._call_notes(aci, conversation_id, None, [note])

    def call_interrupted(self, reply_id: int, heard: str) -> None:
        """The user talked over the answer: history keeps what they heard of it."""
        self.store.set_message_text(reply_id, heard)

    async def call_note(self, aci: str, text: str) -> None:
        conversation_id = self.store.current_conversation().id
        await self._send(self.store.add_outgoing(conversation_id, aci, "note", text, None))

    async def call_summary(self, aci: str, lines: list[str], minutes: int) -> None:
        """A short summary of the call as a note (kept out of Gemma's history)."""
        transcript = "\n".join(lines)[-12_000:]
        messages = [
            {"role": "system", "content": prompts.CALL_SUMMARY_RULES.format(name=self.cfg.name)},
            {"role": "user", "content": transcript},
        ]
        try:
            completion = await self.gemma.chat(messages, max_tokens=300)
        except GemmaError as exc:
            log.warning("call summary: Gemma failed: %s", exc)
            return
        summary = completion.content.strip()
        if summary:
            await self.call_note(aci, prompts.CALL_SUMMARY.format(minutes=minutes, summary=summary))

    async def _call_notes(self, aci: str, conversation_id: int, reply_to: int | None, notes: list[str]) -> None:
        for note in notes:
            await self._send(self.store.add_outgoing(conversation_id, aci, "note", note, reply_to))

    def _is_stale(self, message: Message) -> bool:
        """Sent before this start and older than the recovery window: missed while the bridge was down."""
        sent_at = (message.signal_ts or 0) / 1000
        return 0 < sent_at < self._started_at and self._clock() - sent_at > self.cfg.recover_window_seconds

    def _take_digest(self) -> str:
        count = int(self.store.get_state(UNANNOUNCED_KEY) or 0)
        if not count:
            return ""
        self.store.set_state(UNANNOUNCED_KEY, "0")
        return prompts.DIGEST.format(count=count, things="thing" if count == 1 else "things")

    async def handle_envelope(self, envelope: dict[str, Any]) -> None:
        decision = classify(envelope, self.cfg.allowed_acis)
        if decision.kind is Kind.VOICE and self.voice is None:  # voice off: a file like any other
            decision = Decision(Kind.UNSUPPORTED, reason="voice is off", discard=decision.audio + decision.discard)
        timestamp = envelope.get("timestamp")
        self._discard(decision.discard)  # strangers' files and unsupported types, at once
        if decision.kind is Kind.STRANGER:
            log.info("ignored a message from %s at %s", sender_aci(envelope), timestamp)
            return
        if decision.kind is Kind.IGNORE:
            log.debug("ignored envelope: %s", decision.reason)
            return
        aci = sender_aci(envelope)
        if aci is None or not isinstance(timestamp, int):
            return
        kind = _KINDS[decision.kind]
        text = decision.text
        if decision.kind is Kind.PHOTO:
            text = f"[photo] {decision.text or '(no caption)'}"
            others = sum(1 for ref in decision.discard if ref.content_type != LONG_TEXT)
            if others:  # said in the message itself, so Gemma does not invent what the file holds
                text += f" (+{others} other file{'s' if others > 1 else ''} I can't open)"
        if decision.kind is Kind.VOICE:  # the transcript is added when its turn comes
            # D4: audio Signal did not mark as a recorded voice note is someone else's voice, not the user's words
            text = prompts.AUDIO_MARK if decision.audio[0].voice_note is False else prompts.VOICE_MARK
            if decision.text:  # typed with the file: kept on its own line, below the transcript to come
                text += f"\n{prompts.TYPED_WITH_AUDIO.format(text=decision.text)}"
        conversation = self.store.current_conversation()
        with self.store.tx():  # a photo message and its attachments, or nothing
            message_id = self.store.add_incoming(conversation.id, aci, timestamp, kind, text, defer_seq=True)
            if message_id is None:
                log.info("duplicate delivery of %s ignored", timestamp)
                return
            for ref in (*decision.photos, *decision.audio):
                self.store.add_attachment(message_id, ref.id, ref.content_type, ref.size)
        log.info(
            "message %d in: %s, %d chars, %d photos, %d voice",
            message_id,
            kind,
            len(text),
            len(decision.photos),
            len(decision.audio),
        )
        with contextlib.suppress(SignalError, NotAllowedError):
            await self.signal.receipt(aci, timestamp)
        self.profiler.note_activity()
        if kind == "command" and not self._no_call.is_set() and text.split()[0].lower() not in WAITING_COMMANDS:
            # during a call a command (no Gemma) is answered at once; /voice name even changes the call's voice
            self._background.add(task := asyncio.create_task(self._command_now(message_id)))
            task.add_done_callback(self._background.discard)
            return
        await self.queue.put(message_id)

    async def process(self, message_id: int) -> None:
        message = self.store.message(message_id)
        if message.status != "received":
            return
        if self.devices is not None and message.kind != "text":
            self.devices.cancel()  # only a text (typed or spoken) can be the yes
        if self._is_stale(message):
            if self.devices is not None:
                self.devices.cancel()
            self.store.set_status(message.id, "failed")
            self._drop_attachments(message.id)
            await self._offline_note(message)
            log.info("message %d arrived after downtime: offline note instead of an answer", message.id)
            return
        self.profiler.note_activity()
        pin = find_pin(message.text) if self.maps else None
        if self.maps and pin:
            self.maps.set_here(*pin)  # "near me" for the next 3 hours (memory only)
        # joins the conversation now, after earlier answers, in the conversation that is open now (/new)
        self.store.assign_seq(message.id, self.store.current_conversation().id)
        message = self.store.message(message.id)
        if message.kind == "command":
            await self._reply(message, await self.commands.handle(message), "command", "answered")
            return
        if message.kind == "unsupported":
            await self._reply(message, prompts.TEXT_ONLY_VOICE if self.voice else prompts.TEXT_ONLY, "note", "answered")
            return
        typing = asyncio.create_task(self._typing_loop(message.peer_aci))
        busy = asyncio.create_task(self._busy_note(message))
        text: str
        notes: list[str] = []
        kind: MessageKind
        status: MessageStatus
        try:
            attachments = self.store.attachments(message.id)
            unheard: tuple[str, MessageStatus] | None = None
            if message.text.split("\n", 1)[0] in (prompts.VOICE_MARK, prompts.AUDIO_MARK):  # not transcribed yet
                unheard = await self._hear(message, attachments)
                message = self.store.message(message.id)
            if message.text.startswith((prompts.VOICE_MARK, prompts.AUDIO_MARK)):  # heard (also before a restart)
                attachments = []
            images, photo_notes = await self._load_photos(message, attachments)
            # an "ask first" device action waits for this message: a clear yes is carried out by code, not Gemma
            confirmed = (
                await self.devices.confirm(message.text, message.peer_aci)
                if self.devices and self.devices.pending
                else None
            )
            if unheard is not None:  # no usable voice note: a note, no Gemma
                text, kind, status = unheard[0], "note", unheard[1]
            elif confirmed is not None:
                text, kind, status = confirmed, "note", "answered"
            elif attachments and not images:  # no usable photo: a note, no Gemma (files go first, in finally)
                text, kind, status = prompts.PHOTO_FAILED, "note", "answered"
            else:
                conversation = self.context.enforce_limit(self.store.conversation(message.conversation_id))
                reply = await self.brain.answer(conversation, message, images, recent=self._recent_photos(message))
                if images:
                    self._recent_photo = (message.conversation_id, message.id, images, self._clock())
                text = reply.text
                if self.language != ENGLISH and self.devices is not None and self.devices.pending:
                    text = self.devices.question()  # Gemma in another language may claim it is done: code's words only
                # separate "note" messages: sent to the user, never part of Gemma's history (it would imitate them)
                device_note = self.devices.take_note() if self.devices else ""
                music_note = self.music.take_note() if self.music else ""
                notes = [
                    note
                    for note in (
                        reply.sources,
                        device_note,
                        music_note,
                        *photo_notes,
                        reply.notice,
                        self._take_digest(),
                    )
                    if note
                ]
                kind = "text"
                status = "answered"
                log.info(
                    "message %d answered: %d tool calls, %d prompt tokens",
                    message.id,
                    reply.tool_calls,
                    reply.prompt_tokens,
                )
        except GemmaError as exc:
            log.error("message %d: Gemma failed: %s", message.id, exc)
            text = prompts.UNAVAILABLE.format(name=self.cfg.name)
            kind = "note"
            status = "failed"
            if self.devices:
                self.devices.cancel()  # the user never saw a question, so no yes may act on it
            device_note = self.devices.take_note() if self.devices else ""
            music_note = self.music.take_note() if self.music else ""
            # what changed before Gemma failed is still reported (and never carried to a later answer)
            notes = [note for note in (device_note, music_note) if note]
        finally:
            typing.cancel()
            busy.cancel()
            self._drop_attachments(message.id)  # photos live only until answered
            with contextlib.suppress(SignalError, NotAllowedError):
                await self.signal.typing(message.peer_aci, stop=True)
        await self._reply(message, text, kind, status)
        for note in notes:
            await self._send(
                self.store.add_outgoing(message.conversation_id, message.peer_aci, "note", note, message.id)
            )
        if kind == "text" and self._speaks(message):
            await self._speak(message, text)
        self.profiler.note_activity()

    async def _hear(self, message: Message, attachments: list[Attachment]) -> tuple[str, MessageStatus] | None:
        """Transcribe the message's voice note into its text ("[voice] ..."); a note and status when it can't be."""
        if self.voice is None or not attachments:
            return prompts.VOICE_UNCLEAR, "answered"
        audio = attachments[0]
        limit = self.cfg.voice_max_audio_bytes
        if max(audio.size, self._file_size(audio.signal_id)) > limit:
            return prompts.VOICE_TOO_LONG, "answered"
        try:
            data = await self.signal.get_attachment(message.peer_aci, audio.signal_id)
        except (SignalError, NotAllowedError) as exc:
            log.warning("message %d: voice note not readable: %s", message.id, exc)
            return prompts.VOICE_UNCLEAR, "answered"
        kind = sniff(data)
        if kind is None:  # claimed audio or no type, but not audio: like any other file
            return prompts.TEXT_ONLY_VOICE, "answered"
        if len(data) > limit:
            return prompts.VOICE_TOO_LONG, "answered"
        if len(data) < MIN_AUDIO_BYTES:
            return prompts.VOICE_UNCLEAR, "answered"
        started = time.monotonic()
        try:
            transcript = await self.voice.transcribe(data)
        except VoiceUnavailableError as exc:
            log.warning("message %d: voice service unavailable: %s", message.id, exc)
            return prompts.VOICE_DOWN, "failed"
        except VoiceTooLongError:
            return prompts.VOICE_TOO_LONG, "answered"
        except VoiceError as exc:
            log.warning("message %d: voice note not heard: %s", message.id, exc)
            return prompts.VOICE_UNCLEAR, "answered"
        log.info(
            "message %d heard: %s, %d bytes, %.1f s audio, %d chars in %.1f s",
            message.id,
            kind,
            len(data),
            transcript.duration,
            len(transcript.text),
            time.monotonic() - started,
        )
        if not transcript.text:
            return prompts.VOICE_UNCLEAR, "answered"
        mark = prompts.AUDIO_MARK if message.text.startswith(prompts.AUDIO_MARK) else prompts.VOICE_MARK
        typed = message.text.partition("\n")[2]  # the user's words sent with the file stay with it
        self.store.set_message_text(message.id, f"{mark} {transcript.text}" + (f"\n{typed}" if typed else ""))
        self._drop_attachments(message.id)  # heard: the audio is not needed any more
        if self.store.get_state(VOICE_ECHO_KEY) == "on":
            echo = prompts.VOICE_ECHO.format(text=transcript.text)
            await self._send(
                self.store.add_outgoing(message.conversation_id, message.peer_aci, "note", echo, message.id)
            )
        return None

    def _speaks(self, message: Message) -> bool:
        """Whether Gemma's answer to this message also goes out as a voice note (/voice auto, on, off)."""
        if self.voice is None:
            return False
        mode = self.store.get_state(VOICE_MODE_KEY) or "auto"
        return mode == "on" or (mode == "auto" and message.text.startswith(prompts.VOICE_MARK))

    async def _speak(self, message: Message, answer: str) -> None:
        """The spoken version of a sent answer as a Signal voice note; on any failure the text stands alone."""
        words = spoken_text(answer, self.cfg.voice_max_spoken_chars)
        if self.voice is None or not words:
            return
        path: Path | None = None
        try:
            path = await to_m4a(await self.voice.speak(words), self.cfg.voice_dir)
            await self.signal.send_voice_note(message.peer_aci, path)
            log.info("message %d: voice answer sent, %d chars spoken", message.id, len(words))
        except (VoiceError, SignalError, NotAllowedError, OSError) as exc:
            log.warning("message %d: no voice answer: %s", message.id, type(exc).__name__)
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    async def _load_photos(self, message: Message, attachments: list[Attachment]) -> tuple[list[str], list[str]]:
        """Data URIs of the message's photos (at most photo_max_count) and notes about the ones left out."""
        if not attachments:
            return [], []
        images: list[str] = []
        failed = 0
        for attachment in attachments[: self.cfg.photo_max_count]:
            try:
                size = max(attachment.size, self._file_size(attachment.signal_id))  # the envelope can understate it
                if size > self.cfg.photo_max_bytes:
                    raise PhotoError(f"too big ({size} bytes)")
                data = await self.signal.get_attachment(message.peer_aci, attachment.signal_id)
                if len(data) > self.cfg.photo_max_bytes:
                    raise PhotoError(f"too big ({len(data)} bytes)")
                jpeg = await prepare(data, self.cfg.data_dir / "photos", max_side=self.cfg.photo_max_side)
                images.append(data_uri(jpeg))
            except (PhotoError, SignalError, NotAllowedError) as exc:
                failed += 1
                log.warning("message %d: photo %d not usable: %s", message.id, attachment.id, exc)
        notes = []
        if len(attachments) > self.cfg.photo_max_count:
            notes.append(prompts.PHOTO_LIMIT.format(count=self.cfg.photo_max_count))
        if failed and images:
            notes.append(prompts.PHOTO_SOME_FAILED.format(failed=failed))
        return images, notes

    def _recent_photos(self, message: Message) -> dict[int, list[str]] | None:
        """The last photo, shown again for a follow-up in the same conversation within photo_follow_up_seconds."""
        if self._recent_photo is None:
            return None
        conversation_id, photo_id, images, at = self._recent_photo
        if conversation_id != message.conversation_id or self._clock() - at > self.cfg.photo_follow_up_seconds:
            self._recent_photo = None
            return None
        return {photo_id: images}

    def _file_size(self, signal_id: str) -> int:
        try:
            return (self.cfg.signal_attachments_dir / signal_id).stat().st_size
        except OSError:
            return 0

    def _drop_attachments(self, message_id: int) -> None:
        """Delete signal-cli's copies of a message's photos and mark them deleted."""
        for attachment in self.store.attachments(message_id):
            self._discard((AttachmentRef(attachment.signal_id, attachment.content_type, attachment.size),))
            self.store.mark_attachment_deleted(attachment.id)

    def _discard(self, refs: tuple[AttachmentRef, ...]) -> None:
        """Delete files from signal-cli's attachments folder (names checked by the gate; never a path)."""
        folder = self.cfg.signal_attachments_dir
        for ref in refs:
            path = folder / ref.id
            if path.parent != folder or ref.id in ("", ".", ".."):
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not delete attachment file: %s", type(exc).__name__)

    def sweep_files(self) -> int:
        """Delete old files nothing waits for: signal-cli's downloads that no unanswered photo or voice note needs
        (with their `<id>.preview` sidecars kept alongside), and leftovers of interrupted photo conversions and
        spoken answers."""
        keep = self.store.open_attachment_ids()
        cutoff = self._clock() - SWEEP_SECONDS
        removed = 0
        folders: tuple[tuple[Path, set[str]], ...] = (
            (self.cfg.signal_attachments_dir, keep),
            (self.cfg.data_dir / "photos", set()),
            (self.cfg.voice_dir, set()),
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

    async def _sweeper(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            self.sweep_files()

    def _mail_synced(self) -> bool:
        """The mail sync finished a round since the start (the digest's mail part waits for it)."""
        return self.mail_sync is None or self.mail_sync.rounds > 0

    async def _status_lines(self) -> list[str]:
        """Extra lines for mini_status: mail, the calendar, and which devices answer."""
        lines = [self.mail_sync.status_line(self.cfg.timezone)] if self.mail_sync else []
        if self.mail_sorter:
            lines.append(self.mail_sorter.status_line())
        if self.calendar:
            lines.append(await self.calendar.status_line())
        if self.devices:
            lines.append(await self.devices.reachability())
        if self.calls:
            lines.append(self.calls.status_line())
        if self.music:
            lines.append(self.music.status_line())
        if self.web:
            lines.append(f"Web search: {self.web.usage()}")
        if self.maps:
            lines.append(f"Maps: {self.maps.usage()}")
        return lines

    async def _alert(self, text: str) -> None:
        """A watcher's alert or recovery: a note to every allowed user (never part of Gemma's history)."""
        conversation = self.store.current_conversation()
        for aci in sorted(self.cfg.allowed_acis):
            await self._send(self.store.add_outgoing(conversation.id, aci, "note", text, None))
        log.warning("alert sent: %d line(s)", text.count("\n") + 1)

    async def status(self) -> StatusInfo:
        return StatusInfo(
            gemma=await self.gemma.status(),
            queue_length=self.queue.qsize(),
            last_learning_run=self.store.get_state(LAST_RUN_KEY),
            db_bytes=self.store.db_size_bytes(),
            messages=self.store.count_messages(),
            restarts=self.signal.restarts,
            parts=tuple(line.line() for line in part_lines(self.cfg, self.part_status.snapshot(), self._clock())),
        )

    async def _learn_command(self, url: str, message: Message) -> str:
        """/learn <link>: read the page, extract the facts about the user (one structured Gemma call), report."""
        try:
            page = await self._fetch(url)
        except FetchError as exc:
            return f"Could not read the link: {exc}"
        document_id = self.store.add_document(page.final_url, page.title, page.text, message.id)
        try:
            result = await self.profiler.learn_page(page, message.id, document_id)
        except (LearnError, GemmaError) as exc:
            log.warning("message %d: learning from a page failed: %s", message.id, exc)
            return "I read the page but could not take the facts from it right now. Try again in a bit."
        self.context.refresh_snapshot()
        added = [fact for fact in self.profile.facts() if fact.id in result.added]
        lines = [f"Learned from {page.title or page.final_url}:"]
        lines += [f"Saved #{fact.id}: {fact.text}" for fact in added] or ["Nothing new: I already know what it says."]
        if result.duplicates:
            lines.append(f"Already known: {len(result.duplicates)}")
        if result.rejected:
            lines.append(f"Not allowed: {len(result.rejected)}")
        if added:
            lines.append("Undo one with /forget N.")
        return "\n".join(lines)

    async def _fetch(self, url: str) -> Page:
        """Fetch in a thread with an overall limit, so a slow site cannot hold the only worker."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._fetcher.fetch, url), self._fetch_timeout)
        except TimeoutError:
            raise FetchError(f"the site was too slow (over {self._fetch_timeout:.0f} s)") from None

    async def _receive_loop(self) -> None:
        async for envelope in self.signal.envelopes():
            try:
                await self.handle_envelope(envelope)
            except Exception:
                log.exception("could not handle an incoming envelope")

    async def _command_now(self, message_id: int) -> None:
        try:
            await self.process(message_id)
        except Exception:
            log.exception("message %d failed", message_id)
            await self._reply_after_failure(message_id)

    async def _worker(self) -> None:
        while True:
            message_id = await self.queue.get()
            await self._no_call.wait()  # texts that come in during a call are answered after it
            self._processing = True
            self._worker_idle.clear()
            try:
                await self.process(message_id)
            except Exception:
                log.exception("message %d failed", message_id)
                await self._reply_after_failure(message_id)
            finally:
                self._processing = False
                self._worker_idle.set()
                self.queue.task_done()

    async def _reply_after_failure(self, message_id: int) -> None:
        """Whatever went wrong, the user hears back instead of silence."""
        try:
            message = self.store.message(message_id)
            if message.status == "received":
                await self._reply(message, prompts.CONFUSED, "note", "failed")
            else:
                self.store.set_status(message_id, "failed")
        except Exception:
            log.exception("message %d: could not send the failure note", message_id)

    async def _reply(self, message: Message, text: str, kind: MessageKind, status: MessageStatus) -> None:
        with self.store.tx():
            out_id = self.store.add_outgoing(message.conversation_id, message.peer_aci, kind, text, message.id)
            self.store.set_status(message.id, status)
        await self._send(out_id)

    async def _send(self, out_id: int) -> None:
        out = self.store.message(out_id)
        timestamp = await self._deliver(out.peer_aci, out.text, f"message {out_id}")
        if timestamp is None:
            self.store.set_status(out_id, "failed")
            return
        self.store.mark_sent(out_id, timestamp)
        log.info("message %d sent: %s, %d chars", out_id, out.kind, len(out.text))

    async def _deliver(self, aci: str, text: str, what: str) -> int | None:
        """Send one text (three tries); its Signal timestamp, or None when it could not be sent."""
        for attempt in range(3):
            try:
                return await self.signal.send(aci, text)
            except UntrustedIdentityError:
                log.error(  # no number or account ID in the log: the guide has the command
                    "%s not sent: the user's safety number changed. Check it in Signal, then trust it with "
                    "signal-cli (setup_assistant.md, 7. Operations, 'Safety number changed')",
                    what,
                )
                return None
            except NotAllowedError:
                log.error("%s not sent: recipient not allowed", what)
                return None
            except SignalError as exc:
                log.warning("%s: send attempt %d failed: %s", what, attempt + 1, exc)
                await asyncio.sleep(self._retry_delay * (attempt + 1))
        return None

    async def _typing_loop(self, aci: str) -> None:
        while True:
            with contextlib.suppress(SignalError, NotAllowedError):
                await self.signal.typing(aci)
            await asyncio.sleep(self.cfg.typing_refresh_seconds)

    async def _busy_note(self, message: Message) -> None:
        await asyncio.sleep(self.cfg.busy_note_seconds)
        busy = prompts.BUSY.format(name=self.cfg.name)
        out_id = self.store.add_outgoing(message.conversation_id, message.peer_aci, "note", busy, message.id)
        await asyncio.shield(self._send(out_id))  # once started, finish sending even if the answer arrives

    def _mark_alive(self) -> None:
        self.store.set_state(ALIVE_KEY, f"{self._clock():.0f}")

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(60)
            self._mark_alive()


def setup_logging(log_dir: Path, *, foreground: bool) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir.chmod(0o700)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = logging.handlers.WatchedFileHandler(log_dir / "bridge.log")  # reopens after newsyslog rotation
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if foreground:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


def signal_client(cfg: Config) -> SignalClient:
    """signal-cli as configured: with [calls] the JVM build (Homebrew's native one cannot place calls) with Java,
    IPv4 and the call helper in its environment, subscribed to call events."""
    if not cfg.calls_enabled:
        return SignalClient(signal_cli_command(cfg.signal_cli, cfg.bot_number), cfg.allowed_acis)
    cfg.call_socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    java_home = cfg.calls_java_home or find_java_home()
    if java_home is None:
        log.warning("calls: no Java found with /usr/libexec/java_home; set java_home in [calls] if signal-cli fails")
    env = call_env(java_home=java_home, tunnel=cfg.call_tunnel, socket_dir=cfg.call_socket_dir)
    command = signal_cli_command(str(cfg.call_signal_cli), cfg.bot_number, jvm=True)
    return SignalClient(command, cfg.allowed_acis, env=env, calls=True)


async def _serve(app: App) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is not None:
        for signum in (os_signal.SIGTERM, os_signal.SIGINT):
            loop.add_signal_handler(signum, task.cancel)
    try:
        await app.run()
    except asyncio.CancelledError:
        log.info("stopping")
    finally:
        if app.calls:
            await app.calls.hangup()  # before signal-cli stops, so the caller hears the call end
        await app.signal.stop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="local-assistant", description="Private Signal assistant on the local Gemma")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--foreground", action="store_true", help="also log to the terminal")
    parser.add_argument(
        "--mail-login", metavar="ADDRESS", help="sign in one configured mailbox (device code), then exit"
    )
    parser.add_argument(
        "--mail-only",
        action="store_true",
        help="with --mail-login: sign in mail alone, without the mailbox's calendar (Calendars.Read)",
    )
    parser.add_argument(
        "--spotify-login", action="store_true", help="sign in to Spotify (paste the address), then exit"
    )
    args = parser.parse_args(argv)
    if args.mail_only and not args.mail_login:
        parser.error("--mail-only goes with --mail-login <address>")
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"local-assistant: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if args.mail_login:
        try:
            signed_in = asyncio.run(
                device_login(cfg, args.mail_login, TokenStore(cfg.mail_token_path), mail_only=args.mail_only)
            )
        except MailAuthError as exc:
            print(f"local-assistant: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"signed in: {signed_in} (the bridge picks the new sign-in up by itself)")
        return
    if args.spotify_login:
        if not cfg.spotify_enabled:
            print("local-assistant: add [spotify] with your app's client_id to the config first", file=sys.stderr)
            raise SystemExit(2)
        try:
            asyncio.run(login(SpotifyTokens(cfg.spotify_token_path, cfg.spotify_client_id), name=cfg.name))
        except SpotifyAuthError as exc:
            print(f"local-assistant: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return
    setup_logging(cfg.log_dir, foreground=args.foreground)
    store = Store.open(cfg.db_path)
    signal = signal_client(cfg)
    gemma = OllamaGemma(
        cfg.ollama_base_url,
        cfg.model,
        unreachable_seconds=cfg.gemma_unreachable_seconds,
        request_timeout=cfg.gemma_request_timeout,
    )
    log.info("starting: model %s, %d allowed account(s)", cfg.model, len(cfg.allowed_acis))
    try:
        asyncio.run(_serve(App(cfg, store, signal, gemma, Fetcher())))
    finally:
        store.close()
