"""Her own user: the layout, the role account, the launchd values, doctor's install checks, and the move into it.

Estia runs as `_estia`, a hidden role account with its own group and no login. Her data lives in its home
`/var/estia` (0700, readable only by it); her code is a root-owned copy under `/usr/local/estia` (releases/, current,
bin/, tools/) made by `estia-deploy`. The owner's checkout is a source, never run by the daemons, and root runs only
the tools in /usr/local/estia/tools (see deploy).

Every system path is taken under ESTIA_ROOT (default "/"; ignored for root, see hostsec.estia_root), and every
privileged step is a fixed argv through a `hostsec.Host`, so tests and `--rehearse` work in a temporary tree, create
no user and start nothing.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import plistlib
import pwd
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from local_assistant.hostsec import (
    CHMOD,
    CHOWN,
    DSCL,
    DSEDITGROUP,
    LAUNCHCTL,
    LINK_OUT,
    RM,
    SUDO,
    SYSADMINCTL,
    TAR,
    VENDOR,
    Check,
    Host,
    clean,
    estia_root,
    signal_account,
    strays,
    tail,
)

__all__ = ["LAUNCHCTL", "Check", "Host"]

ROLE_USER = "_estia"
ROLE_GROUP = "_estia"
FULL_NAME = "Estia"
HOME = Path("/var/estia")
CODE = Path("/usr/local/estia")
RELEASES = CODE / "releases"
CURRENT = CODE / "current"
BIN = CODE / "bin"
TOOLS = CODE / "tools"  # what root runs (estia, estia-deploy, deploy): changed only with estia-deploy --update-tools
MARKER = CODE / "migration.json"  # the move: who, when, where her old folders are held (root-owned, 0644, no secrets)
SAVED = CODE / "rollback"  # the old service files, kept for the rollback
HELD = SAVED / "data"  # her old folders from your home, moved here (0700, root only) until the rollback or remove-old
DAEMONS = Path("/Library/LaunchDaemons")
# her services' output (stdout and stderr): launchd opens these files as root, so they are in a folder only root can
# change (root's, 0755), never one of hers; each file is hers (0600), emptied in-process (app.cap_streams, LogCap)
STREAM_LOGS = Path("/var/log/estia")
NEWSYSLOG = Path("/etc/newsyslog.d/local-assistant.conf")  # the old log rotation rule (logs now rotate themselves)
BRIDGE = "com.local-assistant.bridge"
VOICE = "com.local-assistant.voice"
LABELS = (BRIDGE, VOICE)
STREAM_LOG = {BRIDGE: "launchd.log", VOICE: "voice.log"}  # each service's output file in STREAM_LOGS
# her services' output in her own folder (an install from before STREAM_LOGS): launchd opens it as root, so a link
# of hers there leads root's writes anywhere; a ✗ until the tools' update and one more deploy move it
LOGS_IN_HER_FOLDER = (
    "its output is in her own folder, which launchd opens as root: "
    "a program of her account could make root write into a file it names"
)
ROLLBACK_DAYS = 7
# her group's number (dseditgroup -i): the highest one in 450-499 no user and no group has. sysadminctl is given it as
# -UID too, but ignores -UID (and -home) for a role account on macOS 27: her account's own number is the one it picks
ROLE_IDS = range(499, 449, -1)
CODE_UID = 0  # who owns the code: root
NO_SHELL = "/usr/bin/false"
DSCL_NATIVE = "dsAttrTypeNative:"  # dscl -read's prefix for a native attribute: "dsAttrTypeNative:IsHidden: 1"
# her folders, the same place under the owner's home (old) and hers (new): config and keys, data, Signal, logs, and
# the voice service's caches (its Neural Engine compile cache, about 15 minutes saved; FluidAudio's speech models)
DATA_MAP: tuple[tuple[str, str], ...] = tuple(
    (path, path)
    for path in (
        ".config/local-assistant",
        "Library/Application Support/local-assistant",
        ".local/share/signal-cli",
        "Library/Logs/local-assistant",
        "Library/Caches/voice-service",
        ".cache/fluidaudio",
    )
)
CACHES = frozenset({"Library/Caches/voice-service", ".cache/fluidaudio"})  # copied, but no secret: left in your home
# what the rollback brings back from her home on top of the old folders: her keys, messages and Signal sessions
OVERLAY = (".config/local-assistant", "Library/Application Support/local-assistant", ".local/share/signal-cli")
DATABASES = frozenset({"account.db", "assistant.db"})  # signal-cli's and hers: SQLite in WAL mode
JOURNALS = ("-wal", "-shm")  # SQLite replays a -wal into whatever database sits next to it
PROGRAM = "a program (an execute bit)"  # in her data: never copied back into your folders
HER_CONFIG = ".config/local-assistant/config.toml"
AVATAR_MAX_BYTES = 5_000_000  # config.AVATAR_MAX_BYTES: a larger picture is never sent
AVATAR_TYPES = (".png", ".jpg", ".jpeg")
AVATAR_SECONDS = 30.0  # the picture's copy: a reader that blocks (a FIFO swapped in) is stopped, the picture skipped
HEAD = "/usr/bin/head"
REALPATH = "/bin/realpath"
TEE = "/usr/bin/tee"
STAT = "/usr/bin/stat"
CAT = "/bin/cat"
MKDIR = "/bin/mkdir"
MV = "/bin/mv"
MIGRATE = "scripts/migrate-to-own-user.sh"
SHARE = "scripts/share-projects.sh"  # her read-only access to your projects folder: as you, no sudo
ESTIA = f"{SUDO} {BIN}/estia"  # always /usr/bin/sudo: a sudo planted earlier on your PATH would catch the password
VOICE_HEALTH_URL = "http://127.0.0.1:8150/health"
# the end of a move, a setup or a deploy while her voice still loads (the Neural Engine compiles it again): with
# [calls] the bridge texts once it is ready (voice_ready.ReadyNote); without them no text follows
VOICE_LOADING = "Calls work once her voice is ready (up to about 15 minutes); she texts you then."
VOICE_LOADING_NO_CALLS = "voice: loading (up to about 15 minutes)"
# the bridge texts only after a long load: a read still not ready 120 s after its start (voice_ready.LONG_LOAD_SECONDS),
# its reads 15 s apart (POLL_SECONDS). Still loading this long after the bridge was seen up, it surely counts one.
VOICE_WAIT_SECONDS = 120.0 + 2 * 15.0
VOICE_POLL_SECONDS = 5.0
VOICE_WAITING = f"voice: loading (checking again for up to {VOICE_WAIT_SECONDS:.0f} s)"
VOICE_STOPPED = "voice: not checked to the end (stopped); curl -s http://127.0.0.1:8150/health shows it"
DEPLOY_CMD = f"{SUDO} {BIN}/estia-deploy"
HER_PATH = f"{CURRENT}/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"  # no folder you can change
_PATH_ALLOWED = frozenset(HER_PATH.split(":"))
_PLIST_KEYS = frozenset(
    {
        "Label",
        "UserName",
        "GroupName",
        "ProgramArguments",
        "EnvironmentVariables",
        "WorkingDirectory",
        "Umask",
        "RunAtLoad",
        "KeepAlive",
        "ThrottleInterval",
        "StandardOutPath",
        "StandardErrorPath",
    }
)
# CFFIXED_USER_HOME: Foundation's home (Swift, the voice service), since macOS keeps /var/empty in her account record
_ENV_KEYS = frozenset({"HOME", "CFFIXED_USER_HOME", "PATH", "LANG", "PYTHONUNBUFFERED"})
_PLACEHOLDER = re.compile(r"@([A-Z_]+)@")


def lookup_uid(name: str) -> int | None:
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def _say(text: str) -> None:
    """Print, even when the terminal is gone (a dropped SSH session): the restore must go on."""
    with contextlib.suppress(OSError):
        print(text, flush=True)


# ------------------------------------------------------------------ the role account


def ids_in(listing: str) -> set[int]:
    """The numbers in `dscl . -list /Users UniqueID` (or /Groups PrimaryGroupID) output."""
    found: set[int] = set()
    for line in listing.splitlines():
        words = line.split()
        if len(words) >= 2 and words[-1].lstrip("-").isdigit():
            found.add(int(words[-1]))
    return found


def free_id(uids: set[int], gids: set[int], candidates: range = ROLE_IDS) -> int:
    """The highest number in the role-account range that no user and no group has (her group's number)."""
    for number in candidates:
        if number not in uids and number not in gids:
            return number
    raise RuntimeError("no free number in 450-499 for her group (no user and no group may have it)")


def account_plan(number: int, *, group: bool = True) -> list[tuple[str, ...]]:
    """The commands that make `_estia`: its own group (unless `group` is False: an earlier run made it), then a hidden
    role account with no shell and the home /var/estia (the folder itself is made by the migration)."""
    return [
        *([(DSEDITGROUP, "-o", "create", "-i", str(number), "-r", FULL_NAME, ROLE_GROUP)] if group else []),
        (
            SYSADMINCTL,
            "-addUser",
            ROLE_USER,
            "-fullName",
            FULL_NAME,
            "-UID",
            str(number),
            "-GID",
            str(number),
            "-shell",
            NO_SHELL,
            "-home",
            str(HOME),
            "-roleAccount",
        ),
    ]


def account_settings() -> list[tuple[str, ...]]:
    """Run every time (they hold when run again): her home, no shell, hidden, and no password at all: the legacy
    Password "*"
    (no password matches it) with every other way to authenticate deleted (a shadow hash sysadminctl may have made,
    and the authentication authority that points to it). `-delete` of an attribute that is not there fails: that
    failure is expected."""
    user = f"/Users/{ROLE_USER}"
    return [
        (DSCL, ".", "-create", user, "UserShell", NO_SHELL),
        (DSCL, ".", "-create", user, "IsHidden", "1"),
        (DSCL, ".", "-delete", user, "AuthenticationAuthority"),
        (DSCL, ".", "-delete", user, "ShadowHashData"),
        (DSCL, ".", "-create", user, "Password", "*"),
    ]


def account_problem(record: str) -> str | None:
    """What is wrong with her directory record as `dscl . -read` shows it (UserShell, IsHidden, Password,
    AuthenticationAuthority; None: no shell, hidden, no password). Its home stays /var/empty: macOS keeps it, and her
    services get theirs explicitly. dscl names a native attribute with its prefix ("dsAttrTypeNative:IsHidden: 1",
    live on macOS 27): the prefix is left out before the key is read."""
    values: dict[str, str] = {}
    for line in record.splitlines():
        key, sep, value = line.removeprefix(DSCL_NATIVE).partition(":")
        if sep and not line.startswith(" "):
            values[key.strip()] = value.strip()
    if values.get("UserShell") != NO_SHELL:
        return f"its shell is not {NO_SHELL}"
    if values.get("IsHidden") != "1":
        return "it shows on the login window"
    if "AuthenticationAuthority" in values:
        return "it has a way to log in (AuthenticationAuthority)"
    if values.get("Password") != "*":
        return 'its password is not "*" (none)'
    return None


def run_checked(host: Host, argv: Sequence[str]) -> None:
    """A fixed argv through `host`; a non-zero exit raises with the tool's last lines (it never prints a secret)."""
    code, out = host.run(list(argv))
    if code != 0:
        why = tail(out)
        raise RuntimeError(
            f"{Path(argv[0]).name} {' '.join(argv[1:3])} … failed (exit {code})" + (f": {why}" if why else "")
        )


def make_account(host: Host, say: Callable[[str], None], *, dry: bool = False) -> None:
    """Her account, made so that a second run finishes what a first one left: an existing group is used, and the
    settings (no shell, hidden, no password) are set every time; then her home, 0700 and hers. The move and
    `estia account` (as root, from the root-run tools) both run it."""
    if lookup_uid(ROLE_USER) is None:
        _, users = host.run([DSCL, ".", "-list", "/Users", "UniqueID"], read_only=True)
        _, groups = host.run([DSCL, ".", "-list", "/Groups", "PrimaryGroupID"], read_only=True)
        code, record = host.run([DSCL, ".", "-read", f"/Groups/{ROLE_GROUP}", "PrimaryGroupID"], read_only=True)
        found = re.search(r"PrimaryGroupID:\s*(\d+)", record) if code == 0 else None
        if found and int(found.group(1)) not in ids_in(users):
            number, group = int(found.group(1)), False  # an earlier run made her group
        else:
            number, group = free_id(ids_in(users), ids_in(groups)), True
        for argv in account_plan(number, group=group):
            run_checked(host, argv)
    else:
        say(f"user {ROLE_USER}: there already")
    for argv in account_settings():
        if "-delete" in argv:
            host.run(list(argv))  # fails when the attribute is not there: that is fine
        else:
            run_checked(host, argv)
    home = host.path(HOME)
    if dry:
        say(f"would make {HOME} (0700)")
    else:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        home.chmod(0o700)
    run_checked(host, [CHOWN, f"{ROLE_USER}:{ROLE_GROUP}", str(home)])


# ------------------------------------------------------------------ launchd


def find_brew_prefix() -> str:
    for prefix in ("/opt/homebrew", "/usr/local"):
        if Path(prefix, "bin", "brew").exists():
            return prefix
    return "/opt/homebrew"


def plist_values() -> dict[str, str]:
    """The values both service files get: her user and group, her home, the root-owned release."""
    return {
        "RUN_USER": ROLE_USER,
        "RUN_GROUP": ROLE_GROUP,
        "RUN_HOME": str(HOME),
        "CODE": str(CURRENT),
        "LOG_DIR": str(STREAM_LOGS),
    }


def render(template: str, values: Mapping[str, str], *, plist: bool) -> str:
    """Fill in @NAME@ placeholders; a plist's values are XML-escaped. An unknown placeholder, or an empty or multi-line
    value, is an error."""

    def one(found: re.Match[str]) -> str:
        name = found.group(1)
        if name not in values:
            raise ValueError(f"unknown placeholder @{name}@")
        value = values[name]
        if not value or "\n" in value:
            raise ValueError(f"empty or multi-line value for @{name}@")
        return escape(value) if plist else value

    return _PLACEHOLDER.sub(one, template)


def render_file(template: Path, out: Path, values: Mapping[str, str]) -> None:
    """Render a template into `out` (mode 0644): a *.plist.in is checked with plutil -lint first, and on any error
    nothing is written."""
    plist = template.name.endswith(".plist.in")
    text = render(template.read_text(encoding="utf-8"), values, plist=plist)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if plist:
            lint = subprocess.run(["/usr/bin/plutil", "-lint", str(tmp)], capture_output=True, check=False)
            if lint.returncode != 0:
                raise ValueError(f"{template.name}: the rendered plist is not valid (plutil -lint)")
        tmp.chmod(0o644)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)


def read_plist(path: Path) -> dict[str, Any] | None:
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return data if isinstance(data, dict) else None


def plist_problem(plist: Mapping[str, Any], label: str) -> str | None:
    """What makes a service file anything but hers (None when it is): its label, her user and group, a program and
    working folder under `current`, her home, a PATH of root-owned folders only, its output in /var/log/estia (a
    root-owned folder: launchd opens it as root), and no other keys (no sockets, no other environment such as DYLD_*
    or PYTHON*)."""
    if plist.get("Label") != label:
        return f"Label is not {label}"
    extra = sorted(set(plist) - _PLIST_KEYS)
    if extra:
        return f"keys a service of hers never has: {', '.join(extra)}"
    if plist.get("UserName") != ROLE_USER or plist.get("GroupName") != ROLE_GROUP:
        return f"UserName and GroupName are not {ROLE_USER}"
    args = plist.get("ProgramArguments")
    if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
        return "ProgramArguments is not a list of words"
    if not args[0].startswith(f"{CURRENT}/") or ".." in Path(args[0]).parts:
        return f"the program is not under {CURRENT}"
    env = plist.get("EnvironmentVariables") or {}
    if not isinstance(env, dict) or set(env) - _ENV_KEYS:
        return f"EnvironmentVariables holds more than {', '.join(sorted(_ENV_KEYS))}"
    if env.get("HOME") != str(HOME):
        return f"HOME is not {HOME}"
    if env.get("CFFIXED_USER_HOME") != str(HOME):
        return f"CFFIXED_USER_HOME is not {HOME}"
    if "PATH" in env and any(entry not in _PATH_ALLOWED for entry in str(env["PATH"]).split(":")):
        return f"PATH has a folder outside {HER_PATH}"
    folder = str(plist.get("WorkingDirectory", CURRENT))
    if folder != str(CURRENT) and not folder.startswith(f"{CURRENT}/"):
        return f"WorkingDirectory is not under {CURRENT}"
    for key in ("StandardOutPath", "StandardErrorPath"):
        value = str(plist.get(key, ""))
        if value and Path(value).parent == HOME / "Library/Logs/local-assistant" and ".." not in value:
            return LOGS_IN_HER_FOLDER
        if value and (Path(value).parent != STREAM_LOGS or ".." in value):
            return f"{key} is not in {STREAM_LOGS}"
    return None


def make_stream_logs(host: Host) -> None:
    """/var/log/estia (root's, 0755) and each service's output file in it (0600; hers on the Mac), made before launchd
    opens them as root: in a folder only root can change, so no link of hers is ever followed. A file there is kept
    (never emptied). A name that is not a regular file of hers with one name (her account may `ln` her file from her
    home: the same volume) is unlinked and made fresh, never followed and never refused: a refusal would let her
    account stop every update."""
    folder = host.path(STREAM_LOGS)
    folder.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.mkdir(folder, 0o755)
    if not stat.S_ISDIR(os.lstat(folder).st_mode):
        raise OSError(f"{STREAM_LOGS} is not a folder (a link?): nothing was opened there")
    as_root = os.geteuid() == 0
    if as_root:
        os.chown(folder, 0, 0, follow_symlinks=False)
    os.chmod(folder, 0o755, follow_symlinks=False)
    her = pwd.getpwnam(ROLE_USER) if as_root else None
    for name in STREAM_LOG.values():
        path = folder / name
        try:
            info: os.stat_result | None = os.lstat(path)
        except FileNotFoundError:
            info = None
        kept = (
            info is not None
            and stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and (her is None or info.st_uid == her.pw_uid)
        )
        if info is not None and not kept:
            os.unlink(path)  # root's folder: only this name goes, whatever it led to
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | (0 if kept else os.O_EXCL)
        fd = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"{STREAM_LOGS / name}: not a regular file")
            if her is not None:
                os.fchown(fd, her.pw_uid, her.pw_gid)
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)


def moved(root: Path = Path("/")) -> bool:
    """She runs as her own user: the bridge's service file runs as _estia, and no rollback is on record."""
    plist = read_plist(root / DAEMONS.relative_to("/") / f"{BRIDGE}.plist")
    if plist is None or plist.get("UserName") != ROLE_USER:
        return False
    marker = read_marker(root)
    return marker is None or not marker.get("rolled_back")


# ------------------------------------------------------------------ doctor's install checks


def _shown(root: Path, path: Path) -> str:
    return "/" + str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def read_account() -> str:
    """Her account record as the directory service shows it to anyone (no password hash is readable)."""
    done = subprocess.run(
        [
            DSCL,
            ".",
            "-read",
            f"/Users/{ROLE_USER}",
            "UserShell",
            "IsHidden",
            "Password",
            "AuthenticationAuthority",
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return done.stdout


def _user_check(root: Path, account: Callable[[], str] | None) -> Check:
    uid = lookup_uid(ROLE_USER)
    if uid is None:
        return Check(
            "own_user",
            "fail",
            "Own user",
            f"the user {ROLE_USER} does not exist",
            f"{ESTIA} account makes it again (no shell, hidden, no password)",
        )
    home = root / HOME.relative_to("/")
    try:
        info = home.stat()
    except OSError:
        marker = read_marker(root)
        held = held_copy(marker) and time.time() < float((marker or {}).get("moved_at", 0)) + ROLLBACK_DAYS * 86400
        return Check(
            "own_user",
            "fail",
            "Own user",
            f"{HOME} is missing",
            f"{ESTIA} rollback brings back your copy of her data (within {ROLLBACK_DAYS} days of the move)"
            if held  # a new install keeps no copy; after the 7 days or remove-old none is left
            else f"no copy of her data is held: restore {HOME} from your backup, if there is one",
        )
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o077:
        return Check(
            "own_user",
            "fail",
            "Own user",
            f"{HOME} is not hers alone (mode {stat.S_IMODE(info.st_mode):o})",
            f"{SUDO} {CHOWN} {ROLE_USER}:{ROLE_GROUP} {HOME} && {SUDO} {CHMOD} 700 {HOME}",
        )
    if account is not None:
        problem = account_problem(account())
        if problem:
            return Check(
                "own_user",
                "fail",
                "Own user",
                f"{ROLE_USER}: {problem}",
                f"{ESTIA} account sets it again (no shell, hidden, no password)",
            )
    return Check(
        "own_user", "ok", "Own user", f"she runs as {ROLE_USER} (no password, no login): only that user reads {HOME}"
    )


def _escapes(link: Path, inside: Path) -> bool:
    real = os.path.realpath(link)
    top = os.path.realpath(inside)
    return real != top and not real.startswith(f"{top}/")


def _code_check(root: Path) -> Check:
    code = root / CODE.relative_to("/")
    current = code / "current"
    target = current.resolve() if current.is_symlink() else None
    releases = code / "releases"
    if target is None or not target.is_dir() or target.parent != releases.resolve():
        return Check("code", "fail", "Code", "no current release", f"{DEPLOY_CMD} <your checkout>")
    for parent in (code.parent, code.parent.parent):  # /usr/local and /usr: whoever can rename the code replaces it
        try:
            info = parent.lstat()
        except OSError:
            continue
        if info.st_uid != CODE_UID or stat.S_IMODE(info.st_mode) & 0o022:
            return Check(
                "code",
                "fail",
                "Code",
                f"{_shown(root, parent)} can be changed by an account other than root, and with it {CODE}",
                f"{SUDO} {CHOWN} root:wheel {_shown(root, parent)} && {SUDO} {CHMOD} go-w {_shown(root, parent)}",
            )
    loose: list[Path] = []
    for folder, dirs, files in os.walk(code):
        here = Path(folder)
        for path in (here, *(here / name for name in [*files, *dirs])):
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                if info.st_uid != CODE_UID or _escapes(
                    path, code
                ):  # also a chain of links: followed as the kernel does
                    loose.append(path)
                continue
            if info.st_uid != CODE_UID or stat.S_IMODE(info.st_mode) & 0o022:
                loose.append(path)
        dirs[:] = [d for d in dirs if not (here / d).is_symlink()]
    if loose:
        return Check(
            "code",
            "fail",
            "Code",
            f"{len(set(loose))} file(s) in {CODE} can be changed by an account other than root, or lead out of it "
            f"(first: {_shown(root, loose[0])})",
            f"{DEPLOY_CMD} (a fresh, root-owned release)",
        )
    return Check("code", "ok", "Code", f"root-owned release {target.name}; the checkout is a source, never run")


def _plists_check(root: Path, plists: Mapping[str, dict[str, Any] | None]) -> Check:
    wrong: list[str] = []
    in_her_folder: list[str] = []
    daemons = root / DAEMONS.relative_to("/")
    for label, plist in plists.items():
        name = label.rsplit(".", 1)[1]
        if plist is None:
            if label == BRIDGE:
                wrong.append("the bridge's service file is missing")
            continue
        problem = plist_problem(plist, label)
        if problem == LOGS_IN_HER_FOLDER:
            in_her_folder.append(name)
        elif problem:
            wrong.append(f"{name}: {problem}")
            continue
        info = (daemons / f"{label}.plist").lstat()
        if info.st_uid != CODE_UID or stat.S_IMODE(info.st_mode) & 0o022 or not stat.S_ISREG(info.st_mode):
            wrong.append(f"{name}: its service file can be changed by an account other than root")
    if wrong:
        return Check("plists", "fail", "Services", "; ".join(wrong), f"{DEPLOY_CMD} (it renders both again)")
    if in_her_folder:  # an install from before /var/log/estia: the tools' update, then a deploy
        text = f"{' and '.join(in_her_folder)}: {LOGS_IN_HER_FOLDER}"
        fix = f"{DEPLOY_CMD} --update-tools (read the diff), then {DEPLOY_CMD}: it moves them to {STREAM_LOGS}"
        return Check("plists", "fail", "Services", text, fix)
    names = " and ".join(label.rsplit(".", 1)[1] for label, plist in plists.items() if plist is not None)
    return Check("plists", "ok", "Services", f"{names} run as {ROLE_USER} from {CURRENT}")


def _trusted(path: Path, root: Path) -> str | None:
    """Why a program she runs could be changed by an account other than root (None: it cannot): the program and every
    folder above it, after its links are followed, are root's and writable by nobody else (up to `root`)."""
    real = Path(os.path.realpath(path))
    if not real.exists():
        return "not found"
    top = Path(os.path.realpath(root))
    for item in (real, *real.parents):
        if top != Path("/") and not item.is_relative_to(top):
            break
        try:
            info = item.lstat()
        except OSError:
            return "cannot be read"
        if info.st_uid != CODE_UID or (stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX):
            return f"{item} can be changed by an account other than root"
    return None


def program_check(programs: Sequence[tuple[str, Path]], root: Path = Path("/")) -> Check:
    """Doctor's line for what she runs (her signal-cli, the call helper's Java and tunnel, the folders on her PATH):
    each must be root's alone, or whoever can change it runs code as her, with her data."""
    loose = [f"{name} ({why})" for name, path in programs if (why := _trusted(path, root)) is not None]
    if loose:
        return Check(
            "programs",
            "fail",
            "Her programs",
            f"{'; '.join(loose)}: whoever can change it runs code as her, with her data",
            f"{DEPLOY_CMD} copies her programs into the release; then take the line out of her config ({ESTIA} config)",
        )
    return Check("programs", "ok", "Her programs", "everything she runs is root's: the release and macOS's own")


_KEPT_ASIDE = re.compile(r"^\.before-[0-9]{8}-[0-9]{6}$")  # an earlier copy of hers a retried move kept aside


def kept_aside(her_home: Path) -> list[Path]:
    """Her earlier copies a retried move kept aside: the direct children of her home named .before-YYYYMMDD-HHMMSS
    that are real folders (lstat: a link is never one, nothing is followed), oldest first. None when her home cannot
    be read (a dry run as you)."""
    try:
        names = sorted(name for name in os.listdir(her_home) if _KEPT_ASIDE.match(name))
    except OSError:
        return []
    found: list[Path] = []
    for name in names:
        try:
            if stat.S_ISDIR(os.lstat(her_home / name).st_mode):
                found.append(her_home / name)
        except OSError:
            continue
    return found


def human_size(count: int) -> str:
    """A size in decimal units, as Finder shows it: 999 bytes, 12 kB, 4.0 MB, 1.4 GB."""
    if count < 1000:
        return f"{count} bytes"
    if count < 1_000_000:
        return f"{count / 1000:.0f} kB"
    if count < 1_000_000_000:
        return f"{count / 1_000_000:.1f} MB"
    return f"{count / 1_000_000_000:.1f} GB"


def delete_tree(path: Path) -> None:
    """A folder and everything in it, as root, never following a link: only a real folder (lstat), and only with
    Python's link-safe rmtree (it walks by file descriptor and refuses a folder swapped for a link meanwhile)."""
    if not shutil.rmtree.avoids_symlink_attacks:
        raise OSError("this Python cannot delete a folder without following links")
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise OSError("not a folder")
    shutil.rmtree(path)


def read_marker(root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((root / MARKER.relative_to("/")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def held_copy(marker: Mapping[str, Any] | None) -> bool:
    """Whether the move's record holds her old folders for a rollback: a move from an install that ran as you, not
    rolled back and not removed. A new install (`fresh`) holds none."""
    return marker is not None and not (marker.get("removed") or marker.get("rolled_back") or marker.get("fresh"))


def _rollback_check(root: Path, now: float) -> Check | None:
    marker = read_marker(root)
    if marker is None or not held_copy(marker):
        return None
    moved_at = float(marker.get("moved_at", 0))
    until = moved_at + ROLLBACK_DAYS * 86400
    day = time.strftime("%Y-%m-%d", time.localtime(until))
    if now < until:
        return Check(
            "rollback",
            "ok",
            "Old copy",
            f"her old folders are held in {HELD} (root only) until {day}: {ESTIA} rollback goes back",
        )
    return Check(
        "rollback",
        "warn",
        "Old copy",
        f"her old folders are still held in {HELD} (root only) after {day}",
        f"{ESTIA} remove-old",
    )


def install_checks(root: Path = Path("/"), *, account: Callable[[], str] | None = None) -> list[Check]:
    """Doctor's lines for her own user: the user and its home, the root-owned code, the service files and the rollback
    window. Read-only. Nothing installed at all: no line (doctor's daemon line covers it); the services running as the
    owner: one warning that names the migration. `account`: her directory record (read on the real Mac only)."""
    daemons = root / DAEMONS.relative_to("/")
    plists = {label: read_plist(daemons / f"{label}.plist") for label in LABELS}
    if not moved(root):
        if plists[BRIDGE] is None:
            return []
        return [
            Check(
                "own_user",
                "warn",
                "Own user",
                "she runs as your user: every program you run can read her data and change her code",
                f"move her into her own user: {MIGRATE} --dry-run, then the same without --dry-run",
            )
        ]
    if account is None and root == Path("/"):
        account = read_account
    checks = [_user_check(root, account), _code_check(root), _plists_check(root, plists)]
    window = _rollback_check(root, time.time())
    if window is not None:
        checks.append(window)
    return checks


def her_programs(cfg: Any, root: Path = Path("/")) -> list[tuple[str, Path]]:
    """What she runs, by name: her signal-cli (or the JVM build, its Java and the call helper with [calls]) and the
    folders on her PATH (both service files)."""
    programs: list[tuple[str, Path]] = []
    daemons = root / DAEMONS.relative_to("/")
    folders: list[str] = []
    for label in LABELS:
        plist = read_plist(daemons / f"{label}.plist") or {}
        env = plist.get("EnvironmentVariables") or {}
        if isinstance(env, dict):
            folders += [entry for entry in str(env.get("PATH", "")).split(":") if entry and entry not in folders]
    programs += [(f"PATH {entry}", root / entry.lstrip("/")) for entry in folders]
    if cfg.calls_enabled:
        programs.append(("signal-cli (calls)", Path(cfg.call_signal_cli)))
        programs.append(("the call helper", Path(cfg.call_tunnel)))
        java = cfg.java_home  # the config's, else the release's own JDK
        if java is not None:
            programs.append(("Java", Path(java) / "bin" / "java"))
    else:
        found = cfg.signal_cli if "/" in cfg.signal_cli else shutil.which(cfg.signal_cli, path=HER_PATH)
        programs.append(("signal-cli", Path(found or f"/{cfg.signal_cli} (not on her PATH)")))
    return programs


def voice_health(live: bool) -> Mapping[str, object] | None:
    """The voice service's /health answer (127.0.0.1 only, no proxy, 5 s); None when it does not answer, and always
    off the live Mac (a test or a rehearsal never asks the running service)."""
    if not live:
        return None
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(VOICE_HEALTH_URL, timeout=5) as response:
            data = json.loads(response.read(10_000))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _voice_ready(health: Mapping[str, object] | None) -> bool:
    return health is not None and health.get("stt") == "ready" and health.get("tts") == "ready"


def voice_line(health: Mapping[str, object] | None, *, calls: bool = True) -> str:
    """What the end of a move, a setup or a deploy says about her voice: "voice: ready" when both engines are ready
    (speech in, speech out), else with [calls] when calls work (the bridge texts once it is ready), else that it
    loads."""
    if _voice_ready(health):
        return "voice: ready"
    return VOICE_LOADING if calls else VOICE_LOADING_NO_CALLS


def calls_in(text: str) -> bool:
    """Whether a config turns calls on: a [calls] table next to [voice] (without [voice] the bridge refuses it)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    return isinstance(data.get("calls"), dict) and isinstance(data.get("voice"), dict)


def voice_end(
    health: Callable[[], Mapping[str, object] | None],
    *,
    calls: bool,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    say: Callable[[str], None],
) -> str:
    """The voice line at the end of a move, a setup or a deploy, once the bridge was seen up. Without [calls]: the
    state now. With [calls] and the voice still loading: read again until ready or VOICE_WAIT_SECONDS from now, so
    "she texts you then" is said only when the bridge will count a long load and text. Its caller is done by then: a
    Ctrl-C or a hang-up ends only the wait (VOICE_STOPPED)."""
    try:
        found = health()
        if not calls or _voice_ready(found):
            return voice_line(found, calls=calls)
        say(VOICE_WAITING)
        deadline = clock() + VOICE_WAIT_SECONDS
        while True:
            sleep(VOICE_POLL_SECONDS)
            found = health()
            if _voice_ready(found) or clock() >= deadline:
                return voice_line(found)
    except KeyboardInterrupt:
        return VOICE_STOPPED


# ------------------------------------------------------------------ the move into her own user

DeployStep = Callable[[], int]
_KEY_LINE = re.compile(r'^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)"([^"]*)"(.*)$')
_ARRAY_LINE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*\[(.*)\]\s*(#.*)?$")
_TABLE_LINE = re.compile(r"^\s*\[\s*([A-Za-z0-9_.-]+)\s*\]\s*(#.*)?$")
MOVED_NOTE = "  # moved: her release has its own copy (migrate-to-own-user.sh)"
# the programs she runs, by config key: what the release holds in vendor/ instead (config's defaults find it there)
_PROGRAM_KEYS = {
    ("", "signal_cli"): "signal-cli-native",
    ("calls", "signal_cli"): "signal-cli-*-jvm",
    ("calls", "java_home"): "openjdk.jdk",
    ("calls", "tunnel"): "signal-call-tunnel",
}
_VENDOR_SHAPE = re.compile(
    r"(?:^|/)vendor/(signal-cli-[0-9][0-9A-Za-z.]*-jvm/signal-cli-[0-9][0-9A-Za-z.]*/bin/signal-cli)$"
)


@dataclass(frozen=True)
class Owner:
    """The person whose install moves (SUDO_USER under sudo, else whoever runs it)."""

    name: str
    home: Path
    uid: int


def projects_on(text: str) -> bool:
    """The config has [projects]: projects are an extra, off without it."""
    try:
        return isinstance(tomllib.loads(text).get("projects"), dict)
    except tomllib.TOMLDecodeError:
        return False


def her_projects_root(text: str, home: Path) -> Path | None:
    """[projects] root as the owner meant it (default ~/dev, ~ the owner's home), absolute; None when projects are off
    (no [projects]), the root is relative or the config cannot be read."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    projects = data.get("projects")
    value = projects.get("root", "~/dev") if isinstance(projects, dict) else None
    return owner_path(value, home) if isinstance(value, str) else None


def _with_projects_root(text: str, root: Path) -> str | None:
    """`text` with `[projects] root = "<root>"`: the line replaced, added below the table's header, or a new table at
    the end. None when the result would not read back as exactly that (never a broken config)."""
    value = json.dumps(str(root), ensure_ascii=False)  # a TOML basic string
    lines = text.splitlines(keepends=True)
    table, header, done = "", None, False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("["):
            found = _TABLE_LINE.match(line)
            table = found.group(1) if found else "[[array]]"
            if table == "projects" and header is None:
                header = index
            continue
        key = _KEY_LINE.match(line.rstrip("\n"))
        if table == "projects" and key and key.group(2) == "root":
            lines[index] = f"{key.group(1)}root{key.group(3)}{value}{key.group(5)}\n"
            done = True
    if not done and header is not None:
        lines.insert(header + 1, f"root = {value}\n")
    elif not done:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += ["\n[projects]\n", f"root = {value}\n"]
    new = "".join(lines)
    try:
        projects = tomllib.loads(new).get("projects")
    except tomllib.TOMLDecodeError:
        return None
    return new if isinstance(projects, dict) and projects.get("root") == str(root) else None


def _projects_note(text: str, out: str, old_home: Path) -> tuple[str, str]:
    """Her config with the owner's projects folder by its absolute path, and the note that says so; projects off (no
    [projects]): her config as it is."""
    if not projects_on(text):
        return out, "projects: off (no [projects] in your config): no folder of yours is shared with her"
    root = her_projects_root(text, old_home)
    if root is None:
        return out, (
            "projects.root: not an absolute path or ~/…: the project tools find nothing; set an absolute folder "
            f"({ESTIA} config)"
        )
    new = _with_projects_root(out, root)
    if new is None:
        return out, f'projects.root: not rewritten; set root = "{root}" in her [projects] by hand'
    if root != old_home and root.is_relative_to(old_home):
        return (
            new,
            f'projects.root: set to "{root}" (~ now means {HOME}); her user reads it read-only once {SHARE} runs',
        )
    return new, f'projects.root: set to "{root}", outside your home: give {ROLE_USER} read access to it yourself'


def _in_home(value: str, old_home: Path) -> bool:
    return value.startswith("~") or value == str(old_home) or value.startswith(f"{old_home}/")


def _without_home_folders(line: str, key: str, old_home: Path) -> tuple[str, int] | None:
    """A one-line list setting with its entries in the owner's home left out (the line's indent and comment kept), and
    how many went; None when the line cannot be read back as a list of strings."""
    try:
        values = tomllib.loads(line.strip()).get(key)
    except tomllib.TOMLDecodeError:
        return None
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        return None
    kept = [item for item in values if not _in_home(item, old_home)]
    found = _ARRAY_LINE.match(line.rstrip("\n"))
    comment = f"  {found.group(3)}" if found and found.group(3) else ""
    indent = line[: len(line) - len(line.lstrip())]
    new = f"{indent}{key} = [{', '.join(json.dumps(item, ensure_ascii=False) for item in kept)}]{comment}\n"
    try:
        if tomllib.loads(new.strip()).get(key) != kept:
            return None
    except tomllib.TOMLDecodeError:
        return None
    return new, len(values) - len(kept)


_WATCH_LOGS_START = re.compile(r"^\s*watch_logs\s*=\s*\[")


def _list_end(lines: Sequence[str], start: int) -> int | None:
    """The index of the line that closes the list opened on `lines[start]` (brackets counted outside strings and
    comments); None when it never closes."""
    depth = 0
    for index in range(start, len(lines)):
        quote = ""
        escaped = False
        for char in lines[index]:
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\" and quote == '"':
                    escaped = True
                elif char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == "#":
                break
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return index
    return None


def _watch_logs_note(count: int) -> str:
    folders = "folder" if count == 1 else "folders"
    return f"health.watch_logs: {count} {folders} in your home left out of her copy (she cannot read your home)"


def config_notes(
    text: str, old_home: Path, vendor: Path | None = None, *, avatar_copy: Path | None = None
) -> tuple[str, list[str]]:
    """Her config, ready for her home. A program she runs (signal_cli, [calls] signal_cli, java_home, tunnel) that is
    not in /usr/local/estia is commented out when `vendor` (the release's vendor/) has her own copy, so the default
    finds that; a JVM signal-cli of another version than the default is pointed at the same build in the release.
    Without her own copy the key is named (keys only, never values). With [projects], its root becomes the owner's
    folder by its absolute path (default ~/dev), named with that path; without it nothing is added. `avatar_copy` (the
    picture copied into her config folder) replaces the avatar's path. In [health] watch_logs, the folders in the
    owner's home are left out (counted). Every other path that starts with ~ or the owner's home, also inside a list,
    is named: ~ now means /var/estia, and the owner's home is closed to her."""
    lines: list[str] = []
    notes: list[str] = []
    table = ""
    source = text.splitlines(keepends=True)
    skip_to = -1
    for index, line in enumerate(source):
        if index <= skip_to:
            continue
        header = _TABLE_LINE.match(line)
        if header:
            table = header.group(1)
        if line.lstrip().startswith("#"):
            lines.append(line)
            continue
        end = None
        if table == "health" and _WATCH_LOGS_START.match(line) and not _ARRAY_LINE.match(line.rstrip("\n")):
            end = _list_end(source, index)  # a list written over several lines
        if end is not None:
            block = "".join(source[index : end + 1])
            left = _without_home_folders(block, "watch_logs", old_home)
            if left is not None and left[1]:
                lines.append(left[0])
                notes.append(_watch_logs_note(left[1]))
                skip_to = end
                continue
        found = _KEY_LINE.match(line.rstrip("\n"))
        if found:
            key, value = found.group(2), found.group(4)
            name = f"{table}.{key}" if table else key
            at_home = _in_home(value, old_home)
            program = _PROGRAM_KEYS.get((table, key))
            if (table, key) == ("", "avatar") and avatar_copy is not None:
                lines.append(f"{found.group(1)}{key}{found.group(3)}{json.dumps(str(avatar_copy))}{found.group(5)}\n")
                notes.append("avatar: the picture was copied into her config folder; her config points at the copy")
                continue
            if program is not None and not value.startswith(f"{CODE}/"):
                shaped = _VENDOR_SHAPE.search(value) if program == "signal-cli-*-jvm" else None
                if shaped and vendor is not None and (vendor / shaped.group(1)).is_file():
                    new = f'{found.group(1)}{key}{found.group(3)}"{CURRENT}/vendor/{shaped.group(1)}"{found.group(5)}'
                    lines.append(new + "\n")
                    notes.append(f"{name}: pointed at the same build in her release")
                    continue
                if vendor is not None and any(vendor.glob(program)):
                    lines.append(f"{found.group(1)}# {line.strip()}{MOVED_NOTE}\n")
                    notes.append(f"{name}: commented out in her copy; her release has its own")
                    continue
                notes.append(f"{name}: a program outside {CODE}; her release has no copy of it, so she runs yours")
            elif at_home and name != "projects.root":
                notes.append(f"{name}: a path in a home folder; ~ now means {HOME}, and your home is closed to her")
        else:
            listed = _ARRAY_LINE.match(line.rstrip("\n"))
            if listed and any(_in_home(item, old_home) for item in re.findall(r'"([^"]*)"', listed.group(2))):
                name = f"{table}.{listed.group(1)}" if table else listed.group(1)
                left = _without_home_folders(line, listed.group(1), old_home) if name == "health.watch_logs" else None
                if left is not None:
                    lines.append(left[0])
                    notes.append(_watch_logs_note(left[1]))
                    continue
                notes.append(f"{name}: a path in a home folder; ~ now means {HOME}, and your home is closed to her")
        lines.append(line)
    out, note = _projects_note(text, "".join(lines), old_home)
    return out, [*notes, note]


def owner_path(value: str, home: Path) -> Path | None:
    """A path from the owner's config as the owner meant it: ~ is the owner's home (never root's or hers). None for a
    relative path or another user's ~name."""
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    path = Path(value)
    return path if path.is_absolute() else None


CONFIG_LIMIT = 1_000_000  # bytes of a config root reads her number from: hers are a few KB


def bot_number(config: Path) -> str | None:
    """bot_number from a config (opened without following a link at its name, a regular file of at most
    `CONFIG_LIMIT` bytes); None when there is none."""
    try:
        fd = os.open(config, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            raw = handle.read(CONFIG_LIMIT + 1)
        if len(raw) > CONFIG_LIMIT:
            return None
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    number = data.get("bot_number")
    return number if isinstance(number, str) and number else None


def has_account(signal_data: Path, number: str) -> bool:
    """Whether signal-cli holds her number as a verified account: its own account file says `registered: true`.
    signal-cli lists a number in accounts.json as soon as its registration starts, before Signal's code is entered."""
    account = signal_account(signal_data, number) if number else None
    return account is not None and account.get("registered") is True


def _size(folder: Path) -> int:
    total = 0
    for here, _dirs, files in os.walk(folder):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(here) / name).lstat().st_size
    return total


@contextlib.contextmanager
def interruptions_raise() -> Iterator[None]:
    """A hang-up (a dropped SSH session) or a TERM becomes an exception, so the restore runs instead of leaving her
    stopped."""

    def stop(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    saved: dict[signal.Signals, Any] = {}
    try:
        for number in (signal.SIGHUP, signal.SIGTERM):
            saved[number] = signal.signal(number, stop)
    except ValueError:  # not the main thread: nothing to catch there
        pass
    try:
        yield
    finally:
        for number, handler in saved.items():
            signal.signal(number, handler)


@contextlib.contextmanager
def interruptions_ignored() -> Iterator[None]:
    """While her old setup comes back, a hang-up, a TERM or a Ctrl-C is ignored (by the tools it runs too): the restore
    always finishes."""
    saved: dict[signal.Signals, Any] = {}
    try:
        for number in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            saved[number] = signal.signal(number, signal.SIG_IGN)
    except ValueError:  # not the main thread: nothing to catch there
        pass
    try:
        yield
    finally:
        for number, handler in saved.items():
            if handler is not None:
                signal.signal(number, handler)


class Migration:
    """scripts/migrate-to-own-user.sh: the owner's install into `_estia`, and back (rollback, within 7 days).

    mode "real" (as root: build the release while she runs, then stop, create the user, copy her data as her, hold
    the old folders where only root reaches them, install the services, start, check), "rehearse" (everything into
    ESTIA_ROOT from a copy of her data; no user, no launchctl, your folders untouched), "dry" (print every step, write
    nothing)."""

    def __init__(
        self,
        host: Host,
        owner: Owner,
        *,
        checkout: Path,
        mode: str,
        deploy: DeployStep | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        say: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.owner = owner
        self.checkout = checkout
        self.mode = mode
        self.dry = mode == "dry"
        self.deploy = deploy or self._deploy
        self.clock = clock
        self.sleep = sleep
        self.say = say or _say
        self.her_home = host.path(HOME)
        self.code = host.path(CODE)
        self.daemons = host.path(DAEMONS)
        self.saved = host.path(SAVED)
        self.held = host.path(HELD)
        self.newsyslog = host.path(NEWSYSLOG)
        self.marker = host.path(MARKER)
        self.root = host.root
        self.started: list[str] = []
        self.held_map: list[tuple[str, str]] = []
        self.projects_root: Path | None = None  # your projects folder, as her config names it after the move
        self.voice_health: Callable[[], Mapping[str, object] | None] = lambda: voice_health(self.host.live)
        self.calls = False  # her config has [calls]: the bridge texts once her voice is ready after a long load

    # ---------------------------------------------------------------- small steps (printed in a dry run)

    def _do(self, text: str, action: Callable[[], object]) -> None:
        if self.dry:
            self.say(f"would {text}")
        else:
            action()

    def _run(self, argv: Sequence[str]) -> None:
        run_checked(self.host, argv)

    def _as(self, user: str) -> list[str]:
        """A command run as `user`: through sudo when root does the real move; as is in a rehearsal or a test."""
        return [SUDO, "-u", user, "-H", "--"] if self.host.live and os.geteuid() == 0 else []

    def _old_folders(self, *, secrets_only: bool = False) -> list[Path]:
        """Her folders in your home: really there (no link on the way out of your home), never followed elsewhere."""
        home = os.path.realpath(self.owner.home)
        found = [self.owner.home / old for old, _new in DATA_MAP if not (secrets_only and old in CACHES)]
        return [
            folder
            for folder in found
            if folder.is_dir() and not folder.is_symlink() and os.path.realpath(folder).startswith(f"{home}/")
        ]

    def _deploy(self) -> int:
        from local_assistant import deploy  # deploy imports this module

        try:
            uv = deploy.find_uv(self.owner)
        except deploy.DeployError as exc:
            self.say(f"✗ {exc}")
            return 1
        run = deploy.Deploy(self.host, self.owner, self.checkout, uv=uv, say=self.say, check_health=False)
        return run.run(restart=False)

    def _installed(self, label: str) -> bool:
        return (self.daemons / f"{label}.plist").exists()

    def _hers(self, label: str) -> bool:
        """Its service file runs as her own user (a failed try's, when no move is on record)."""
        plist = read_plist(self.daemons / f"{label}.plist")
        return plist is not None and plist.get("UserName") == ROLE_USER

    def _stop(self, labels: list[str]) -> None:
        for label in labels:
            self.host.run([LAUNCHCTL, "bootout", f"system/{label}"])
        if self.host.live:
            for label in labels:
                for _ in range(50):  # bootout is asynchronous; bootstrapping before it ends fails with error 5
                    if self.host.run([LAUNCHCTL, "print", f"system/{label}"], read_only=True)[0] != 0:
                        break
                    self.sleep(0.2)

    def _start(self, labels: list[str]) -> None:
        for label in labels:
            self.started.append(label)
            self._run([LAUNCHCTL, "bootstrap", "system", str(DAEMONS / f"{label}.plist")])

    # ---------------------------------------------------------------- before anything stops

    def preflight(self, labels: list[str]) -> list[str]:
        """What would make the move fail, found before she is stopped. Her folders are walked first (lstat, nothing
        followed): with a link out of them (a folder on the way included) or a pipe, socket or device, nothing of
        hers is read at all. A link made after that walk, before root reads her number and her Signal account (a
        number and a yes or no come of those reads, each capped), is caught by the walk of her copy before she
        starts."""
        problems: list[str] = []
        real = Path(os.path.realpath(self.owner.home))
        for old, _new in DATA_MAP:  # her copy must hold only her files: no link out, no pipe, socket or device
            for path, what in strays(self.owner.home / old, real / old, skip=[self.owner.home / VENDOR]):
                fix = "replace it with the files it leads to" if what == LINK_OUT else "delete it"
                problems.append(f"{clean(str(path))}: {what}; {fix}, then run this again")  # a name you chose: cleaned
        if problems:
            return problems
        number = bot_number(self.owner.home / HER_CONFIG)
        if number is None:
            problems.append("no config in your home to move: set her up first (scripts/setup.sh)")
        elif not has_account(self.owner.home / ".local/share/signal-cli", number):
            problems.append("her number is not verified with Signal yet: scripts/setup.sh registers it")
        # her voice service: the release builds it from the commit's own sources (deploy.build_voice)
        need = sum(_size(folder) for folder in self._old_folders()) + 3 * 2**30  # her data, plus about 3 GB of code
        try:
            free = shutil.disk_usage(self.root if self.root.exists() else self.root.parent).free
        except OSError:
            free = need
        if free < need:
            problems.append(f"{free / 2**30:.1f} GB free; the move needs about {need / 2**30:.1f} GB")
        return problems

    # ---------------------------------------------------------------- the move

    def run(self) -> int:
        marker = read_marker(self.root)
        if marker is not None and not marker.get("rolled_back") and moved(self.root):
            when = time.strftime("%Y-%m-%d", time.localtime(float(marker.get("moved_at", 0))))
            self.say(f"✓ She runs in her own user ({ROLE_USER}) since {when}. Updates: {DEPLOY_CMD}")
            return 0
        # her old setup: the services that run as you (a service file of hers with no move on record is a failed
        # try's: never kept as her old setup, and gone again if this try fails)
        labels = [label for label in LABELS if self._installed(label) and not self._hers(label)]
        fresh = not labels
        unchanged = "Nothing changed." if fresh else "Nothing changed: she keeps running as before."
        problems = self.preflight(labels)
        if problems:
            for problem in problems:
                self.say(f"✗ {problem}")
            self.say(unchanged)
            return 1
        if (
            self.host.live
            and self.host.run(["/usr/bin/pgrep", "-f", r"\.venv/bin/local-assistant --foreground"], read_only=True)[0]
            == 0
        ):
            self.say("✗ A bridge runs in a terminal: stop it first (Ctrl-C there).")
            return 1
        with interruptions_raise():  # a hang-up (a dropped SSH session) or a TERM raises from here on
            return self._move(labels, fresh, unchanged)

    def _move(self, labels: list[str], fresh: bool, unchanged: str) -> int:
        voice = VOICE in labels
        self.say("Building her release: a few minutes, while she keeps running.")
        try:
            built = self.deploy()
        except KeyboardInterrupt:
            built = 130
        if built != 0:
            self.say(f"✗ Her release was not built. {unchanged}")
            return 1
        offline = "she is offline for a minute or two" if labels else "she starts at the end"
        self.say(f"Moving her into her own user {ROLE_USER} (home {HOME}, code {CODE}): {offline}.")
        try:
            self._save_old(labels)
            self._stop([label for label in LABELS if self._installed(label)])  # a failed try's service too
            self._make_user()
            self._copy_data()
            self._check_copy()
            self._hold_old()
            self._install_plists(voice)
            self._do(
                f"remove {NEWSYSLOG} (her logs now rotate themselves)",
                lambda: self.newsyslog.unlink(missing_ok=True),
            )
            since = self.clock()
            self._start([BRIDGE, *([VOICE] if voice else [])])
            if self.host.live:
                self._check(since)
        except BaseException as exc:  # a Ctrl-C or a hang-up too: never leave her stopped
            why = str(exc) or type(exc).__name__
            if fresh:
                self.say(
                    f"✗ {why}. Nothing was installed: she does not run yet. Fix what this names, then run "
                    "scripts/install-daemon.sh again."
                )
            else:
                self.say(f"✗ {why}. Her old setup comes back.")
            self._restore(labels)
            return 1
        self._write_marker(voice, fresh)
        until = time.strftime("%Y-%m-%d", time.localtime(self.clock() + ROLLBACK_DAYS * 86400))
        if self.mode == "rehearse":
            self.say(f"✓ Rehearsal done in {self.root}. It holds a copy of her keys: delete it when done (rm -rf).")
        elif not self.dry:
            if fresh:
                self._remove_held()
                self.say(
                    f"✓ She runs as {ROLE_USER}, from {CURRENT}; her files are only in {HOME}: a new install keeps "
                    "no other copy, so it has no rollback."
                )
            else:
                self.say(
                    f"✓ She runs as {ROLE_USER}. Her old folders are held in {HELD}, for root only, until {until}: "
                    f"{ESTIA} rollback goes back; after that, {ESTIA} remove-old deletes them."
                )
            self._prune_releases()
            copies = kept_aside(self.her_home)
            if copies:
                listed = ", ".join(str(HOME / copy.name) for copy in copies)
                also = "." if fresh else ", with her old folders (no rollback after)."
                self.say(f"Earlier copies of hers kept aside: {listed}. {ESTIA} remove-old deletes them{also}")
            self.say(
                "From now on her files are hers alone. A copy a program of yours made before the move still works: "
                'if one may have, follow SECURITY.md, "If the Mac is compromised".'
            )
            self.say(f"Check: {ESTIA} doctor. Updates: {DEPLOY_CMD}")
            if self.host.live:
                self._voice_end(voice)
        else:
            self.say(
                "Nothing changed (a dry run). The real move asks for your password once and stops her for a minute "
                "or two after the release is built; on any failure her old setup comes back."
            )
        return 0

    def _check(self, since: float) -> None:
        from local_assistant import deploy

        checker = deploy.Deploy(self.host, self.owner, self.checkout, uv="", clock=self.clock, sleep=self.sleep)
        problem = checker.health(since)
        if problem is not None:
            raise RuntimeError(problem)

    def _voice_end(self, voice: bool) -> None:
        """The last line of a finished move: her voice ready, or when calls work. It may wait (voice_end), so it comes
        after the move is done: a Ctrl-C or a hang-up here leaves her moved."""
        if voice:
            self.say(voice_end(self.voice_health, calls=self.calls, clock=self.clock, sleep=self.sleep, say=self.say))

    def _prune_releases(self) -> None:
        """After a successful move: the releases other than the one `current` names (earlier tries that never went
        live) are deleted, each a real folder (a link there is left, never followed); never without a `current` that
        names a folder in releases/. Says how many and the space freed."""
        releases, current = self.host.path(RELEASES), self.host.path(CURRENT)
        if not current.is_symlink():
            return
        live = Path(os.path.realpath(current))
        if not live.is_dir() or live.parent != Path(os.path.realpath(releases)):  # names no live release: delete none
            return
        try:
            names = sorted(name for name in os.listdir(releases) if not name.startswith("."))
        except OSError:
            return
        count = freed = 0
        for name in names:
            path = releases / name
            try:
                if not stat.S_ISDIR(os.lstat(path).st_mode) or Path(os.path.realpath(path)) == live:
                    continue
                size = _size(path)
                delete_tree(path)
            except OSError as exc:
                self.say(f"! {RELEASES / name} was not deleted ({exc.strerror or exc})")
                continue
            count += 1
            freed += size
        if count:
            plural = "s" if count != 1 else ""
            self.say(f"Deleted {count} release{plural} she does not run ({human_size(freed)} freed).")

    def _save_old(self, labels: list[str]) -> None:
        """Her old service files and log rule, kept for the rollback."""
        sources = [self.daemons / f"{label}.plist" for label in labels] + (
            [self.newsyslog] if self.newsyslog.exists() else []
        )
        for source in sources:
            target = self.saved / source.name

            def keep(source: Path = source, target: Path = target) -> None:
                self.saved.mkdir(parents=True, exist_ok=True, mode=0o755)
                shutil.copy2(source, target, follow_symlinks=False)
                target.chmod(0o644)

            self._do(f"keep {source} as {target}", keep)

    def _make_user(self) -> None:
        make_account(self.host, self.say, dry=self.dry)

    def _copy_data(self) -> None:
        """Her folders, read by you (tar as your user: root never follows your links) and written by her (tar as
        _estia), then private (folders 0700, files 0600). The code in vendor/ is left out: it goes into the
        release. An earlier copy in her home (after a rollback or a failed try) is kept aside, never deleted, by her
        own mkdir and mv: her home may hold links her account planted (.config to your ~/.config, .before-<time> to
        /), so root never makes, moves or writes anything there by path. Her chmod names only the folders just
        copied (the ones doctor checks). Her config is read and rewritten as her too."""
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        for _old, new in DATA_MAP:
            earlier = self.her_home / new
            if self.dry:
                try:
                    earlier.exists()
                except PermissionError:  # her home is hers alone after a first try
                    self.say(f"would keep any earlier copy of hers in {HOME}/.before-<time>")
                    break
                continue
            if os.path.lexists(earlier):
                aside = self.her_home / f".before-{stamp}" / new
                self._run([*self._as(ROLE_USER), MKDIR, "-p", str(aside.parent)])
                self._run([*self._as(ROLE_USER), MV, str(earlier), str(aside)])
                self.say(f"kept her earlier copy of {new} in {HOME}/.before-{stamp}")
        old_folders = self._old_folders()
        sources = [str(folder.relative_to(self.owner.home)) for folder in old_folders]
        if not sources:
            return
        news = [Path(new) for old, new in DATA_MAP if self.owner.home / old in old_folders]
        copied = [str(self.her_home / new) for new in news]
        on_the_way = sorted({str(self.her_home / up) for new in news for up in new.parents if up != Path(".")})
        reader = [
            *self._as(self.owner.name),
            TAR,
            "-c",
            "-f",
            "-",
            "-C",
            str(self.owner.home),
            "--exclude",
            VENDOR,
            *sources,
        ]
        writer = [*self._as(ROLE_USER), TAR, "-x", "-f", "-", "-C", str(self.her_home)]
        if self.dry:
            self.say(f"would copy {', '.join(f'~/{s}' for s in sources)} to {HOME} (as you, then as {ROLE_USER})")
        code, out = self.host.pipe(reader, writer, cwd=Path("/"))
        if code != 0:
            why = tail(out)
            raise RuntimeError("copying her data failed" + (f": {why}" if why else ""))
        self._run([*self._as(ROLE_USER), CHMOD, "-R", "go-rwx", *copied])
        self._run([*self._as(ROLE_USER), CHMOD, "go-rwx", *on_the_way])  # made by her tar: hers
        config = self.her_home / HER_CONFIG
        original = self._read_config(config)
        text = ""
        notes: list[str] = []
        if original is not None:
            vendor = self.host.path(CURRENT) / "vendor"
            avatar = self._copy_avatar(original)
            text, notes = config_notes(
                original, self.owner.home, vendor if vendor.is_dir() else None, avatar_copy=avatar
            )
            self.projects_root = her_projects_root(original, self.owner.home)
            self.calls = calls_in(original)
        if text and not self.dry:  # as her (tee): never root writing through a path in her home
            code, _ = self.host.run([*self._as(ROLE_USER), TEE, str(config)], input=text, cwd=Path("/"))
            if code != 0:
                raise RuntimeError(f"her config {HOME / HER_CONFIG} could not be rewritten (exit {code})")
        for note in notes:
            self.say(f"config: {note}")

    def _read_config(self, config: Path) -> str | None:
        """Her copy of your config (your tar made it for her), read as her; in a dry run (as you, nothing copied)
        your own. None when it cannot be read."""
        if self.dry:
            try:
                fd = os.open(self.owner.home / HER_CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, encoding="utf-8") as handle:
                    return handle.read()
            except (OSError, UnicodeDecodeError):
                return None
        try:  # a FIFO swapped in never hangs the move: cat is stopped
            code, out = self.host.run(
                [*self._as(ROLE_USER), CAT, str(config)], read_only=True, cwd=Path("/"), timeout=AVATAR_SECONDS
            )
        except ValueError:  # not UTF-8
            return None
        return out if code == 0 else None

    def _copy_avatar(self, text: str) -> Path | None:
        """Her picture (the config's `avatar`) when it is a regular file in your home (not a link), at most 5 MB:
        checked and read as you, written as her into her config folder, like her data (root never reads your paths).
        Its path in her home, or None (nothing copied: her config keeps the note)."""
        try:
            value = tomllib.loads(text).get("avatar")
        except tomllib.TOMLDecodeError:
            return None
        path = owner_path(value, self.owner.home) if isinstance(value, str) and value.strip() else None
        if path is None or ".." in path.parts or not path.is_relative_to(self.owner.home) or path == self.owner.home:
            return None
        code, out = self.host.run(  # as you: no link on the way out of your home (the last part is checked below)
            [*self._as(self.owner.name), REALPATH, str(self.owner.home), str(path.parent)], read_only=True
        )
        resolved = out.splitlines()
        if (
            code != 0
            or len(resolved) != 2
            or Path(resolved[1]) != Path(resolved[0]) / path.parent.relative_to(self.owner.home)
        ):
            return None
        suffix = path.suffix.lower() if path.suffix.lower() in AVATAR_TYPES else ""
        folder = Path(HER_CONFIG).parent
        target, shown = self.her_home / folder / f"avatar{suffix}", HOME / folder / f"avatar{suffix}"
        code, out = self.host.run([*self._as(self.owner.name), STAT, "-f", "%HT %z", str(path)], read_only=True)
        kind, _, size = out.strip().rpartition(" ")
        if code != 0 or kind != "Regular File" or not size.isdecimal() or int(size) > AVATAR_MAX_BYTES:
            return None  # missing, a link, not a file, or larger than 5 MB
        if self.dry:
            self.say(f"would copy her picture into {shown} (read as you, written as {ROLE_USER})")
            return None
        self.host.run([*self._as(ROLE_USER), RM, "-f", str(target)])  # never write through a link left there
        reader = [*self._as(self.owner.name), HEAD, "-c", str(AVATAR_MAX_BYTES + 1), str(path)]
        code, _ = self.host.pipe(
            reader, [*self._as(ROLE_USER), TEE, str(target)], cwd=Path("/"), timeout=AVATAR_SECONDS
        )
        try:
            info = os.lstat(target)
            copied = code == 0 and stat.S_ISREG(info.st_mode) and 0 < info.st_size <= AVATAR_MAX_BYTES
        except OSError:
            copied = False
        if not copied:  # it changed while it was read: nothing half-copied stays
            self.host.run([*self._as(ROLE_USER), RM, "-f", str(target)])
            return None
        if self.host.run([*self._as(ROLE_USER), CHMOD, "600", str(target)])[0] != 0:
            self.host.run([*self._as(ROLE_USER), RM, "-f", str(target)])
            return None
        return shown

    def _check_copy(self) -> None:
        """Her copy, walked again as written (lstat, nothing followed): a link out of her home, a FIFO, a socket or a
        device made while her release built stops the move before anything of hers starts from there."""
        if self.dry:
            return
        real = Path(os.path.realpath(self.her_home))
        found = [item for _old, new in DATA_MAP for item in strays(self.her_home / new, real)]
        if found:
            path, what = found[0]
            more = f" (and {len(found) - 1} more)" if len(found) > 1 else ""
            raise RuntimeError(f"{clean(str(HOME / path.relative_to(self.her_home)))}: {what} in her copy{more}")

    def _hold_old(self) -> None:
        """Her old folders with secrets leave your home for a folder only root reaches (a rename: nothing in them is
        re-owned or changed), kept for the rollback. The caches stay where they are."""
        for folder in self._old_folders(secrets_only=True):
            if self.mode == "rehearse":
                self.say(f"would move {folder} into {HELD} (left as it is in a rehearsal)")
                continue
            target = self.held / f"{int(self.clock())}-{len(self.held_map)}"  # never one an earlier move left
            if self.dry:
                self.say(f"would move {folder} into {HELD} (root only)")
                continue
            self.held.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.held.chmod(0o700)
            os.rename(folder, target)
            self.held_map.append((str(folder), str(target)))

    def _install_plists(self, voice: bool) -> None:
        """Both service files, from the root-run tools' templates, each checked before it is written."""
        templates = self.host.path(TOOLS) / "launchd"
        if not templates.is_dir():
            templates = self.host.path(CURRENT) / "launchd"
        if self.dry and not templates.is_dir():
            templates = self.checkout / "launchd"
        for label in [BRIDGE, *([VOICE] if voice else [])]:
            target = self.daemons / f"{label}.plist"

            def install(label: str = label, target: Path = target) -> None:
                text = render((templates / f"{label}.plist.in").read_text(encoding="utf-8"), plist_values(), plist=True)
                problem = plist_problem(plistlib.loads(text.encode("utf-8")), label)
                if problem:
                    raise ValueError(f"{label}.plist: {problem}")
                render_file(templates / f"{label}.plist.in", target, plist_values())

            self._do(f"install {target} (runs as {ROLE_USER} from {CURRENT})", install)
            self._run([CHOWN, "root:wheel", str(target)])
        self._do(
            f"make {STREAM_LOGS} (root's) and her services' output files in it", lambda: make_stream_logs(self.host)
        )

    def _unhold(self, held: Sequence[tuple[str, str]]) -> None:
        """The held folders back into your home, by rename (yours, as they were). A folder made there since is kept
        aside, never overwritten. Root reaches each one a name at a time from your home (`_owner_folder`), never
        through a link you made; one it cannot reach that way stays held and is printed with its command."""
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        for source_name, target_name in held:
            source, target = Path(source_name), Path(target_name)
            if not target.is_dir() or target.is_symlink():
                continue
            fd = self._owner_folder(source.parent)
            try:
                with contextlib.suppress(FileNotFoundError):
                    os.stat(source.name, dir_fd=fd, follow_symlinks=False)  # something there since: kept aside
                    aside = f"{source.name}.before-rollback-{stamp}"
                    os.rename(source.name, aside, src_dir_fd=fd, dst_dir_fd=fd)
                os.rename(target, source.name, dst_dir_fd=fd)
            finally:
                os.close(fd)

    def _owner_folder(self, folder: Path) -> int:
        """An open folder in your home, reached a name at a time with O_NOFOLLOW from your home itself: each one on
        the way a real folder of yours (one gone since is made again, yours, 0700). A link on the way, a folder not
        yours or a path outside your home raises: root never follows a link a program of yours put there."""
        home = self.owner.home
        if folder != home and not folder.is_relative_to(home):
            raise OSError(f"{folder} is not in your home")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        here = home
        try:
            fd = os.open(home, flags)
        except OSError as exc:
            raise OSError(f"{home}: not a folder of yours ({exc.strerror})") from exc
        try:
            for name in folder.relative_to(home).parts:
                if os.fstat(fd).st_uid != self.owner.uid:
                    raise OSError(f"{here} is not yours")
                here = here / name
                try:
                    child = os.open(name, flags, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(name, 0o700, dir_fd=fd)
                    child = os.open(name, flags, dir_fd=fd)
                    os.fchown(child, self.owner.uid, -1)
                except OSError as exc:
                    raise OSError(f"{here}: a link or no folder on the way, not followed ({exc.strerror})") from exc
                os.close(fd)
                fd = child
            if os.fstat(fd).st_uid != self.owner.uid:
                raise OSError(f"{here} is not yours")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _stale_journals(self, present: Sequence[str], owner: Owner) -> list[Path]:
        """The -wal and -shm files in your old folders next to a database her copy replaces: left by an unclean stop,
        they would be replayed into her newer database."""
        stale: list[Path] = []
        for rel in present:
            for here, _dirs, files in os.walk(self.her_home / rel):  # links are not followed
                for name in sorted(DATABASES.intersection(files)):
                    database = owner.home / (Path(here) / name).relative_to(self.her_home)
                    stale += [Path(f"{database}{end}") for end in JOURNALS if os.path.lexists(f"{database}{end}")]
        return stale

    def _present(self) -> list[str]:
        """Her data folders the copy back takes (those that are there)."""
        return [rel for rel in OVERLAY if (self.her_home / rel).is_dir()]

    def _overlay_problems(self, present: Sequence[str]) -> list[tuple[Path, str]]:
        """What in her newer data must never reach your folders, where your bridge runs: a program (an execute bit),
        a link out of her home, a FIFO or a device, as (path, what); her vendor/ is left out of the copy. Her data
        holds none of them: one found means her account planted it, or a program of yours put it in your folder
        before the move. Walked with lstat, nothing followed; a socket (a call's, left by a crash) is passed over:
        tar never copies one."""
        real = Path(os.path.realpath(self.her_home))
        vendor = self.her_home / VENDOR
        found: list[tuple[Path, str]] = []
        for rel in present:
            found += [
                (path, what) for path, what in strays(self.her_home / rel, real, skip=[vendor]) if what != "a socket"
            ]
            for here, dirs, files in os.walk(self.her_home / rel):  # links are not followed
                dirs[:] = [name for name in dirs if Path(here) / name != vendor]
                for name in files:
                    path = Path(here) / name
                    with contextlib.suppress(OSError):
                        info = path.lstat()
                        if stat.S_ISREG(info.st_mode) and info.st_mode & 0o111:
                            found.append((path, PROGRAM))
        return found

    def _in_her_home(self, path: Path) -> Path:
        """A path under her home as the Mac names it (/var/estia/…), whatever the root."""
        return HOME / path.relative_to(self.her_home)

    @staticmethod
    def _defuse_argv(path: Path, what: str) -> list[str]:
        """Her own fix for what her data never holds: a program's execute bit off, anything else deleted."""
        return [CHMOD, "a-x", "--", str(path)] if what == PROGRAM else [RM, "--", str(path)]

    def _defuse(self, present: Sequence[str]) -> None:
        """A failed move after she ran: what her data never holds is made harmless as her (her own files, so nothing
        of yours is reached), each named, so the copy back can bring her Signal sessions with your old services."""
        for path, what in self._overlay_problems(present):
            code, out = self.host.run([*self._as(ROLE_USER), *self._defuse_argv(path, what)], cwd=Path("/"))
            done = "its execute bit taken off" if what == PROGRAM else "deleted"
            result = f"{done} as her" if code == 0 else f"not changed ({tail(out)})"
            self.say(f"! {clean(str(self._in_her_home(path)))}: {what}; {result} before her newer data was copied back")

    def _overlay(self, owner: Owner) -> None:
        """What she saved since the move (her keys, messages and Signal sessions) on top of your old folders, read as
        her and written as you; her config stays the one from before the move. The old -wal and -shm next to each
        database she replaces are removed first (as you), so SQLite never replays them into her newer one. Anything
        that stops the copy raises: your old services must never run on your old Signal sessions alone."""
        present = self._present()
        if not present:
            return
        problems = self._overlay_problems(present)
        if problems:
            path, what = problems[0]
            raise RuntimeError(f"her newer data was not copied back ({clean(str(self._in_her_home(path)))}: {what})")
        stale = self._stale_journals(present, owner)
        if stale:
            code, out = self.host.run([*self._as(owner.name), RM, "-f", "--", *map(str, stale)], cwd=Path("/"))
            if code != 0:
                raise RuntimeError(
                    f"her newer data was not copied back: old database journals in your folders could not be "
                    f"removed ({tail(out)})"
                )
        reader = [
            *self._as(ROLE_USER),
            TAR,
            "-c",
            "-f",
            "-",
            "-C",
            str(self.her_home),
            "--exclude",
            HER_CONFIG,
            "--exclude",
            VENDOR,  # code, never data (the move leaves it out too): your bridge runs what is there
            *present,
        ]
        writer = [*self._as(owner.name), TAR, "-x", "-f", "-", "-C", str(owner.home)]
        code, out = self.host.pipe(reader, writer, cwd=Path("/"))
        if code != 0:
            raise RuntimeError(f"her newer data was not copied back ({tail(out)})")

    def _restore(self, labels: list[str]) -> None:
        """Back to her old setup after a failed move: the new services out, the old folders and files back, with what
        she saved meanwhile when the new bridge ran. Hang-ups, TERMs and Ctrl-Cs are ignored meanwhile; her old service
        files go back and her old services start even when a step before failed, and a folder still held is printed
        with the command that puts it back."""
        with interruptions_ignored():
            ran = bool(self.started)
            held = list(self.held_map)
            why = ""
            try:
                self._stop(list(self.started))
                self.started.clear()
                self._remove_new_plists(labels)
                if self.mode != "rehearse" and not self.dry:
                    if ran:  # before your folders are back: her own fix, as her
                        self._defuse(self._present())
                    self._unhold(held)
                    if ran:
                        self._overlay(self.owner)
                    self.held_map.clear()
            except Exception as exc:
                why = str(exc) or type(exc).__name__
            finally:
                problems = self._bring_back(labels)
            self._left_over(why, problems, held if self.mode != "rehearse" and not self.dry else [])

    def _remove_new_plists(self, labels: list[str]) -> None:
        """The service files of a failed move that were not her old setup (a fresh install's, a failed try's) are
        booted out and deleted: a file left would say she moved and start her at the next boot from a half-made
        copy. Her old setup's files are put back by `_bring_back`."""
        for label in LABELS:
            if label in labels or not self._installed(label):
                continue
            target = self.daemons / f"{label}.plist"
            self._stop([label])
            self._do(f"remove {DAEMONS / target.name}", functools.partial(target.unlink, missing_ok=True))

    def _bring_back(self, labels: list[str]) -> list[str]:
        """Her old service files and log rule put back, then her old services started: each step on its own, so one
        that fails never stops the next. What failed, as text."""
        problems: list[str] = []
        for label in labels:
            saved = self.saved / f"{label}.plist"
            if saved.exists():
                try:
                    self._do(f"put back {label}", functools.partial(self._put_back, saved, label))
                except Exception as exc:
                    problems.append(f"{DAEMONS / saved.name} was not put back ({exc})")
        saved_rule = self.saved / NEWSYSLOG.name
        if saved_rule.exists() and not self.newsyslog.exists():
            try:
                self._do(f"put back {NEWSYSLOG}", lambda: shutil.copy2(saved_rule, self.newsyslog))
            except Exception as exc:
                problems.append(f"{NEWSYSLOG} was not put back ({exc})")
        for label in labels:
            self.host.run([LAUNCHCTL, "bootstrap", "system", str(DAEMONS / f"{label}.plist")])
        return problems

    def _left_over(self, why: str, problems: Sequence[str], held: Sequence[tuple[str, str]]) -> bool:
        """What the restore could not do, said plainly: each folder still held as the exact commands that put it back.
        Root moves it straight into your home under a fresh name (`mv -h`: no folder on the way, and a link at that
        name is never followed), then you move it into place without admin rights (it is yours already); a folder made
        in its place since is renamed aside first. True: nothing is left over."""
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        lines: list[str] = []
        for source, target in held:
            if not os.path.lexists(target):
                continue
            fresh = shlex.quote(str(self._restore_name(Path(source), stamp)))
            lines.append(f"{SUDO} {MV} -h {shlex.quote(target)} {fresh}")  # every program by its full path
            if os.path.lexists(source):
                lines.append(f"{MV} {shlex.quote(source)} {shlex.quote(f'{source}.before-rollback-{stamp}')}")
            lines.append(f"{MKDIR} -p {shlex.quote(str(Path(source).parent))} && {MV} {fresh} {shlex.quote(source)}")
        if not (why or problems or lines):
            return True
        for problem in [why, *problems] if why else problems:
            self.say(f"✗ {problem}")
        if lines:
            self.say(
                f"Her old folders still held in {HELD} (root only); put each back in your Terminal (root moves it "
                "straight into your home, then you move it into place, no admin rights):"
            )
            for line in lines:
                self.say(f"  {line}")
        return False

    def _restore_name(self, source: Path, stamp: str) -> Path:
        """A fresh name directly in your home for a held folder on its way back (no folder in between to follow)."""
        home = self.owner.home
        rel = source.relative_to(home) if source.is_relative_to(home) else Path(source.name)
        slug = "-".join(part.lstrip(".").replace(" ", "-") for part in rel.parts)
        return home / f"estia-restore-{slug}-{stamp}"

    def _start_again(self, labels: Sequence[str]) -> list[str]:
        """Her own services started again after a rollback that stopped short: never your old services on your old
        Signal sessions alone."""
        for label in labels:
            self.host.run([LAUNCHCTL, "bootstrap", "system", str(DAEMONS / f"{label}.plist")])
        return []

    def _put_back(self, saved: Path, label: str) -> None:
        target = self.daemons / f"{label}.plist"
        tmp = target.with_name(f".{target.name}.back")
        shutil.copy2(saved, tmp, follow_symlinks=False)
        tmp.chmod(0o644)
        os.replace(tmp, target)

    def _write_marker(self, voice: bool, fresh: bool) -> None:
        data = {
            "owner": self.owner.name,
            "home": str(self.owner.home),
            "moved_at": self.clock(),
            "held": [list(pair) for pair in self.held_map],
            "voice": voice,
            "fresh": fresh,
            "rolled_back": False,
            "removed": False,
            "projects_root": str(self.projects_root) if self.projects_root is not None else None,
        }
        self._do(f"note the move in {MARKER}", lambda: _write_json(self.marker, data))

    def _remove_held(self) -> None:
        if self.held.is_dir():
            shutil.rmtree(self.held)

    # ---------------------------------------------------------------- back, and the old copies

    def _marker_owner(self, marker: Mapping[str, Any]) -> Owner | None:
        name, home = str(marker.get("owner", "")), str(marker.get("home", ""))
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            return self.owner if (self.owner.name, str(self.owner.home)) == (name, home) else None
        return Owner(entry.pw_name, Path(home) if home else Path(entry.pw_dir), entry.pw_uid)

    def rollback(self) -> int:
        marker = read_marker(self.root)
        if marker is None or not held_copy(marker):
            self.say("✗ Nothing to roll back to: no earlier setup is on record.")
            return 1
        until = float(marker.get("moved_at", 0)) + ROLLBACK_DAYS * 86400
        if self.clock() > until:
            day = time.strftime("%Y-%m-%d", time.localtime(until))
            self.say(f"✗ The rollback window ended on {day}.")
            return 1
        owner = self._marker_owner(marker)
        if owner is None:
            self.say(f"✗ The account the move came from ({marker.get('owner')}) is gone.")
            return 1
        held = [
            (str(pair[0]), str(pair[1]))
            for pair in marker.get("held", [])
            if isinstance(pair, list) and len(pair) == 2 and Path(str(pair[1])).is_relative_to(self.held)
        ]
        labels = [label for label in LABELS if (self.saved / f"{label}.plist").exists()]
        problems = self._overlay_problems(self._present())
        if problems:  # found before anything stops: she keeps running as her own user
            self.say(
                f"✗ Nothing changed: she keeps running as {ROLE_USER}. Her newer data holds what must never reach your "
                "folders, where your bridge would run, and your old services never start without it (your old Signal "
                "sessions alone would break decryption):"
            )
            for path, what in problems:  # names her account chose: cleaned for root's terminal
                self.say(f"  {clean(str(self._in_her_home(path)))}: {what}")
            self.say(
                f"Her account, or a program of yours before the move, put it there; if you did not, follow SECURITY.md, "
                f'"If the Mac is compromised". Otherwise fix each as her, then run {ESTIA} rollback again:'
            )
            for path, what in problems:
                fix = [SUDO, "-u", ROLE_USER, *self._defuse_argv(self._in_her_home(path), what)]
                self.say(f"  {clean(shlex.join(fix))}")
            return 1
        stopped = [label for label in LABELS if self._installed(label)]
        with interruptions_ignored():  # a hang-up or a Ctrl-C never leaves her half way back
            why = ""
            copied = False
            try:
                self._stop(stopped)
                self.started = []
                if not self.dry:
                    self._unhold(held)
                self._do(
                    f"copy what she saved since the move from {HOME} back into your folders",
                    lambda: self._overlay(owner),
                )
                copied = True
            except Exception as exc:
                why = str(exc) or type(exc).__name__
            finally:  # your old services only with what she saved since: else hers again
                problems_left = self._bring_back(labels) if copied else self._start_again(stopped)
            if not copied:
                self._left_over(why, problems_left, [] if self.dry else held)
                self.say(
                    f"She runs as {ROLE_USER} again: your old services never start without what she saved since "
                    f"(Signal sessions, keys). Fix what this names, then run {ESTIA} rollback again."
                )
                return 1
            if not self._left_over(why, problems_left, [] if self.dry else held):
                self.say(f"Then run {ESTIA} rollback again: it copies what she saved since the move.")
                return 1
        marker["rolled_back"] = True
        marker["rolled_back_at"] = self.clock()
        self._do(f"note the rollback in {MARKER}", lambda: _write_json(self.marker, marker))
        self.say(
            f"✓ She runs as {owner.name} again, from your folders, with what she saved since the move (messages, "
            f"Signal sessions, keys). Her config is the one from before the move; changes made to hers since stay in "
            f"{HOME}/{HER_CONFIG}. {ROLE_USER}, {HOME} and {CODE} are kept; remove them by hand when sure."
        )
        return 0

    def remove_old(self) -> int:
        """Her old folders held for the rollback (when on record), and the earlier copies of hers a retried move kept
        aside in her home (kept_aside), each named."""
        marker = read_marker(self.root)
        held = held_copy(marker)
        copies = kept_aside(self.her_home)
        if not (held or copies):  # the copies go even without a move on record (every try failed)
            self.say("✗ No old copies on record.")
            return 1
        if held and marker is not None:
            self._do(f"delete {HELD} (her old folders, root only)", self._remove_held)
            marker["removed"] = True
            self._do(f"note it in {MARKER}", lambda: _write_json(self.marker, marker))
        failed = False
        for copy in copies:
            shown = HOME / copy.name
            try:
                self._do(f"delete {shown} (an earlier copy of hers, kept aside)", functools.partial(delete_tree, copy))
            except OSError as exc:
                failed = True
                self.say(f"✗ {shown} was not deleted ({exc.strerror or exc})")
                continue
            if not self.dry:
                self.say(f"deleted {shown}")
        if self.dry:
            self.say("Nothing changed (a dry run).")
        elif held:
            self.say("✓ Her old folders are gone; no rollback from here on.")
        elif not failed:
            self.say("✓ Her earlier copies are gone.")
        return 1 if failed else 0


def _write_json(path: Path, data: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, path)


def _owner() -> Owner:
    if os.geteuid() == 0:
        name = os.environ.get("SUDO_USER", "")
        if not name or name == "root":
            raise SystemExit(f"run it from your own account: {MIGRATE} (it asks for sudo itself)")
        entry = pwd.getpwnam(name)
        return Owner(entry.pw_name, Path(entry.pw_dir), entry.pw_uid)
    entry = pwd.getpwuid(
        os.getuid()
    )  # yourself: your home as $HOME says (a test or a rehearsal may point it elsewhere)
    return Owner(entry.pw_name, Path(os.environ.get("HOME") or entry.pw_dir), entry.pw_uid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ownuser", description="Her own user: the move, and the plist renderer.")
    sub = parser.add_subparsers(dest="command", required=True)
    move = sub.add_parser("migrate", help="move her install into her own user")
    move.add_argument("--checkout", type=Path)
    group = move.add_mutually_exclusive_group()
    for flag in ("--dry-run", "--rehearse", "--rollback", "--remove-old"):
        group.add_argument(flag, action="store_true")
    sub.add_parser("account", help="her account again: no shell, hidden, no password (as root)")
    render_cmd = sub.add_parser("render", help="fill in a launchd template")
    render_cmd.add_argument("template", type=Path)
    render_cmd.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "render":
        values = plist_values()
        for key in ("RUN_USER", "RUN_GROUP", "RUN_HOME", "CODE", "LOG_DIR"):
            values[key] = os.environ.get(f"LA_{key}") or values[key]
        try:
            render_file(args.template, args.out, values)
        except (OSError, ValueError) as exc:
            print(f"{args.template}: {exc}", file=sys.stderr)
            return 1
        return 0
    root = estia_root()
    if args.command == "account":
        if os.geteuid() != 0 and root == Path("/"):
            print(f"run it with sudo: {SUDO} {BIN}/estia account", file=sys.stderr)
            return 2
        try:
            make_account(Host(root), print)
        except (RuntimeError, OSError) as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        print(f"✓ {ROLE_USER}: no shell, hidden, no password. Check: {ESTIA} doctor")
        return 0
    mode = "dry" if args.dry_run else "rehearse" if args.rehearse else "real"
    if mode == "rehearse" and root == Path("/"):
        print("a rehearsal needs a target folder: ESTIA_ROOT=/tmp/estia-rehearsal", file=sys.stderr)
        return 2
    if mode == "real" and os.geteuid() != 0 and root == Path("/"):
        print(f"run it with sudo (or use --dry-run or --rehearse): {MIGRATE}", file=sys.stderr)
        return 2
    if args.checkout is None and not (args.rollback or args.remove_old):
        print("the move needs --checkout <your checkout>", file=sys.stderr)
        return 2
    host = Host(root, dry_run=mode == "dry")
    checkout = args.checkout.resolve() if args.checkout else Path("/")
    migration = Migration(host, _owner(), checkout=checkout, mode=mode)
    if args.rollback:
        return migration.rollback()
    if args.remove_old:
        return migration.remove_old()
    return migration.run()


if __name__ == "__main__":
    raise SystemExit(main())
