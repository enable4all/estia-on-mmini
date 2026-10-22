"""The bridge: Signal in, Gemma out. Wires every part together and runs them (spec section 3).

Messages are handled one at a time (one Gemma slot). Logs carry metadata only, never message text or
image data. Step 3 adds photos (fetched, converted, shown to Gemma once, then deleted), the Mac
health watcher (alerts as notes) and the project tools; voice notes are heard and answered through the
local voice service (transcript in, spoken answer out, audio deleted).

The call, document, start-up and status methods live in four hosts that hold this app as `self.app`: call_host.py,
documents_host.py, lifecycle.py and status_host.py. App keeps one-line delegates for the names the tests call.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import logging.handlers
import os
import signal as os_signal
import stat
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, TextIO

from local_assistant import code_notes, hostsec, prompts
from local_assistant.babble import Babble, Session, tidy
from local_assistant.brain import Brain
from local_assistant.calendar_feed import CalendarFeed
from local_assistant.calendar_graph import OutlookCalendars
from local_assistant.calendar_tools import CalendarTools, clean_note
from local_assistant.calendar_watch import CalendarWatch
from local_assistant.call_host import CALL_MAX_TOKENS, CallGlance, CallHost, rundown_reads
from local_assistant.calls import CallManager, CallSettings, Turn
from local_assistant.chords import ChordTool
from local_assistant.commands import (
    VOICE_ECHO_KEY,
    VOICE_MODE_KEY,
    VOICE_NAME_KEY,
    VOICES,
    Commands,
    Guard,
    StatusInfo,
)
from local_assistant.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    find_java_home,
    load_config,
    quiet_hours_of,
)
from local_assistant.context import ContextBuilder
from local_assistant.devices import Devices
from local_assistant.documents import (
    Documents,
    Helper,
)
from local_assistant.documents_host import DocumentsHost
from local_assistant.fetcher import Fetcher, FetchError, Page
from local_assistant.followups import FollowUps
from local_assistant.gate import LONG_TEXT, AttachmentRef, Decision, Kind, classify, sender_aci
from local_assistant.gemma import GemmaClient, GemmaError, ModelPausedError, ModelPin, OllamaGemma, PinnedGemma
from local_assistant.graph import Graph
from local_assistant.health import Health, HealthCheck
from local_assistant.initiative import (
    Initiative,
    Settings,
    Writer,
)
from local_assistant.language import CODES, ENGLISH, LANGUAGE_KEY
from local_assistant.lifecycle import ALIVE_KEY, ALIVE_SECONDS, WELCOME_KEY, Lifecycle
from local_assistant.mail_auth import AccessTokens, MailAuthError, TokenStore, device_login
from local_assistant.mail_digest import MailDigest, MailNumbers
from local_assistant.mail_http import http
from local_assistant.mail_prefilter import Jev
from local_assistant.mail_sort import MailSorter
from local_assistant.mail_sync import MailSync
from local_assistant.mail_tools import MailTools
from local_assistant.maps import Maps, find_pin, forget_home_address
from local_assistant.morning_digest import MorningDigest
from local_assistant.music_tools import MusicTools
from local_assistant.part_status import PartStatus
from local_assistant.photos import PhotoError, data_uri, prepare
from local_assistant.profile import Profile
from local_assistant.profiler import UNANNOUNCED_KEY, LearnError, Profiler
from local_assistant.projects import Projects
from local_assistant.reminders import Reminders, ReminderScheduler
from local_assistant.signal_io import (
    NotAllowedError,
    ReceiveProblem,
    SignalClient,
    SignalError,
    UntrustedIdentityError,
    call_env,
    signal_cli_command,
)
from local_assistant.signal_text import Styled, netted, styled, unlinked
from local_assistant.spotify import Spotify
from local_assistant.spotify_auth import SpotifyAuthError, SpotifyTokens, login
from local_assistant.status_host import StatusHost
from local_assistant.store import Attachment, Message, MessageKind, MessageStatus, Store
from local_assistant.tools import Tools, project_schemas, tool_schemas
from local_assistant.video import CaptionHelper, VideoReader, video_id
from local_assistant.voice import (
    VoiceClient,
    VoiceError,
    VoiceTooLongError,
    VoiceUnavailableError,
    sniff,
    spoken_text,
    to_m4a,
)
from local_assistant.voice_ready import QUIET_HOURS, ReadyNote
from local_assistant.watchers import Scheduler
from local_assistant.web_search import WebSearch

log = logging.getLogger("local_assistant")

# what the tests, the evals and `python -m local_assistant` import from this module (some of it lives in the hosts now)
__all__ = [
    "ALIVE_KEY",
    "ALIVE_SECONDS",
    "CALL_MAX_TOKENS",
    "WAITING_COMMANDS",
    "WELCOME_KEY",
    "App",
    "main",
    "model_pin",
    "rundown_reads",
    "setup_logging",
    "signal_client",
]

MODEL_CHECK_SECONDS = 300.0  # the model's pin is read this often (and before every chat)
# during a call these wait: they change the conversation or the language, or use Gemma
WAITING_COMMANDS = frozenset({"/new", "/learn", "/language", "/babble", "/docs"})  # /docs: she sends nothing on a call
# The three waits below and DOCUMENTS_FIRST_SECONDS are given to the host that uses them when it is built (CallHost,
# DocumentsHost): a test shortens one by patching its name on this module.

# a call's first answer waits at most this long for the glance of the day (counted from the turn; the calendar is read
# from the moment the call starts); not ready by then, it joins from the first turn after it is
GLANCE_WAIT_SECONDS = 3.0
# a call's model warm-up (owner, 17 Oct): the model's load and its read of the first turn's prompt, cut off after this
# (live, 17 Oct: a cold load took 7 s and a 16,855-token prompt 12.5 s)
WARM_UP_SECONDS = 60.0
# the warm-up waits this long for the glance's calendar, longer than a turn does (review, 17 Oct): the first turn comes
# some 20 s into the call and finds a slow calendar read, and a glance that differs from the warm-up's makes Ollama read
# the tools and the whole history again (6-8 s); the wait delays the warm-up by at most 5 s more
WARM_UP_GLANCE_SECONDS = 8.0
# the documents folder ([documents]): the first check comes this long after the start (given to the host, like the
# waits above)
DOCUMENTS_FIRST_SECONDS = 30.0
# shorter than this is a slip of the thumb, not speech (OpenClaw's minimum)
MIN_AUDIO_BYTES = 1024
PAGE_TITLE_CHARS = 200  # a page's title in the /learn reply: the fetcher's own cap
# a photo message is stored as text "[photo] caption", a voice message as "[voice] transcript";
# their files are in the attachments table
_KINDS: dict[Kind, MessageKind] = {
    Kind.TEXT: "text",
    Kind.PHOTO: "text",
    Kind.VOICE: "text",
    Kind.DOCUMENT: "text",
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
        model_pin: ModelPin | None = None,
        security_level: Callable[[], str | None] | None = None,
        warm_up: bool | None = None,
    ) -> None:
        """`security_level`: reads the Mac's security level for her list of what she can do (default: as doctor
        reads it, on the live Mac only: hostsec.mac_security_level). `warm_up`: a call's model warm-up
        (`call_warm_up`); None: only with Ollama itself (an OllamaGemma with a model named, also behind the model
        pin), so a scripted Gemma in the tests gets none and its script keeps its order."""
        self._wire_core(
            cfg,
            store,
            signal,
            gemma,
            fetcher,
            clock=clock,
            retry_delay=retry_delay,
            fetch_timeout=fetch_timeout,
            model_pin=model_pin,
            security_level=security_level,
            warm_up=warm_up,
        )
        self._wire_mail()
        self._wire_calendar_and_digest()
        self._wire_extras()
        self._wire_voice_and_calls()
        self._wire_reminders_and_initiative()
        self._given_health = health  # read by _wire_tools_and_brain
        self._wire_tools_and_brain()
        self._wire_commands()

    def _wire_core(
        self,
        cfg: Config,
        store: Store,
        signal: SignalClient,
        gemma: GemmaClient,
        fetcher: Fetcher,
        *,
        clock: Callable[[], float],
        retry_delay: float,
        fetch_timeout: float,
        model_pin: ModelPin | None,
        security_level: Callable[[], str | None] | None,
        warm_up: bool | None,
    ) -> None:
        """The parts every other part reads: config, store, Signal, the model, the guard, the language, the rules."""
        self.cfg = cfg
        self.store = store
        self.signal = signal
        self._warms_up = isinstance(gemma, OllamaGemma) and bool(cfg.model) if warm_up is None else warm_up
        # nothing reaches a model Ollama lists as a cloud one, nor (with model_digest) one without the pinned digest
        self.model_pin = model_pin
        self._paused_why = ""  # why answers were last paused: the note when they come back says what changed
        if model_pin is not None:
            gemma = PinnedGemma(gemma, model_pin)  # its changes are told from the start's check on (run)
        self.gemma = gemma
        self._fetcher = fetcher
        self._clock = clock
        self._retry_delay = retry_delay
        self._fetch_timeout = fetch_timeout
        self._processing = False
        self.lifecycle = Lifecycle(self)
        # the last photo, in memory only (its files are deleted), for follow-up questions: (conversation, message, images, at)
        self._recent_photo: tuple[int, int, list[str], float] | None = None
        # the user's devices and the lock: a new linked device is held, /lock stops everything until /unlock
        self.guard = Guard(store, cfg.allowed_acis, tz=cfg.timezone, clock=clock)
        self.guard.seed()
        self._start_up_waits = False  # the reminders' start-up check, put off while locked
        self._identity_changed: set[str] = set()  # accounts whose safety number changed since the start (memory only)
        self.profile = Profile(
            store,
            max_chars=cfg.profile_max_chars,
            notes_max_chars=cfg.notes_max_chars,
            notes_per_message=cfg.notes_per_message_chars,
            fact_max_chars=cfg.fact_max_chars,
        )
        # her list of what she can do names the Mac's security level: read at the start and every 4 hours (None: not
        # read yet, or off the live Mac)
        self.status_host = StatusHost(
            self, security_level or (lambda: hostsec.mac_security_level(Path.home(), cfg.bot_number))
        )
        # one language at a time: the config's is the starting value, a typed /language (kept in the database) wins
        chosen_language = store.get_state(LANGUAGE_KEY)
        self.language = chosen_language if chosen_language in CODES else cfg.language
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._no_call = asyncio.Event()
        self._no_call.set()
        # language practice (/babble): its own conversation, no tools, the recap at the end; typed only
        self.babble = Babble(
            store,
            gemma,
            cfg.timezone,
            send=self._send,
            user_language=lambda: self.language,
            quiet_hours=quiet_hours_of(cfg.initiative_quiet_hours),  # a recap after an idle end waits for morning
            rules=self._practice_rules,
            on_change=self._practice_changed,
            in_call=lambda: not self._no_call.is_set(),
            busy=lambda: self._processing or not self.queue.empty(),
            locked=lambda: self.guard.locked,  # no answers at all: a recap due meanwhile waits until /unlock
            clock=clock,
        )
        self.context = ContextBuilder(
            store,
            self.profile,
            cfg.timezone,
            window_tokens=cfg.window_tokens,
            emergency_tokens=cfg.emergency_tokens,
            rules=self._rules(self.language),
            soul_path=cfg.soul_path,
            name=cfg.name,
            style=cfg.personality_style,
            calm_on_worry=cfg.personality_calm_on_worry,
        )
        # what each part's last real read or call did, for /status and doctor (<data_dir>/status.json)
        self.part_status = PartStatus(cfg.data_dir / "status.json", clock=clock)

    def _wire_mail(self) -> None:
        """Mail (read-only Graph): sync, sorting, the digest's mail part and Gemma's mail tools."""
        cfg, store, clock, gemma = self.cfg, self.store, self._clock, self.gemma
        # mail (read-only Graph): sync, sorting at idle time, the digest's mail part, Gemma's mail tools
        self.mail_sync: MailSync | None = None
        self.mail_sorter: MailSorter | None = None
        self.mail_digest: MailDigest | None = None
        self._mail_tools: MailTools | None = None
        if cfg.mail_enabled:
            tokens = TokenStore(cfg.mail_token_path)
            graph = Graph(AccessTokens(cfg, tokens))
            numbers = MailNumbers(store, cfg.timezone, clock)
            self.mail_sync = MailSync(
                store, graph, cfg, tokens, notify=self._alert, clock=clock, status=self.part_status
            )
            jev = Jev(cfg.mail_jev_url) if cfg.mail_jev_url else None  # the pre-filter's second step
            self.mail_sorter = MailSorter(store, graph, gemma, cfg, busy=self._is_busy, jev=jev)
            self.mail_digest = MailDigest(store, cfg, numbers, clock=clock, status=self.part_status)
            self._mail_tools = MailTools(store, graph, cfg, numbers, self.mail_digest, clock=clock)

    def _wire_calendar_and_digest(self) -> None:
        """The calendars and the morning digest."""
        cfg, store, clock = self.cfg, self.store, self._clock
        # the calendars: Google iCal addresses (tools/ical) and Outlook calendars of the mailboxes (their own Graph,
        # Calendars.Read only); the digest's block and Gemma's calendar tools
        self.calendar: CalendarTools | None = None
        self._calendar_feed: CalendarFeed | None = None
        if cfg.calendar_enabled:
            outlook = (
                OutlookCalendars(cfg, status=self.part_status, clock=clock)
                if any(source.mailbox for source in cfg.calendars)
                else None
            )
            feed = CalendarFeed(cfg, status=self.part_status, outlook=outlook.reader if outlook else None)
            self._calendar_feed = feed
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
                send=partial(self._alert, private=True),  # mail senders and subjects, calendar titles and places
                clock=clock,
                mail_ready=self._mail_synced,
                calendar_seconds=cfg.calendar_helper_timeout + 5,
            )

    def _wire_extras(self) -> None:
        """Music, web search, maps and the lights and plugs."""
        cfg, store = self.cfg, self.store
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
        # the home address belongs only in the private config: rows an earlier version kept go (maps on or off)
        forget_home_address(store, cfg.maps_home if cfg.maps_enabled else "", cfg.web_keys_path)
        # lights and plugs (through the device helper); "ask first" confirmations and notes are code
        self.devices = Devices(cfg, status=self.part_status) if cfg.tapo_enabled else None

    def _wire_voice_and_calls(self) -> None:
        """The voice service and voice calls."""
        cfg, clock, signal = self.cfg, self._clock, self.signal
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
        self._background: set[asyncio.Task[None]] = set()  # commands answered during a call
        self._worker_idle = asyncio.Event()  # no text being answered (a call turn waits for it)
        self._worker_idle.set()
        self.calls: CallManager | None = None
        self._call_voice: VoiceClient | None = None
        self.voice_ready: ReadyNote | None = None
        # the app's side of a call (calls.Host): what CallManager asks for each turn, the notes and the summary
        self.call_host = CallHost(
            self,
            glance_wait=GLANCE_WAIT_SECONDS,
            warm_up_seconds=WARM_UP_SECONDS,
            warm_up_glance=WARM_UP_GLANCE_SECONDS,
        )
        if cfg.calls_enabled:
            self._call_voice = VoiceClient(
                cfg.voice_url,
                voice=cfg.voice_name,
                speed=cfg.calls_speed,
                status=self.part_status,
                language=self.language,
            )
            # "My voice is ready" after a long load (a compile) or a call that rang meanwhile: code's note, once a start
            self.voice_ready = ReadyNote(
                self.call_host.call_voice_ready,
                self.call_host.call_note,
                cfg.allowed_acis,
                tz=cfg.timezone,
                # never waking anyone, unless they called: the initiative's quiet hours, else 22:00-08:00
                quiet_hours=quiet_hours_of(cfg.initiative_quiet_hours) or QUIET_HOURS,
                locked=lambda: self.guard.locked,  # waits for /unlock
                clock=clock,
            )
            self.calls = CallManager(
                signal,
                self._call_voice,
                self.call_host,
                cfg.allowed_acis,
                CallSettings(
                    socket_dir=cfg.call_socket_dir,
                    end_of_turn_s=cfg.calls_end_of_turn_seconds,
                    idle_prompt_s=cfg.calls_idle_prompt_seconds,
                    max_s=cfg.calls_max_minutes * 60,
                    summary=cfg.calls_summary,
                    fillers=cfg.calls_fillers,
                    acknowledge=cfg.calls_acknowledge,
                    answer_after_s=cfg.calls_answer_after_seconds,
                    barge=cfg.calls_barge,
                    early_end=cfg.calls_early_end,
                    early_end_pause_s=cfg.calls_early_end_pause_seconds,
                    pause=cfg.calls_pause,
                ),
                tz=cfg.timezone,
                language=self.language,
                name=cfg.name,
                practice=self._practised,  # a call during practice: in the practised language, its end the recap
                locked=lambda: self.guard.locked,
                missed=self.voice_ready.missed,  # a ring while the voice loads: the caller gets the ready note
                reached=self.voice_ready.reached,  # a call that connected: that caller needs no ready note
            )

    def _wire_reminders_and_initiative(self) -> None:
        """Reminders and wake-up calls, the messages she starts herself and the check-ins."""
        cfg, store, clock, gemma = self.cfg, self.store, self._clock, self.gemma
        # reminders and wake-up calls the user sets (on unless [reminders] enabled = false); code fires them
        self.reminders: Reminders | None = None
        self.reminder_scheduler: ReminderScheduler | None = None
        if cfg.reminders_enabled:
            self.reminders = Reminders(
                store,
                cfg.timezone,
                max_open=cfg.reminders_max_open,
                max_per_day=cfg.reminders_max_per_day,
                max_calls_per_day=cfg.calls_max_calls_per_day,
                calls=cfg.wake_calls,
                name=cfg.name,
                clock=clock,
            )
            self.reminder_scheduler = ReminderScheduler(
                store,
                self.reminders,
                self._send,
                calls=self.calls if cfg.wake_calls else None,
                in_call=lambda: not self._no_call.is_set(),  # texts wait during a call, like every text
                locked=lambda: self.guard.locked,  # and everything while locked; a call reminder then never calls
                clock=clock,
            )
        # messages she starts herself (opt-in: [initiative] enabled = true, or the user's typed /initiative on, which
        # wins and needs no restart, so the gate is always there; off, it only keeps house): code finds every trigger
        # and checks the gate, Gemma only writes; the check-ins are found in the idle pass, the calendar notes are code's
        settings = Settings.of(cfg)
        watch = None
        if settings.calendar and self._calendar_feed is not None:
            watch = CalendarWatch(self._calendar_feed, cfg.timezone, digest_minute=settings.digest_minute, clock=clock)
        self.initiative: Initiative | None = Initiative(
            store,
            settings,
            Writer(gemma, name=cfg.name, language=lambda: self.language, style=self.context.style),
            self._send,
            tz=cfg.timezone,
            acis=cfg.allowed_acis,
            busy=self._is_busy,
            practising=lambda: self.babble.running() is not None,  # her message would land in the roleplay
            locked=lambda: self.guard.locked,
            calendar=watch,
            reminders=cfg.reminders_enabled,
            wake_calls=cfg.reminders_enabled and cfg.wake_calls,
            clock=clock,
        )
        self.followups: FollowUps | None = None
        if settings.followups:
            initiative = self.initiative
            self.followups = FollowUps(
                store, gemma, cfg.timezone, enabled=lambda: initiative.enabled and not initiative.quiet, clock=clock
            )

    def _wire_tools_and_brain(self) -> None:
        """Host checks, videos, documents, Gemma's tools, the watchers, the brain and the profiler."""
        cfg, store, clock, gemma = self.cfg, self.store, self._clock, self.gemma
        self.health = self._given_health or Health(
            store,
            cfg,
            signal_restarts=lambda: self.signal.restarts,
            clock=clock,
            extra_lines=self._status_lines,
        )
        # YouTube links in a message: their captions through tools/youtube (always on with read_link), the last video
        # kept in memory for 10 minutes per account; a long one read in parts by Gemma
        self.videos = VideoReader(
            CaptionHelper(cfg.youtube_helper, cfg.youtube_helper_timeout, status=self.part_status), gemma
        )
        self._busy: dict[int, asyncio.Task[None]] = {}  # each answer's busy note, until it is sent or not needed
        # documents: the PDFs in the owner's ~/Estia-KB and the ones the user sends, read by tools/documents (no AI
        # model) into her index; off without [documents]
        self.documents: Documents | None = None
        if cfg.documents_enabled:
            helper = Helper(cfg.documents_helper, cfg.documents_helper_timeout, status=self.part_status)
            self.documents = Documents(
                store, cfg.documents_folder, helper, status=self.part_status, tz=cfg.timezone, clock=clock
            )
        self.documents_host = DocumentsHost(self, first_seconds=DOCUMENTS_FIRST_SECONDS)
        self.tools = Tools(
            store,
            self.profile,
            cfg.timezone,
            self._fetch,
            status=self.health.report,
            projects=Projects(cfg) if cfg.projects_enabled else None,  # an extra: off without [projects]
            mail=self._mail_tools,
            devices=self.devices,
            calendar=self.calendar,
            music=self.music,
            calls=cfg.calls_enabled,
            web=self.web,
            maps=self.maps,
            reminders=self.reminders,
            videos=self.videos,
            documents=self.documents,
            chords=ChordTool(language=lambda: self.language) if cfg.chords_enabled else None,  # off without [chords]
            base_schemas=tool_schemas(
                host_label=cfg.host_label, checks=[check.label for check in cfg.health_extra_checks]
            ),
            project_schemas=project_schemas(host_label=cfg.host_label, projects_root=cfg.projects_root),
            name=cfg.name,
        )
        # Hermes "no-agent" watchers: checks without Gemma, alerts as notes to the allowed user; in quiet hours (the
        # initiative's, else 22:00-08:00: never waking anyone) they wait for the morning. Security notes go at once.
        self.scheduler = Scheduler(
            store,
            self._alert,
            cfg.timezone,
            clock=clock,
            host=cfg.host_label,
            quiet_hours=quiet_hours_of(cfg.initiative_quiet_hours) or QUIET_HOURS,
        )
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
            language=lambda: self.language,
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
            after_learning=self.followups,
        )
        self.tools.set_consolidate(self.profiler.consolidate_for)
        self.tools.set_learn_page(self.profiler.learn_page)
        self.tools.set_notify(self._tool_note)

    def _wire_commands(self) -> None:
        """The slash commands and the chosen voice."""
        cfg, store = self.cfg, self.store
        voice_client = self.voice or self._call_voice  # the same service: the one asked whether it has a language
        self.commands = Commands(
            store,
            self.profile,
            self.context,
            cfg.timezone,
            self.status,
            self._on_new,
            learn=self._learn_command,
            voice=self.voice is not None,
            set_voice=self._set_voice,
            language=self.language,
            set_language=self._set_language,
            voice_languages=voice_client.languages if voice_client else None,
            reminders=self.reminders,
            initiative=self.initiative,
            babble=self.babble,
            guard=self.guard,
            capabilities=self.capability_lines,
            changed=self._settings_changed,
            docs=self.documents_host.docs_command if self.documents is not None else None,
        )
        chosen = store.get_state(VOICE_NAME_KEY)
        if chosen in VOICES:
            self._set_voice(chosen)

    async def run(self) -> None:
        self.lifecycle.started_at = self._clock()
        self.lifecycle.offline_since = self.store.get_state(ALIVE_KEY)
        await self.signal.start()
        await (
            self._pin_check_at_start()
        )  # before recovery: nothing recovered reaches a model that is not the pinned one
        await self.lifecycle.name_profile()
        await self.lifecycle.profile_picture()
        self.context.refresh_snapshot()
        if self.guard.locked:  # the late note would not go out: the check waits for /unlock
            self._start_up_waits = True
            log.warning("lock: still locked from before the restart; nothing is sent until /unlock")
        else:
            # before recovery: a note that may have gone out is never sent again
            await self.lifecycle.reminders_start_up()
        self.lifecycle.initiative_start_up()  # the same for a message she started
        await self.lifecycle.recover()
        await self.lifecycle.welcome()
        if self.model_pin is not None and self.model_pin.paused:  # after recovery: it never sends this note again
            await self._alert(self._paused_note())
        if self.followups is not None:
            self.followups.start()  # plans said before the first start with follow-ups are never looked at
        self.lifecycle.sweep_files()
        self.lifecycle.mark_alive()
        async with asyncio.TaskGroup() as group:
            group.create_task(self._receive_loop(), name="receive")
            group.create_task(self.status_host.problems_loop(), name="receive-problems")
            group.create_task(self.status_host.linked_devices_loop(), name="linked-devices")
            group.create_task(self.status_host.security_level_loop(), name="security-level")
            group.create_task(self._worker(), name="worker")
            group.create_task(self.profiler.run_forever(), name="profiler")
            group.create_task(self.lifecycle.heartbeat(), name="heartbeat")
            group.create_task(self.scheduler.run_forever(), name="watchers")
            group.create_task(self.lifecycle.sweeper(), name="sweeper")
            group.create_task(self._warm_up(self.language), name="voice-warm-up")
            practice = self.babble.running()
            if practice is not None and practice.code != self.language:  # a restart during practice: its voice too
                group.create_task(self._warm_up(practice.code), name="practice-warm-up")
            group.create_task(self.babble.run_forever(), name="practice")
            if self.mail_sync and self.mail_sorter:
                group.create_task(self.mail_sync.run_forever(), name="mail-sync")
                group.create_task(self.mail_sorter.run_forever(), name="mail-sort")
            if self.morning_digest:
                group.create_task(self.morning_digest.run_forever(), name="morning-digest")
            if self.calls:
                group.create_task(self.call_host.calls_forever(), name="calls")
            if self.voice_ready is not None:
                group.create_task(self.voice_ready.run_forever(), name="voice-ready")
            if self.reminder_scheduler:
                group.create_task(self.reminder_scheduler.run_forever(), name="reminders")
            if self.initiative:
                group.create_task(self.initiative.run_forever(), name="initiative")
            if self.music:
                group.create_task(self.lifecycle.music_reminder(), name="music-reminder")
            if self.model_pin is not None:
                group.create_task(self._model_loop(), name="model-pin")
            if self.cfg.keep_days is not None:
                group.create_task(self.lifecycle.retention_loop(), name="retention")
            if self.documents is not None:
                group.create_task(self.documents_host.documents_forever(), name="documents")

    # status: StatusHost (status_host.py) does the work; these names stay for the tests, the commands and her rules
    def capability_lines(self) -> list[str]:
        return self.status_host.capability_lines()

    async def _status_lines(self) -> list[str]:
        return await self.status_host.status_lines()

    async def status(self) -> StatusInfo:
        return await self.status_host.status()

    def on_problem(self, problem: ReceiveProblem) -> None:
        self.status_host.on_problem(problem)

    async def check_linked_devices(self) -> int | None:
        return await self.status_host.check_linked_devices()

    def _settings_changed(self) -> None:
        """A level her list shows changed (/voice, /initiative, /evening, the Mac's security): her rules follow from the
        next answer."""
        self.context.set_rules(self._rules(self.language))

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
            reminders=cfg.reminders_enabled,
            projects=cfg.projects_enabled,
            documents=cfg.documents_enabled,
            chords=cfg.chords_enabled,
            language=language,
            practice_last=prompts.practice_last(self.babble.last()),
            capabilities=self.capability_lines(),
        )

    def _practice_rules(self, code: str, level: str) -> str:
        """The rules of a practice answer: the practice line in place of the language line, and no tool sections (no
        tools are offered during practice)."""
        cfg = self.cfg
        return prompts.rules(
            name=cfg.name,
            host=cfg.host_label,
            practice=(code, level),
            practice_last=prompts.practice_last(self.babble.last()),
        )

    def _practice_changed(self) -> None:
        """Practice started or ended: her rules name the last setting ("what can you do?"); a started language's voice
        is loaded in the background."""
        self.context.set_rules(self._rules(self.language))
        session = self.babble.running()
        if session is not None and session.code != self.language:
            self._background.add(task := asyncio.create_task(self._warm_up(session.code)))
            task.add_done_callback(self._background.discard)

    def _practised(self, aci: str) -> str | None:
        """The language this user practises now (a call takes it), or None."""
        session = self.babble.running(aci)
        return session.code if session is not None else None

    def _practice_of(self, message: Message) -> Session | None:
        """The running practice session this message belongs to (it was moved into its conversation), or None."""
        session = self.babble.running(message.peer_aci)
        return session if session is not None and session.conversation_id == message.conversation_id else None

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

    def _is_busy(self) -> bool:
        return self._processing or not self.queue.empty() or not self._no_call.is_set()

    # voice calls: CallHost (call_host.py) does the work; these names stay for the tests and calls.Host, one release
    def call_started(self, aci: str, *, practice: bool = False) -> None:
        self.call_host.call_started(aci, practice=practice)

    def call_ended(self) -> None:
        self.call_host.call_ended()

    async def call_turn(
        self,
        aci: str,
        text: str,
        on_tools: Callable[[], None],
        *,
        practice: bool = False,
        on_retry: Callable[[], None] | None = None,
    ) -> Turn:
        return await self.call_host.call_turn(aci, text, on_tools, practice=practice, on_retry=on_retry)

    async def call_warm_up(self, aci: str, *, practice: bool = False) -> None:
        await self.call_host.call_warm_up(aci, practice=practice)

    async def practice_call_ended(self, aci: str) -> None:
        await self.call_host.practice_call_ended(aci)

    async def call_drop_waiting(self, aci: str) -> None:
        await self.call_host.call_drop_waiting(aci)

    def call_interrupted(self, reply_id: int, heard: str) -> None:
        self.call_host.call_interrupted(reply_id, heard)

    async def call_note(self, aci: str, text: str) -> None:
        await self.call_host.call_note(aci, text)

    async def call_chords(self, aci: str) -> None:
        await self.call_host.call_chords(aci)

    async def call_summary(self, aci: str, lines: list[str], minutes: int) -> None:
        await self.call_host.call_summary(aci, lines, minutes)

    @property
    def _glance(self) -> CallGlance | None:
        return self.call_host.glance  # the host holds it; the tests read it here

    # start-up, recovery, sweeping and retention: Lifecycle (lifecycle.py) does the work; these names stay for the tests,
    # `process` and `_unlocked`
    def sweep_files(self) -> int:
        return self.lifecycle.sweep_files()

    async def retention_pass(self) -> bool:
        return await self.lifecycle.retention_pass()

    def _welcome_text(self) -> str:
        return self.lifecycle.welcome_text()

    def _is_stale(self, message: Message) -> bool:
        return self.lifecycle.is_stale(message)

    async def _offline_note(self, message: Message) -> None:
        await self.lifecycle.offline_note(message)

    async def _reminders_start_up(self) -> None:
        await self.lifecycle.reminders_start_up()

    def _take_digest(self) -> str:
        count = int(self.store.get_state(UNANNOUNCED_KEY) or 0)
        if not count:
            return ""
        self.store.set_state(UNANNOUNCED_KEY, "0")
        return prompts.DIGEST.format(count=count, things="thing" if count == 1 else "things")

    async def handle_envelope(self, envelope: dict[str, Any]) -> None:
        decision = classify(envelope, self.cfg.allowed_acis)
        if decision.kind is Kind.VOICE and self.voice is None:  # voice off: a file like any other
            decision = Decision(
                Kind.UNSUPPORTED,
                reason="voice is off",
                discard=decision.audio + decision.discard,
                device=decision.device,
            )
        if decision.kind is Kind.DOCUMENT and self.documents is None:  # documents off: a file like any other
            decision = Decision(
                Kind.UNSUPPORTED,
                reason="documents are off",
                discard=decision.documents + decision.discard,
                device=decision.device,
            )
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
        if not self._identity_changed or aci in self._identity_changed:  # their message opened: trusted again
            self._identity_changed.discard(aci)
            self.part_status.identity_ok()
        if not self.guard.known(aci, decision.device):  # a device she does not answer (yet): held, never read
            self._discard(decision.photos + decision.audio + decision.documents)
            note = self.guard.hold(aci, decision.device)
            if note and (sent := await self._deliver(aci, note, "the held-device note")):  # also while locked
                self.lifecycle.keep_sent(aci, note, sent)  # in her history: she knows a message was held
            return
        self.guard.seen(aci, decision.device)  # a device she answers: its days of silence start again
        words = decision.text.split() if decision.kind is Kind.COMMAND else []
        command = words[0].lower() if words else ""
        if self.guard.locked:  # nothing works but /unlock from the device that locked her
            self._discard(decision.photos + decision.audio + decision.documents)
            reply = self.guard.unlock(aci, decision.device) if command == "/unlock" else None
            if reply is None:
                log.info("a message while locked: not read, not answered")
                return
            await self._lock_reply(aci, timestamp, reply)
            if not self.guard.locked:
                await self._unlocked()
            return
        if command == "/lock":  # at once, not behind an answer in the queue: what is on its way is held back
            reply = self.guard.lock(aci, decision.device)
            if reply is not None:
                await self._lock_reply(aci, timestamp, reply)
                await self._locked_now()
            return
        kind = _KINDS[decision.kind]
        text = decision.text
        if decision.kind is Kind.PHOTO:
            text = f"[photo] {decision.text or '(no caption)'}"
            others = sum(1 for ref in decision.discard if ref.content_type != LONG_TEXT)
            if others:  # said in the message itself, so Gemma does not invent what the file holds
                text += f" (+{others} other file{'s' if others > 1 else ''} I can't open)"
        if decision.kind is Kind.DOCUMENT:  # saved when its turn comes; the words sent with it on the line below
            text = prompts.DOCUMENT_MARK + (f"\n{decision.text}" if decision.text else "")
        if decision.kind is Kind.VOICE:  # the transcript is added when its turn comes
            # D4: audio Signal did not mark as a recorded voice note is someone else's voice, not the user's words
            text = prompts.AUDIO_MARK if decision.audio[0].voice_note is False else prompts.VOICE_MARK
            if decision.text:  # typed with the file: kept on its own line, below the transcript to come
                text += f"\n{prompts.TYPED_WITH_AUDIO.format(text=decision.text)}"
        conversation = self.store.current_conversation()
        with self.store.tx():  # a photo message and its attachments, or nothing
            message_id = self.store.add_incoming(
                conversation.id, aci, timestamp, kind, text, defer_seq=True, device=decision.device
            )
            if message_id is None:
                log.info("duplicate delivery of %s ignored", timestamp)
                return
            for ref in (*decision.photos, *decision.audio, *decision.documents):
                self.store.add_attachment(message_id, ref.id, ref.content_type, ref.size, filename=ref.filename)
        log.info(
            "message %d in: %s, %d chars, %d photos, %d voice, %d documents",
            message_id,
            kind,
            len(text),
            len(decision.photos),
            len(decision.audio),
            len(decision.documents),
        )
        self.profiler.note_activity()
        if self.initiative is not None:
            self.initiative.note_activity()  # a text she is writing waits: Gemma's slot is the user's
        # queued before anything is awaited: a practice call that ends meanwhile drains the queue first, so a text
        # stored during the call is its practice turn even while its read receipt is still on its way
        if kind == "command" and not self._no_call.is_set() and text.split()[0].lower() not in WAITING_COMMANDS:
            # during a call a command (no Gemma) is answered at once; /voice name even changes the call's voice
            self._background.add(task := asyncio.create_task(self._command_now(message_id)))
            task.add_done_callback(self._background.discard)
        else:
            self.queue.put_nowait(message_id)
        with contextlib.suppress(SignalError, NotAllowedError):
            await self.signal.receipt(aci, timestamp)

    async def process(self, message_id: int) -> None:
        message = self.store.message(message_id)
        if message.status != "received":
            return
        if self.guard.locked:  # it came in before /lock: never answered (nor later)
            self.store.set_status(message.id, "ignored")
            self._drop_attachments(message.id)
            log.info("message %d not answered: locked", message.id)
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
        held = self.babble.held() if message.kind == "text" else None
        if held is not None and held.aci.lower() == message.peer_aci.lower():
            # practice ended while the user was away (an idle end, its recap held for the morning): the recap goes out
            # now, so they know before this message is answered in chat
            await self.babble.finish(held)
        # joins the conversation now, after earlier answers, in the conversation that is open now (/new); a text
        # during practice joins the practice conversation (never the chat's history)
        practising = self.babble.running(message.peer_aci) if message.kind == "text" else None
        target = practising.conversation_id if practising is not None else self.store.current_conversation().id
        self.store.assign_seq(message.id, target)
        message = self.store.message(message.id)
        if practising is not None and self.devices is not None:
            self.devices.cancel()  # a practice message is never a device's yes
        if message.kind == "command":
            answer = await self.commands.handle(message)
            quotes = code_notes.quotes_outside(message.text), code_notes.quotes_private(message.text)
            await self._reply(message, answer, "command", "answered", outside=quotes[0], private=quotes[1])
            return
        if message.kind == "unsupported":
            await self._reply(message, prompts.TEXT_ONLY_VOICE if self.voice else prompts.TEXT_ONLY, "note", "answered")
            return
        typing = asyncio.create_task(self._typing_loop(message.peer_aci))
        busy = asyncio.create_task(self._busy_note(message))
        self._busy[message.id] = busy
        text: str
        notes: list[str] = []
        outside: set[str] = set()  # the notes that quote outside text (code_notes)
        private: set[str] = set()  # those quoting mail, a calendar or documents
        kind: MessageKind
        status: MessageStatus
        try:
            attachments = self.store.attachments(message.id)
            unheard: tuple[str, MessageStatus] | None = None
            if message.text.split("\n", 1)[0] in (prompts.VOICE_MARK, prompts.AUDIO_MARK):  # not transcribed yet
                language = practising.code if practising is not None else None  # practice: heard in its language
                unheard = await self._hear(message, attachments, language=language)
                message = self.store.message(message.id)
            if message.text.startswith((prompts.VOICE_MARK, prompts.AUDIO_MARK)):  # heard (also before a restart)
                attachments = []
            document_note: str | None = None
            if message.text.split("\n", 1)[0] == prompts.DOCUMENT_MARK:  # PDFs not saved yet
                document_note, saved = await self._save_documents(message, attachments)
                message = self.store.message(message.id)
                attachments = []
                words = message.text.partition("\n")[2]
                if saved and words and practising is None:  # her answer to the words sent with it follows
                    await self._send(
                        self.store.add_outgoing(
                            message.conversation_id, message.peer_aci, "note", document_note, message.id, private=True
                        )
                    )
                    document_note = None
            elif message.text.startswith(prompts.DOCUMENT_MARK):  # saved before a restart: never saved again
                attachments = []
                where, _, words = message.text.removeprefix(prompts.DOCUMENT_MARK).strip().partition("\n")
                if not words:  # nothing to answer: code's note again, no Gemma
                    saved_before = where and where != prompts.DOCUMENT_NONE_SAVED
                    document_note = (
                        prompts.DOCUMENT_SAVED_BEFORE.format(path=where)
                        if saved_before
                        else prompts.DOCUMENT_NOT_SAVED["failed"]
                    )
            images, photo_notes = await self._load_photos(message, attachments)
            # an "ask first" device action waits for this message: a clear yes is carried out by code, not Gemma
            confirmed = (
                await self.devices.confirm(message.text, message.peer_aci)
                if self.devices and self.devices.pending and practising is None
                else None
            )
            if unheard is not None:  # no usable voice note: a note, no Gemma
                text, kind, status = unheard[0], "note", unheard[1]
            elif document_note is not None:  # saved (or not), no words with it: code's note, no Gemma
                text, kind, status = document_note, "note", "answered"
                outside = private = {document_note}  # the PDF's name
            elif confirmed is not None:
                text, kind, status = confirmed, "note", "answered"
            elif attachments and not images:  # no usable photo: a note, no Gemma (files go first, in finally)
                text, kind, status = prompts.PHOTO_FAILED, "note", "answered"
            else:
                conversation = self.context.enforce_limit(self.store.conversation(message.conversation_id))
                spoken = message.text.startswith((prompts.VOICE_MARK, prompts.AUDIO_MARK))
                reply = await self.brain.answer(
                    conversation,
                    message,
                    images,
                    recent=self._recent_photos(message),
                    practice=self.babble.turn(spoken=spoken) if practising is not None else None,
                )
                if images:
                    self._recent_photo = (message.conversation_id, message.id, images, self._clock())
                text = reply.text  # when no reminder was set: without her talk of it, code's line why after hers
                if self.language != ENGLISH and self.devices is not None and self.devices.pending:
                    # Gemma in another language may claim it is done: code's words only
                    text = "\n".join(part for part in (self.devices.question(), reply.not_set) if part)
                # separate "note" messages, never her words: in her history as code_notes results (code_notes.py)
                device_note = self.devices.take_note() if self.devices else ""
                music_note = self.music.take_note() if self.music else ""
                notes = [
                    note
                    for note in (
                        reply.sources,
                        reply.chords,  # [chords]: code's lines, after the page they came from
                        device_note,
                        music_note,
                        *photo_notes,
                        reply.notice,
                        reply.reminder,
                        reply.device,
                        self._take_digest(),
                    )
                    if note
                ]
                kind = "text"
                status = "answered"
                outside = {reply.sources, music_note} | ({reply.chords, reply.notice} if reply.read_outside else set())
                log.info(
                    "message %d answered: %d tool calls, %d prompt tokens",
                    message.id,
                    reply.tool_calls,
                    reply.prompt_tokens,
                )
        except GemmaError as exc:
            log.error("message %d: Gemma failed: %s", message.id, exc)
            text = (
                self._paused_note()
                if isinstance(exc, ModelPausedError)
                else prompts.UNAVAILABLE.format(name=self.cfg.name)
            )
            kind = "note"
            status = "failed"
            if self.devices:
                self.devices.cancel()  # the user never saw a question, so no yes may act on it
            device_note = self.devices.take_note() if self.devices else ""
            music_note = self.music.take_note() if self.music else ""
            # what changed before Gemma failed is still reported (and never carried to a later answer)
            notes, outside = [note for note in (device_note, music_note) if note], {music_note}
        finally:
            typing.cancel()
            busy.cancel()
            self._busy.pop(message.id, None)
            self._drop_attachments(message.id)  # photos live only until answered
            with contextlib.suppress(SignalError, NotAllowedError):
                await self.signal.typing(message.peer_aci, stop=True)
        await self._reply(message, text, kind, status, outside=text in outside, private=text in private)
        for note in notes:
            out_id = self.store.add_outgoing(
                message.conversation_id, message.peer_aci, "note", note, message.id, outside=note in outside
            )
            await self._send(out_id)
        if kind == "text" and self._speaks(message):
            await self._speak(message, text)
        self.profiler.note_activity()

    # documents: DocumentsHost (documents_host.py) does the work; these names stay for the tests and `process`
    async def _save_documents(self, message: Message, attachments: list[Attachment]) -> tuple[str, bool]:
        return await self.documents_host.save_documents(message, attachments)

    async def documents_tick(self) -> None:
        await self.documents_host.documents_tick()

    async def _hear(
        self, message: Message, attachments: list[Attachment], *, language: str | None = None
    ) -> tuple[str, MessageStatus] | None:
        """Transcribe the message's voice note into its text ("[voice] ..."), in `language` when given (practice); a
        note and status when it can't be."""
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
            transcript = await self.voice.transcribe(data, language=language)
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
        # someone else's voice (an audio file): its echo without links (unlinked, then the net), as code's other notes
        heard = netted(unlinked(transcript.text)) if mark == prompts.AUDIO_MARK else transcript.text
        if heard and self.store.get_state(VOICE_ECHO_KEY) == "on":
            echo = prompts.VOICE_ECHO.format(text=heard)
            out_id = self.store.add_outgoing(  # someone else's voice: outside text
                message.conversation_id, message.peer_aci, "note", echo, message.id, outside=mark == prompts.AUDIO_MARK
            )
            await self._send(out_id)
        return None

    def _speaks(self, message: Message) -> bool:
        """Whether Gemma's answer to this message also goes out as a voice note (/voice auto, on, off)."""
        if self.voice is None:
            return False
        mode = self.store.get_state(VOICE_MODE_KEY) or "auto"
        return mode == "on" or (mode == "auto" and message.text.startswith(prompts.VOICE_MARK))

    async def _speak(self, message: Message, answer: str) -> None:
        """The spoken version of a sent answer as a Signal voice note; on any failure the text stands alone. A practice
        answer is spoken in the practised language, without its "→" line."""
        practice = self._practice_of(message)
        if practice is not None:
            answer = tidy(answer, spoken=True)
        words = spoken_text(answer, self.cfg.voice_max_spoken_chars)
        if self.voice is None or not words or self.guard.locked:
            return
        path: Path | None = None
        try:
            language = practice.code if practice is not None else None
            path = await to_m4a(await self.voice.speak(words, language=language), self.cfg.voice_dir)
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

    def _mail_synced(self) -> bool:
        """The mail sync finished a round since the start (the digest's mail part waits for it)."""
        return self.mail_sync is None or self.mail_sync.rounds > 0

    async def _alert(self, text: str, *, private: bool = False) -> None:
        """A watcher's alert or recovery, the digest: a note to every allowed user (`private`: mail or calendar text)."""
        conversation = self.store.current_conversation()
        for aci in sorted(self.cfg.allowed_acis):
            await self._send(self.store.add_outgoing(conversation.id, aci, "note", text, None, private=private))
        log.warning("alert sent: %d line(s)", text.count("\n") + 1)

    def _on_new(self) -> None:
        """/new: learn from the closing conversation soon; the kept video's captions go (memory only)."""
        self.profiler.request_soon()
        self.videos.clear()

    async def _tool_note(self, message: Message, text: str) -> None:
        """Code's note during an answer (before a long video is read): sent at once, never in that answer's own history
        (code_notes.in_order); it says what takes the time, so the busy note is not needed."""
        busy = self._busy.get(message.id)
        if busy is not None:
            busy.cancel()
        await self._send(self.store.add_outgoing(message.conversation_id, message.peer_aci, "note", text, message.id))

    async def _learn_command(self, url: str, message: Message) -> str:
        """/learn <link>: read the page, extract the facts about the user (one structured Gemma call), report."""
        if video_id(url) is not None:  # /learn keeps the page it reads; a video's captions are never kept
            return prompts.VIDEO_LEARN_COMMAND
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
        if result.over_cap_by:  # one batch: nothing of it was saved
            return prompts.LEARN_FULL.format(limit=self.profile.max_chars, over=result.over_cap_by)
        added = [fact for fact in self.profile.facts() if fact.id in result.added]
        # the page's own title is outside text: without its links, else the link the user sent (not where it led)
        lines = [f"Learned from {clean_note(page.title, PAGE_TITLE_CHARS) or url}:"]
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

    async def _reply(
        self,
        message: Message,
        text: str,
        kind: MessageKind,
        status: MessageStatus,
        *,
        outside: bool = False,
        private: bool = False,
    ) -> None:
        with self.store.tx():
            out_id = self.store.add_outgoing(
                message.conversation_id, message.peer_aci, kind, text, message.id, outside=outside, private=private
            )
            self.store.set_status(message.id, status)
        await self._send(out_id)

    async def _send(self, out_id: int) -> None:
        """Send a stored message (with its footer, a code line kept apart from the text, in italics) and mark it."""
        out = self.store.message(out_id)
        if self.guard.locked:  # nothing goes out while locked, and nothing later: never sent again
            self.store.set_status(out_id, "ignored")
            log.info("message %d held back: locked", out_id)
            return
        text = f"{out.text}\n*{out.footer}*" if out.footer else out.text
        timestamp = await self._deliver(out.peer_aci, text, f"message {out_id}")
        if timestamp is None:
            self.store.set_status(out_id, "failed")
            return
        self.store.mark_sent(out_id, timestamp)
        log.info("message %d sent: %s, %d chars", out_id, out.kind, len(out.text))

    async def _deliver(self, aci: str, text: str, what: str) -> int | None:
        """Send one text (three tries); its Signal timestamp, or None when it could not be sent. Every text goes out
        here: its markup (**bold**, a "- " list, …) becomes Signal's text styles (signal_text); the caller's stored
        text keeps the markup, so a resend converts again. Should the conversion fail, the text goes out as written,
        without styles (as signal_io does with styles signal-cli would refuse)."""
        try:
            message = styled(text)
        except Exception:
            log.exception("%s: text styles failed; sent as written", what)  # never the text
            message = Styled(text, ())
        for attempt in range(3):
            try:
                return await self.signal.send(aci, message.text, styles=message.styles)
            except UntrustedIdentityError:
                self._identity_changed.add(aci)
                self.part_status.identity_changed()  # doctor and /status show it, with the steps
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

    # the lock, the model's pin

    async def _lock_reply(self, aci: str, timestamp: int, reply: str) -> None:
        """/lock and /unlock are answered at once, in code's words, and kept out of the history."""
        with contextlib.suppress(SignalError, NotAllowedError):
            await self.signal.receipt(aci, timestamp)
        await self._deliver(aci, reply, "the lock note")

    async def _locked_now(self) -> None:
        """Locked: a running call ends, a device waiting for a yes waits no more."""
        if self.devices is not None:
            self.devices.cancel()
        if self.calls is not None and self.calls.in_call:
            await self.calls.hangup()

    async def _unlocked(self) -> None:
        """Unlocked: the start-up check of the reminders when it waited, then what came due meanwhile."""
        if self._start_up_waits:
            self._start_up_waits = False
            await self._reminders_start_up()
        if self.reminder_scheduler is not None:
            self.reminder_scheduler.notify()

    def _paused_why_now(self) -> str:
        return self.model_pin.why if self.model_pin is not None else ""

    def _paused_note(self) -> str:
        if self._paused_why_now() == "cloud":
            return prompts.MODEL_CLOUD.format(model=self.cfg.model, host=self.cfg.host_label, name=self.cfg.name)
        return prompts.MODEL_PAUSED.format(model=self.cfg.model, host=self.cfg.host_label)

    def _paused_spoken(self) -> str:
        return prompts.MODEL_CLOUD_SPOKEN if self._paused_why_now() == "cloud" else prompts.MODEL_PAUSED_SPOKEN

    def _keep_pause(self) -> None:
        why = self._paused_why_now()
        self.part_status.model_paused(why == "digest")
        self.part_status.model_cloud(why == "cloud")
        if why:
            self._paused_why = why

    async def _pin_check_at_start(self) -> None:
        """The model's pin, read once before anything reaches the model; status.json keeps what is known now. Later
        changes are told as they happen (a pause found now is told after recovery)."""
        if self.model_pin is None:
            self._keep_pause()  # no pin: nothing is paused
            return
        if await self.model_pin.check() is not None:
            self._keep_pause()
        self.model_pin.on_change(self._model_changed)

    def _model_changed(self, paused: bool) -> None:
        """Answers paused or back: status.json, and one note to the user (code's words)."""
        was = self._paused_why
        self._keep_pause()
        if paused:
            note = self._paused_note()
        elif was == "cloud":
            note = prompts.MODEL_CLOUD_RESUMED.format(model=self.cfg.model, host=self.cfg.host_label)
        else:
            note = prompts.MODEL_RESUMED.format(model=self.cfg.model)
        self._background.add(task := asyncio.create_task(self._alert(note)))
        task.add_done_callback(self._background.discard)

    async def _model_loop(self) -> None:
        assert self.model_pin is not None
        while True:
            await asyncio.sleep(MODEL_CHECK_SECONDS)
            try:
                await self.model_pin.check()
            except Exception:
                log.exception("model pin: the check failed")


BRIDGE_LOG_BYTES = 10_000_000  # bridge.log is rotated in-process at this size ...
BRIDGE_LOG_FILES = 5  # ... keeping this many older files (bridge.log.1 to .5)
STREAM_LOG_BYTES = 10_000_000  # launchd.log (what the bridge writes to stdout and stderr) is emptied over this size


def cap_file(fd: int, limit: int) -> bool:
    """Empty the regular file behind `fd` when it is larger than `limit` (a terminal or a pipe is left alone);
    whether it was emptied. launchd opens the daemon's log for appending, so writing goes on at the new end."""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= limit:
            return False
        os.ftruncate(fd, 0)
        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
        return True
    except OSError:
        return False


class CappedStream:
    """sys.stdout or sys.stderr with its file kept under a size: checked before every write."""

    def __init__(self, stream: TextIO, limit: int) -> None:
        self._stream = stream
        self._limit = limit

    def write(self, text: str) -> int:
        with contextlib.suppress(OSError, ValueError):
            self._stream.flush()
            cap_file(self._stream.fileno(), self._limit)
        return self._stream.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def cap_streams(limit: int) -> None:
    """launchd.log: the bridge's stdout and stderr (uncaught errors, Python's warnings) go there, outside bridge.log.
    Emptied now when over `limit`, and before any write that finds it over."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            fd = stream.fileno()
        except (OSError, ValueError, AttributeError):
            continue
        if stat.S_ISREG(os.fstat(fd).st_mode):
            cap_file(fd, limit)
            setattr(sys, name, CappedStream(stream, limit))


def setup_logging(
    log_dir: Path,
    *,
    foreground: bool,
    bridge_cap: int = BRIDGE_LOG_BYTES,
    stream_cap: int = STREAM_LOG_BYTES,
) -> None:
    """bridge.log rotated in-process (10 MB x 5 older files), launchd.log capped in-process: nothing outside the bridge
    (no newsyslog, no root) touches its logs. Every file it makes is private (umask 077, as the daemon's Umask)."""
    os.umask(0o077)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir.chmod(0o700)
    cap_streams(stream_cap)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "bridge.log", maxBytes=bridge_cap, backupCount=BRIDGE_LOG_FILES, encoding="utf-8"
    )
    with contextlib.suppress(OSError):
        (log_dir / "bridge.log").chmod(0o600)  # one made before the umask (an older install)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if foreground:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


def signal_data() -> Path:
    """signal-cli's data folder under $HOME (her own user's account record keeps /var/empty as home: Java would look
    there), the same folder signal-cli uses by default."""
    return Path.home() / ".local" / "share" / "signal-cli"


def signal_client(cfg: Config) -> SignalClient:
    """signal-cli as configured: with [calls] the JVM build (Homebrew's native one cannot place calls) with Java,
    IPv4 and the call helper in its environment, subscribed to call events."""
    if not cfg.calls_enabled:
        return SignalClient(
            signal_cli_command(cfg.signal_cli, cfg.bot_number, config_dir=signal_data()), cfg.allowed_acis
        )
    cfg.call_socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    java_home = cfg.java_home or find_java_home()
    if java_home is None:
        log.warning("calls: no Java found with /usr/libexec/java_home; set java_home in [calls] if signal-cli fails")
    env = call_env(java_home=java_home, tunnel=cfg.call_tunnel, socket_dir=cfg.call_socket_dir, home=Path.home())
    command = signal_cli_command(str(cfg.call_signal_cli), cfg.bot_number, jvm=True, config_dir=signal_data())
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


def model_pin(cfg: Config, gemma: OllamaGemma) -> ModelPin:
    """Always: Ollama's own entry for the alias is read at the start, every 5 minutes and before every chat; once a
    read sees a cloud model, no message goes to it; with model_digest, any other digest pauses answers too."""
    return ModelPin(gemma.model_entry, cfg.model_digest)


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
    pin = model_pin(cfg, gemma)
    log.info(
        "starting: model %s (%s), %d allowed account(s)",
        cfg.model,
        "pinned" if cfg.model_digest else "not pinned",
        len(cfg.allowed_acis),
    )
    try:
        asyncio.run(_serve(App(cfg, store, signal, gemma, Fetcher(), model_pin=pin)))
    finally:
        store.close()
