"""`scripts/doctor.sh`: is everything in place to start her (by the config's name, default Estia)? Each line says what
was found and, when something is missing, the command that fixes it.

Read-only: nothing is installed, started or sent; signal-cli is not run (only its account list is read), the
database is only read (the language chosen with /language, the reminders' times and counts), and no secret or
private value is printed (key files: present, private and which keys only; never the bot's number or a reminder's
text).
Key files are looked for next to the config file (for the default config, where the bridge reads them).

Her Signal account's devices, a changed safety number and the model's pause come from status.json too (times and a
count only); the model's pin is compared with what Ollama lists now; retention from the database's state. The projects
folder is read as the user doctor runs as (as her: what her project tools can read, with git's fixed argv). Then the
Mac's own checks (`hostsec`: firewall, SSH passwords, FileVault, her file modes, her Registration Lock, set or not, never
its PIN) and those of her own user (`ownuser`), mapped one to one. Run as `_estia` (`/usr/bin/sudo /usr/local/estia/bin/estia
doctor`), a check that needs root shows ! with the owner's command: doctor never escalates.

"Always on" means alive, not just installed: launchd's state and last exit code for the bridge (`launchctl print`, fixed
argv, read-only) and its last sign of life (`last_alive` in the database, written every minute): running with a sign of
life in the last `ALIVE_SECONDS` is ✓; no sign of life for longer, restarting, stopped or not loaded is ✗ with where to
look (the logs). launchd's run count is never used: deploys, reboots and restarts raise it too.

An optional part whose setup is in place shows what its last real use did, from the bridge's status.json
(part_status): ✓ working, ○ set up but not tested yet, ✗ failing with the fix. Doctor never uses a part itself, so
a failure whose fix was saved since (a sign-in, key, login, secret address or helper) is ○ again until the next use.
One read goes out: with Hue lights, the device helper's `hue-check` asks the paired Hue Bridge, at its own address on
the home network and with the certificate saved at pairing, which lights it has (nothing is switched; the key stays
in the helper).
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import json
import os
import platform
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
from collections.abc import Collection
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from local_assistant import hostsec, ownuser, prompts, share
from local_assistant.config import (
    CODE_ROOT,
    DEFAULT_AVATAR,
    DEFAULT_CONFIG_PATH,
    DEFAULT_NAME,
    DEFAULT_STYLE,
    INTERRUPT_NEW,
    LETTER_AVATARS,
    OWNER_HOME,
    AvatarError,
    CalendarSource,
    Config,
    ConfigError,
    find_java_home,
    is_estia_kb,
    load_config,
    read_avatar,
)
from local_assistant.devices import helper_env
from local_assistant.documents import SHARE_TOKEN
from local_assistant.gemma import is_remote
from local_assistant.hostsec import Check
from local_assistant.initiative import ENABLED_KEY, QUIET_KEY, Evening, Settings, clock_text, evening_of, saved_evening
from local_assistant.keyfile import read_keys
from local_assistant.language import CODES, ENGLISH, LANGUAGE_KEY
from local_assistant.mail_auth import TokenStore
from local_assistant.part_status import (
    Record,
    calendar_mailbox,
    calendar_part,
    describe,
    in_estia_kb,
    label_of,
    mail_part,
    read,
    read_security,
)
from local_assistant.projects import Access, Projects
from local_assistant.reminders import FAILED, PROBLEM_DAYS, STUCK_S, iso, summary
from local_assistant.soul import has_parts, load_soul, soul_part
from local_assistant.spotify_auth import SpotifyTokens
from local_assistant.store import Store
from local_assistant.timefmt import local_when
from local_assistant.voice import detector_state

Status = Literal["ok", "untested", "warn", "fail"]
MARKS: dict[Status, str] = {"ok": "✓", "untested": "○", "warn": "!", "fail": "✗"}
_STATES: dict[str, Status] = {"working": "ok", "untested": "untested", "failing": "fail"}
TOOLS = ("signal-cli", "ollama", "uv")
PLACEHOLDER_NUMBER = "+300000000000"  # config.example.toml's values
PLACEHOLDER_ACI = "00000000-0000-0000-0000-000000000000"
ALIVE_KEY = "last_alive"  # app.ALIVE_KEY: the bridge writes it after signal-cli subscribed, then every 60 s
ALIVE_SECONDS = 180  # a sign of life older than this (three missed beats) means it is not alive
LAUNCHCTL = "/bin/launchctl"
PMSET = "/usr/bin/pmset"  # read only: `pmset -g`
HUE_CHECK_SECONDS = 20  # doctor's limit for the device helper's run (its own 12 s are checked between requests)
_LAUNCHD_STATE = re.compile(r"^\s*state = (.+?)\s*$", re.MULTILINE)  # the first is the service's own
_LAUNCHD_EXIT = re.compile(r"^\s*last exit code = (-?\d+)\b", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    status: Status
    part: str
    text: str
    fix: str = ""


class Probe:
    """The outside world (replaced in tests)."""

    def __init__(self, home: Path | None = None, *, owner_sudo: str | None = None) -> None:
        self.home = home or Path.home()
        self.owner_sudo = owner_sudo  # root's listing of your sudo rules (estia doctor passes it to her doctor)

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

    def security_checks(self, home: Path, bot_number: str) -> list[Check]:
        """The Mac's checks around her (read-only, fixed tools, nothing applied; `home`: hers)."""
        host = hostsec.Host(hostsec.estia_root(), say=lambda _line: None)
        return hostsec.doctor_checks(host, home, bot_number, sudo=self.sudo_check())

    def sudo_check(self) -> Check | None:
        """Passwordless sudo where doctor can read it: from root's listing of your rules (in her own user, estia doctor
        lists them as root and hands them over), else as you without a password (hostsec.owner_sudo). None as her
        without that listing, and under a test's root: sudo is never run there."""
        if self.owner_sudo is not None:
            return hostsec.sudo_listing(self.owner_sudo)
        if hostsec.estia_root() != Path("/") or self.her_user():
            return None
        return hostsec.owner_sudo(hostsec.Host(Path("/"), say=lambda _line: None))

    def install_checks(self) -> list[Check]:
        """Her own user's checks: the user, the root-owned code, the service files, the rollback window."""
        return ownuser.install_checks(hostsec.estia_root())

    def moved(self) -> bool:
        """She runs as her own user here (the bridge's service file says so)."""
        return ownuser.moved(hostsec.estia_root())

    def her_user(self) -> bool:
        """This doctor runs as her (_estia)."""
        return os.geteuid() == (ownuser.lookup_uid(ownuser.ROLE_USER) or -1)

    def projects(self, cfg: Config) -> Access:
        """What this user can read in the projects folder (as her: what her project tools can read)."""
        return asyncio.run(Projects(cfg).access())

    def bridge_launchd(self) -> str | None:
        """`launchctl print` of the bridge's service (read-only, fixed argv, no shell); None when it cannot be asked:
        never under a test's root, never longer than a few seconds."""
        if hostsec.estia_root() != Path("/"):
            return None
        try:
            done = subprocess.run(
                [LAUNCHCTL, "print", f"system/{ownuser.BRIDGE}"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout + done.stderr

    def hue_check(self, cfg: Config, lights: list[str]) -> dict[str, Any] | None:
        """The device helper's read-only check of the paired Hue Bridge (`hue-check`: reachable, the certificate saved
        at pairing, its key, which of these lights it has; nothing switched); None when it was not asked or gave no
        answer: never under a test's root."""
        if hostsec.estia_root() != Path("/"):
            return None
        try:
            done = subprocess.run(
                [*cfg.tapo_helper, "hue-check", *lights],
                capture_output=True,
                text=True,
                timeout=HUE_CHECK_SECONDS,
                check=False,
                env=helper_env(),
            )
            data = json.loads(done.stdout.strip().splitlines()[-1])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return None
        return data if isinstance(data, dict) else None

    def signal_cli_version(self, program: str) -> str | None:
        """`signal-cli --version` of the program the bridge runs (fixed argv, no shell); None when it cannot be asked:
        never under a test's root."""
        found = shutil.which(program)
        if hostsec.estia_root() != Path("/") or found is None:
            return None
        try:
            done = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    def power(self) -> str | None:
        """`pmset -g`: the Mac's sleep and restart settings (read-only, fixed argv, no shell); None when it cannot be
        asked: never under a test's root."""
        if hostsec.estia_root() != Path("/"):
            return None
        try:
            done = subprocess.run([PMSET, "-g"], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def open_home(self, home: Path, shared: Collection[Path]) -> tuple[list[Path], bool]:
        """What directly in your home every account may open, and whether the whole home was read (as her: only the
        usual names, by name). Read-only: nothing changes."""
        return share.open_in_home(home, shared)

    def program_checks(self, cfg: Config) -> list[Check]:
        """What she runs: root's alone (her signal-cli, the call helper's Java and tunnel, the folders on her PATH)."""
        root = hostsec.estia_root()
        return [ownuser.program_check(ownuser.her_programs(cfg, root), root)]


def run(config_path: Path, probe: Probe) -> list[Finding]:
    """Every check, in the order of the setup guide; the optional parts only when they are turned on."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        fix = "" if config_path.exists() else f"cp config.example.toml {config_path} && chmod 600 {config_path}"
        if not config_path.exists() and probe.moved():  # her config is hers: readable only as her user
            fix = prompts.DOCTOR_OWN_USER_CONFIG
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
    models = _models(cfg, probe)  # Ollama's list, read once
    own = probe.moved()
    findings += [
        _model(cfg, models),
        _model_pin(cfg, models, config_path if own else None),
        _signal_number(cfg, probe),
        *([] if (signal_cli := _signal_cli(cfg, probe)) is None else [signal_cli]),
        _picture(cfg),
        _personality(
            config_path.parent, cfg.name, own=own, style=cfg.personality_style, calm=cfg.personality_calm_on_worry
        ),
        _language(cfg, probe),
    ]
    if cfg.reminders_enabled:
        findings.append(_reminders(cfg, probe.now()))
    findings.append(_initiative(cfg, probe.now()))
    findings.append(_evening(cfg))
    findings.append(_history(cfg, probe.now()))
    if cfg.projects_enabled:  # an extra: off without [projects], nothing to read
        findings.append(_projects(cfg, probe, own))
    findings += _signal_security(cfg, probe.now())
    records = read(cfg.data_dir / "status.json")  # what the bridge saw; nothing known without it
    states = _States(cfg, config_path.parent, records, probe.now(), config_path)
    findings += _optional_parts(cfg, config_path.parent, probe, states)
    home = _home_open(cfg, probe, own=own)
    if home is not None:
        findings.append(home)
    findings.append(_daemon(cfg, probe, own))
    power = _power(probe)
    if power is not None:
        findings.append(power)
    findings += _host(probe, cfg)
    return findings


def _home_open(cfg: Config, probe: Probe, *, own: bool) -> Finding | None:
    """In her own user, with Projects or Documents on, her account passes through your home by name: what in it
    lets every account in, she may open too (finding 32; the share scripts name the same). As her, doctor cannot list
    your home: it looks up the usual names and says so. No line before her own user, without a share, or with
    nothing open."""
    if not own:
        return None
    shared = {
        path: Path(match.group(0))
        for on, path in ((cfg.projects_enabled, cfg.projects_root), (cfg.documents_enabled, cfg.documents_folder))
        if on and (match := OWNER_HOME.match(str(path)))
    }
    if not shared:
        return None
    home = next(iter(shared.values()))  # yours: the share opens only folders in your own home
    found, whole = probe.open_home(home, [path for path, its_home in shared.items() if its_home == home])
    if not found:
        return None
    text = share.OPEN_TEXT.format(names=share.open_names(found))
    if not whole:
        text += prompts.DOCTOR_HOME_USUAL_NAMES
    return Finding("warn", "Your home", text, share.OPEN_FIX.format(paths=shlex.join(str(path) for path in found)))


def _power(probe: Probe) -> Finding | None:
    """Whether she keeps running: a Mac that sleeps stops her, and one without autorestart stays off after a power
    cut (setup_assistant.md, 2.8). A laptop has no autorestart: there only sleep counts. Not read: no line."""
    text = probe.power()
    sleep = re.search(r"^\s*sleep\s+(\d+)", text or "", re.MULTILINE)
    restart = re.search(r"^\s*autorestart\s+(\d+)", text or "", re.MULTILINE)
    if sleep is None and restart is None:
        return None
    problems = []
    if sleep is not None and sleep.group(1) != "0":
        problems.append(prompts.DOCTOR_POWER_SLEEPS.format(minutes=sleep.group(1)))
    if restart is not None and restart.group(1) != "1":
        problems.append(prompts.DOCTOR_POWER_NO_RESTART)
    if not problems:
        return Finding("ok", "Power", prompts.DOCTOR_POWER_OK)
    return Finding(
        "warn", "Power", prompts.DOCTOR_POWER_OFF.format(problems="; ".join(problems)), prompts.DOCTOR_POWER_FIX
    )


def _finding(check: Check) -> Finding:
    """A check as doctor shows it: a neutral fact (info) is ○, like a part with nothing to test."""
    return Finding("untested" if check.status == "info" else check.status, check.part, check.text, check.fix)


def _host(probe: Probe, cfg: Config) -> list[Finding]:
    """The Mac's checks and her own user's, one finding each; one that cannot run is a warning, never a crash."""
    found: list[Finding] = []
    try:
        found += [_finding(check) for check in probe.security_checks(probe.home, cfg.bot_number)]
    except (OSError, ValueError) as exc:
        found.append(Finding("warn", "Mac security", prompts.DOCTOR_HOST_FAILED.format(why=type(exc).__name__)))
    try:
        found += [_finding(check) for check in probe.install_checks()]
        if probe.moved():
            found += [_finding(check) for check in probe.program_checks(cfg)]
    except (OSError, ValueError) as exc:
        found.append(Finding("warn", "Own user", prompts.DOCTOR_HOST_FAILED.format(why=type(exc).__name__)))
    return found


def _projects(cfg: Config, probe: Probe, own: bool) -> Finding:
    """The projects folder as the user doctor runs as reads it: ✓ a tracked README opens; ✗ (in her own user, with
    your share command) when nothing can be read; ○ when there is nothing to check."""
    root = cfg.projects_root
    access = probe.projects(cfg)
    share = prompts.DOCTOR_PROJECTS_SHARE_FIX.format(root=root) if own else ""
    closed: Status = "fail" if own else "warn"  # she runs as you: your own permissions keep you out, nothing to share
    if access.state == "missing":
        return Finding(
            "untested", "Projects", prompts.DOCTOR_PROJECTS_MISSING.format(root=root), prompts.DOCTOR_PROJECTS_ROOT_FIX
        )
    if access.state == "closed":
        return Finding(closed, "Projects", prompts.DOCTOR_PROJECTS_CLOSED.format(root=root), share)
    if access.readme is None and access.unreadable:
        text = prompts.DOCTOR_PROJECTS_NONE_READ.format(root=root, closed=access.unreadable)
        return Finding(closed, "Projects", text, share)
    if access.readme is None:
        return Finding("untested", "Projects", prompts.DOCTOR_PROJECTS_EMPTY.format(root=root))
    if access.unreadable:
        text = prompts.DOCTOR_PROJECTS_SOME.format(root=root, closed=access.unreadable)
        return Finding("warn", "Projects", text, share)
    return Finding("ok", "Projects", prompts.DOCTOR_PROJECTS_OK.format(root=root, count=access.projects))


def _signal_security(cfg: Config, now: float) -> list[Finding]:
    """A changed safety number and her own Signal account's devices, as the bridge last saw them (status.json)."""
    security = read_security(cfg.data_dir / "status.json")
    if security.identity_changed is None:
        safety = Finding("ok", prompts.SAFETY_NUMBER_LABEL, prompts.DOCTOR_SAFETY_UNCHANGED)
    else:
        when = local_when(security.identity_changed, now, cfg.timezone)
        text = prompts.SAFETY_NUMBER_CHANGED.format(when=when)
        safety = Finding("fail", prompts.SAFETY_NUMBER_LABEL, text, prompts.SAFETY_NUMBER_FIX)
    count = security.linked_devices
    if count is None:
        devices = Finding("untested", prompts.LINKED_LABEL, prompts.DOCTOR_DEVICES_NOT_COUNTED)
    else:
        when = local_when(security.devices_checked or now, now, cfg.timezone)
        if count <= 1:
            devices = Finding("ok", prompts.LINKED_LABEL, prompts.LINKED_ONE.format(when=when))
        else:
            text = prompts.LINKED_MORE.format(count=count, when=when)
            devices = Finding("fail", prompts.LINKED_LABEL, text, prompts.LINKED_FIX)
    return [safety, devices]


def _model_pin(cfg: Config, models: list[dict[str, Any]] | None, her_config: Path | None = None) -> Finding:
    """The model alias against `model_digest`: pinned and matching, pinned and not (answers paused), or not pinned
    (with the line that pins the digest Ollama lists now). `her_config`: she runs as her own user, so the fix says how
    to edit her config as her and restart her."""
    how = prompts.DOCTOR_OWN_EDIT if her_config is not None else ""
    base = _ollama(cfg)
    wanted = cfg.model if ":" in cfg.model else f"{cfg.model}:latest"
    listed = next((item for item in models or [] if item.get("name") == wanted), None)
    digest = str(listed.get("digest") or "").strip().lower().removeprefix("sha256:") if listed else ""
    if not cfg.model_digest:
        fix = (
            prompts.DOCTOR_PIN_FIX.format(digest=digest, model=cfg.model)
            if digest
            else prompts.DOCTOR_PIN_FIX_UNKNOWN.format(base=base, model=cfg.model)
        )
        return Finding("warn", "Model pin", prompts.DOCTOR_PIN_NONE.format(model=cfg.model), fix + how)
    if not digest:
        return Finding("warn", "Model pin", prompts.DOCTOR_PIN_UNREAD.format(model=cfg.model))
    if digest == cfg.model_digest:
        return Finding("ok", "Model pin", prompts.DOCTOR_PIN_OK.format(model=cfg.model))
    fix = prompts.DOCTOR_PIN_CHANGED_FIX.format(digest=digest) + how
    return Finding("fail", "Model pin", prompts.DOCTOR_PIN_CHANGED.format(model=cfg.model), fix)


def _history(cfg: Config, now: float) -> Finding:
    """[privacy] keep_days: kept forever, or pruned each night (the last time from the database's state)."""
    if cfg.keep_days is None:
        return Finding("ok", "History", prompts.DOCTOR_HISTORY_KEPT)
    try:
        with closing(_read_only(cfg)) as conn:
            row = conn.execute("SELECT value FROM state WHERE key = 'privacy:pruned_at'").fetchone()
    except sqlite3.Error:
        row = None
    try:
        last = float(row[0]) if row is not None else None
    except ValueError:
        last = None
    if last is None:
        return Finding("untested", "History", prompts.DOCTOR_HISTORY_NOT_YET.format(days=cfg.keep_days))
    text = prompts.DOCTOR_HISTORY_PRUNED.format(days=cfg.keep_days, when=local_when(last, now, cfg.timezone))
    if now - last > 3 * 86_400:  # it runs each night: three nights without one means it does not run
        return Finding("warn", "History", text, prompts.DOCTOR_HISTORY_FIX)
    return Finding("ok", "History", text)


def _mac(probe: Probe, name: str) -> Finding:
    system, machine = probe.platform()
    if system == "darwin" and machine == "arm64":
        return Finding("ok", "Mac", "macOS on Apple silicon")
    return Finding(
        "fail", "Mac", f"this is {system} on {machine}", f"{name} needs a Mac with Apple silicon (M1 or later)"
    )


def _tools(probe: Probe) -> Finding:
    if probe.her_user():  # Homebrew's tools are yours and not on her PATH
        return Finding("ok", "Tools", prompts.DOCTOR_TOOLS_HERS)
    missing = [tool for tool in TOOLS if probe.which(tool) is None]
    if not missing:
        return Finding("ok", "Tools", ", ".join(TOOLS))
    return Finding("fail", "Tools", f"missing: {', '.join(missing)}", f"brew install {' '.join(missing)}")


def _ollama(cfg: Config) -> str:
    return cfg.ollama_base_url.removesuffix("/").removesuffix("/v1")


def _models(cfg: Config, probe: Probe) -> list[dict[str, Any]] | None:
    """Ollama's model list (/api/tags); None when it does not answer properly."""
    try:
        listed = probe.get_json(f"{_ollama(cfg)}/api/tags").get("models", [])
    except (OSError, ValueError, AttributeError):
        return None
    return [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else None


def _model(cfg: Config, models: list[dict[str, Any]] | None) -> Finding:
    base = _ollama(cfg)
    if models is None:
        return Finding("fail", "Model", f"Ollama does not answer at {base}", "brew services start ollama")
    wanted = cfg.model if ":" in cfg.model else f"{cfg.model}:latest"
    listed = next((item for item in models if item.get("name") == wanted), None)
    if listed is not None and is_remote(listed):  # the model runs only on this Mac
        return Finding(
            "fail", "Model", prompts.DOCTOR_MODEL_CLOUD.format(model=cfg.model), prompts.model_cloud_fix(cfg.model)
        )
    names = {str(item.get("name", "")) for item in models}
    if cfg.model in names or f"{cfg.model}:latest" in names:
        return Finding("ok", "Model", f"Ollama has {cfg.model}")
    return Finding(
        "fail",
        "Model",
        f"Ollama runs but has no model called {cfg.model}",
        f"ollama pull {cfg.model}" if cfg.model != "local-worker" else f"run {prompts.SETUP_MODEL_FIX}",
    )


def _signal_number(cfg: Config, probe: Probe) -> Finding:
    """The bot's number in signal-cli's list, and its own account file not saying the registration is unfinished:
    signal-cli lists a number as soon as register starts, before the SMS code (an unreadable file changes nothing)."""
    signal_data = probe.home / ".local/share/signal-cli"
    try:
        entry = next(iter(hostsec.signal_listed(signal_data, cfg.bot_number)), None) if cfg.bot_number else None
    except (OSError, ValueError):
        entry = None
    if entry is None:
        return Finding(
            "fail",
            "Signal number",
            "signal-cli has no account for bot_number",
            "register the bot's number: setup_assistant.md, 2.3 (signal-cli ... register, then verify)",
        )
    account = hostsec.account_file(signal_data, entry)  # by signal-cli's own name only: never a path
    if account is not None and account.get("registered") is False:
        return Finding("fail", "Signal number", prompts.DOCTOR_SIGNAL_UNFINISHED, prompts.DOCTOR_SIGNAL_UNFINISHED_FIX)
    return Finding("ok", "Signal number", "the bot's number is registered with signal-cli")


def _signal_cli(cfg: Config, probe: Probe) -> Finding | None:
    """The version of the signal-cli the bridge runs: written for 0.14.x (its register, verify and receive requests);
    not read, no line."""
    text = probe.signal_cli_version(cfg.signal_cli)
    if not text:
        return None
    version = text.split()[-1]
    if version.startswith("0.14."):
        return Finding("ok", "signal-cli", text)
    return Finding(
        "warn", "signal-cli", prompts.DOCTOR_SIGNAL_CLI_OTHER.format(version=version), prompts.DOCTOR_SIGNAL_CLI_FIX
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
    if path.parent == LETTER_AVATARS:  # her name's first letter (owner, 15 Oct)
        return Finding("ok", "Profile picture", prompts.DOCTOR_PICTURE_LETTER.format(letter=path.stem))
    return Finding("ok", "Profile picture", prompts.DOCTOR_PICTURE_OWN.format(file=path.name))


def _personality(
    folder: Path, name: str, *, own: bool = False, style: str = DEFAULT_STYLE, calm: bool = True
) -> Finding:
    path = folder / "soul.md"
    if not path.exists():  # the default: her built-in style, hearth or playful ([personality] style)
        return Finding("ok", "Personality", f"no soul.md: {name}'s built-in style, {style} (soul.example.md: your own)")
    soul = load_soul(path)
    if soul.problem:
        chmod = f"{hostsec.SUDO} -u {ownuser.ROLE_USER} {hostsec.CHMOD} 600 {path}" if own else f"chmod 600 {path}"
        return Finding("warn", "Personality", f"soul.md is not used: {soul.problem}", f"{chmod}, then check it")
    text = "soul.md"
    if has_parts(soul.text):  # what texts and calls each get (a kind with nothing keeps the built-in style)
        texts, calls = len(soul_part(soul.text, call=False)), len(soul_part(soul.text, call=True))
        template = (
            prompts.DOCTOR_SOUL_PARTS
            if texts and calls
            else prompts.DOCTOR_SOUL_NO_TEXTS
            if calls
            else prompts.DOCTOR_SOUL_NO_CALLS
            if texts
            else prompts.DOCTOR_SOUL_NOTHING
        )
        text = template.format(texts=f"{texts:,}", calls=f"{calls:,}", style=style)
    if not calm:  # the user's choice: said, nothing to fix
        text += prompts.DOCTOR_SOUL_CALM_OFF
    return Finding("ok", "Personality", text)


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


def _evening(cfg: Config) -> Finding:
    """The evening note (owner, D4; it needs no /initiative on): off, or on at its time and by whose switch, the user's
    /evening (which wins) or the config's time for the one allowed account; a warning when the config's time reaches
    no one (several accounts, none typed /evening on), which the bridge only logs at its start. Never an account."""
    acis = tuple(sorted(cfg.allowed_acis))
    configured = Settings.of(cfg).evening  # minutes, None when left out
    try:
        store = Store.open_read_only(cfg.db_path)
        try:
            saved, held = saved_evening(store), evening_of(store, configured, acis)
        finally:
            store.close()
    except sqlite3.Error:  # no database yet: the config decides
        saved = None
        held = Evening(acis[0], configured) if configured is not None and len(acis) == 1 else None
    if held is not None:
        template = prompts.DOCTOR_EVENING_TYPED if saved is not None else prompts.DOCTOR_EVENING_CONFIG
        return Finding("ok", "Evening note", template.format(time=held.time))
    if saved is not None:  # typed /evening off, or on by an account no longer allowed
        gone = saved.get("on") is True
        return Finding(
            "ok", "Evening note", prompts.DOCTOR_EVENING_GONE if gone else prompts.DOCTOR_EVENING_OFF_BY_USER
        )
    if configured is not None:
        unused = prompts.DOCTOR_EVENING_UNUSED.format(time=clock_text(configured))
        return Finding("warn", "Evening note", unused, prompts.DOCTOR_EVENING_UNUSED_FIX)
    return Finding("ok", "Evening note", prompts.DOCTOR_EVENING_OFF)


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
            entries = [  # the evening note is never counted (owner, D4)
                entry for entry in store.initiative_entries(since=iso(now - 28 * 86_400)) if entry.kind != "evening"
            ]
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


def _interrupt_wording(cfg: Config) -> list[Finding]:
    """[calls] in an older wording of what stops her: still read, with the new wording to write (and an old barge
    beside interrupt, which is not read)."""
    new = f'interrupt = "{INTERRUPT_NEW[cfg.calls_barge]}"'
    found: list[Finding] = []
    if cfg.calls_interrupt_old:
        text = prompts.DOCTOR_INTERRUPT_OLD.format(old=cfg.calls_interrupt_old)
        found.append(Finding("warn", "Calls setting", text, prompts.DOCTOR_INTERRUPT_OLD_FIX.format(new=new)))
    if cfg.calls_barge_unread:
        text = prompts.DOCTOR_BARGE_UNREAD.format(barge=cfg.calls_barge_unread, new=new)
        found.append(Finding("warn", "Calls setting", text, prompts.DOCTOR_BARGE_UNREAD_FIX))
    return found


def _speech_detector(cfg: Config, probe: Probe) -> Finding:
    """With [calls] interrupt = "only speech" (the default): whether the voice service's speech detector is ready (else
    any sound stops her on calls meanwhile)."""
    try:
        health = probe.get_json(f"{cfg.voice_url.rstrip('/')}/health")
    except (OSError, ValueError):
        health = None
    state = detector_state(health if isinstance(health, dict) else None)
    if state == "ready":
        return Finding("ok", "Speech detector", "ready")
    log = f"{ownuser.ESTIA} logs voice" if probe.moved() else prompts.DOCTOR_VOICE_LOG  # her output: /var/log/estia
    text, fix = {
        "old voice service": (prompts.DOCTOR_DETECTOR_OLD, prompts.DOCTOR_VOICE_REINSTALL),
        "loading": (prompts.DOCTOR_DETECTOR_LOADING, prompts.DOCTOR_DETECTOR_LOADING_FIX.format(log=log)),
        "unavailable": (prompts.DOCTOR_DETECTOR_UNAVAILABLE, prompts.DOCTOR_DETECTOR_UNAVAILABLE_FIX.format(log=log)),
    }.get(state, (prompts.DOCTOR_DETECTOR_SILENT, prompts.DOCTOR_VOICE_REINSTALL))
    return Finding("warn", "Speech detector", text, fix)


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


def _fix_saved(cfg: Config, folder: Path, part: str, reason: str, config: Path | None = None) -> float | None:
    """When the setup that fixes this failure was last saved; None for a failure that saving cannot fix (no answer,
    too many requests, ...). A mailbox's own sign-in time: renewing its token (mail does, hourly) is no sign-in, and
    for missing consent only a sign-in that asked for Calendars.Read counts. A CalDAV calendar's password: the time
    scripts/set-calendar-password.sh saved it; its calendar's name, and a Hue light's id: the config's own time
    (`config`)."""
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
        case "calendar", "not_set" | "gone" | "no_password" | "password_refused":
            return _address_saved(cfg, folder / "calendar.env", part)
        case "calendar", "no_calendar":
            return _changed(config) if config is not None else None
        case "devices", "no_light":  # the light's id corrected in [[tapo.devices]]
            return _changed(config) if config is not None else None
        case "devices", "login":
            return _changed(folder / "tapo.env")
        case "devices", "hue_key" | "certificate" | "not_paired":  # scripts/hue-pair.sh
            return _changed(folder / "hue.env")
        case "web" | "places", "no_key" | "refused":
            return _changed(folder / "web.env")
        case "calendar", "helper_missing":
            return _changed(Path(cfg.calendar_helper[0]))
        case "devices", "helper_missing":
            return _changed(Path(cfg.tapo_helper[0]))
        case "youtube", "helper_missing":
            return _changed(Path(cfg.youtube_helper[0]))
        case "documents", "helper_missing":
            return _changed(Path(cfg.documents_helper[0]))
    return None


def _address_saved(cfg: Config, path: Path, part: str) -> float | None:
    """When this Google calendar's address or CalDAV calendar's password was saved: the time
    scripts/set-calendar-url.sh or set-calendar-password.sh keeps next to it. Without one (saved by hand), the file's
    own time, but only when no other calendar keeps its secret in the file."""
    shared = [source for source in cfg.calendars if source.secret_key]
    source = next((source for source in shared if calendar_part(source.name) == part), None)
    if source is None:
        return None
    saved = read_keys(path, (source.saved_key,)).get(source.saved_key, "")
    if saved.isascii() and saved.isdigit():
        return float(saved)
    return _changed(path) if len(shared) == 1 else None


@dataclass(frozen=True)
class _States:
    """The parts' last real use, for the parts whose setup is in place."""

    cfg: Config
    folder: Path  # where the key, login and secret address files are
    records: dict[str, Record]
    now: float
    config: Path | None = None  # the config file (its time: a calendar's name corrected there)

    def finding(self, part: str, detail: str = "") -> Finding:
        record = self.records.get(part)
        saved = _fix_saved(self.cfg, self.folder, part, record.reason, self.config) if record is not None else None
        line = describe(
            part,
            record,
            self.now,
            self.cfg.timezone,
            saved=saved,
            mailbox=calendar_mailbox(self.cfg, part),
            estia_kb=in_estia_kb(self.cfg, part),
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
        java = cfg.java_home or probe.java_home()
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
        if cfg.calls_barge == "speech":
            found.append(_speech_detector(cfg, probe))
        found += _interrupt_wording(cfg)
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
        if any(spec.hue for spec in cfg.tapo_devices):
            found.append(_hue(cfg, folder, probe))
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
    found.append(_youtube(cfg, states, own=probe.moved()))  # always on, with read_link
    if cfg.documents_enabled:
        found.append(_documents(cfg, states, own=probe.moved()))
    return found


def _documents(cfg: Config, states: _States, *, own: bool) -> Finding:
    """The documents helper in place and the folder as the user doctor runs as reads it (as her: listed, and the
    share's token file read), then what her last look did. Read-only: the helper is not run, the folder not changed."""
    helper = Path(cfg.documents_helper[0])
    if not os.access(helper, os.X_OK):  # in her own user a deploy builds it in the release (deploy.DOCUMENTS_BINARY)
        if own and _release_without_documents():  # the deploy's build failed: the release went live without it
            fix = prompts.DOCTOR_DOCUMENTS_REBUILD_FIX.format(deploy=ownuser.DEPLOY_CMD)
            return Finding("fail", "Documents", prompts.DOCTOR_DOCUMENTS_NOT_BUILT, fix)
        fix = (
            prompts.DOCTOR_YOUTUBE_DEPLOY_FIX.format(deploy=ownuser.DEPLOY_CMD)
            if own
            else prompts.DOCTOR_DOCUMENTS_BUILD_FIX
        )
        return Finding("fail", "Documents", prompts.DOCTOR_DOCUMENTS_NO_HELPER, fix)
    folder = cfg.documents_folder
    if own and folder.is_relative_to(ownuser.HOME):  # ~ in her config is her home: the folder is always yours
        return Finding("fail", "Documents", prompts.DOCTOR_DOCUMENTS_HERS, prompts.DOCTOR_DOCUMENTS_HERS_FIX)
    if own and not is_estia_kb(folder):  # the share opens only ~/Estia-KB: another folder stays closed to her
        fix = prompts.DOCTOR_DOCUMENTS_HERS_FIX
        if _guarded(folder):
            fix = f"{fix}. {prompts.DOCTOR_DOCUMENTS_WHY_HOME}"
        return Finding("fail", "Documents", prompts.DOCTOR_DOCUMENTS_NOT_KB.format(folder=folder), fix)
    refused = Finding(
        "fail",
        "Documents",
        prompts.DOCTOR_DOCUMENTS_REFUSED.format(folder=folder),
        prompts.DOCTOR_DOCUMENTS_REFUSED_KB_FIX if is_estia_kb(folder) else prompts.DOCTOR_DOCUMENTS_REFUSED_FIX,
    )
    try:
        listed = stat.S_ISDIR(os.lstat(folder).st_mode)
    except FileNotFoundError:  # before her own user the bridge runs as you: the folder only, no share
        fix = (
            prompts.DOCTOR_DOCUMENTS_MAKE_FIX
            if own
            else prompts.DOCTOR_DOCUMENTS_MKDIR_FIX.format(folder=shlex.quote(str(folder)))
        )
        return Finding("fail", "Documents", prompts.DOCTOR_DOCUMENTS_MISSING.format(folder=folder), fix)
    except PermissionError as exc:
        if exc.errno == errno.EPERM:  # macOS itself refuses (not the ACL): the share would change nothing
            return refused
        listed = False
    except OSError:
        listed = False
    if listed:
        try:
            with os.scandir(folder):
                pass
        except PermissionError as exc:
            if exc.errno == errno.EPERM:
                return refused
            listed = False
        except OSError:
            listed = False
    if not listed:
        text = prompts.DOCTOR_DOCUMENTS_CLOSED.format(folder=folder)
        return Finding("fail" if own else "warn", "Documents", text, prompts.DOCTOR_DOCUMENTS_SHARE_FIX)
    if own:  # in her own user the share's token must be readable: the test file
        try:
            token = (folder / SHARE_TOKEN).read_text(encoding="ascii", errors="replace").strip()
        except OSError:
            token = ""
        if not token:
            text = prompts.DOCTOR_DOCUMENTS_UNSHARED.format(folder=folder)
            return Finding("fail", "Documents", text, prompts.DOCTOR_DOCUMENTS_SHARE_FIX)
    return states.finding("documents", prompts.DOCTOR_DOCUMENTS_OK.format(folder=folder))


def _release_without_documents() -> bool:
    """Whether the running release says its documents build failed (release.json `documents`: false; estia-deploy
    goes live without the helper, an extra). False when it says nothing (a checkout, an older release)."""
    try:
        meta = json.loads((CODE_ROOT / "release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("documents") is False


_GUARDED = frozenset({"documents", "desktop", "downloads"})  # macOS guards them (TCC); a Mac's usual disk ignores case


def _guarded(folder: Path) -> bool:
    """Whether `folder` is in a home's Documents, Desktop or Downloads."""
    text = str(folder)
    home = OWNER_HOME.match(text)
    below = Path(text[home.end() :]).parts if home else ()  # ("/", "Documents", ...)
    return len(below) > 1 and below[1].casefold() in _GUARDED


HUE_LABEL = "Hue Bridge"
_HUE_PROBLEMS = {
    "unreachable": (
        "no answer at the address saved at pairing",
        "check that the bridge is plugged in; a new address: scripts/hue-pair.sh <address>",
    ),
    "certificate": (
        "its certificate is not the one saved at pairing: nothing was sent to it",
        "if the bridge was replaced or reset: scripts/hue-pair.sh",
    ),
    "key": ("the bridge refused its key", "scripts/hue-pair.sh"),
    "not_paired": ("not paired yet", "scripts/hue-pair.sh"),
}
_HUE_UNREAD = ("the bridge's answer could not be read", "run doctor again later")
_HUE_FOUND = "reachable, its certificate the one saved at pairing"


def _hue(cfg: Config, folder: Path, probe: Probe) -> Finding:
    """With Hue lights: the bridge paired (a private hue.env, read here only for its mode), then the device helper's
    read-only check: reachable, the certificate saved at pairing, its key, each configured Hue light on it, and which
    of them the bridge cannot reach (switched off at the wall: a warning, the bridge is fine)."""
    lights = [spec for spec in cfg.tapo_devices if spec.hue]
    if not _private_file(folder / "hue.env"):
        return Finding("fail", HUE_LABEL, *_HUE_PROBLEMS["not_paired"])
    answer = probe.hue_check(cfg, [spec.light for spec in lights])
    if answer is None:
        return Finding("untested", HUE_LABEL, "paired, not checked: the device helper did not answer")
    if not answer.get("ok"):
        return Finding("fail", HUE_LABEL, *_HUE_PROBLEMS.get(str(answer.get("error")), _HUE_UNREAD))
    found = answer.get("found")
    found = found if isinstance(found, list) else []
    missing = [spec.name for spec in lights if spec.light not in found]
    if missing:
        return Finding(
            "fail",
            HUE_LABEL,
            f"{_HUE_FOUND}; not on the bridge: {', '.join(missing)}",
            "scripts/tapo-discover.sh lists the bridge's lights; fix the light id in [[tapo.devices]]",
        )
    disconnected = answer.get("disconnected")
    silent = [spec.name for spec in lights if isinstance(disconnected, list) and spec.light in disconnected]
    if silent:
        return Finding(
            "warn",
            HUE_LABEL,
            f"{_HUE_FOUND}; not answering the bridge: {', '.join(silent)}",
            "if it was switched off at the wall, switch it on there",
        )
    return Finding("ok", HUE_LABEL, f"{_HUE_FOUND}, {len(lights)} of {len(lights)} lights found")


def _youtube(cfg: Config, states: _States, *, own: bool = False) -> Finding:
    """The YouTube helper in place, with the library's version from its own files (nothing is run, no network), then
    what its last real read did. Missing is a warning: only video links are affected. In her own user a deploy builds
    it in the release (`own`), else the uv line."""
    python = Path(cfg.youtube_helper[0])
    if not python.exists():
        return Finding(
            "warn",
            "YouTube",
            "the YouTube helper is not set up: video links cannot be read",
            prompts.DOCTOR_YOUTUBE_DEPLOY_FIX.format(deploy=ownuser.DEPLOY_CMD)
            if own
            else "cd tools/youtube && uv sync --frozen",
        )
    found = sorted(python.parents[1].glob("lib/python*/site-packages/youtube_transcript_api-*.dist-info"))
    version = found[-1].name.removeprefix("youtube_transcript_api-").removesuffix(".dist-info") if found else ""
    return states.finding("youtube", f"youtube-transcript-api {version}" if version else "")


def _calendar(cfg: Config, folder: Path, source: CalendarSource, states: _States) -> Finding:
    """One calendar: a Google one needs the helper and its address in calendar.env, a CalDAV one the helper and its
    app-specific password there (its line names the service); an Outlook one its mailbox's sign-in (no helper, no
    calendar.env). Then what its last real read did."""
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
    caldav = source.kind == "caldav"
    script = ("scripts/set-calendar-password.sh" if caldav else "scripts/set-calendar-url.sh") + (
        "" if part == "calendar" else f" {source.name}"
    )
    if not Path(cfg.calendar_helper[0]).exists():
        return Finding("fail", label, "the calendar helper is not set up", "cd tools/ical && uv sync")
    if not _private_file(folder / "calendar.env"):
        return Finding("fail", label, "no private calendar.env", script)
    if not read_keys(folder / "calendar.env", (source.secret_key,)):  # only whether it is there: never the secret
        saved = "app-specific password" if caldav else "address"
        return Finding("fail", label, f"no {saved} saved in calendar.env ({source.secret_key})", script)
    return states.finding(part, source.service if caldav else "")


def _last_alive(cfg: Config) -> float | None:
    """The bridge's last sign of life in the database, only read; None when none is on record (or no database yet:
    after the move, doctor runs only as her, who can read hers)."""
    try:
        with closing(_read_only(cfg)) as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (ALIVE_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    try:
        return float(row[0]) if row is not None else None
    except (TypeError, ValueError):
        return None


def _daemon(cfg: Config, probe: Probe, own: bool) -> Finding:
    """Installed, and alive: launchd's state and last exit code, and the bridge's last sign of life."""
    plist = ownuser.DAEMONS / f"{ownuser.BRIDGE}.plist"
    if not (hostsec.estia_root() / plist.relative_to("/")).exists():  # a test: its own root
        return Finding("warn", "Always on", prompts.DOCTOR_ALWAYS_ON_MISSING, "scripts/install-daemon.sh")
    logs = f"{ownuser.ESTIA} logs launchd" if own else f"tail -n 50 {cfg.log_dir / 'launchd.log'}"
    bridge_log = f"{ownuser.ESTIA} logs bridge" if own else f"tail -n 50 {cfg.log_dir / 'bridge.log'}"
    now = probe.now()
    alive = _last_alive(cfg)
    fresh = alive is not None and now - alive <= ALIVE_SECONDS
    launchd = probe.bridge_launchd()
    state = _LAUNCHD_STATE.search(launchd or "")
    if state is None and "could not find service" not in (launchd or "").lower():
        launchd = None  # an answer without a state that is not "not loaded": launchd was not read
    found_exit = _LAUNCHD_EXIT.search(launchd or "")
    code = int(found_exit.group(1)) if found_exit else None

    def silent() -> str:
        assert alive is not None
        minutes = max(0, int((now - alive) // 60))
        if minutes < 120:
            text = prompts.DOCTOR_ALWAYS_ON_SILENT.format(minutes=minutes)
        else:
            text = prompts.DOCTOR_ALWAYS_ON_SILENT_SINCE.format(when=local_when(alive, now, cfg.timezone))
        return text + (prompts.DOCTOR_ALWAYS_ON_EXIT.format(code=code) if code else "")

    if launchd is None:  # launchd could not be asked: the sign of life alone
        if alive is None:  # installed is not alive: no ✓ (review of 14 Oct)
            return Finding("untested", "Always on", prompts.DOCTOR_ALWAYS_ON_UNREAD, bridge_log)
        if fresh:
            when = local_when(alive, now, cfg.timezone)
            return Finding("ok", "Always on", prompts.DOCTOR_ALWAYS_ON_ALIVE_ONLY.format(when=when))
        return Finding("fail", "Always on", silent(), bridge_log)
    if state is None:
        fix = f"{hostsec.SUDO} {LAUNCHCTL} bootstrap system {plist}"
        return Finding("fail", "Always on", prompts.DOCTOR_ALWAYS_ON_NOT_LOADED, fix)
    if state.group(1) != "running":
        if code:  # launchd starts it again after a failure (KeepAlive): restarting
            return Finding("fail", "Always on", prompts.DOCTOR_ALWAYS_ON_RESTARTING.format(code=code), logs)
        text = prompts.DOCTOR_ALWAYS_ON_STOPPED_NEVER if code is None else prompts.DOCTOR_ALWAYS_ON_STOPPED
        restart = (
            f"{ownuser.ESTIA} restart" if own else f"{hostsec.SUDO} {LAUNCHCTL} kickstart -k system/{ownuser.BRIDGE}"
        )
        return Finding("fail", "Always on", text.format(code=code), restart)
    if alive is None:
        return Finding("warn", "Always on", prompts.DOCTOR_ALWAYS_ON_NONE_YET, bridge_log)
    if not fresh:
        return Finding("fail", "Always on", silent(), bridge_log)
    return Finding("ok", "Always on", prompts.DOCTOR_ALWAYS_ON_ALIVE.format(when=local_when(alive, now, cfg.timezone)))


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
    # root's `sudo -l -U <owner>` on stdin (estia doctor): told by the environment, which a doctor from before ignores
    listing = sys.stdin.read(200_000) if os.environ.get("ESTIA_OWNER_SUDO") == "stdin" else None
    findings = run(args.config, Probe(owner_sudo=listing))
    print(render(findings))
    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
