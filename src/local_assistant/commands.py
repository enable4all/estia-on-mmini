"""Slash commands, answered without Gemma (spec section 5)."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.babble import LEVELS, NOT_YET, OFFERED, START_LEVEL, Babble, listing, listing_lines
from local_assistant.context import ContextBuilder
from local_assistant.initiative import Initiative
from local_assistant.language import ENGLISH, LANGUAGE_KEY, LANGUAGES, find
from local_assistant.profile import Profile, by_category, category_of
from local_assistant.reminders import ReminderError, Reminders, parse_when
from local_assistant.soul import SOUL_MAX_CHARS, has_parts, soul_part
from local_assistant.store import Fact, Message, Store
from local_assistant.timefmt import local_date, local_datetime

log = logging.getLogger(__name__)

_SOURCES = {"chat": "learned from chat", "remember": "you asked", "link": "from a link"}
VOICE_MODE_KEY = "voice_mode"  # auto (default): a voice note gets a voice answer; on: every answer; off
VOICE_ECHO_KEY = "voice_echo"  # on: what was heard is sent first; off (default)
_VOICE_MODES = {
    "auto": "auto (a voice note gets a voice answer)",
    "on": "on (every answer)",
    "off": "off",
}
_VOICE_ECHO = {"on": "on (I show what I heard first)", "off": "off"}
VOICE_NAME_KEY = "voice_name"  # a Kokoro voice chosen with /voice name (else the config's)
# Kokoro's English voices the voice service can load: (name, description)
VOICES: dict[str, str] = {
    "am_michael": "American, male, warm",
    "am_fenrir": "American, male, deeper",
    "am_puck": "American, male, lively",
    "am_echo": "American, male",
    "am_eric": "American, male",
    "am_liam": "American, male",
    "am_onyx": "American, male, low",
    "am_adam": "American, male",
    "bm_george": "British, male",
    "bm_fable": "British, male",
    "bm_lewis": "British, male",
    "bm_daniel": "British, male",
    "af_heart": "American, female",
    "af_bella": "American, female",
    "af_nicole": "American, female, soft",
    "af_sarah": "American, female",
    "bf_emma": "British, female",
    "bf_isabella": "British, female",
}


@dataclass(frozen=True)
class StatusInfo:
    gemma: str
    queue_length: int
    last_learning_run: str | None
    db_bytes: int
    messages: int
    restarts: int
    parts: tuple[str, ...] = ()  # what each part that is turned on last did (part_status), one line each
    security: tuple[str, ...] = ()  # part_status.security_lines: the safety number, her devices, the model's pause
    reminders: str = ""  # "Reminders: 3 open · next Tue 13 Oct 07:00 (call) · 0 failed" (times and counts only)
    initiative: tuple[str, ...] = ()  # the messages she starts: counts, the improvised note, the last skips


# the lock (/lock, /unlock) in the state table: it survives a restart and needs no sudo
LOCK_KEY = "lock:on"  # "on" while locked
LOCK_DEVICE_KEY = "lock:device"  # "<aci> <device>": only that device can /unlock
LOCK_SINCE_KEY = "lock:since"  # when (epoch seconds)
UNLOCK_WAIT_SECONDS = 600  # /unlock works this long after /lock at the earliest
HELD_NOTED_KEY = "devices:noted:{aci}:{device}"  # when the last held-device note went out
HELD_NOTE_SECONDS = 3_600.0  # at most one note an hour per held device
PHONE = 1  # Signal's device 1: the phone the account was registered on
TRUST_DAYS = 14  # an allowed linked device silent this long is held again (Signal reuses a removed device's number)


class Guard:
    """Which of the allowed users' devices she answers, and the lock. Rollout rule: device 1 (the phone the account was
    registered on) of every allowed account is known from the start; every linked device (2 and up) is held until a
    known device allows it with /devices ok N. A held message gets no answer and never reaches Gemma: a code note goes to
    the account, so the phone sees it too. Calls name no device: a call from any of the user's devices rings.

    Signal gives a new linked device the lowest free number from 2, so a number is not a device: an allowed device
    silent for TRUST_DAYS is held again, and /devices forget N (after unlinking it) removes one at once.

    /lock (from a known device) stops every answer and call until /unlock comes from the same device, at least
    UNLOCK_WAIT_SECONDS later; nothing else works meanwhile. The lock is kept in the state table: it survives a
    restart."""

    def __init__(
        self, store: Store, acis: frozenset[str], *, tz: ZoneInfo, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self._acis = frozenset(aci.lower() for aci in acis)
        self._tz = tz
        self._clock = clock

    def seed(self) -> int:
        """The phone (device 1) of each allowed account, when it is not in the table yet; how many were added."""
        added = 0
        for aci in sorted(self._acis):
            if self._store.device_source(aci, PHONE) is None:
                self._store.set_device(aci, PHONE, "primary")
                added += 1
        if added:
            log.info("devices: the phone of %d account(s) is known from the start", added)
        return added

    def _stamp(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).isoformat(timespec="seconds")

    def _silent(self, aci: str, device: int) -> bool:
        """An allowed linked device whose last message (or its allowing) is older than TRUST_DAYS."""
        item = self._store.device(aci, device)
        if item is None or item.source != "allowed":
            return False
        try:
            last = datetime.fromisoformat(item.last_seen or item.first_seen).timestamp()
        except ValueError:
            return True
        return self._clock() - last > TRUST_DAYS * 86_400

    def known(self, aci: str, device: int | None) -> bool:
        """Whether she answers this device of this account: the phone, or a device allowed with /devices ok N that
        sent a message within TRUST_DAYS."""
        if device is None or device < 1 or aci not in self._acis:
            return False
        source = self._store.device_source(aci, device)
        return source == "primary" or (source == "allowed" and not self._silent(aci, device))

    def seen(self, aci: str, device: int | None) -> None:
        """A message from a device she answers: its silence starts again."""
        if device is not None and device != PHONE:
            self._store.touch_device(aci, device, self._stamp())

    def hold(self, aci: str, device: int | None) -> str:
        """A message from a device she does not answer: remembered as held; the note to send, or "" when one went out
        for this device within the hour."""
        silent = device is not None and self._silent(aci, device)
        if device is not None and (silent or self._store.device_source(aci, device) is None):
            self._store.set_device(aci, device, "held")
        log.warning("devices: a message from an unknown device (%s) was held", device if device is not None else "?")
        key = HELD_NOTED_KEY.format(aci=aci, device=device if device is not None else "none")
        last = self._store.get_state(key)
        now = self._clock()
        if last is not None and now - float(last) < HELD_NOTE_SECONDS:
            return ""
        self._store.set_state(key, f"{now:.0f}")
        if device is None:
            return prompts.DEVICE_HELD_UNNAMED
        if silent:
            return prompts.DEVICE_HELD_SILENT.format(device=device, days=TRUST_DAYS)
        return prompts.DEVICE_HELD.format(device=device)

    def listing(self, aci: str) -> str:
        """/devices: the devices she answers and the held ones, each list under a bold heading, one per line."""
        known, held = [], []
        for item in self._store.devices(aci):
            day = local_date(item.first_seen, self._tz)
            if item.source == "primary":
                known.append(prompts.DEVICES_PHONE.format(device=item.device))
            elif item.source == "allowed":
                known.append(prompts.DEVICES_ALLOWED.format(device=item.device, day=day))
            else:
                held.append(prompts.DEVICES_HELD_LINE.format(device=item.device, day=day))
        blocks = ["\n".join([prompts.DEVICES_HEADER, *(known or ["-"])])]
        if held:
            blocks.append("\n".join([prompts.DEVICES_HELD_HEADER, *held]))
        blocks.append(f"{prompts.DEVICES_CALLS}\n{prompts.DEVICES_FORGET_HINT.format(days=TRUST_DAYS)}")
        return "\n\n".join(blocks)

    def devices(self, aci: str, device: int | None, args: str) -> str:
        """/devices ok N or /devices forget N."""
        words = args.lower().split()
        if len(words) == 2 and words[0] == "forget":
            return self.forget(aci, device, words[1].lstrip("#"))
        return self.allow(aci, device, args)

    def forget(self, aci: str, device: int | None, number: str) -> str:
        """/devices forget N: only from a device she already answers; never the phone."""
        if not number.isdecimal() or int(number) < 1:
            return prompts.DEVICES_USAGE
        if not self.known(aci, device):
            log.warning("devices: /devices forget from a device that is not known was refused")
            return prompts.DEVICES_NOT_FROM_KNOWN
        wanted = int(number)
        if wanted == PHONE:
            return prompts.DEVICES_FORGET_PHONE
        if not self._store.forget_device(aci, wanted):
            return prompts.DEVICES_FORGET_UNKNOWN.format(device=wanted)
        self._store.delete_state(HELD_NOTED_KEY.format(aci=aci, device=wanted))  # a new device there gets its note
        log.info("devices: device %d forgotten from device %s", wanted, device)
        return prompts.DEVICES_FORGOTTEN.format(device=wanted)

    def allow(self, aci: str, device: int | None, args: str) -> str:
        """/devices ok N: only from a device she already answers, only for a device that was held."""
        words = args.lower().split()
        number = words[1].lstrip("#") if len(words) == 2 and words[0] == "ok" else ""
        if not number.isdecimal() or int(number) < 1:
            return prompts.DEVICES_USAGE
        if not self.known(aci, device):
            log.warning("devices: /devices ok from a device that is not known was refused")
            return prompts.DEVICES_NOT_FROM_KNOWN
        wanted = int(number)
        source = self._store.device_source(aci, wanted)
        if source in ("primary", "allowed"):
            return prompts.DEVICES_OK_ALREADY.format(device=wanted)
        if source != "held":
            return prompts.DEVICES_OK_UNKNOWN.format(device=wanted)
        self._store.set_device(aci, wanted, "allowed")
        self._store.touch_device(aci, wanted, self._stamp())  # its TRUST_DAYS start now
        log.info("devices: device %d allowed from device %s", wanted, device)
        return prompts.DEVICES_OK.format(device=wanted)

    # the lock

    @property
    def locked(self) -> bool:
        return self._store.get_state(LOCK_KEY) == "on"

    @property
    def lock_device(self) -> tuple[str, int] | None:
        """The account and device that locked her (only they can /unlock)."""
        aci, _, device = (self._store.get_state(LOCK_DEVICE_KEY) or "").partition(" ")
        return (aci, int(device)) if aci and device.isdecimal() else None

    def lock(self, aci: str, device: int | None) -> str | None:
        """/lock from a known device: locked at once; the reply. None (no answer) while locked or from any other."""
        if self.locked or device is None or not self.known(aci, device):
            return None
        now = self._clock()
        self._store.set_state(LOCK_DEVICE_KEY, f"{aci} {device}")
        self._store.set_state(LOCK_SINCE_KEY, str(int(now)))  # whole seconds, never later than now
        self._store.set_state(LOCK_KEY, "on")
        log.warning("lock: locked from device %d; no answers and no calls until /unlock", device)
        return prompts.LOCKED.format(device=device, minutes=UNLOCK_WAIT_SECONDS // 60)

    def unlock(self, aci: str, device: int | None) -> str | None:
        """/unlock: only from the device that locked her, after the wait; None (no answer) from any other device."""
        if not self.locked:
            return prompts.UNLOCK_NOT_LOCKED if self.known(aci, device) else None
        if self.lock_device != (aci, device):
            log.warning("lock: /unlock from another device was ignored")
            return None
        since = float(self._store.get_state(LOCK_SINCE_KEY) or 0)
        if self._clock() < since + UNLOCK_WAIT_SECONDS:
            at = datetime.fromtimestamp(since + UNLOCK_WAIT_SECONDS, self._tz).strftime("%H:%M")
            return prompts.UNLOCK_TOO_SOON.format(time=at)
        for key in (LOCK_KEY, LOCK_DEVICE_KEY, LOCK_SINCE_KEY):
            self._store.delete_state(key)
        log.warning("lock: unlocked")
        return prompts.UNLOCKED


StatusProvider = Callable[[], Awaitable[StatusInfo]]
Learn = Callable[[str, Message], Awaitable[str]]  # link, the /learn message → reply text
Docs = Callable[[str, Message], Awaitable[str]]  # /docs' words after the command, its message → reply text
VoiceLanguages = Callable[[], Awaitable[tuple[str, ...] | None]]  # the voice service's languages; None: no answer


class Commands:
    def __init__(
        self,
        store: Store,
        profile: Profile,
        context: ContextBuilder,
        tz: ZoneInfo,
        status: StatusProvider,
        on_new: Callable[[], None],
        learn: Learn | None = None,
        *,
        voice: bool = False,
        set_voice: Callable[[str], None] | None = None,
        language: str = ENGLISH,
        set_language: Callable[[str], None] | None = None,
        voice_languages: VoiceLanguages | None = None,
        reminders: Reminders | None = None,
        initiative: Initiative | None = None,
        babble: Babble | None = None,
        guard: Guard | None = None,
        capabilities: Callable[[], list[str]] | None = None,
        changed: Callable[[], None] | None = None,
        docs: Docs | None = None,
    ) -> None:
        """`capabilities`: code's list of what she can do here, which /help shows above the commands (the lines her
        rules carry); `changed`: told when a level that list shows changes (/voice auto|on|off, /initiative on|off,
        /evening), so her rules follow."""
        self._store = store
        self._profile = profile
        self._context = context
        self._tz = tz
        self._status_provider = status
        self._on_new = on_new
        self._learn = learn
        self._voice = voice
        self._set_voice = set_voice
        self._language = language
        self._set_language = set_language
        self._voice_languages = voice_languages
        self._reminders = reminders
        self._initiative = initiative
        self._babble = babble
        self._guard = guard
        self._capabilities = capabilities
        self._changed = changed or (lambda: None)
        self._docs = docs  # the documents (/docs, /docs send N): off without [documents]

    async def handle(self, message: Message) -> str:
        command, _, rest = message.text.strip().partition(" ")
        args = rest.strip()
        match command.lower():
            case "/new":
                return self._new()
            case "/profile":
                return self._profile_text()
            case "/forget":
                return self._forget(args, message.id)
            case "/fix":
                return self._fix(args, message.id)
            case "/status":
                return await self._status()
            case "/learn":
                return await self._learn_link(args, message)
            case "/voice":
                return self._voice_settings(args.lower())
            case "/soul":
                return self._soul()
            case "/voices":
                return self._voice_settings("voices")
            case "/language":
                return await self._language_setting(args)
            case "/babble":
                return await self._babble_setting(args, message)
            case "/remind":
                return self._remind(args, message)
            case "/reminders":
                return self._reminder_list(args, message)
            case "/quiet":
                return self._quiet(args.lower())
            case "/docs":
                return await self._docs(args, message) if self._docs is not None else prompts.DOCS_OFF
            case "/initiative":
                return self._initiative_switch(args.lower())
            case "/evening":
                return self._evening_switch(args.lower(), message)
            case "/devices" if self._guard is not None:
                if not args:
                    return self._guard.listing(message.peer_aci)
                return self._guard.devices(message.peer_aci, message.source_device, args)
            case "/lock" if self._guard is not None:  # the bridge locks at once, when it arrives; this is the same
                return self._guard.lock(message.peer_aci, message.source_device) or prompts.LOCK_NOT_FROM_KNOWN
            case "/unlock" if self._guard is not None:
                return self._guard.unlock(message.peer_aci, message.source_device) or prompts.LOCK_NOT_FROM_KNOWN
            case "/help":
                return self._help()
            case _:
                return f"Unknown command {command}.\n\n{self._help()}"

    def _help(self) -> str:
        """The list of what she can do here (word for word as her rules carry it), then the commands."""
        return prompts.help_text(self._capabilities() if self._capabilities is not None else [])

    def _remind(self, args: str, message: Message) -> str:
        """/remind [call] [daily|weekdays|weekly] <when> <text>: the same checks and note as Gemma's tool."""
        if self._reminders is None:
            return prompts.REMINDERS_OFF
        words = args.split()
        call, repeat = False, "none"
        while words and words[0].lower() in ("call", "daily", "weekdays", "weekly"):
            flag = words.pop(0).lower()
            call, repeat = (True, repeat) if flag == "call" else (call, flag)
        if not words:
            return prompts.REMINDER_USAGE
        try:
            (kind, value), used = parse_when(words)
            if not words[used:]:
                return prompts.REMINDER_USAGE
            created = self._reminders.set(
                message.peer_aci,
                text=" ".join(words[used:]),
                at=str(value) if kind == "at" else None,
                in_minutes=value if kind == "in" else None,
                call=call,
                repeat=repeat,
                source="command",
                source_message_id=message.id,
            )
        except ReminderError as exc:
            return f"Not set: {exc}."
        return created.note

    def _reminder_list(self, args: str, message: Message) -> str:
        """/reminders: the list; /reminders cancel N, cN (a check-in) or all."""
        if self._reminders is None:
            return prompts.REMINDERS_OFF
        words = args.lower().split()
        if not words:
            return self._reminders.listing(message.peer_aci, marked=True)
        if len(words) != 2 or words[0] != "cancel":
            return prompts.REMINDERS_USAGE
        target = words[1].lstrip("#")
        try:
            if target == "all":
                return self._reminders.cancel_all(message.peer_aci)
            if target.startswith("c") and target[1:].isdecimal():
                return self._reminders.cancel_checkin(message.peer_aci, int(target[1:]))
            if target.isdecimal():
                return self._reminders.cancel(message.peer_aci, int(target))
        except ReminderError as exc:
            reason = str(exc)
            return f"{reason[:1].upper()}{reason[1:]}."
        return prompts.REMINDERS_USAGE

    def _quiet(self, args: str) -> str:
        """/quiet pauses every message she starts herself (the evening note too) until /quiet off; reminders the user
        set still come. With none of them on there is nothing to pause, but /quiet off still ends a /quiet left from
        before (the evening note turned off since): the documents' note reads it too."""
        if self._initiative is None:
            return prompts.QUIET_DISABLED
        if not (self._initiative.enabled or self._initiative.evening() is not None):
            if args == "off" and self._initiative.quiet:
                self._initiative.set_quiet(False)
                return prompts.QUIET_OFF_NOTHING_ON
            return prompts.QUIET_DISABLED
        if args in ("", "on"):
            return self._initiative.set_quiet(True)
        if args == "off":
            return self._initiative.set_quiet(False)
        return prompts.QUIET_USAGE

    def _initiative_switch(self, args: str) -> str:
        """/initiative on|off: whether she may message first at all (opt-in). Only a typed command reaches it, never a
        voice note, a call or Gemma; it wins over the config."""
        if self._initiative is None:
            return prompts.QUIET_DISABLED
        if not args:
            return self._initiative.state_text()
        if args in ("on", "off"):
            reply = self._initiative.set_enabled(args == "on")
            self._changed()  # the list in her rules shows on or off
            return reply
        return prompts.INITIATIVE_USAGE

    def _evening_switch(self, args: str, message: Message) -> str:
        """/evening, /evening on|off|20:30: the evening note, code's three questions at that time (off until turned on).
        Only a typed command reaches it, never a voice note, a call or Gemma; it wins over the config and goes only to
        the account that typed it."""
        if self._initiative is None:
            return prompts.EVENING_NONE
        reply = self._initiative.set_evening(message.peer_aci, args)
        if args:
            self._changed()  # the list in her rules names its time while it is on
        return reply

    def _soul(self) -> str:
        """The style in use: soul.md (with [chat] and [call] lines, what texts and calls each get), or why none is and
        the built-in style."""
        soul = self._context.soul()
        style = self._context.built_in_style
        if soul.text:
            off = "" if self._context.calm_on_worry else f"\n\n{prompts.SOUL_CALM_OFF}"
            sizes = {"chars": f"{len(soul.text):,}", "limit": f"{SOUL_MAX_CHARS:,}"}
            if not has_parts(soul.text):
                return prompts.SOUL_IN_USE.format(text=soul.text, **sizes) + off
            texts, calls = soul_part(soul.text, call=False), soul_part(soul.text, call=True)
            shown = {
                "texts": prompts.SOUL_FOR_TEXTS.format(chars=f"{len(texts):,}", text=texts)
                if texts
                else prompts.SOUL_TEXTS_BUILT_IN.format(style=style),
                "calls": prompts.SOUL_FOR_CALLS.format(chars=f"{len(calls):,}", text=calls)
                if calls
                else prompts.SOUL_CALLS_BUILT_IN.format(style=style),
            }
            return prompts.SOUL_IN_PARTS.format(**sizes, **shown) + off
        other = next(name for name in prompts.BUILT_IN_STYLES if name != style)
        return prompts.SOUL_NONE.format(
            problem=soul.problem,
            style=style,
            words=prompts.STYLE_WORDS[style],
            other=other,
            other_words=prompts.STYLE_WORDS[other],
        )

    async def _learn_link(self, args: str, message: Message) -> str:
        url = args.split()[0] if args else ""
        if not url.startswith(("https://", "http://")):
            return "Usage: /learn <link>, e.g. /learn https://www.linkedin.com/in/you"
        if self._learn is None:
            return "Learning from links is not available."
        return await self._learn(url, message)

    def _voice_settings(self, args: str) -> str:
        if not self._voice:
            return "Voice notes aren't set up on the Mac."
        words = args.split()
        if words and words[0] in _VOICE_MODES and len(words) == 1:
            self._store.set_state(VOICE_MODE_KEY, words[0])
            self._changed()  # the list in her rules shows the voice answers' level
            return f"Voice replies: {_VOICE_MODES[words[0]]}."
        if words == ["voices"]:
            listed = "\n".join(f"{name}: {about}" for name, about in VOICES.items())
            return f"Voices (try one with /voice name <voice>):\n{listed}"
        if len(words) == 2 and words[0] == "name":
            name = words[1].lower()
            if name not in VOICES:
                return f"Unknown voice {words[1]}. /voice voices lists them."
            self._store.set_state(VOICE_NAME_KEY, name)
            if self._set_voice is not None:
                self._set_voice(name)
            gender = VOICES[name].split(", ")
            return f"Voice: {name} ({', '.join(gender[:2])}). Calls and voice notes use it now."
        if len(words) == 2 and words[0] == "echo" and words[1] in _VOICE_ECHO:
            self._store.set_state(VOICE_ECHO_KEY, words[1])
            return f"Transcript echo: {_VOICE_ECHO[words[1]]}."
        if words:
            return (
                "Usage: /voice auto, /voice on, /voice off, /voice echo on or off, /voice voices, /voice name <voice>"
            )
        mode = self._store.get_state(VOICE_MODE_KEY) or "auto"
        echo = self._store.get_state(VOICE_ECHO_KEY) or "off"
        return (
            f"Voice replies: {_VOICE_MODES.get(mode, mode)}.\nTranscript echo: {_VOICE_ECHO.get(echo, echo)}.\n"
            "Change with /voice auto, on or off, and /voice echo on or off."
        )

    async def _language_setting(self, args: str) -> str:
        """/language: the list; /language <code or name>: switch (only ever by this typed command). The text switches
        at once; when the voice service cannot do the new language yet, the reply says how to update it."""
        if not args:
            return prompts.language_list(self._language)
        practising = self._babble.running() if self._babble is not None else None
        if practising is not None:  # the user's own language stays as it is during practice
            english = LANGUAGES[practising.code].english
            return prompts.LANGUAGE_IN_PRACTICE.format(english=english, level=practising.level)
        chosen = find(args)
        if chosen is None:
            return prompts.LANGUAGE_UNKNOWN.format(name=args, listing=prompts.language_list(self._language))
        if chosen.code == self._language:
            if chosen.code == ENGLISH:
                return prompts.LANGUAGE_SAME_ENGLISH
            return prompts.LANGUAGE_SAME.format(english=chosen.english, own=chosen.own, code=chosen.code)
        self._store.set_state(LANGUAGE_KEY, chosen.code)
        self._language = chosen.code
        if self._set_language is not None:
            self._set_language(chosen.code)
        if chosen.code == ENGLISH:
            return prompts.LANGUAGE_SET_ENGLISH
        reply = prompts.LANGUAGE_SET.format(own=chosen.own, code=chosen.code, english=chosen.english)
        installed = await self._voice_languages() if self._voice_languages is not None else None
        if installed is not None and chosen.code not in installed:
            reply += "\n" + prompts.LANGUAGE_VOICE_TOO_OLD.format(english=chosen.english)
        return reply

    async def _babble_setting(self, args: str, message: Message) -> str:
        """/babble: resume the last practice (the first time: the list); /babble <code or name> [a2|b1]: start one (a
        running one of another language ends with its recap first); /babble off: end it with the recap. Only this typed
        command starts or ends practice, never a voice note, a call or Gemma (no tool)."""
        babble = self._babble
        if babble is None:
            return prompts.BABBLE_UNAVAILABLE
        aci = message.peer_aci
        session = babble.session()
        if session is not None and session.aci.lower() != aci.lower():
            if not session.ended:
                return prompts.BABBLE_TAKEN  # one session at a time, the practising user's
            await babble.finish(session)  # ended, its recap held for the morning: it goes to its owner now
        words = args.split()
        running = babble.running(aci)
        if not words:
            if running is not None:
                return self._practising(running.code, running.level)
            await self._finish_held(babble)
            last = babble.last()
            return await self._practice_start(aci, *last, resumed=True) if last else listing(None)
        if [word.lower() for word in words] == ["off"]:
            ended = babble.end("off") or babble.held()
            if ended is None:
                last = babble.last()
                if last is None:
                    return prompts.BABBLE_NONE_FIRST
                return prompts.BABBLE_NONE.format(english=LANGUAGES[last[0]].english, level=last[1])
            await babble.finish(ended)
            return prompts.BABBLE_ENDED.format(english=LANGUAGES[ended.code].english, level=ended.level)
        chosen = find(words[0]) if len(words) <= 2 else None
        if chosen is None:
            return prompts.BABBLE_UNKNOWN.format(name=args, listing=listing(babble.last()))
        if chosen.code in NOT_YET or chosen.code not in OFFERED:
            why = prompts.BABBLE_NOT_YET.get(chosen.code, f"{chosen.english}: not yet.")
            return f"{why}\n\n{listing(babble.last())}"
        level = words[1].upper() if len(words) == 2 else START_LEVEL
        if level not in LEVELS:
            return prompts.BABBLE_LEVELS.format(level=level, code=chosen.code)
        if running is not None and running.code == chosen.code:
            if len(words) == 1 or level == running.level:
                return self._practising(running.code, running.level)
            return prompts.BABBLE_LEVEL_FIXED.format(level=running.level, code=chosen.code, wanted=level)
        if running is not None:
            babble.end("switch")
        await self._finish_held(babble)
        return await self._practice_start(aci, chosen.code, level, resumed=False)

    @staticmethod
    def _practising(code: str, level: str) -> str:
        return prompts.BABBLE_RUNNING.format(name=prompts.language_label(code), code=code, level=level)

    @staticmethod
    async def _finish_held(babble: Babble) -> None:
        """A session that ended but whose recap waits (for the morning, or a call's end): its recap goes out first."""
        held = babble.held()
        if held is not None:
            await babble.finish(held)

    async def _practice_start(self, aci: str, code: str, level: str, *, resumed: bool) -> str:
        """Start practice and say how it goes; the very first start also says its limits, and a voice service without
        the language is named (as /language does)."""
        assert self._babble is not None
        first = self._babble.last() is None
        self._babble.start(aci, code, level)
        language = LANGUAGES[code]
        fields = {"name": prompts.language_label(code), "code": code, "level": level, "english": language.english}
        lines = [prompts.BABBLE_RESUMED.format(**fields) if resumed else prompts.BABBLE_STARTED.format(**fields)]
        if first:
            lines.append(prompts.BABBLE_LIMITS)
        installed = await self._voice_languages() if self._voice_languages is not None and code != ENGLISH else None
        if installed is not None and code not in installed:
            lines.append(prompts.LANGUAGE_VOICE_TOO_OLD.format(english=language.english))
        reply = "\n\n".join(lines)
        if resumed:  # the list below, the last one marked; never "start with" (practice runs)
            reply += "\n\n" + prompts.BABBLE_RESUMED_LIST.format(lines=listing_lines((code, level)))
        return reply

    def _new(self) -> str:
        self._on_new()  # learn from the closing conversation soon (Hermes: save before reset)
        self._store.start_new_conversation()
        self._context.refresh_snapshot()
        count = len(self._profile.facts())
        return f"New conversation started. I still have your profile ({count} facts) and can search our earlier chats."

    def _profile_text(self) -> str:
        """/profile: one fact per line with its number, under its category as a bold heading (the snapshot's order;
        without a category: Other; no category at all: no headings), a blank line between groups."""
        facts = self._profile.facts()
        if not facts:
            return 'Your profile is empty so far. Tell me about yourself, or say "remember that ...".'
        used = sum(len(fact.text) for fact in facts)

        def line(fact: Fact, text: str) -> str:
            return f"#{fact.id} {text} ({_SOURCES[fact.source_kind]}, {local_date(fact.created_at, self._tz)})"

        blocks = [prompts.PROFILE_HEADER.format(used=used, limit=self._profile.max_chars)]
        groups = by_category(facts)
        if groups is None:
            blocks.append("\n".join(line(fact, fact.text) for fact in facts))
        else:
            blocks += [
                "\n".join([f"**{category}**", *(line(f, rest) for f, rest in entries)]) for category, entries in groups
            ]
        return "\n\n".join([*blocks, prompts.PROFILE_FOOTER])

    def _forget(self, args: str, message_id: int) -> str:
        if not args:
            return "Usage: /forget N (the fact number from /profile) or /forget words"
        matches = self._profile.find(args)
        if len(matches) > 1:
            listed = "\n".join(f"#{fact.id} {fact.text}" for fact in matches[:10])
            return f"Several facts match, send /forget N with one of:\n{listed}"
        fact = self._profile.forget(matches[0].id, source_message_id=message_id) if matches else None
        if fact is None:
            return f'No profile fact matches "{args}". See /profile.'
        self._context.refresh_snapshot()
        return f"Forgotten: #{fact.id} {fact.text}"

    def _fix(self, args: str, message_id: int) -> str:
        words = args.split(maxsplit=1)  # "N text", also with a new line after N
        number = words[0].removeprefix("#") if words else ""
        if len(words) < 2 or not number.isdecimal():
            return prompts.FIX_USAGE
        fact_id, text = int(number), words[1].strip()
        fact = next((fact for fact in self._profile.facts() if fact.id == fact_id), None)
        if fact is None:
            return prompts.FIX_NO_FACT.format(number=fact_id)
        kept = category_of(fact.text)
        if kept is not None and category_of(text) is None:  # /profile shows the category as a heading: it stays
            text = f"{kept[0]}: {text}"
        problem = self._profile.fix(fact_id, text, source_message_id=message_id)
        if problem:
            return prompts.FIX_REFUSED.format(reason=problem)
        self._context.refresh_snapshot()
        return prompts.FIX_DONE.format(number=fact_id, text=text)

    async def _status(self) -> str:
        info = await self._status_provider()
        facts = self._profile.facts()
        used = sum(len(fact.text) for fact in facts)
        learned = local_datetime(info.last_learning_run, self._tz) if info.last_learning_run else "not yet"
        sections = {
            prompts.STATUS_RUNNING: [
                f"Gemma: {info.gemma}",
                f"Waiting messages: {info.queue_length}",
                f"Signal connection restarts: {info.restarts}",
            ],
            prompts.STATUS_MEMORY: [
                f"Profile: {len(facts)} facts, {round(100 * used / self._profile.max_chars)}% full",
                f"Last learning run: {learned}",
                f"History: {info.messages} messages, database {info.db_bytes / 1_000_000:.1f} MB",
            ],
            prompts.STATUS_PARTS: list(info.parts),
            prompts.STATUS_SECURITY: list(info.security),
            prompts.STATUS_REMINDERS: [*([info.reminders] if info.reminders else []), *info.initiative],
        }
        return "\n\n".join("\n".join([f"**{head}**", *lines]) for head, lines in sections.items() if lines)
