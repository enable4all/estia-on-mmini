"""Configuration, read from ~/.config/local-assistant/config.toml.

The file says who may talk to the bot, so it must be private (mode 0600).
"""

from __future__ import annotations

import dataclasses
import ipaddress
import os
import re
import stat
import subprocess
import tomllib
import unicodedata
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from local_assistant.language import CODES

# the folder the code runs from: a checkout, or a release of an install in its own user (root-owned, read-only)
CODE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "local-assistant" / "config.toml"
LOCALTIME = Path("/etc/localtime")  # on a Mac a link into the time zone database
JAVA_HOME_TOOL = Path("/usr/libexec/java_home")
DEFAULT_HOST_LABEL = "the Mac"
DEFAULT_NAME = "Estia"  # the assistant's name until the config's `name` gives her another (every home names its own)
# her built-in styles ([personality] style), used while there is no usable soul.md; hearth when left out
STYLES = ("hearth", "playful")
DEFAULT_STYLE = "hearth"
# the Signal profile picture until the config's `avatar` names another ("" for none): Estia's e., when the repo has it
DEFAULT_AVATAR = CODE_ROOT / "docs" / "brand" / "estia-avatar.png"
LETTER_AVATARS = CODE_ROOT / "docs" / "brand" / "letters"  # a.png to z.png: a name's first letter, the orange stop
AVATAR_MAX_BYTES = 5_000_000
_IMAGE_STARTS = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")  # PNG, JPEG
_NUMBER = re.compile(r"^\+[0-9]{8,15}$")
_DIGEST = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
KEEP_DAYS_MAX = 36_500  # [privacy] keep_days: at most 100 years
_ZONE_KEY = re.compile(r"zoneinfo(?:\.default)?/(.+)$")


LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})  # services on this Mac only


class ConfigError(Exception):
    """The configuration file is missing, unsafe or invalid."""


def system_timezone() -> ZoneInfo:
    """The Mac's own time zone: /etc/localtime links into the zone database (.../zoneinfo/Europe/Amsterdam).
    UTC when that cannot be told."""
    try:
        target = os.readlink(LOCALTIME)
    except OSError:
        return ZoneInfo("UTC")
    found = _ZONE_KEY.search(target)
    if found is not None:
        try:
            return ZoneInfo(found.group(1))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return ZoneInfo("UTC")


def find_java_home(tool: Path = JAVA_HOME_TOOL, *, timeout: float = 5.0) -> Path | None:
    """The JDK macOS knows (for the call helper's signal-cli); None when there is none."""
    try:
        done = subprocess.run(  # a fixed tool, no shell, no arguments
            [str(tool)], capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    home = Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None
    return home if home is not None and home.is_absolute() else None


def _default_tz() -> ZoneInfo:
    return system_timezone()


def _default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "local-assistant"


def _default_log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "local-assistant"


def _default_projects_root() -> Path:
    return Path.home() / "dev"


def _default_attachments_dir() -> Path:
    return Path.home() / ".local" / "share" / "signal-cli" / "attachments"


def _default_watched_logs() -> tuple[Path, ...]:
    return (_default_log_dir(),)


DOCUMENTS_FOLDER = "Estia-KB"  # the documents folder's one name (the share, the default, doctor): directly in a home
OWNER_HOME = re.compile(r"/Users/(?!Shared(?:/|$))[^/]+(?=/|$)")  # a home on a Mac: /Users/<name>, never Shared


def is_estia_kb(folder: Path) -> bool:
    """Whether `folder` is an owner's ~/Estia-KB, the only folder the share opens to her: that name, as the share
    writes it, directly in a home on a Mac."""
    return folder.name == DOCUMENTS_FOLDER and OWNER_HOME.fullmatch(str(folder.parent)) is not None


def _default_documents_folder() -> Path:
    """The owner's ~/Estia-KB, written out (in her own user her config names the owner's folder in full). Directly in
    the home: macOS guards Documents, Desktop and Downloads (live, 14 Oct: the share could not open ~/Documents to her).
    "-KB" keeps it apart from the README's clone ~/estia on a Mac's usual disk, which ignores case (AGENTS.md,
    Gotchas)."""
    return Path.home() / DOCUMENTS_FOLDER


PROJECT_LEVELS = ("summary", "docs", "code")
_ACCOUNT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_VOICE_URL = re.compile(r"^http://(127\.0\.0\.1|localhost):[0-9]{1,5}$")
_VOICE_NAME = re.compile(r"^[a-z]{2}_[a-z]{2,20}$")


@dataclass(frozen=True)
class MailAccount:
    name: str  # short, shown to the user and Gemma, e.g. "family"
    address: str


DEFAULT_CALENDAR = "calendar"  # the calendar of a [calendar] without sources: its address is CALENDAR_ICS_URL
MAX_CALENDARS = 8


@dataclass(frozen=True)
class CalendarSource:
    """One calendar: a Google calendar through its iCal address (in calendar.env), the Outlook calendar of a mailbox
    she already reads (Microsoft Graph, delegated Calendars.Read), or a CalDAV calendar (iCloud, Fastmail, Nextcloud:
    its server, user name and calendar name here, its app-specific password in calendar.env), read only."""

    name: str  # short and unique, like a mailbox name ("family"); in part names, scripts and calendar.env keys
    label: str = ""  # shown before its events when there are several ("Family"); default: from the name
    mailbox: str = ""  # a [[mail.accounts]] name: that mailbox's calendar; "" = a Google iCal address
    caldav: str = ""  # a CalDAV server's https address ("https://caldav.icloud.com"); "" = not a CalDAV calendar
    user: str = ""  # the CalDAV account's user name
    calendar: str = ""  # the CalDAV calendar's name as the calendar app shows it ("Family")

    @property
    def shown(self) -> str:
        return self.label or self.name.replace("_", " ").replace("-", " ").capitalize()

    @property
    def kind(self) -> str:
        """ "outlook", "caldav" or "google"."""
        return "outlook" if self.mailbox else "caldav" if self.caldav else "google"

    @property
    def service(self) -> str:
        """The service by name, for people: Google, Outlook, iCloud, Fastmail, Nextcloud, else the server's own name
        ("nas.example.com"; never the protocol's)."""
        if self.kind != "caldav":
            return {"outlook": "Outlook", "google": "Google"}[self.kind]
        parts = urllib.parse.urlsplit(self.caldav)
        host = (parts.hostname or "").lower()
        for site, name in (("icloud.com", "iCloud"), ("fastmail.com", "Fastmail")):
            if host == site or host.endswith(f".{site}"):
                return name
        return "Nextcloud" if "/remote.php/" in f"{parts.path}/" else host.rstrip(".")

    @property
    def _suffix(self) -> str:
        return "" if self.name == DEFAULT_CALENDAR else "_" + self.name.upper().replace("-", "_")

    @property
    def ical_key(self) -> str:
        """A Google calendar's key in calendar.env ("school-days": CALENDAR_ICS_URL_SCHOOL_DAYS); "" for the others."""
        return f"CALENDAR_ICS_URL{self._suffix}" if self.kind == "google" else ""

    @property
    def secret_key(self) -> str:
        """Its secret's key in calendar.env: a Google calendar's address (ical_key), a CalDAV calendar's password
        (CALDAV_PASSWORD[_NAME]); "" for a Microsoft calendar."""
        return f"CALDAV_PASSWORD{self._suffix}" if self.kind == "caldav" else self.ical_key

    @property
    def saved_key(self) -> str:
        """The key next to it that scripts/set-calendar-url.sh or set-calendar-password.sh fills with when it saved the
        secret ("school-days": CALENDAR_SAVED_AT_SCHOOL_DAYS), for doctor; "" for a Microsoft calendar."""
        return f"CALENDAR_SAVED_AT{self._suffix}" if self.secret_key else ""


@dataclass(frozen=True)
class ExtraCheck:
    """One more service for the status report and the health watcher: a local URL or a launchd service."""

    label: str  # shown in the report and alerts, e.g. "Search index"
    http: str = ""  # a local URL that must answer; a JSON "status" must be ready, ok, healthy, up, running or pass
    launchd: str = ""  # or a launchd target like "system/com.example.backup"
    stuck_after_seconds: float = 0  # launchd only: no new run for this long means stuck (0: not checked)

    @property
    def slug(self) -> str:
        """The label as lowercase words joined by dashes ("Search index" -> "search-index")."""
        return re.sub(r"[^a-z0-9]+", "-", self.label.casefold()).strip("-")

    @property
    def key(self) -> str:
        """The stable id of its alerts."""
        return f"extra-{self.slug}"


@dataclass(frozen=True)
class DeviceSpec:
    name: str  # as in the Tapo, Kasa or Hue app, e.g. "living room lamp"; Gemma knows only names
    host: str  # a Kasa device's address on the home network (a DHCP reservation keeps it fixed); "" for Hue
    kind: str  # "light" or "plug"
    rule: str  # "normal", "ask first" or "never off"
    brand: str = "kasa"  # "kasa" (Kasa and Tapo, python-kasa) or "hue" (through the paired Hue Bridge)
    light: str = ""  # a Hue light's id on the bridge (CLIP v2); the bridge's own address is in hue.env

    @property
    def hue(self) -> bool:
        return self.brand == "hue"

    @property
    def service(self) -> str:
        """The brand by name, for people, as a calendar's service: "TP-Link" (the maker of Kasa and Tapo, which brand
        "kasa" covers: "Kasa" alone would read as if a Tapo plug were not set up) or "Philips Hue"."""
        return "Philips Hue" if self.hue else "TP-Link"


def _default_tapo_helper() -> tuple[str, ...]:
    tapo = CODE_ROOT / "tools" / "tapo"
    return (str(tapo / ".venv" / "bin" / "python"), "-I", str(tapo / "helper.py"))  # -I: isolated mode


def _default_ical_helper() -> tuple[str, ...]:
    ical = CODE_ROOT / "tools" / "ical"
    return (str(ical / ".venv" / "bin" / "python"), "-I", str(ical / "helper.py"))


def _default_youtube_helper() -> tuple[str, ...]:
    youtube = CODE_ROOT / "tools" / "youtube"
    return (str(youtube / ".venv" / "bin" / "python"), "-I", str(youtube / "helper.py"))


def _default_documents_helper() -> tuple[str, ...]:
    """tools/documents' Swift helper as SwiftPM builds it (`swift build -c release`)."""
    return (str(CODE_ROOT / "tools" / "documents" / ".build" / "release" / "documents-helper"),)


# what a release of her own user holds in vendor/ besides the call helpers: Homebrew's signal-cli and a JDK, copied by
# estia-deploy, so nothing she runs sits in a folder the owner can change
_NATIVE_SIGNAL_CLI = Path("signal-cli-native/bin/signal-cli")
_JDK_HOME = Path("openjdk.jdk/Contents/Home")


def default_signal_cli() -> str:
    """The release's own copy of signal-cli when it has one, else "signal-cli" on PATH (a checkout)."""
    native = CODE_ROOT / "vendor" / _NATIVE_SIGNAL_CLI
    return str(native) if native.is_file() else "signal-cli"


# where tools/call-tunnel's scripts put the JVM signal-cli and our call helper, under <data_dir>/vendor
_CALLS_SIGNAL_CLI = Path("signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli")
_CALLS_TUNNEL = Path("signal-call-tunnel/signal-call-tunnel/target/release/signal-call-tunnel")


# the longest socket path the call helper makes in the folder: "<dir>/signal-call-<u64>.sock" (sun_path is 104 bytes)
_LONGEST_SOCKET_NAME = len("/signal-call-18446744073709551615.sock")


def _fits_sockets(folder: Path) -> bool:
    """Whether the call helper's socket paths in this folder fit in 103 bytes (104 with the closing zero byte)."""
    return len(os.fsencode(folder)) + _LONGEST_SOCKET_NAME <= 103


def _default_call_socket_dir(data_dir: Path) -> Path:
    """<data_dir>/calls, or ~/.la-calls when a long home folder (a short name of 9+ characters) makes that too long."""
    folder = data_dir / "calls"
    return folder if _fits_sockets(folder) else Path.home() / ".la-calls"


@dataclass(frozen=True)
class Config:
    bot_number: str
    allowed_acis: frozenset[str]
    timezone: ZoneInfo = field(default_factory=_default_tz)  # default: the Mac's own time zone
    host_label: str = DEFAULT_HOST_LABEL  # what the status report and tool descriptions call this machine
    name: str = DEFAULT_NAME  # the assistant's name: her rules, greetings, welcome, notes and Signal profile
    avatar: Path | None = None  # her Signal profile picture (PNG or JPEG, checked at the start); None: none
    language: str = "en"  # the language at the start; /language changes it (kept in the database, which then wins)
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "local-worker"
    # the Ollama digest the model alias must have (64 hex digits); "" = not pinned: answers never pause for it
    model_digest: str = ""
    signal_cli: str = "signal-cli"
    data_dir: Path = field(default_factory=_default_data_dir)
    log_dir: Path = field(default_factory=_default_log_dir)
    reply_max_tokens: int = 600
    window_tokens: int = 12_000
    emergency_tokens: int = 20_000
    summary_tokens: int = 500
    profile_max_chars: int = 4_000  # the user's own facts, in every prompt (owner, 16 Oct)
    notes_max_chars: int = 12_000  # her notes from chats; only the ones that may matter come with a message
    notes_per_message_chars: int = 1_500  # those notes, at most, in the user's newest message before its words
    fact_max_chars: int = 300
    idle_learn_seconds: float = 1_800
    soon_learn_seconds: float = 60
    busy_note_seconds: float = 60
    typing_refresh_seconds: float = 10
    gemma_unreachable_seconds: float = 120
    gemma_request_timeout: float = 600
    recover_window_seconds: float = 600
    max_tool_calls: int = 5
    learn_tool_calls: int = 10
    # host status, project overview, photos
    health_interval_hours: float = 4
    health_confirm_minutes: float = 5
    disk_free_min_percent: float = 10
    health_extra_checks: tuple[ExtraCheck, ...] = ()  # more services to check, from [health] extra_checks
    watched_log_dirs: tuple[Path, ...] = field(default_factory=_default_watched_logs)  # *.log files scanned for ERROR
    # projects: an extra, off without [projects] (not everyone is a developer); on, every project is at summary
    projects_enabled: bool = False
    projects_root: Path = field(default_factory=_default_projects_root)
    projects_default: str = "summary"
    projects_docs: frozenset[str] = frozenset()
    projects_code: frozenset[str] = frozenset()
    signal_attachments_dir: Path = field(default_factory=_default_attachments_dir)
    photo_max_bytes: int = 20_000_000
    photo_max_count: int = 4
    photo_max_side: int = 1536
    photo_follow_up_seconds: float = 300
    # the morning digest (calendar, then mail), every day
    digest_time: str = "09:15"
    # mail (2 Exchange mailboxes through Microsoft Graph, read-only); off without [mail]
    mail_tenant_id: str = ""
    mail_client_id: str = ""
    mail_accounts: tuple[MailAccount, ...] = ()
    mail_prefilter: bool = True  # newsletters, lists and auto-replies recognised from their headers (no model)
    mail_jev_url: str = ""  # the local AgentJev service (e.g. http://127.0.0.1:8147); "" = no Jev
    mail_jev_threshold: float = 0.9  # Jev this sure that a mail needs nothing -> noise without Gemma
    mail_sync_minutes: float = 5
    mail_keep_days: int = 30
    mail_first_sync_days: int = 30
    mail_reply_days: int = 7
    # voice notes through the local voice service (tools/voice); off without [voice]
    voice_url: str = ""
    voice_name: str = "af_heart"  # a female Kokoro voice
    voice_speed: float = 1.05
    voice_max_spoken_chars: int = 1500
    voice_max_audio_bytes: int = 10_000_000
    # lights and plugs on the home network through tools/tapo (python-kasa: Kasa and Tapo); off without [tapo]
    tapo_devices: tuple[DeviceSpec, ...] = ()
    tapo_helper_timeout: float = 15
    tapo_confirm_seconds: float = 120
    tapo_helper: tuple[str, ...] = field(default_factory=_default_tapo_helper)  # the command, without arguments
    # calendars, read-only: Google iCal addresses (only tools/ical reads them, from calendar.env) and the Outlook
    # calendars of signed-in mailboxes (Graph, Calendars.Read); off without [calendar]
    calendar_enabled: bool = False
    calendar_sources: tuple[CalendarSource, ...] = ()  # [[calendar.sources]]; none: one Google calendar, "calendar"
    calendar_digest: bool = True
    calendar_helper_timeout: float = 30
    calendar_helper: tuple[str, ...] = field(default_factory=_default_ical_helper)  # the command, without arguments
    # YouTube links in a message: their captions through tools/youtube (always on with read_link: no switch); the
    # helper stops itself after 20 s
    youtube_helper_timeout: float = 30
    youtube_helper: tuple[str, ...] = field(default_factory=_default_youtube_helper)  # the command, without arguments
    # documents: the PDFs in the owner's ~/Estia-KB (her access: one ACL, scripts/share-documents.sh) and the
    # ones the user sends on Signal, read by tools/documents (PDFKit, Vision on the Mac) into her database's index;
    # off without [documents]
    documents_enabled: bool = False
    documents_folder: Path = field(default_factory=_default_documents_folder)  # always absolute
    documents_look_minutes: float = 60  # how often she lists the folder (and whenever the user asks about documents)
    documents_helper_timeout: float = 75  # the helper stops itself after 60 s a document
    documents_helper: tuple[str, ...] = field(default_factory=_default_documents_helper)  # without arguments
    # chords: transposed, moved for a capo, a chord's notes and the key of given chords, worked out by code on the Mac
    # (theory.py); an extra, off without [chords] (an empty section): without it no tool, rules section or list line
    chords_enabled: bool = False

    # voice calls (the JVM signal-cli + our signal-call-tunnel build, see tools/call-tunnel); off without [calls]
    calls_enabled: bool = False
    calls_signal_cli: Path | None = None  # default: data_dir / "vendor" / ...
    calls_java_home: Path | None = None  # default: what /usr/libexec/java_home says
    calls_tunnel: Path | None = None  # default: data_dir / "vendor" / ...
    calls_socket_dir: Path | None = None  # default: data_dir / "calls", or ~/.la-calls when that is too long
    calls_speed: float = 1.05
    calls_end_of_turn_seconds: float = 2.3  # owner, 15 Oct: room to think (2.8); 17 Oct: 0.5 s less
    calls_idle_prompt_seconds: float = 90
    calls_max_minutes: float = 60
    calls_summary: bool = True
    calls_fillers: bool = False  # "hmm, let me think" lines while she thinks (user: off)
    calls_acknowledge: bool = True  # a short "Got it." as soon as a request of 5+ words is taken in (user: on)
    calls_answer_after_seconds: float = 5.0  # let it ring about twice
    calls_wake_call: bool = True  # "call me" reminders place a Signal call (with [calls] only: see wake_calls)
    calls_max_calls_per_day: int = 3  # call reminders a day (a retry is part of the same one)
    # what stops her when the caller talks over her ([calls] interrupt): "speech" (the default, "only speech": the voice
    # service's speech detector) or "loudness" ("any sound"); the other one runs beside it and is only counted. The
    # internal names stay in the code and the logs.
    calls_barge: str = "speech"
    calls_interrupt_old: str = ""  # an older wording that was read ('barge = "speech"'): doctor suggests the new one
    calls_barge_unread: str = ""  # an old barge line beside interrupt, which wins: doctor says it is not read
    # the end of a turn (owner, 17 Oct): Smart Turn asked at a short pause ([calls] early_end, early_end_pause_seconds),
    # and what keeps a turn open ([calls] pause): "speech" ("no speech", the default: noise in a pause counts as pause)
    # or "loudness" ("quiet": any sound); call_audio.TurnEnd's names
    calls_early_end: bool = True
    calls_early_end_pause_seconds: float = 0.6
    calls_pause: str = "speech"

    # reminders the user sets ("remind me at 17:00 to ..."): on without the [reminders] section
    reminders_enabled: bool = True
    reminders_max_open: int = 50
    reminders_max_per_day: int = 20  # new reminders a day

    # messages she starts herself (initiative): opt-in, off unless enabled here or by the user's typed /initiative on
    # (the state table's value wins); /quiet pauses them all
    initiative_enabled: bool = False
    initiative_followups: bool = True  # 1: a check-in on something the user said they would do
    initiative_calendar: bool = True  # 2: a calendar change or an early start tomorrow (needs [calendar])
    initiative_lookback: bool = True  # 3: a Sunday evening look back with one question
    initiative_improvise: bool = True  # 4: a spontaneous note
    initiative_max_per_day: int = 1  # all kinds together
    initiative_followups_per_week: int = 2
    initiative_improvise_per_week: int = 1
    initiative_quiet_hours: str = "22:00-08:00"  # none of them then; "" for none
    initiative_lookback_time: str = "Sun 19:00"
    initiative_early_check: str = "20:00"
    # the evening note (/evening): code's three questions at this time, apart from the kinds above (it needs no
    # `enabled`); "" (left out): off. The user's typed /evening on|off|HH:MM wins over it
    initiative_evening: str = ""

    # Spotify by text message (the official Web API, the user's own app: a Client ID, PKCE); off without [spotify]
    spotify_client_id: str = ""
    spotify_default_device: str = ""  # a speaker name; empty: Spotify's active device, else the first it lists
    spotify_volume_start: int = 30
    spotify_volume_cap: int = 60
    spotify_bose: tuple[tuple[str, str], ...] = ()  # speaker name → private address, to wake it from standby

    # web search: Ollama's search first, Brave's as backup; keys only in web.env (0600); off without [web]
    web_enabled: bool = False
    web_country: str = "ALL"
    web_brave_monthly_cap: int = 2000  # Brave's free plan: 2,000 a month (Brave is the backup; Ollama comes first)
    web_keys_path: Path = DEFAULT_CONFIG_PATH.parent / "web.env"
    # places and routes (Google Places and Routes; key GOOGLE_MAPS_API_KEY in web.env); off without [maps]
    maps_enabled: bool = False
    maps_home: str = ""  # the home address: the start of a route and, rounded to ~1 km, "near me" without a pin
    maps_daily_cap: int = 30  # per API and day: Google's free monthly allowance with room to spare
    maps_region: str = ""  # two-letter country code that biases results (like NL); empty: none sent

    # her personality (the user's own soul.md, style only); None: no style file (the tests)
    soul_path: Path | None = None
    # [personality] style: her built-in style while there is no usable soul.md (hearth or playful)
    personality_style: str = DEFAULT_STYLE
    # [personality] calm_on_worry: code's line after a usable soul.md (no joke on a worry); the user may turn it off
    personality_calm_on_worry: bool = True

    # [privacy] keep_days: messages, pages and tool calls older than this are deleted; None = kept, as before
    keep_days: int | None = None

    @property
    def spotify_enabled(self) -> bool:
        return bool(self.spotify_client_id)

    @property
    def spotify_token_path(self) -> Path:
        return self.data_dir / "spotify-tokens.json"

    @property
    def vendor_dir(self) -> Path:
        """Where the call helpers are (tools/call-tunnel's scripts): the code's own vendor folder when it has one (a
        release of an install in its own user, root-owned), else the data folder's (a checkout)."""
        shipped = CODE_ROOT / "vendor"
        return shipped if shipped.is_dir() else self.data_dir / "vendor"

    @property
    def call_signal_cli(self) -> Path:
        """The JVM signal-cli that places and answers calls."""
        return self.calls_signal_cli or self.vendor_dir / _CALLS_SIGNAL_CLI

    @property
    def java_home(self) -> Path | None:
        """Java for the JVM signal-cli: the config's, else the release's own JDK; None: the one macOS knows."""
        if self.calls_java_home is not None:
            return self.calls_java_home
        shipped = CODE_ROOT / "vendor" / _JDK_HOME
        return shipped if shipped.is_dir() else None

    @property
    def call_tunnel(self) -> Path:
        """Our signal-call-tunnel build, the helper signal-cli starts per call."""
        return self.calls_tunnel or self.vendor_dir / _CALLS_TUNNEL

    @property
    def call_socket_dir(self) -> Path:
        """The call helper's audio sockets (0700); its paths must stay under the 104-byte socket limit."""
        return self.calls_socket_dir or _default_call_socket_dir(self.data_dir)

    @property
    def wake_calls(self) -> bool:
        """Whether a "call me" reminder places a Signal call (it needs [calls]); else it is a text."""
        return self.calls_enabled and self.calls_wake_call

    @property
    def tapo_enabled(self) -> bool:
        return bool(self.tapo_devices)

    @property
    def voice_enabled(self) -> bool:
        return bool(self.voice_url)

    @property
    def voice_dir(self) -> Path:
        """Spoken answers (M4A) live here until sent."""
        return self.data_dir / "voice"

    @property
    def mail_enabled(self) -> bool:
        return bool(self.mail_accounts)

    @property
    def calendars(self) -> tuple[CalendarSource, ...]:
        """The calendars in the config's order: [[calendar.sources]], or the one Google calendar named "calendar"."""
        if not self.calendar_enabled:
            return ()
        return self.calendar_sources or (CalendarSource(DEFAULT_CALENDAR),)

    def calendar_mailbox(self, source: CalendarSource) -> MailAccount | None:
        """The mailbox whose Outlook calendar this is; None for a Google calendar."""
        return next((account for account in self.mail_accounts if account.name == source.mailbox), None)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assistant.db"

    @property
    def mail_token_path(self) -> Path:
        return self.data_dir / "mail-tokens.json"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load and validate the config file; refuse a file that others can read."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {path} (copy config.example.toml there, chmod 600)") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{path} must be private: chmod 600 {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    # her style file lives next to the config that was loaded (another --config never reads the default soul.md)
    return dataclasses.replace(parse_config(raw), soul_path=path.parent / "soul.md")


def parse_config(raw: dict[str, object]) -> Config:
    """Validate raw TOML values and build a Config."""
    number = raw.get("bot_number")
    if not isinstance(number, str) or not _NUMBER.match(number):
        raise ConfigError("bot_number must look like +306900000000")
    acis_raw = raw.get("allowed_acis")
    if not isinstance(acis_raw, list) or not acis_raw:
        raise ConfigError("allowed_acis must list at least one Signal ACI (UUID)")
    acis: set[str] = set()
    for item in acis_raw:
        try:
            acis.add(str(uuid.UUID(str(item))))
        except ValueError as exc:
            raise ConfigError(f"allowed_acis: not a UUID: {item!r}") from exc
    tz = system_timezone()
    if "timezone" in raw:
        tz_name = str(raw["timezone"])
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"unknown timezone: {tz_name!r}") from exc
    strings: dict[str, str] = {}
    for key, default in (
        ("ollama_base_url", "http://127.0.0.1:11434/v1"),
        ("model", "local-worker"),
        ("signal_cli", default_signal_cli()),
    ):
        value = raw.get(key, default)
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{key} must be a non-empty string")
        strings[key] = value
    try:
        model_url = urllib.parse.urlsplit(strings["ollama_base_url"])
        local_model = model_url.scheme == "http" and model_url.hostname in LOCAL_HOSTS
    except ValueError:
        local_model = False
    if not local_model:  # mail, calendar and profile text go to the model: they never leave this Mac
        raise ConfigError("ollama_base_url must be Ollama on this Mac, like http://127.0.0.1:11434/v1")
    health = _table(raw, "health")
    projects = _table(raw, "projects")
    default_level = projects.get("default", "summary")
    if default_level not in PROJECT_LEVELS:
        raise ConfigError(f"projects.default must be one of {', '.join(PROJECT_LEVELS)}")
    mail = _table(raw, "mail")
    mail_settings = _mail_settings(mail)
    return Config(
        bot_number=number,
        allowed_acis=frozenset(acis),
        timezone=tz,
        host_label=_host_label(raw),
        name=_name(raw),
        avatar=_avatar(raw, _name(raw)),
        language=_language(raw),
        ollama_base_url=strings["ollama_base_url"],
        model=_model(strings["model"]),
        model_digest=_model_digest(raw),
        signal_cli=strings["signal_cli"],
        health_interval_hours=_positive(health, "interval_hours", 4, "health"),
        health_confirm_minutes=_positive(health, "confirm_minutes", 5, "health"),
        disk_free_min_percent=_positive(health, "disk_free_min_percent", 10, "health"),
        **_health_settings(health),
        projects_enabled="projects" in raw,
        projects_root=Path(str(projects.get("root", _default_projects_root()))).expanduser(),
        projects_default=str(default_level),
        projects_docs=_names(projects, "docs"),
        projects_code=_names(projects, "code"),
        digest_time=_digest_time(raw, mail),
        **mail_settings,
        **_voice_settings(raw.get("voice")),
        **_tapo_settings(_table(raw, "tapo")),
        **_calendar_settings(raw.get("calendar"), mail_settings.get("mail_accounts", ())),
        **_calls_settings(raw.get("calls"), voice="voice" in raw),
        **_spotify_settings(raw.get("spotify")),
        **_web_settings(raw.get("web")),
        **_maps_settings(raw.get("maps")),
        **_documents_settings(raw.get("documents")),
        chords_enabled=_chords_on(raw.get("chords")),
        **_reminder_settings(raw.get("reminders")),
        **_initiative_settings(raw.get("initiative")),
        soul_path=DEFAULT_CONFIG_PATH.parent / "soul.md",
        **_personality_settings(raw.get("personality")),
        keep_days=_keep_days(raw.get("privacy")),
    )


def is_cloud_model(name: str) -> bool:
    """An Ollama cloud model by its name: the tag ends in "-cloud" or is "cloud" (gpt-oss:120b-cloud, gemma4:cloud).
    It runs on Ollama's servers. A local name that points at one is found by Ollama's own entry (gemma.ModelEntry)."""
    _, colon, tag = name.strip().casefold().rpartition("/")[2].rpartition(":")
    return bool(colon) and (tag == "cloud" or tag.endswith("-cloud"))  # a name without a tag ("my-cloud") is local


def _model(name: str) -> str:
    """The model always runs on this Mac: a cloud model's name is refused."""
    if is_cloud_model(name):
        raise ConfigError(
            f'model "{name}" is an Ollama cloud model: it runs on Ollama\'s servers, and the model runs only on this '
            'Mac. Use one downloaded here ("ollama list" shows them).'
        )
    return name


def _model_digest(raw: dict[str, object]) -> str:
    """The digest Ollama reports for the model alias (/api/tags), as 64 lowercase hex digits; "" when not pinned."""
    if "model_digest" not in raw:
        return ""
    value = raw["model_digest"]
    found = _DIGEST.match(value.strip().lower()) if isinstance(value, str) else None
    if found is None:
        raise ConfigError('model_digest must be the model\'s sha256 digest, like "sha256:" and 64 hex digits')
    return found.group(1)


def _keep_days(privacy: object) -> int | None:
    """[privacy] keep_days: how many days of history are kept (whole days, at least 1); left out: all of it."""
    if privacy is None:
        return None
    if not isinstance(privacy, dict):
        raise ConfigError("[privacy] must be a table")
    if "keep_days" not in privacy:
        return None
    days = privacy["keep_days"]
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= KEEP_DAYS_MAX:
        raise ConfigError(f"privacy.keep_days must be a whole number of days from 1 to {KEEP_DAYS_MAX}")
    return days


class _PersonalitySettings(TypedDict, total=False):
    personality_style: str
    personality_calm_on_worry: bool


def _personality_settings(personality: object) -> _PersonalitySettings:
    """[personality] as Config keyword arguments: `style`, her built-in style (hearth when left out, so an install
    from before without a soul.md moves to hearth), and `calm_on_worry`, code's line after a usable soul.md (on when
    left out)."""
    if personality is None:
        return {}
    if not isinstance(personality, dict):
        raise ConfigError("[personality] must be a table")
    style = personality.get("style", DEFAULT_STYLE)
    if not isinstance(style, str) or style not in STYLES:
        choices = " or ".join(f'"{name}"' for name in STYLES)
        raise ConfigError(f"personality.style must be {choices} (default: {DEFAULT_STYLE})")
    calm = personality.get("calm_on_worry", True)
    if not isinstance(calm, bool):
        raise ConfigError("personality.calm_on_worry must be true or false (default: true)")
    return {"personality_style": style, "personality_calm_on_worry": calm}


def _host_label(raw: dict[str, object]) -> str:
    """What this machine is called in the status report and in the tool descriptions ("the study Mac")."""
    value = raw.get("host_label", DEFAULT_HOST_LABEL)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40 or not value.isprintable():
        raise ConfigError("host_label must be 1-40 characters, like the study Mac (default: the Mac)")
    return value.strip()


def _name(raw: dict[str, object]) -> str:
    return check_name(raw.get("name", DEFAULT_NAME))


def check_name(value: object) -> str:
    """The assistant's name: 1-20 letters (of any language), spaces or hyphens, starting and ending with a letter."""
    name = value.strip() if isinstance(value, str) else ""
    if not (
        0 < len(name) <= 20
        and name[0].isalpha()
        and name[-1].isalpha()
        and all(ch.isalpha() or ch in " -" for ch in name)
    ):
        raise ConfigError(f"name must be 1-20 letters, spaces or hyphens (default: {DEFAULT_NAME})")
    return name


def default_avatar(name: str) -> Path | None:
    """The picture for an `avatar` left out (owner, 15 Oct): for a name other than Estia, its first letter, a to z,
    with Estia's orange full stop ("t." for ours, Tiffany, docs/brand/letters, drawn by docs/brand/source/letters.py),
    an accented letter as its base letter (NFKD: Á, É, Ñ, Ö); else, and for any other first letter (another script, or
    Ø and Ł, which have no base letter), Estia's e. None when the repo's file is not there."""
    letter = unicodedata.normalize("NFKD", name[:1])[:1].lower()
    if name.casefold() != DEFAULT_NAME.casefold() and len(letter) == 1 and "a" <= letter <= "z":
        picture = LETTER_AVATARS / f"{letter}.png"
        if picture.is_file():
            return picture
    return DEFAULT_AVATAR if DEFAULT_AVATAR.is_file() else None


def _avatar(raw: dict[str, object], name: str) -> Path | None:
    """The Signal profile picture: a full path (~ allowed); "" for none; left out: `default_avatar` for her name.
    Only the setting's form is checked here: the file is checked where it is used (`read_avatar`, at the bridge's
    start and in doctor), so a picture that was moved or deleted never stops the bridge."""
    if "avatar" not in raw:
        return default_avatar(name)
    value = raw["avatar"]
    if not isinstance(value, str):
        raise ConfigError('avatar must be the path of a PNG or JPEG file, or "" for none')
    if not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ConfigError(f"avatar: {path} must be a full path (or start with ~/)")
    return path


class AvatarError(Exception):
    """Why the picture cannot be used: a fixed phrase that never names the file (it goes to the log)."""


def read_avatar(path: Path) -> bytes:
    """The picture's bytes: a regular file, a PNG or JPEG, at most 5 MB. AvatarError says why not."""
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise AvatarError("not a file")
        if info.st_size > AVATAR_MAX_BYTES:
            raise AvatarError("larger than 5 MB")
        with path.open("rb") as handle:
            data = handle.read(AVATAR_MAX_BYTES + 1)
    except FileNotFoundError as exc:
        raise AvatarError("no such file") from exc
    except OSError as exc:
        raise AvatarError(f"cannot be read ({type(exc).__name__})") from exc
    if len(data) > AVATAR_MAX_BYTES:  # it grew after the size was read
        raise AvatarError("larger than 5 MB")
    if not data.startswith(_IMAGE_STARTS):
        raise AvatarError("not a PNG or JPEG file")
    return data


def _language(raw: dict[str, object]) -> str:
    """The language she listens and speaks in at the start: one of the 11 codes."""
    value = raw.get("language", "en")
    if value not in CODES:
        raise ConfigError(f"language must be one of {', '.join(CODES)} (default: en)")
    return str(value)


class _HealthSettings(TypedDict, total=False):
    health_extra_checks: tuple[ExtraCheck, ...]
    watched_log_dirs: tuple[Path, ...]


_CHECK_LABEL = re.compile(r"^\w[\w .'()/&+-]{0,39}$")
_LAUNCHD_TARGET = re.compile(r"^(?:system|gui/[0-9]+|user/[0-9]+)/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _health_settings(health: dict[str, object]) -> _HealthSettings:
    """[health] extra_checks and watch_logs as Config keyword arguments (only what is set)."""
    settings: _HealthSettings = {}
    if "extra_checks" in health:
        settings["health_extra_checks"] = _extra_checks(health["extra_checks"])
    if "watch_logs" in health:
        folders = health["watch_logs"]
        if not isinstance(folders, list) or not all(isinstance(item, str) and item.strip() for item in folders):
            raise ConfigError("health.watch_logs must be a list of folders")
        paths = tuple(Path(item).expanduser() for item in folders)
        if not all(path.is_absolute() for path in paths):
            raise ConfigError("health.watch_logs: each folder must be an absolute path (or start with ~)")
        settings["watched_log_dirs"] = paths
    return settings


def _extra_checks(raw: object) -> tuple[ExtraCheck, ...]:
    """[[health.extra_checks]]: a label and exactly one of http (a local URL) or launchd (a service)."""
    if not isinstance(raw, list):
        raise ConfigError("health.extra_checks must be a list of {label, http} or {label, launchd} tables")
    checks: list[ExtraCheck] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError("health.extra_checks: each check must be a table with a label and http or launchd")
        label = item.get("label")
        if not isinstance(label, str) or not _CHECK_LABEL.match(label.strip()) or not ExtraCheck(label.strip()).slug:
            raise ConfigError("health.extra_checks: each label must be 1-40 letters, digits or spaces")
        label = label.strip()
        http, launchd = item.get("http", ""), item.get("launchd", "")
        if bool(http) == bool(launchd):
            raise ConfigError(f"health.extra_checks: {label}: give exactly one of http or launchd")
        stuck = 0.0
        if "stuck_after_minutes" in item:
            minutes = item["stuck_after_minutes"]
            if not launchd or isinstance(minutes, bool) or not isinstance(minutes, int | float) or minutes <= 0:
                raise ConfigError(
                    f"health.extra_checks: {label}: stuck_after_minutes must be a positive number (launchd only)"
                )
            stuck = float(minutes) * 60
        if http and not _local_http(http):
            raise ConfigError(
                f"health.extra_checks: {label}: http must start with http:// and name a local address "
                "(127.0.0.1, localhost or a home network address)"
            )
        if launchd and (not isinstance(launchd, str) or not _LAUNCHD_TARGET.match(launchd)):
            raise ConfigError(f"health.extra_checks: {label}: launchd must be a service like system/com.example.backup")
        checks.append(ExtraCheck(label, http=str(http), launchd=str(launchd), stuck_after_seconds=stuck))
    if len({check.key for check in checks}) != len(checks):
        raise ConfigError("health.extra_checks: labels must be unique")
    return tuple(checks)


def _local_http(value: object) -> bool:
    """An http:// URL on this Mac or the home network (the status check never goes further)."""
    if not isinstance(value, str) or not value.isascii() or not value.isprintable() or " " in value:
        return False
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port  # a bad port raises ValueError
    except ValueError:
        return False
    if parts.scheme != "http" or not host:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or any(address in net for net in _HOME_NETWORKS if address.version == net.version)


def _digest_time(raw: dict[str, object], mail: dict[str, object]) -> str:
    """The top-level digest_time; the older [mail] digest_time still works when it is not set."""
    key = "digest_time" if "digest_time" in raw else "mail.digest_time"
    value = raw.get("digest_time", mail.get("digest_time", "09:15"))
    if not isinstance(value, str) or not _HHMM.match(value):
        raise ConfigError(f"{key} must look like 09:15")
    if value >= "12:00":  # a digest missed by downtime is sent until noon only
        raise ConfigError(f"{key} must be before 12:00")
    return value


def _table(raw: dict[str, object], name: str) -> dict[str, object]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _positive(table: dict[str, object], key: str, default: float, section: str) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{section}.{key} must be a positive number")
    return float(value)


def _names(table: dict[str, object], key: str) -> frozenset[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"projects.{key} must be a list of project folder names")
    return frozenset(str(item) for item in value)


class _MailSettings(TypedDict, total=False):
    mail_tenant_id: str
    mail_client_id: str
    mail_accounts: tuple[MailAccount, ...]
    mail_sync_minutes: float
    mail_keep_days: int
    mail_first_sync_days: int
    mail_reply_days: int
    mail_prefilter: bool
    mail_jev_url: str
    mail_jev_threshold: float


def _local_service(url: str) -> bool:
    """A plain http address on this Mac with a port and nothing else (no login, path or query)."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "http"
        and parts.hostname in LOCAL_HOSTS
        and port is not None
        and parts.username is None
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment
    )


def _mail_settings(mail: dict[str, object]) -> _MailSettings:
    """The [mail] table as Config keyword arguments (nothing when the table is missing)."""
    if not mail:
        return {}
    ids: dict[str, str] = {}
    for key in ("tenant_id", "client_id"):
        try:
            ids[key] = str(uuid.UUID(str(mail.get(key, ""))))
        except ValueError as exc:
            raise ConfigError(f"mail.{key} must be the app registration's {key.replace('_', ' ')} (a UUID)") from exc
    raw_accounts = mail.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        raise ConfigError("mail.accounts must list at least one mailbox ([[mail.accounts]] with name and address)")
    accounts: list[MailAccount] = []
    for item in raw_accounts:
        name = item.get("name") if isinstance(item, dict) else None
        address = item.get("address") if isinstance(item, dict) else None
        if not isinstance(name, str) or not _ACCOUNT_NAME.match(name):
            raise ConfigError("mail.accounts: each name must be short, lowercase letters, digits, - or _")
        if not isinstance(address, str) or not _ADDRESS.match(address):
            raise ConfigError(f"mail.accounts: {name}: address must be an email address")
        accounts.append(MailAccount(name, address.lower()))
    if len({a.name for a in accounts}) != len(accounts) or len({a.address for a in accounts}) != len(accounts):
        raise ConfigError("mail.accounts: names and addresses must be unique")
    days: dict[str, int] = {}
    for key, default in (("keep_days", 30), ("first_sync_days", 30), ("reply_days", 7)):
        value = mail.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"mail.{key} must be a positive whole number")
        days[key] = value
    prefilter = mail.get("prefilter", True)
    if not isinstance(prefilter, bool):
        raise ConfigError("mail.prefilter must be true or false")
    jev_url = mail.get("jev_url", "")
    if not isinstance(jev_url, str) or (jev_url and not _local_service(jev_url)):
        raise ConfigError("mail.jev_url must be the local AgentJev service, like http://127.0.0.1:8147")
    threshold = mail.get("jev_threshold", 0.9)
    if isinstance(threshold, bool) or not isinstance(threshold, int | float) or not 0.5 <= threshold <= 1:
        raise ConfigError("mail.jev_threshold must be from 0.5 to 1")
    return {
        "mail_prefilter": prefilter,
        "mail_jev_url": jev_url.rstrip("/"),
        "mail_jev_threshold": float(threshold),
        "mail_tenant_id": ids["tenant_id"],
        "mail_client_id": ids["client_id"],
        "mail_accounts": tuple(accounts),
        "mail_sync_minutes": _positive(mail, "sync_minutes", 5, "mail"),
        "mail_keep_days": days["keep_days"],
        "mail_first_sync_days": days["first_sync_days"],
        "mail_reply_days": days["reply_days"],
    }


class _VoiceSettings(TypedDict, total=False):
    voice_url: str
    voice_name: str
    voice_speed: float
    voice_max_spoken_chars: int
    voice_max_audio_bytes: int


def _voice_settings(voice: object) -> _VoiceSettings:
    """The [voice] table as Config keyword arguments; an empty [voice] turns voice on with the defaults."""
    if voice is None:
        return {}
    if not isinstance(voice, dict):
        raise ConfigError("[voice] must be a table")
    url = voice.get("url", "http://127.0.0.1:8150")
    if not isinstance(url, str) or not _VOICE_URL.match(url):
        raise ConfigError("voice.url must be the local voice service, like http://127.0.0.1:8150")
    name = voice.get("voice", "af_heart")
    if not isinstance(name, str) or not _VOICE_NAME.match(name):
        raise ConfigError("voice.voice must be a Kokoro voice name like af_heart")
    speed = voice.get("speed", 1.05)
    if isinstance(speed, bool) or not isinstance(speed, int | float) or not 0.7 <= speed <= 1.5:
        raise ConfigError("voice.speed must be between 0.7 and 1.5")
    limits: dict[str, int] = {}
    for key, default, low, high in (
        ("max_spoken_chars", 1500, 100, 1500),
        ("max_audio_bytes", 10_000_000, 1, 10_000_000),
    ):
        value = voice.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigError(f"voice.{key} must be a whole number from {low:,} to {high:,}")
        limits[key] = value
    return {
        "voice_url": url,
        "voice_name": name,
        "voice_speed": float(speed),
        "voice_max_spoken_chars": limits["max_spoken_chars"],
        "voice_max_audio_bytes": limits["max_audio_bytes"],
    }


# private unicast ranges only (RFC 1918, IPv6 unique local): not 0.0.0.0, link-local, broadcast or test ranges
_HOME_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_DEVICE_NAME = re.compile(r"^[\w][\w '.-]{0,39}$")
DEVICE_KINDS = ("light", "plug")
DEVICE_RULES = ("normal", "ask first", "never off")
DEVICE_BRANDS = ("kasa", "hue")
_HUE_LIGHT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")  # a CLIP v2 resource id


class _TapoSettings(TypedDict, total=False):
    tapo_devices: tuple[DeviceSpec, ...]
    tapo_helper_timeout: float
    tapo_confirm_seconds: float


def _tapo_settings(tapo: dict[str, object]) -> _TapoSettings:
    """The [tapo] table as Config keyword arguments (nothing when the table is missing)."""
    if not tapo:
        return {}
    raw_devices = tapo.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise ConfigError("tapo.devices must list at least one device ([[tapo.devices]] with name, host, kind, rule)")
    devices: list[DeviceSpec] = []
    for item in raw_devices:
        fields = item if isinstance(item, dict) else {}
        name, host = fields.get("name"), fields.get("host")
        kind, rule = fields.get("kind", "light"), fields.get("rule", "normal")
        brand, light = fields.get("brand", "kasa"), fields.get("light")
        if not isinstance(name, str) or not _DEVICE_NAME.match(name.strip()):
            raise ConfigError("tapo.devices: each name must be 1-40 letters, digits, spaces or - ' .")
        if brand not in DEVICE_BRANDS:
            raise ConfigError(f"tapo.devices: {name}: brand must be kasa or hue (kasa when left out)")
        if kind not in DEVICE_KINDS or rule not in DEVICE_RULES:
            raise ConfigError(f"tapo.devices: {name}: kind must be light or plug, rule normal, ask first or never off")
        if brand == "hue":
            devices.append(DeviceSpec(name.strip(), "", str(kind), str(rule), "hue", _hue_light(name, host, light)))
            continue
        if light is not None:
            raise ConfigError(f'tapo.devices: {name}: only a Hue light has a light id (brand = "hue")')
        try:
            address = ipaddress.ip_address(str(host))
        except ValueError as exc:
            raise ConfigError(f"tapo.devices: {name}: host must be the device's IP address") from exc
        if not any(address in network for network in _HOME_NETWORKS if address.version == network.version):
            raise ConfigError(f"tapo.devices: {name}: host must be an address on the home network")
        devices.append(DeviceSpec(name.strip(), str(address), str(kind), str(rule)))
    hosts = [d.host for d in devices if not d.hue]
    lights = [d.light for d in devices if d.hue]
    if (
        len({d.name.casefold() for d in devices}) != len(devices)
        or len(set(hosts)) != len(hosts)
        or len(set(lights)) != len(lights)
    ):
        raise ConfigError("tapo.devices: names, hosts and Hue light ids must be unique")
    return {
        "tapo_devices": tuple(devices),
        "tapo_helper_timeout": _positive(tapo, "helper_timeout", 15, "tapo"),
        "tapo_confirm_seconds": _positive(tapo, "confirm_seconds", 120, "tapo"),
    }


def _hue_light(name: str, host: object, light: object) -> str:
    """A Hue light's id on the bridge (lower case); it has no host: the bridge's address is saved at pairing."""
    if host is not None:
        raise ConfigError(
            f"tapo.devices: {name}: a Hue light has no host: the Hue Bridge's address is saved by scripts/hue-pair.sh"
        )
    if not isinstance(light, str) or not _HUE_LIGHT.match(light.strip().casefold()):
        raise ConfigError(
            f"tapo.devices: {name}: light must be the Hue Bridge's id for it (scripts/tapo-discover.sh lists them)"
        )
    return light.strip().casefold()


class _CalendarSettings(TypedDict, total=False):
    calendar_enabled: bool
    calendar_digest: bool
    calendar_helper_timeout: float
    calendar_sources: tuple[CalendarSource, ...]


def _calendar_settings(calendar: object, accounts: tuple[MailAccount, ...]) -> _CalendarSettings:
    """The [calendar] table as Config keyword arguments; an empty [calendar] turns the calendar on."""
    if calendar is None:
        return {}
    if not isinstance(calendar, dict):
        raise ConfigError("[calendar] must be a table")
    digest = calendar.get("digest", True)
    if not isinstance(digest, bool):
        raise ConfigError("calendar.digest must be true or false")
    timeout = _positive(calendar, "helper_timeout", 30, "calendar")
    if timeout > 30:  # the digest gives the calendar 5 s more; the tools 45 s
        raise ConfigError("calendar.helper_timeout must be at most 30 seconds")
    settings: _CalendarSettings = {
        "calendar_enabled": True,
        "calendar_digest": digest,
        "calendar_helper_timeout": timeout,
    }
    if "sources" in calendar:
        settings["calendar_sources"] = _calendar_sources(calendar["sources"], accounts)
    return settings


def _caldav_server(value: object) -> bool:
    """A CalDAV server's address: https, a host, no login, query or fragment (the calendar helper checks the same)."""
    if not isinstance(value, str) or not value or len(value) > 200 or not value.isprintable() or " " in value:
        return False
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and not parts.query
        and not parts.fragment
        and "?" not in value
        and "#" not in value
        and (port is None or 0 < port < 65536)
    )


def _caldav_fields(name: str, fields: dict[str, Any]) -> dict[str, str]:
    """A CalDAV calendar's caldav, user and calendar ({} for another kind), checked."""
    if not any(key in fields for key in ("caldav", "user", "calendar")):
        return {}
    if not all(key in fields for key in ("caldav", "user", "calendar")):
        raise ConfigError(f"calendar.sources: {name}: a CalDAV calendar needs caldav, user and calendar")
    if "mailbox" in fields:
        raise ConfigError(f"calendar.sources: {name}: either a mailbox's calendar or a CalDAV one")
    if not _caldav_server(fields["caldav"]):
        raise ConfigError(
            f"calendar.sources: {name}: caldav must be the server's https address, without a login or a query "
            '("https://caldav.icloud.com")'
        )
    user, calendar = fields["user"], fields["calendar"]
    if not isinstance(user, str) or not user.strip() or len(user) > 200 or not user.isprintable():
        raise ConfigError(f"calendar.sources: {name}: user must be the account's user name, on one line")
    if not isinstance(calendar, str) or not calendar.strip() or len(calendar) > 100 or not calendar.isprintable():
        raise ConfigError(
            f"calendar.sources: {name}: calendar must be the calendar's name as the calendar app shows it, "
            "1-100 characters on one line"
        )
    return {"caldav": str(fields["caldav"]), "user": user.strip(), "calendar": calendar.strip()}


def _calendar_sources(raw: object, accounts: tuple[MailAccount, ...]) -> tuple[CalendarSource, ...]:
    """[[calendar.sources]]: a name each (a mailbox name's rule), an optional label, and a mailbox for an Outlook
    calendar (each mailbox once) or caldav, user and calendar for a CalDAV calendar. Names are unique across the kinds,
    "-" and "_" counting as one (calendar.env keys)."""
    if not isinstance(raw, list) or not raw:
        raise ConfigError("calendar.sources must list calendars ([[calendar.sources]] with a name)")
    if len(raw) > MAX_CALENDARS:
        raise ConfigError(f"calendar.sources: at most {MAX_CALENDARS} calendars")
    mailboxes = {account.name for account in accounts}
    sources: list[CalendarSource] = []
    for item in raw:
        fields = item if isinstance(item, dict) else {}
        name = fields.get("name")
        if not isinstance(name, str) or not _ACCOUNT_NAME.match(name):
            raise ConfigError("calendar.sources: each name must be short, lowercase letters, digits, - or _")
        label = fields.get("label", "")
        if "label" in fields and (
            not isinstance(label, str) or not label.strip() or len(label.strip()) > 30 or not label.isprintable()
        ):
            raise ConfigError(f"calendar.sources: {name}: label must be 1-30 characters on one line")
        caldav = _caldav_fields(name, fields)
        mailbox = fields.get("mailbox", "")
        if "mailbox" in fields and not mailboxes:
            raise ConfigError(f"calendar.sources: {name}: a mailbox's calendar needs [mail] with that mailbox")
        if "mailbox" in fields and mailbox not in mailboxes:
            raise ConfigError(f"calendar.sources: {name}: mailbox must be the name of one of the [[mail.accounts]]")
        sources.append(CalendarSource(name, str(label).strip(), str(mailbox), **caldav))
    if len({source.name.replace("-", "_") for source in sources}) != len(sources):
        raise ConfigError("calendar.sources: names must be unique (- and _ count as the same)")
    used = [source.mailbox for source in sources if source.mailbox]
    if len(set(used)) != len(used):
        raise ConfigError("calendar.sources: each mailbox's calendar may be listed at most once")
    return tuple(sources)


class _CallsSettings(TypedDict, total=False):
    calls_enabled: bool
    calls_signal_cli: Path
    calls_java_home: Path
    calls_tunnel: Path
    calls_socket_dir: Path
    calls_speed: float
    calls_end_of_turn_seconds: float
    calls_idle_prompt_seconds: float
    calls_max_minutes: float
    calls_summary: bool
    calls_fillers: bool
    calls_acknowledge: bool
    calls_answer_after_seconds: float
    calls_wake_call: bool
    calls_max_calls_per_day: int
    calls_barge: str
    calls_interrupt_old: str
    calls_barge_unread: str
    calls_early_end: bool
    calls_early_end_pause_seconds: float
    calls_pause: str


# [calls] interrupt, in the words people read, and the rule each one means (owner, 14 Oct; call_audio.Barge's names)
INTERRUPT = {"only speech": "speech", "any sound": "loudness"}
# still read, with doctor's suggestion of the new wording: the short forms, and the old key barge with its values
INTERRUPT_READ = INTERRUPT | {"speech": "speech", "sound": "loudness", "loudness": "loudness"}
INTERRUPT_NEW = {rule: words for words, rule in INTERRUPT.items()}  # the rule's new wording, for doctor
INTERRUPT_ERROR = 'calls.interrupt must be "only speech" or "any sound"'
# [calls] pause, in the words people read: what keeps a turn open (owner, 17 Oct; call_audio.TurnEnd's names)
PAUSE = {"no speech": "speech", "quiet": "loudness"}
PAUSE_ERROR = 'calls.pause must be "no speech" or "quiet"'
EARLY_END_PAUSE = (0.3, 1.5)  # [calls] early_end_pause_seconds: from, to


def _interrupt(calls: dict[str, object]) -> tuple[str, str, str]:
    """What stops her on a call: (the rule, an older wording that was read or "", an old barge line that was not read
    or ""). interrupt wins over the old barge."""
    for key in ("interrupt", "barge"):
        if key in calls and (not isinstance(calls[key], str) or calls[key] not in INTERRUPT_READ):
            raise ConfigError(INTERRUPT_ERROR)
    barge = f'barge = "{calls["barge"]}"' if "barge" in calls else ""
    if "interrupt" in calls:
        words = str(calls["interrupt"])
        return INTERRUPT_READ[words], "" if words in INTERRUPT else f'interrupt = "{words}"', barge
    if "barge" in calls:
        return INTERRUPT_READ[str(calls["barge"])], barge, ""
    return "speech", "", ""


def _early_end(calls: dict[str, object], full: float) -> tuple[bool, float, str]:
    """The end of a turn: (early end on, its short pause, what keeps a turn open as "speech" or "loudness"). A short
    pause set by hand must be below the full pause (end_of_turn_seconds); the default one under a low full pause is
    simply never reached."""
    early = calls.get("early_end", True)
    if not isinstance(early, bool):
        raise ConfigError("calls.early_end must be true or false")
    low, high = EARLY_END_PAUSE
    seconds = calls.get("early_end_pause_seconds", 0.6)
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not low <= seconds <= high:
        raise ConfigError(f"calls.early_end_pause_seconds must be from {low:g} to {high:g}")
    if "early_end_pause_seconds" in calls and seconds >= full:
        raise ConfigError("calls.early_end_pause_seconds must be below calls.end_of_turn_seconds")
    pause = calls.get("pause", "no speech")
    if not isinstance(pause, str) or pause not in PAUSE:
        raise ConfigError(PAUSE_ERROR)
    return early, float(seconds), PAUSE[pause]


def _calls_settings(calls: object, *, voice: bool) -> _CallsSettings:
    """The [calls] table as Config keyword arguments; an empty [calls] turns calls on (it needs [voice])."""
    if calls is None:
        return {}
    if not isinstance(calls, dict):
        raise ConfigError("[calls] must be a table")
    if not voice:
        raise ConfigError("[calls] needs [voice] (the voice service listens and speaks on calls)")
    paths: dict[str, Path] = {}
    for key in ("signal_cli", "java_home", "tunnel", "socket_dir"):
        if key in calls:
            value = calls[key]
            if not isinstance(value, str) or not value:
                raise ConfigError(f"calls.{key} must be a path")
            paths[key] = Path(value).expanduser()
    if "socket_dir" in paths:  # set by hand: used as it is, so it must fit
        if not _fits_sockets(paths["socket_dir"]):
            raise ConfigError("calls.socket_dir is too long for a Unix socket path (keep it under 65 characters)")
    elif not _fits_sockets(_default_call_socket_dir(_default_data_dir())):
        raise ConfigError(
            "your home folder's path is too long for the call sockets: set calls.socket_dir to a short folder "
            "(under 65 characters)"
        )
    speed = calls.get("speed", 1.05)
    if isinstance(speed, bool) or not isinstance(speed, int | float) or not 0.7 <= speed <= 1.5:
        raise ConfigError("calls.speed must be between 0.7 and 1.5")
    numbers: dict[str, float] = {}
    for key, default, low, high in (
        ("end_of_turn_seconds", 2.3, 0.5, 5.0),
        ("idle_prompt_seconds", 90, 10, 3600),
        ("max_call_minutes", 60, 1, 240),
        ("answer_after_seconds", 5, 0, 20),
    ):
        value = calls.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
            raise ConfigError(f"calls.{key} must be from {low:g} to {high:g}")
        numbers[key] = float(value)
    summary = calls.get("summary", True)
    if not isinstance(summary, bool):
        raise ConfigError("calls.summary must be true or false")
    fillers = calls.get("fillers", False)
    if not isinstance(fillers, bool):
        raise ConfigError("calls.fillers must be true or false")
    acknowledge = calls.get("acknowledge", True)
    if not isinstance(acknowledge, bool):
        raise ConfigError("calls.acknowledge must be true or false")
    wake_call = calls.get("wake_call", True)
    if not isinstance(wake_call, bool):
        raise ConfigError("calls.wake_call must be true or false")
    per_day = calls.get("max_calls_per_day", 3)
    if isinstance(per_day, bool) or not isinstance(per_day, int) or not 1 <= per_day <= 10:
        raise ConfigError("calls.max_calls_per_day must be a whole number from 1 to 10")
    barge, interrupt_old, barge_unread = _interrupt(calls)
    early_end, early_pause, pause = _early_end(calls, numbers["end_of_turn_seconds"])
    settings: _CallsSettings = {
        "calls_enabled": True,
        "calls_speed": float(speed),
        "calls_end_of_turn_seconds": numbers["end_of_turn_seconds"],
        "calls_idle_prompt_seconds": numbers["idle_prompt_seconds"],
        "calls_max_minutes": numbers["max_call_minutes"],
        "calls_summary": summary,
        "calls_fillers": fillers,
        "calls_acknowledge": acknowledge,
        "calls_answer_after_seconds": numbers["answer_after_seconds"],
        "calls_wake_call": wake_call,
        "calls_max_calls_per_day": per_day,
        "calls_barge": barge,
        "calls_interrupt_old": interrupt_old,
        "calls_barge_unread": barge_unread,
        "calls_early_end": early_end,
        "calls_early_end_pause_seconds": early_pause,
        "calls_pause": pause,
    }
    if "signal_cli" in paths:
        settings["calls_signal_cli"] = paths["signal_cli"]
    if "java_home" in paths:
        settings["calls_java_home"] = paths["java_home"]
    if "tunnel" in paths:
        settings["calls_tunnel"] = paths["tunnel"]
    if "socket_dir" in paths:
        settings["calls_socket_dir"] = paths["socket_dir"]
    return settings


_SPOTIFY_ID = re.compile(r"^[0-9a-f]{32}$")


class _SpotifySettings(TypedDict, total=False):
    spotify_client_id: str
    spotify_default_device: str
    spotify_volume_start: int
    spotify_volume_cap: int
    spotify_bose: tuple[tuple[str, str], ...]


def _spotify_settings(spotify: object) -> _SpotifySettings:
    """The [spotify] table as Config keyword arguments; it needs the Client ID of the user's Spotify app."""
    if spotify is None:
        return {}
    if not isinstance(spotify, dict):
        raise ConfigError("[spotify] must be a table")
    client_id = spotify.get("client_id")
    if not isinstance(client_id, str) or not _SPOTIFY_ID.match(client_id):
        raise ConfigError("spotify.client_id must be the 32-character Client ID of your Spotify app")
    device = spotify.get("default_device", "")
    if not isinstance(device, str):
        raise ConfigError("spotify.default_device must be a speaker name, like Kitchen speaker")
    levels: dict[str, int] = {}
    for key, default in (("volume_start", 30), ("volume_cap", 60)):
        value = spotify.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ConfigError(f"spotify.{key} must be a whole number from 1 to 100")
        levels[key] = value
    if levels["volume_start"] > levels["volume_cap"]:
        raise ConfigError("spotify.volume_start must not be above spotify.volume_cap")
    bose = spotify.get("bose", {})
    if not isinstance(bose, dict):
        raise ConfigError("[spotify.bose] must map speaker names to addresses")
    speakers: list[tuple[str, str]] = []
    for name, host in bose.items():
        try:
            address = ipaddress.ip_address(str(host))
        except ValueError as exc:
            raise ConfigError(f"spotify.bose: {name}: must be the speaker's IP address") from exc
        if not any(address in network for network in _HOME_NETWORKS if address.version == network.version):
            raise ConfigError(f"spotify.bose: {name}: must be an address on the home network")
        speakers.append((str(name), str(address)))
    return {
        "spotify_client_id": client_id,
        "spotify_default_device": device.strip(),
        "spotify_volume_start": levels["volume_start"],
        "spotify_volume_cap": levels["volume_cap"],
        "spotify_bose": tuple(speakers),
    }


class _WebSettings(TypedDict, total=False):
    web_enabled: bool
    web_country: str
    web_brave_monthly_cap: int


def _web_settings(web: object) -> _WebSettings:
    """The [web] table as Config keyword arguments; an empty [web] turns web search on."""
    if web is None:
        return {}
    if not isinstance(web, dict):
        raise ConfigError("[web] must be a table")
    country = web.get("country", "ALL")
    if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}|ALL", country):
        raise ConfigError("web.country must be a two-letter country code, like NL, or ALL")
    cap = web.get("brave_monthly_cap", 2000)
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= 100_000:
        raise ConfigError("web.brave_monthly_cap must be a whole number from 0 to 100000 (0: Ollama only)")
    return {"web_enabled": True, "web_country": country.upper(), "web_brave_monthly_cap": cap}


class _MapsSettings(TypedDict, total=False):
    maps_enabled: bool
    maps_home: str
    maps_daily_cap: int
    maps_region: str


def _maps_settings(maps: object) -> _MapsSettings:
    """The [maps] table as Config keyword arguments; an empty [maps] turns places and routes on."""
    if maps is None:
        return {}
    if not isinstance(maps, dict):
        raise ConfigError("[maps] must be a table")
    home = maps.get("home", "")
    if not isinstance(home, str) or len(home) > 200:
        raise ConfigError("maps.home must be an address of at most 200 characters")
    cap = maps.get("daily_cap", 30)
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 1000:
        raise ConfigError("maps.daily_cap must be a whole number from 1 to 1000")
    region = maps.get("region", "")
    if not isinstance(region, str) or not re.fullmatch(r"[A-Za-z]{2}|", region.strip()):
        raise ConfigError("maps.region must be a two-letter country code, like NL (leave it out for none)")
    return {
        "maps_enabled": True,
        "maps_home": " ".join(home.split()),
        "maps_daily_cap": cap,
        "maps_region": region.strip().upper(),
    }


class _DocumentsSettings(TypedDict, total=False):
    documents_enabled: bool
    documents_folder: Path
    documents_look_minutes: float


def _chords_on(chords: object) -> bool:
    """[chords]: an empty section turns the extra on; it has no settings."""
    if chords is None:
        return False
    if not isinstance(chords, dict):
        raise ConfigError("[chords] must be a table")
    return True


def _documents_settings(documents: object) -> _DocumentsSettings:
    """The [documents] table as Config keyword arguments; an empty [documents] turns documents on, in
    ~/Estia-KB. The folder is always an absolute path (~ is written out)."""
    if documents is None:
        return {}
    if not isinstance(documents, dict):
        raise ConfigError("[documents] must be a table")
    folder = documents.get("folder", "~/Estia-KB")
    path = Path(folder.strip()).expanduser() if isinstance(folder, str) and folder.strip() else None
    if path is None or not path.is_absolute():
        raise ConfigError("documents.folder must be an absolute path, like /Users/<you>/Estia-KB")
    minutes = documents.get("look_minutes", 60)
    if isinstance(minutes, bool) or not isinstance(minutes, int | float) or not 5 <= minutes <= 1440:
        raise ConfigError("documents.look_minutes must be a number of minutes from 5 to 1440 (default 60)")
    return {"documents_enabled": True, "documents_folder": path, "documents_look_minutes": float(minutes)}


class _ReminderSettings(TypedDict, total=False):
    reminders_enabled: bool
    reminders_max_open: int
    reminders_max_per_day: int


def _reminder_settings(reminders: object) -> _ReminderSettings:
    """The [reminders] table as Config keyword arguments; reminders are on without it."""
    if reminders is None:
        return {}
    if not isinstance(reminders, dict):
        raise ConfigError("[reminders] must be a table")
    enabled = reminders.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("reminders.enabled must be true or false")
    limits: dict[str, int] = {}
    for key, default, high in (("max_open", 50, 500), ("max_per_day", 20, 200)):
        value = reminders.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
            raise ConfigError(f"reminders.{key} must be a whole number from 1 to {high}")
        limits[key] = value
    return {
        "reminders_enabled": enabled,
        "reminders_max_open": limits["max_open"],
        "reminders_max_per_day": limits["max_per_day"],
    }


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def minutes_of(value: str) -> int:
    """ "08:15" as minutes after midnight (ValueError when it is no time)."""
    if not _HHMM.match(value):
        raise ValueError(f"not a time like 08:15: {value!r}")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def quiet_hours_of(value: str) -> tuple[int, int] | None:
    """ "22:00-08:00" as (start, end) minutes, across midnight; "" (or an equal start and end) for none."""
    if not value.strip():
        return None
    start, separator, end = value.strip().partition("-")
    if not separator:
        raise ValueError(f"not like 22:00-08:00: {value!r}")
    hours = (minutes_of(start.strip()), minutes_of(end.strip()))
    return None if hours[0] == hours[1] else hours


def weekday_time_of(value: str) -> tuple[int, int]:
    """ "Sun 19:00" as (weekday, minutes): Monday is 0."""
    day, _, moment = value.strip().partition(" ")
    if day[:3].lower() not in WEEKDAYS:
        raise ValueError(f"not like Sun 19:00: {value!r}")
    return WEEKDAYS.index(day[:3].lower()), minutes_of(moment.strip())


class _InitiativeSettings(TypedDict, total=False):
    initiative_enabled: bool
    initiative_followups: bool
    initiative_calendar: bool
    initiative_lookback: bool
    initiative_improvise: bool
    initiative_max_per_day: int
    initiative_followups_per_week: int
    initiative_improvise_per_week: int
    initiative_quiet_hours: str
    initiative_lookback_time: str
    initiative_early_check: str
    initiative_evening: str


def _initiative_settings(initiative: object) -> _InitiativeSettings:
    """The [initiative] table as Config keyword arguments; off unless `enabled = true` (opt-in), each kind on once they
    are. Only the form is checked here: a kind that needs a part that is not set up (the calendar notes without
    [calendar]) is simply off, and doctor says so."""
    if initiative is None:
        return {}
    if not isinstance(initiative, dict):
        raise ConfigError("[initiative] must be a table")
    switches: dict[str, bool] = {}
    for key in ("enabled", "followups", "calendar", "lookback", "improvise"):
        value = initiative.get(key, key != "enabled")  # opt-in; the kinds are on once she may start messages
        if not isinstance(value, bool):
            raise ConfigError(f"initiative.{key} must be true or false")
        switches[key] = value
    limits: dict[str, int] = {}
    for key, default, high in (("max_per_day", 1, 5), ("followups_per_week", 2, 7), ("improvise_per_week", 1, 7)):
        number = initiative.get(key, default)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= high:
            raise ConfigError(f"initiative.{key} must be a whole number from 1 to {high}")
        limits[key] = number
    texts: dict[str, str] = {}
    for key, fallback, check, example in (
        ("quiet_hours", "22:00-08:00", quiet_hours_of, '"22:00-08:00" (or "" for none)'),
        ("lookback_time", "Sun 19:00", weekday_time_of, '"Sun 19:00"'),
        ("early_check", "20:00", minutes_of, '"20:00"'),
    ):
        text = initiative.get(key, fallback)
        try:
            if not isinstance(text, str):
                raise ValueError(key)
            check(text)
        except ValueError:
            raise ConfigError(f"initiative.{key} must look like {example}") from None
        texts[key] = text.strip()
    evening = initiative.get("evening", "")  # left out or "": off
    if not isinstance(evening, str) or (evening.strip() and not _HHMM.match(evening.strip())):
        raise ConfigError('initiative.evening must look like "21:00" (or "" for none)')
    return {
        "initiative_enabled": switches["enabled"],
        "initiative_followups": switches["followups"],
        "initiative_calendar": switches["calendar"],
        "initiative_lookback": switches["lookback"],
        "initiative_improvise": switches["improvise"],
        "initiative_max_per_day": limits["max_per_day"],
        "initiative_followups_per_week": limits["followups_per_week"],
        "initiative_improvise_per_week": limits["improvise_per_week"],
        "initiative_quiet_hours": texts["quiet_hours"],
        "initiative_lookback_time": texts["lookback_time"],
        "initiative_early_check": texts["early_check"],
        "initiative_evening": evening.strip(),
    }
