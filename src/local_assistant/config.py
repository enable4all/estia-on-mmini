"""Configuration, read from ~/.config/local-assistant/config.toml.

The file says who may talk to the bot, so it must be private (mode 0600).
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
import subprocess
import tomllib
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from local_assistant.language import CODES

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "local-assistant" / "config.toml"
LOCALTIME = Path("/etc/localtime")  # on a Mac a link into the time zone database
JAVA_HOME_TOOL = Path("/usr/libexec/java_home")
DEFAULT_HOST_LABEL = "the Mac"
DEFAULT_NAME = "Estia"  # the assistant's name until the config's `name` gives her another (every home names its own)
# the Signal profile picture until the config's `avatar` names another ("" for none): Estia's e., when the repo has it
DEFAULT_AVATAR = Path(__file__).resolve().parents[2] / "docs" / "brand" / "estia-avatar.png"
AVATAR_MAX_BYTES = 5_000_000
_IMAGE_STARTS = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")  # PNG, JPEG
_NUMBER = re.compile(r"^\+[0-9]{8,15}$")
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
    """One calendar: a Google calendar through its iCal address (in calendar.env), or the Outlook calendar of a
    mailbox she already reads (Microsoft Graph, delegated Calendars.Read)."""

    name: str  # short and unique, like a mailbox name ("family"); in part names, scripts and calendar.env keys
    label: str = ""  # shown before its events when there are several ("Family"); default: from the name
    mailbox: str = ""  # a [[mail.accounts]] name: that mailbox's calendar; "" = a Google iCal address

    @property
    def shown(self) -> str:
        return self.label or self.name.replace("_", " ").replace("-", " ").capitalize()

    @property
    def ical_key(self) -> str:
        """Its key in calendar.env ("school-days": CALENDAR_ICS_URL_SCHOOL_DAYS); "" for a Microsoft calendar."""
        if self.mailbox:
            return ""
        if self.name == DEFAULT_CALENDAR:
            return "CALENDAR_ICS_URL"
        return "CALENDAR_ICS_URL_" + self.name.upper().replace("-", "_")

    @property
    def saved_key(self) -> str:
        """The key next to it that scripts/set-calendar-url.sh fills with when it saved the address ("school-days":
        CALENDAR_SAVED_AT_SCHOOL_DAYS), for doctor; "" for a Microsoft calendar."""
        return self.ical_key.replace("CALENDAR_ICS_URL", "CALENDAR_SAVED_AT", 1)


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
    name: str  # as in the Tapo or Kasa app, e.g. "living room lamp"; Gemma knows only names
    host: str  # its address on the home network (a DHCP reservation keeps it fixed)
    kind: str  # "light" or "plug"
    rule: str  # "normal", "ask first" or "never off"


def _default_tapo_helper() -> tuple[str, ...]:
    tapo = Path(__file__).resolve().parents[2] / "tools" / "tapo"
    return (str(tapo / ".venv" / "bin" / "python"), "-I", str(tapo / "helper.py"))  # -I: isolated mode


def _default_ical_helper() -> tuple[str, ...]:
    ical = Path(__file__).resolve().parents[2] / "tools" / "ical"
    return (str(ical / ".venv" / "bin" / "python"), "-I", str(ical / "helper.py"))


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
    signal_cli: str = "signal-cli"
    data_dir: Path = field(default_factory=_default_data_dir)
    log_dir: Path = field(default_factory=_default_log_dir)
    reply_max_tokens: int = 600
    window_tokens: int = 12_000
    emergency_tokens: int = 20_000
    summary_tokens: int = 500
    profile_max_chars: int = 6_000
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
    voice_speed: float = 1.1
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

    # voice calls (the JVM signal-cli + our signal-call-tunnel build, see tools/call-tunnel); off without [calls]
    calls_enabled: bool = False
    calls_signal_cli: Path | None = None  # default: data_dir / "vendor" / ...
    calls_java_home: Path | None = None  # default: what /usr/libexec/java_home says
    calls_tunnel: Path | None = None  # default: data_dir / "vendor" / ...
    calls_socket_dir: Path | None = None  # default: data_dir / "calls", or ~/.la-calls when that is too long
    calls_speed: float = 1.05
    calls_end_of_turn_seconds: float = 1.8
    calls_idle_prompt_seconds: float = 90
    calls_idle_hangup_seconds: float = 600
    calls_max_minutes: float = 60
    calls_summary: bool = True
    calls_fillers: bool = False  # "hmm, let me think" lines while she thinks (user: off)
    calls_acknowledge: bool = True  # a short "Got it." as soon as a request of 5+ words is taken in (user: on)
    calls_answer_after_seconds: float = 5.0  # let it ring about twice

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

    @property
    def spotify_enabled(self) -> bool:
        return bool(self.spotify_client_id)

    @property
    def spotify_token_path(self) -> Path:
        return self.data_dir / "spotify-tokens.json"

    @property
    def vendor_dir(self) -> Path:
        """Where the call helpers are built and installed (tools/call-tunnel's scripts)."""
        return self.data_dir / "vendor"

    @property
    def call_signal_cli(self) -> Path:
        """The JVM signal-cli that places and answers calls."""
        return self.calls_signal_cli or self.vendor_dir / _CALLS_SIGNAL_CLI

    @property
    def call_tunnel(self) -> Path:
        """Our signal-call-tunnel build, the helper signal-cli starts per call."""
        return self.calls_tunnel or self.vendor_dir / _CALLS_TUNNEL

    @property
    def call_socket_dir(self) -> Path:
        """The call helper's audio sockets (0700); its paths must stay under the 104-byte socket limit."""
        return self.calls_socket_dir or _default_call_socket_dir(self.data_dir)

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
    return parse_config(raw)


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
        ("signal_cli", "signal-cli"),
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
        avatar=_avatar(raw),
        language=_language(raw),
        ollama_base_url=strings["ollama_base_url"],
        model=strings["model"],
        signal_cli=strings["signal_cli"],
        health_interval_hours=_positive(health, "interval_hours", 4, "health"),
        health_confirm_minutes=_positive(health, "confirm_minutes", 5, "health"),
        disk_free_min_percent=_positive(health, "disk_free_min_percent", 10, "health"),
        **_health_settings(health),
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
        soul_path=DEFAULT_CONFIG_PATH.parent / "soul.md",
    )


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


def _avatar(raw: dict[str, object]) -> Path | None:
    """The Signal profile picture: a full path (~ allowed); "" for none; left out: Estia's e. from the repo, if it is
    there. Only the setting's form is checked here: the file is checked where it is used (`read_avatar`, at the
    bridge's start and in doctor), so a picture that was moved or deleted never stops the bridge."""
    if "avatar" not in raw:
        return DEFAULT_AVATAR if DEFAULT_AVATAR.is_file() else None
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
    speed = voice.get("speed", 1.1)
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
        if not isinstance(name, str) or not _DEVICE_NAME.match(name.strip()):
            raise ConfigError("tapo.devices: each name must be 1-40 letters, digits, spaces or - ' .")
        try:
            address = ipaddress.ip_address(str(host))
        except ValueError as exc:
            raise ConfigError(f"tapo.devices: {name}: host must be the device's IP address") from exc
        if not any(address in network for network in _HOME_NETWORKS if address.version == network.version):
            raise ConfigError(f"tapo.devices: {name}: host must be an address on the home network")
        if kind not in DEVICE_KINDS or rule not in DEVICE_RULES:
            raise ConfigError(f"tapo.devices: {name}: kind must be light or plug, rule normal, ask first or never off")
        devices.append(DeviceSpec(name.strip(), str(address), str(kind), str(rule)))
    if len({d.name.casefold() for d in devices}) != len(devices) or len({d.host for d in devices}) != len(devices):
        raise ConfigError("tapo.devices: names and hosts must be unique")
    return {
        "tapo_devices": tuple(devices),
        "tapo_helper_timeout": _positive(tapo, "helper_timeout", 15, "tapo"),
        "tapo_confirm_seconds": _positive(tapo, "confirm_seconds", 120, "tapo"),
    }


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


def _calendar_sources(raw: object, accounts: tuple[MailAccount, ...]) -> tuple[CalendarSource, ...]:
    """[[calendar.sources]]: a name each (a mailbox name's rule), an optional label, and a mailbox for an Outlook
    calendar (each mailbox once). Names are unique across both kinds, "-" and "_" counting as one (calendar.env keys)."""
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
        mailbox = fields.get("mailbox", "")
        if "mailbox" in fields and not mailboxes:
            raise ConfigError(f"calendar.sources: {name}: a mailbox's calendar needs [mail] with that mailbox")
        if "mailbox" in fields and mailbox not in mailboxes:
            raise ConfigError(f"calendar.sources: {name}: mailbox must be the name of one of the [[mail.accounts]]")
        sources.append(CalendarSource(name, str(label).strip(), str(mailbox)))
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
    calls_idle_hangup_seconds: float
    calls_max_minutes: float
    calls_summary: bool
    calls_fillers: bool
    calls_acknowledge: bool
    calls_answer_after_seconds: float


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
        ("end_of_turn_seconds", 1.8, 0.5, 5.0),
        ("idle_prompt_seconds", 90, 10, 3600),
        ("idle_hangup_seconds", 600, 30, 7200),
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
    settings: _CallsSettings = {
        "calls_enabled": True,
        "calls_speed": float(speed),
        "calls_end_of_turn_seconds": numbers["end_of_turn_seconds"],
        "calls_idle_prompt_seconds": numbers["idle_prompt_seconds"],
        "calls_idle_hangup_seconds": numbers["idle_hangup_seconds"],
        "calls_max_minutes": numbers["max_call_minutes"],
        "calls_summary": summary,
        "calls_fillers": fillers,
        "calls_acknowledge": acknowledge,
        "calls_answer_after_seconds": numbers["answer_after_seconds"],
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
