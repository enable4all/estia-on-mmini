"""Slash commands, answered without Gemma (spec section 5)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.context import ContextBuilder
from local_assistant.initiative import Initiative
from local_assistant.language import ENGLISH, LANGUAGE_KEY, find
from local_assistant.profile import Profile
from local_assistant.reminders import ReminderError, Reminders, parse_when
from local_assistant.soul import SOUL_MAX_CHARS
from local_assistant.store import Message, Store
from local_assistant.timefmt import local_date, local_datetime

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
    reminders: str = ""  # "Reminders: 3 open · next Tue 13 Oct 07:00 (call) · 0 failed" (times and counts only)
    initiative: tuple[str, ...] = ()  # the messages she starts: counts, the improvised note, the last skips


StatusProvider = Callable[[], Awaitable[StatusInfo]]
Learn = Callable[[str, Message], Awaitable[str]]  # link, the /learn message → reply text
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
    ) -> None:
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
            case "/remind":
                return self._remind(args, message)
            case "/reminders":
                return self._reminder_list(args, message)
            case "/quiet":
                return self._quiet(args.lower())
            case "/initiative":
                return self._initiative_switch(args.lower())
            case "/help":
                return prompts.HELP
            case _:
                return f"Unknown command {command}.\n\n{prompts.HELP}"

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
            return self._reminders.listing(message.peer_aci)
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
        """/quiet pauses every message she starts herself until /quiet off; reminders the user set still come."""
        if self._initiative is None or not self._initiative.enabled:
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
            return self._initiative.set_enabled(args == "on")
        return prompts.INITIATIVE_USAGE

    def _soul(self) -> str:
        soul = self._context.soul()
        if soul.text:
            return f"My style file (soul.md, {len(soul.text):,} of {SOUL_MAX_CHARS:,} characters):\n{soul.text}"
        return prompts.SOUL_NONE.format(problem=soul.problem)

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

    def _new(self) -> str:
        self._on_new()  # learn from the closing conversation soon (Hermes: save before reset)
        self._store.start_new_conversation()
        self._context.refresh_snapshot()
        count = len(self._profile.facts())
        return f"New conversation started. I still have your profile ({count} facts) and can search our earlier chats."

    def _profile_text(self) -> str:
        facts = self._profile.facts()
        if not facts:
            return 'Your profile is empty so far. Tell me about yourself, or say "remember that ...".'
        used = sum(len(fact.text) for fact in facts)
        lines = [f"Your profile ({used:,} of {self._profile.max_chars:,} characters):"]
        lines += [
            f"#{fact.id} {fact.text} ({_SOURCES[fact.source_kind]}, {local_date(fact.created_at, self._tz)})"
            for fact in facts
        ]
        lines.append(prompts.PROFILE_FOOTER)
        return "\n".join(lines)

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
        fact_id, text = int(number), words[1]
        if all(fact.id != fact_id for fact in self._profile.facts()):
            return prompts.FIX_NO_FACT.format(number=fact_id)
        problem = self._profile.fix(fact_id, text, source_message_id=message_id)
        if problem:
            return prompts.FIX_REFUSED.format(reason=problem)
        self._context.refresh_snapshot()
        return prompts.FIX_DONE.format(number=fact_id, text=text.strip())

    async def _status(self) -> str:
        info = await self._status_provider()
        facts = self._profile.facts()
        used = sum(len(fact.text) for fact in facts)
        learned = local_datetime(info.last_learning_run, self._tz) if info.last_learning_run else "not yet"
        return "\n".join(
            [
                f"Gemma: {info.gemma}",
                f"Waiting messages: {info.queue_length}",
                f"Profile: {len(facts)} facts, {round(100 * used / self._profile.max_chars)}% full",
                f"Last learning run: {learned}",
                f"History: {info.messages} messages, database {info.db_bytes / 1_000_000:.1f} MB",
                f"Signal connection restarts: {info.restarts}",
                *info.parts,
                *([info.reminders] if info.reminders else []),
                *info.initiative,
            ]
        )
