"""`scripts/doctor.sh`: is everything in place to start her (by the config's name, default Estia)? Each line says what
was found and, when something is missing, the command that fixes it.

Read-only: nothing is installed, started or sent; signal-cli is not run (only its account list is read), the
database is only read (the language chosen with /language, the reminders' times and counts), and no secret or
private value is printed (key files: present, private and which keys only; never the bot's number or a reminder's
text).
Key files are looked for next to the config file (for the default config, where the bridge reads them).

An optional part whose setup is in place shows what its last real use did, from the bridge's status.json
(part_status): ✓ working, ○ set up but not tested yet, ✗ failing with the fix. Doctor never uses a part itself, so
a failure whose fix was saved since (a sign-in, key, login, secret address or helper) is ○ again until the next use.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sqlite3
import stat
import sys
import time
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from local_assistant import prompts
from local_assistant.config import (
    DEFAULT_AVATAR,
    DEFAULT_CONFIG_PATH,
    DEFAULT_NAME,
    AvatarError,
    CalendarSource,
    Config,
    ConfigError,
    find_java_home,
    load_config,
    read_avatar,
)
from local_assistant.initiative import ENABLED_KEY, QUIET_KEY, Settings
from local_assistant.keyfile import read_keys
from local_assistant.language import CODES, ENGLISH, LANGUAGE_KEY
from local_assistant.mail_auth import TokenStore
from local_assistant.part_status import Record, calendar_mailbox, calendar_part, describe, label_of, mail_part, read
from local_assistant.reminders import FAILED, PROBLEM_DAYS, STUCK_S, iso, summary
from local_assistant.soul import load_soul
from local_assistant.spotify_auth import SpotifyTokens
from local_assistant.store import Store

Status = Literal["ok", "untested", "warn", "fail"]
MARKS: dict[Status, str] = {"ok": "✓", "untested": "○", "warn": "!", "fail": "✗"}
_STATES: dict[str, Status] = {"working": "ok", "untested": "untested", "failing": "fail"}
TOOLS = ("signal-cli", "ollama", "uv")
PLACEHOLDER_NUMBER = "+300000000000"  # config.example.toml's values
PLACEHOLDER_ACI = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class Finding:
    status: Status
    part: str
    text: str
    fix: str = ""


class Probe:
    """The outside world (replaced in tests)."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or Path.home()

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def platform(self) -> tuple[str, str]:
        return sys.platform, platform.machine()

    def java_home(self) -> Path | None:
        """Where the JVM signal-cli finds Java when [calls] java_home is not set."""
        return find_java_home()

    def now(self) -> float:
        return time.time()

    def get_json(self, url: str) -> Any:
        """GET a local service's JSON answer (OSError or ValueError when it does not answer properly)."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=3) as response:
            return json.loads(response.read(1_000_000))


def run(config_path: Path, probe: Probe) -> list[Finding]:
    """Every check, in the order of the setup guide; the optional parts only when they are turned on."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        fix = "" if config_path.exists() else f"cp config.example.toml {config_path} && chmod 600 {config_path}"
        return [_mac(probe, DEFAULT_NAME), _tools(probe), Finding("fail", "Config", str(exc), fix)]
    findings = [_mac(probe, cfg.name), _tools(probe)]
    placeholders = [
        key
        for key, used in (
            ("bot_number", cfg.bot_number == PLACEHOLDER_NUMBER),
            ("allowed_acis", PLACEHOLDER_ACI in cfg.allowed_acis),
        )
        if used
    ]
    if placeholders:
        findings.append(
            Finding(
                "fail",
                "Config",
                f"still the example's placeholder: {', '.join(placeholders)}",
                "put in the bot's number and your own account ID (setup_assistant.md, 2.4)",
            )
        )
    else:
        findings.append(
            Finding("ok", "Config", f"{config_path} reads fine, {len(cfg.allowed_acis)} allowed account(s)")
        )
    findings += [
        _model(cfg, probe),
        _signal_number(cfg, probe),
        _picture(cfg),
        _personality(config_path.parent, cfg.name),
        _language(cfg, probe),
    ]
    if cfg.reminders_enabled:
        findings.append(_reminders(cfg, probe.now()))
    findings.append(_initiative(cfg, probe.now()))
    records = read(cfg.data_dir / "status.json")  # what the bridge saw; nothing known without it
    findings += _optional_parts(cfg, config_path.parent, probe, _States(cfg, config_path.parent, records, probe.now()))
    findings.append(_daemon())
    return findings


def _mac(probe: Probe, name: str) -> Finding:
    system, machine = probe.platform()
    if system == "darwin" and machine == "arm64":
        return Finding("ok", "Mac", "macOS on Apple silicon")
    return Finding(
        "fail", "Mac", f"this is {system} on {machine}", f"{name} needs a Mac with Apple silicon (M1 or later)"
    )


def _tools(probe: Probe) -> Finding:
    missing = [tool for tool in TOOLS if probe.which(tool) is None]
    if not missing:
        return Finding("ok", "Tools", ", ".join(TOOLS))
    return Finding("fail", "Tools", f"missing: {', '.join(missing)}", f"brew install {' '.join(missing)}")


def _model(cfg: Config, probe: Probe) -> Finding:
    base = cfg.ollama_base_url.removesuffix("/").removesuffix("/v1")
    try:
        names = {str(item.get("name", "")) for item in probe.get_json(f"{base}/api/tags").get("models", [])}
    except (OSError, ValueError, AttributeError):
        return Finding("fail", "Model", f"Ollama does not answer at {base}", "brew services start ollama")
    if cfg.model in names or f"{cfg.model}:latest" in names:
        return Finding("ok", "Model", f"Ollama has {cfg.model}")
    return Finding(
        "fail",
        "Model",
        f"Ollama runs but has no model called {cfg.model}",
        f"ollama pull {cfg.model}"
        if cfg.model != "local-worker"
        else "ollama pull gemma4:26b, then create local-worker from it (setup_assistant.md, 2.2)",
    )


def _signal_number(cfg: Config, probe: Probe) -> Finding:
    accounts = probe.home / ".local/share/signal-cli/data/accounts.json"
    try:
        listed = json.loads(accounts.read_text(encoding="utf-8")).get("accounts", [])
        registered = any(isinstance(item, dict) and item.get("number") == cfg.bot_number for item in listed)
    except (OSError, ValueError, AttributeError):
        registered = False
    if registered:
        return Finding("ok", "Signal number", "the bot's number is registered with signal-cli")
    return Finding(
        "fail",
        "Signal number",
        "signal-cli has no account for bot_number",
        "register the bot's number: setup_assistant.md, 2.3 (signal-cli ... register, then verify)",
    )


def _picture(cfg: Config) -> Finding:
    """Her Signal profile picture. One that cannot be used is a warning: the bridge starts without it."""
    path = cfg.avatar
    if path is None:
        return Finding("ok", "Profile picture", prompts.DOCTOR_PICTURE_NONE)
    try:
        read_avatar(path)
    except AvatarError as exc:
        text = prompts.DOCTOR_PICTURE_BAD.format(path=path, problem=exc, name=cfg.name)
        return Finding("warn", "Profile picture", text, prompts.DOCTOR_PICTURE_FIX)
    if path == DEFAULT_AVATAR:
        return Finding("ok", "Profile picture", prompts.DOCTOR_PICTURE_ESTIA)
    return Finding("ok", "Profile picture", prompts.DOCTOR_PICTURE_OWN.format(file=path.name))


def _personality(folder: Path, name: str) -> Finding:
    path = folder / "soul.md"
    if not path.exists():  # the default: her built-in style
        return Finding("ok", "Personality", f"no soul.md: {name}'s built-in playful style (soul.example.md: your own)")
    soul = load_soul(path)
    if soul.problem:
        return Finding(
            "warn", "Personality", f"soul.md is not used: {soul.problem}", f"chmod 600 {path}, then check it"
        )
    return Finding("ok", "Personality", "soul.md")


def _read_only(cfg: Config) -> sqlite3.Connection:
    """The bridge's database, only read: while the bridge runs, through its write-ahead log; while it is stopped (no
    log), as an unchanging file, so nothing is created next to it (sqlite3.Error when it is missing)."""
    database = cfg.db_path
    mode = "mode=ro" if database.with_name(database.name + "-wal").exists() else "mode=ro&immutable=1"
    return sqlite3.connect(f"{database.as_uri()}?{mode}", uri=True)


def _current_language(cfg: Config) -> str:
    """The language in use: the one chosen with /language (the database's state table), else the config's. A
    database that is missing or cannot be read leaves the config's."""
    try:
        with closing(_read_only(cfg)) as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (LANGUAGE_KEY,)).fetchone()
    except sqlite3.Error:
        return cfg.language
    return row[0] if row is not None and row[0] in CODES else cfg.language


def _reminders(cfg: Config, now: float) -> Finding:
    """The reminders as /status shows them (times and counts, never their text); a warning for a failed one in the
    last 7 days or one stuck firing (a note is tried for an hour)."""
    try:
        with closing(_read_only(cfg)) as conn:
            rows = conn.execute(
                "SELECT due_at, call, status, updated_at FROM reminders WHERE status IN ('open', 'firing')"
                " ORDER BY due_at, id"
            ).fetchall()
            marks = ", ".join("?" for _ in FAILED)
            failed = conn.execute(
                f"SELECT COUNT(*) FROM reminders WHERE outcome IN ({marks}) AND updated_at >= ?",
                (*FAILED, iso(now - PROBLEM_DAYS * 86_400)),
            ).fetchone()[0]
    except sqlite3.Error:  # no database yet, or one from before reminders (the bridge adds them at its next start)
        return Finding("ok", "Reminders", prompts.DOCTOR_REMINDERS_NONE)
    first = (float(rows[0][0]), bool(rows[0][1]) and cfg.wake_calls) if rows else None
    stuck = sum(1 for row in rows if row[2] == "firing" and row[3] < iso(now - STUCK_S))
    text = summary(len(rows), first, int(failed), tz=cfg.timezone, now=now)
    if stuck:
        text += prompts.DOCTOR_REMINDERS_STUCK.format(count=stuck)
    if failed or stuck:
        return Finding("warn", "Reminders", text, prompts.DOCTOR_REMINDERS_FIX)
    return Finding("ok", "Reminders", text)


def _initiative(cfg: Config, now: float) -> Finding:
    """The messages she starts (opt-in: the user's /initiative on or off wins over the config): off, or which kinds are
    on (the calendar notes need [calendar]), the quiet hours, /quiet, the last four weeks as counts and the last skip
    per kind (reason words, never text); a warning when one could not be sent in the last 7 days."""
    try:
        store = Store.open_read_only(cfg.db_path)
        try:
            switch = store.get_state(ENABLED_KEY)
        finally:
            store.close()
    except sqlite3.Error:  # no database yet: the config decides
        switch = None
    if switch == "off":
        return Finding("ok", "Initiative", prompts.DOCTOR_INITIATIVE_OFF_BY_USER)
    if switch != "on" and not cfg.initiative_enabled:
        return Finding("ok", "Initiative", prompts.DOCTOR_INITIATIVE_OFF)
    settings = Settings.of(cfg)
    kinds = [
        label
        for on, label in (
            (settings.followups, "check-ins"),
            (settings.calendar, "calendar notes"),
            (settings.lookback, "look back"),
            (settings.improvise, "improvised note"),
        )
        if on
    ]
    on = "on (/initiative on)" if switch == "on" else "on"
    text = f"{on}: {', '.join(kinds) or 'no kind'} · quiet hours {cfg.initiative_quiet_hours or 'none'}"
    if cfg.initiative_calendar and not cfg.calendar_enabled:
        text += " · calendar notes off (no [calendar])"
    try:
        store = Store.open_read_only(cfg.db_path)
        try:
            quiet = bool(store.get_state(QUIET_KEY))
            entries = store.initiative_entries(since=iso(now - 28 * 86_400))
        finally:
            store.close()
    except sqlite3.Error:  # no database yet, or one from before (the bridge adds the log at its next start)
        return Finding("ok", "Initiative", text)
    sent = [e for e in entries if e.kind != "announce" and e.outcome in ("sent", "unknown")]
    answered = sum(1 for entry in sent if entry.replied_at is not None)
    text += f" · last 4 weeks: {len(sent)} sent, {answered} answered within a day"
    if quiet:
        text += " · paused (/quiet)"
    last: dict[str, str] = {}
    for entry in entries:
        if entry.outcome in ("skipped", "failed"):
            last[entry.kind] = entry.reason or entry.outcome
    if last:
        text += " · last skipped: " + ", ".join(f"{prompts.INITIATIVE_KINDS[k]} {why}" for k, why in last.items())
    week_ago = iso(now - PROBLEM_DAYS * 86_400)
    if any(entry.outcome == "failed" and entry.created_at >= week_ago for entry in entries):
        return Finding("warn", "Initiative", text, prompts.DOCTOR_INITIATIVE_FIX)  # one in the last 7 days
    return Finding("ok", "Initiative", text)


def _language(cfg: Config, probe: Probe) -> Finding:
    """The language in use (chosen with /language, else the config's) and whether the voice service has it."""
    language = _current_language(cfg)
    name = prompts.language_name(language)
    if language == ENGLISH:
        return Finding("ok", "Language", name)
    if not cfg.voice_enabled:
        return Finding("ok", "Language", prompts.DOCTOR_TEXT_ONLY.format(name=name))
    try:
        health = probe.get_json(f"{cfg.voice_url.rstrip('/')}/health")
    except (OSError, ValueError):
        return Finding("warn", "Language", prompts.DOCTOR_VOICE_SILENT.format(name=name))
    listed = health.get("languages") if isinstance(health, dict) else None
    if not isinstance(listed, list):
        return Finding(
            "fail", "Language", prompts.DOCTOR_VOICE_TOO_OLD.format(name=name), prompts.DOCTOR_VOICE_REINSTALL
        )
    if language not in listed:
        return Finding("fail", "Language", prompts.DOCTOR_VOICE_LACKS.format(name=name), prompts.DOCTOR_VOICE_REINSTALL)
    return Finding("ok", "Language", name)


def _private_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    except OSError:
        return False


def _service(probe: Probe, url: str) -> bool:
    try:
        probe.get_json(url)
    except (OSError, ValueError):
        return False
    return True


def _changed(path: Path) -> float | None:
    """When the file was last saved; a symlink's own time (uv sync makes a helper's interpreter as one)."""
    try:
        return path.lstat().st_mtime
    except OSError:
        return None


def _fix_saved(cfg: Config, folder: Path, part: str, reason: str) -> float | None:
    """When the setup that fixes this failure was last saved; None for a failure that saving cannot fix (no answer,
    too many requests, ...). A mailbox's own sign-in time: renewing its token (mail does, hourly) is no sign-in, and
    for missing consent only a sign-in that asked for Calendars.Read counts."""
    kind, _, name = part.partition(":")
    mailbox = calendar_mailbox(cfg, part)
    match kind, reason:
        case "mail", "sign_in":
            address = next((account.address for account in cfg.mail_accounts if account.name == name), "")
            return TokenStore(cfg.mail_token_path).signed_in_at(address) if address else None
        case "calendar", "sign_in" | "no_consent" if mailbox:  # an Outlook calendar: its mailbox's sign-in
            address = next((account.address for account in cfg.mail_accounts if account.name == mailbox), "")
            tokens = TokenStore(cfg.mail_token_path)
            return tokens.signed_in_at(address, calendar=reason == "no_consent") if address else None
        case "spotify", "sign_in" | "refused":
            return SpotifyTokens(cfg.spotify_token_path, cfg.spotify_client_id).signed_in_at()
        case "calendar", "not_set" | "gone":
            return _address_saved(cfg, folder / "calendar.env", part)
        case "devices", "login":
            return _changed(folder / "tapo.env")
        case "web" | "places", "no_key" | "refused":
            return _changed(folder / "web.env")
        case "calendar", "helper_missing":
            return _changed(Path(cfg.calendar_helper[0]))
        case "devices", "helper_missing":
            return _changed(Path(cfg.tapo_helper[0]))
    return None


def _address_saved(cfg: Config, path: Path, part: str) -> float | None:
    """When this Google calendar's address was saved: the time scripts/set-calendar-url.sh keeps next to it. Without
    one (saved by hand), the file's own time, but only when no other Google calendar shares the file."""
    google = [source for source in cfg.calendars if not source.mailbox]
    source = next((source for source in google if calendar_part(source.name) == part), None)
    if source is None:
        return None
    saved = read_keys(path, (source.saved_key,)).get(source.saved_key, "")
    if saved.isascii() and saved.isdigit():
        return float(saved)
    return _changed(path) if len(google) == 1 else None


@dataclass(frozen=True)
class _States:
    """The parts' last real use, for the parts whose setup is in place."""

    cfg: Config
    folder: Path  # where the key, login and secret address files are
    records: dict[str, Record]
    now: float

    def finding(self, part: str, detail: str = "") -> Finding:
        record = self.records.get(part)
        saved = _fix_saved(self.cfg, self.folder, part, record.reason) if record is not None else None
        line = describe(
            part, record, self.now, self.cfg.timezone, saved=saved, mailbox=calendar_mailbox(self.cfg, part)
        )
        text = f"{line.text} ({detail})" if detail else line.text
        return Finding(_STATES[line.state], line.label, text, line.fix)


def _optional_parts(cfg: Config, folder: Path, probe: Probe, states: _States) -> list[Finding]:
    found: list[Finding] = []
    if cfg.voice_enabled:
        found.append(
            states.finding("voice")
            if _service(probe, f"{cfg.voice_url.rstrip('/')}/health")
            else Finding("fail", "Voice", f"no answer at {cfg.voice_url}", "scripts/install-voice-daemon.sh")
        )
    if cfg.calls_enabled:
        missing = [path.name for path in (cfg.call_signal_cli, cfg.call_tunnel) if not path.exists()]
        java = cfg.calls_java_home or probe.java_home()
        if java is None or not (java / "bin" / "java").exists():
            missing.append("Java")
        if not missing:
            found.append(Finding("ok", "Calls", "the JVM signal-cli, the call helper and Java are in place"))
        elif missing == ["Java"]:
            found.append(
                Finding(
                    "fail",
                    "Calls",
                    "no Java for the JVM signal-cli",
                    "set [calls] java_home (setup_assistant.md, 3, Calls)",
                )
            )
        else:
            found.append(
                Finding(
                    "fail",
                    "Calls",
                    f"missing: {', '.join(missing)}",
                    "tools/call-tunnel/install-signal-cli-jvm.sh and tools/call-tunnel/build.sh"
                    + ("; set [calls] java_home" if "Java" in missing else ""),
                )
            )
    if cfg.mail_enabled:
        tokens = TokenStore(cfg.mail_token_path)
        waiting = [account.name for account in cfg.mail_accounts if not tokens.refresh_token(account.address)]
        signed_in = len(cfg.mail_accounts) - len(waiting)
        if waiting:
            found.append(
                Finding(
                    "fail",
                    "Mail",
                    f"not signed in: {', '.join(waiting)} ({signed_in} of {len(cfg.mail_accounts)} signed in)",
                    " ; ".join(f"scripts/mail-login.sh <address of {name}>" for name in waiting),
                )
            )
        # a token file is not a working sign-in (it may have been revoked): each mailbox's last real sync
        found += [
            states.finding(mail_part(account.name)) for account in cfg.mail_accounts if account.name not in waiting
        ]
    if cfg.mail_jev_url:
        found.append(
            Finding("ok", "Mail pre-filter (AgentJev)", "AgentJev answers")
            if _service(probe, f"{cfg.mail_jev_url}/health")
            else Finding(
                "warn",
                "Mail pre-filter (AgentJev)",
                f"no answer at {cfg.mail_jev_url}: mail goes to Gemma until it does",
                "start AgentJev (setup_assistant.md, Mail)",
            )
        )
    found += [_calendar(cfg, folder, source, states) for source in cfg.calendars]
    if cfg.tapo_enabled:
        found.append(
            states.finding("devices", f"{len(cfg.tapo_devices)} device(s)")
            if Path(cfg.tapo_helper[0]).exists()
            else Finding("fail", "Lights and plugs", "the device helper is not set up", "cd tools/tapo && uv sync")
        )
    if cfg.web_enabled:
        keys = read_keys(folder / "web.env", ("OLLAMA_API_KEY", "BRAVE_API_KEY"))
        found.append(
            states.finding("web", f"keys: {', '.join(sorted(keys))}")
            if keys
            else Finding("fail", "Web search", "no search key in a private web.env", "scripts/set-web-keys.sh")
        )
    if cfg.maps_enabled:
        has_key = bool(read_keys(folder / "web.env", ("GOOGLE_MAPS_API_KEY",)))
        found.append(
            states.finding("places", "" if cfg.maps_home else "no home address set")
            if has_key
            else Finding(
                "fail", "Places", "no GOOGLE_MAPS_API_KEY in a private web.env", "scripts/setup-google-maps.sh"
            )
        )
    if cfg.spotify_enabled:
        found.append(
            states.finding("spotify")
            if _private_file(cfg.spotify_token_path)
            else Finding("fail", "Spotify", "not signed in yet", "scripts/spotify-login.sh")
        )
    return found


def _calendar(cfg: Config, folder: Path, source: CalendarSource, states: _States) -> Finding:
    """One calendar: a Google one needs the helper and its address in calendar.env; an Outlook one its mailbox's
    sign-in (no helper, no calendar.env). Then what its last real read did."""
    part = calendar_part(source.name)
    label = label_of(part)
    account = cfg.calendar_mailbox(source)
    if account is not None:
        if not TokenStore(cfg.mail_token_path).refresh_token(account.address):
            return Finding(
                "fail",
                label,
                f"mailbox {account.name} is not signed in",
                f"scripts/mail-login.sh <address of {account.name}>",
            )
        return states.finding(part)
    script = "scripts/set-calendar-url.sh" + ("" if part == "calendar" else f" {source.name}")
    if not Path(cfg.calendar_helper[0]).exists():
        return Finding("fail", label, "the calendar helper is not set up", "cd tools/ical && uv sync")
    if not _private_file(folder / "calendar.env"):
        return Finding("fail", label, "no private calendar.env", script)
    if not read_keys(folder / "calendar.env", (source.ical_key,)):  # only whether it is there: never the address
        return Finding("fail", label, f"no address saved in calendar.env ({source.ical_key})", script)
    return states.finding(part)


def _daemon() -> Finding:
    if Path("/Library/LaunchDaemons/com.local-assistant.bridge.plist").exists():
        return Finding("ok", "Always on", "the LaunchDaemon is installed")
    return Finding("warn", "Always on", "not installed yet (try scripts/run.sh first)", "scripts/install-daemon.sh")


def render(findings: list[Finding]) -> str:
    lines: list[str] = []
    for finding in findings:
        lines.append(f"{MARKS[finding.status]} {finding.part}: {finding.text}")
        if finding.fix and finding.status != "ok":
            lines.append(f"    → {finding.fix}")
    return "\n".join(lines)


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(finding.status == "fail" for finding in findings) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that everything is in place to start the assistant (read-only)."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="the config file to check")
    args = parser.parse_args(argv)
    findings = run(args.config, Probe())
    print(render(findings))
    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
