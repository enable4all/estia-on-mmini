"""Ollama's official build: the one setup keeps or installs, and the one doctor expects (owner, 15 Oct: "ollama we keep
use only the official build").

Why: Homebrew's Ollama has no `libollama_xgrammar.dylib`, so Ollama's engine for Apple silicon (MLX) gives no
JSON-schema output, which she needs; Ollama's own build ships it next to the program, in `mlx_metal_v3` and
`mlx_metal_v4`. The Ollama app holds the same files, but updates itself every hour, starts only when someone logs in
on the Mac's screen, and stops every other `ollama serve` when its own server cannot start.

A signature counts only when Apple's Developer ID chain ends in Ollama's team (`REQUIREMENT`, `codesign -R`): a team
ID alone is a name anyone can put in an ad-hoc signature.

`Install.run`: an Ollama that already answers is kept when its program and every library and metallib in its folder
are signed by Ollama's developer ID (`TEAM_ID`), its version is `MIN_VERSION` or later, both xgrammar libraries sit
next to it and it listens only on this Mac; Homebrew's, the app's or any other stops setup with what is wrong and the
fix (the person runs it), as does a launchd job of Homebrew's or the app's that starts Ollama at a login. Setup's own
service running an older or newer version than the pin is updated in place: the new release downloaded and checked
while the old one runs, `current` switched, the service started again, and switched back when the new one does not
answer. Nothing answering: another launchd job that runs Ollama stops setup (two always-on services would fight for
the port); else the pinned release (`RELEASE`) is downloaded from GitHub into a new, empty folder, checked (its sha256;
every program's, library's and metallib's signature; Apple's notarization of `ollama`; both xgrammar libraries), and
only then moved to `~/opt/ollama/v<version>`, with `~/opt/ollama/current` pointing at it (no admin rights). A
LaunchDaemon (`LABEL`, the system domain) then runs it as the owner from boot, on 127.0.0.1 only: every command names
its tool by full path, the service file on tee's input (never a file of the owner's that a program could swap
meanwhile), the folders on the way to the program the owner's alone, and its log the owner's, 0600, made by root
before launchd opens it.

Read-only checks (the listening program, its signature, its libraries, the launchd jobs) run in a dry run too; every
change is printed instead, and what would stop setup is printed with the rest after it. Off the live Mac (a test's
ESTIA_ROOT) nothing is read or run: no Ollama answers and every change is printed.
"""

from __future__ import annotations

import argparse
import contextlib
import grp
import hashlib
import itertools
import json
import os
import plistlib
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_assistant import prompts
from local_assistant.hostsec import (
    CHMOD,
    CHOWN,
    CODESIGN,
    INSTALL,
    LAUNCHCTL,
    LSOF,
    NETSTAT,
    PS,
    SUDO,
    TAR,
    Host,
    estia_root,
)
from local_assistant.ownuser import STREAM_LOGS

CURL = "/usr/bin/curl"
SPCTL = "/usr/sbin/spctl"
TEE = "/usr/bin/tee"
TEAM_ID = "3MU9H2V9Y9"  # Developer ID Application: Infra Technologies, Inc (Ollama)
# Apple's Developer ID chain (Apple's root, the Developer ID authority, a Developer ID Application leaf) with Ollama's
# team as the leaf's OU: `codesign --verify --strict -R` refuses an ad-hoc signature that only claims the team ID
REQUIREMENT = (
    "=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate "
    f'leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "{TEAM_ID}"'
)
REQUIREMENT_NOT_MET = 3  # codesign's exit code for a valid signature that fails the requirement
MIN_VERSION = (0, 33, 1)  # v0.33.1: JSON-schema output on the MLX engine ("mlxrunner: add structured output support")
MIN_TEXT = ".".join(str(part) for part in MIN_VERSION)


@dataclass(frozen=True)
class Release:
    """One release of Ollama's macOS tarball, pinned by its sha256 (GitHub's asset digest)."""

    version: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://github.com/ollama/ollama/releases/download/v{self.version}/ollama-darwin.tgz"


# the only version tested live (15 Oct: 60.2 tok/s, tools and JSON fine); a new one comes with a commit that changes
# this pin, after the smoke test and the Gemma test set pass on it
RELEASE = Release("0.40.4", "a4ceae4b2aaf18df37ebcbfb56ba3a37a87838631112d66c2be07268693c8d58")
RELEASE_MB = 168  # ollama-darwin.tgz v0.40.4: 167.9 MB
LABEL = "com.local-assistant.ollama"
PORT = 11434
BASE = f"http://127.0.0.1:{PORT}"
DAEMON = Path("/Library/LaunchDaemons") / f"{LABEL}.plist"
LOG_DIR = STREAM_LOGS  # root's: launchd opens the log as root, no link followed
LOG = LOG_DIR / "ollama.log"
LOG_CAP = 10 * 1024 * 1024  # setup empties a larger log of yours (launchd appends: writing goes on at the new end)
LAUNCH_DAEMONS = Path("/Library/LaunchDaemons")
PLIST_BYTES = 1_000_000
XGRAMMAR = ("mlx_metal_v3/libollama_xgrammar.dylib", "mlx_metal_v4/libollama_xgrammar.dylib")
CELLARS = (Path("/opt/homebrew/Cellar"), Path("/usr/local/Cellar"))  # Homebrew on Apple silicon, and on Intel
APP = Path("/Applications/Ollama.app")
KEEP_ALIVE = "120m"  # the tested setup's (local-llm, with its watchdog: none here, see the docs' known gaps)
WAIT_SECONDS = 60
PATH_LINE = 'export PATH="$HOME/opt/ollama/current:$PATH"'
_SIGNED_SUFFIXES = (".dylib", ".so", ".metallib")
_MACHO = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
}
# a listening socket in `netstat -anv -p tcp`: its local address (the port after the last dot), then its process:pid
_LISTEN = re.compile(r"^tcp[46]*\s+\d+\s+\d+\s+(\S+)\s+\S+\s+LISTEN\s+(?:\d+\s+){4}(.+?):(\d+)\s")
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """An Ollama version as three numbers ("0.40.4-rc1" is 0.40.4); None when it is not one."""
    found = _VERSION.match(text.strip())
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def team_of(codesign: str) -> str:
    """The TeamIdentifier `codesign -dv` names ("" for none: unsigned, ad-hoc or Apple's own). Only a name: the
    requirement (`REQUIREMENT`) is what proves it."""
    for line in codesign.splitlines():
        if line.startswith("TeamIdentifier="):
            value = line.removeprefix("TeamIdentifier=").strip()
            return "" if value == "not set" else value
    return ""


def signature_problem(read: Callable[[Sequence[str]], tuple[int, str]], path: str) -> str:
    """Why a file is not signed by Ollama's developer ID ("" when it is): the requirement first (Apple's Developer ID
    chain with Ollama's team), then `codesign -dv` only to name the team in the reason."""
    code, _ = read([CODESIGN, "--verify", "--strict", "-R", REQUIREMENT, path])
    if code == 0:
        return ""
    if code != REQUIREMENT_NOT_MET:
        return prompts.OLLAMA_WHY_UNSIGNED
    _, shown = read([CODESIGN, "-dv", path])
    team = team_of(shown)
    if team != TEAM_ID:
        return prompts.OLLAMA_WHY_TEAM.format(team=team or "no developer ID", want=TEAM_ID)
    return prompts.OLLAMA_WHY_NOT_APPLE


def owner_home() -> Path:
    """Your home from the user database, never $HOME: root's service file runs the program under it at every start."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def homebrews(program: Path) -> bool:
    return any(program.is_relative_to(cellar) for cellar in CELLARS)


def app_of(program: Path) -> Path | None:
    """The .app bundle a program runs from (the Ollama app's server is Ollama.app/Contents/Resources/ollama)."""
    return next((folder for folder in (program, *program.parents) if folder.suffix == ".app"), None)


def libraries_in(folder: Path) -> tuple[list[str], list[str]]:
    """The official build's layout, the one definition doctor and setup share: both xgrammar libraries as files in
    the program's own folder, as Ollama's tarball has them (`XGRAMMAR`). Returns the ones not there, and the ones that
    could not be looked at (a folder closed to the account that asks)."""
    missing: list[str] = []
    unread: list[str] = []
    for library in XGRAMMAR:
        try:
            info = os.stat(folder / library)
        except (FileNotFoundError, NotADirectoryError):
            missing.append(library)
            continue
        except OSError:
            unread.append(library)
            continue
        if not stat.S_ISREG(info.st_mode):
            missing.append(library)
    return missing, unread


def missing_libraries(program: str) -> tuple[list[str], bool]:
    """The xgrammar libraries not next to the program (`libraries_in`), and whether one could not be read: a folder
    closed to the account doctor runs as is never "missing"."""
    missing, unread = libraries_in(Path(program).parent)
    return missing, bool(unread)


# another program or account on Ollama's port: (its program, "" when not found; its uid, None when not read)
Other = tuple[str, int | None]


@dataclass(frozen=True)
class Listener:
    """The program listening on Ollama's port ("" when none is found), the local addresses on that port, as netstat
    shows them ("127.0.0.1.11434", "::1.11434", "*.11434"), the account that runs it (its uid; None: not read), and
    every other program or account that listens on the port too (127.0.0.1 and ::1 are two addresses: one Ollama on
    each)."""

    program: str
    addresses: tuple[str, ...] = ()
    uid: int | None = None
    others: tuple[Other, ...] = ()


def other_machines(addresses: Sequence[str], port: int = PORT) -> list[str]:
    """The addresses other machines can reach: all but 127.0.0.1 and ::1 (this Mac only)."""
    return [address for address in addresses if address not in (f"127.0.0.1.{port}", f"::1.{port}")]


@dataclass(frozen=True)
class Found:
    """The Ollama that answers: its program ("" when not found), its version (None: not read), why its signature is
    not Ollama's ("" when it is; None: not checked, as doctor does not), where it listens (None: not read), the
    account that runs it (its uid; None: not read) and any other program or account on its port (`Listener.others`)."""

    program: str
    version: str | None
    signature: str | None = None
    addresses: tuple[str, ...] | None = None
    uid: int | None = None
    others: tuple[Other, ...] = ()


def account_name(uid: int) -> str:
    """An account's short name ("uid 503" when the user database has none)."""
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid}"


def problem(found: Found, owner: int | None = None) -> tuple[str, str] | None:
    """What makes an Ollama that answers one setup cannot keep, and its fix; None when it is the official build.
    `owner`: your account's uid; an Ollama another account runs gets her prompts, so that account controls her
    answers (not checked when either is unknown)."""
    if not found.program:
        return prompts.OLLAMA_NOT_FOUND, prompts.OLLAMA_NOT_FOUND_FIX
    program = Path(found.program)
    if homebrews(program):
        return prompts.OLLAMA_HOMEBREW.format(program=program), prompts.OLLAMA_HOMEBREW_FIX
    app = app_of(program)
    if app is not None:
        return prompts.OLLAMA_APP.format(state="answers", path=app), prompts.OLLAMA_APP_FIX.format(path=app)
    reasons: list[str] = []
    if found.signature:
        reasons.append(found.signature)
    if found.version is not None:
        parsed = parse_version(found.version)
        if parsed is None or parsed < MIN_VERSION:
            reasons.append(prompts.OLLAMA_WHY_OLD.format(version=found.version or "unknown", needed=MIN_TEXT))
    missing, _unread = missing_libraries(found.program)
    reasons += [prompts.OLLAMA_WHY_LIBRARY.format(library=library) for library in missing]
    reachable = other_machines(found.addresses or ())
    if reachable:
        reasons.append(prompts.OLLAMA_WHY_OPEN.format(addresses=", ".join(reachable), port=PORT))
    if found.uid is not None and owner is not None and found.uid != owner:
        reasons.append(prompts.OLLAMA_WHY_ACCOUNT.format(account=account_name(found.uid)))
    for other, uid in found.others:
        reasons.append(
            prompts.OLLAMA_WHY_SHARED.format(
                program=other or prompts.OLLAMA_SHARED_NOT_FOUND,
                account=prompts.OLLAMA_SHARED_UNREAD if uid is None else account_name(uid),
            )
        )
    if reasons:
        return prompts.OLLAMA_OTHER.format(program=program, why="; ".join(reasons)), prompts.OLLAMA_OTHER_FIX
    return None


def someone_else_runs(found: Found, owner: int | None) -> bool:
    """Another account runs the Ollama that answers, or another program or account listens on its port too: that
    account controls her answers (doctor's ✗)."""
    return bool(found.others) or (found.uid is not None and owner is not None and found.uid != owner)


def endangers(found: Found, owner: int | None) -> bool:
    """Whether her prompts may reach someone other than you: its program not found, another account runs it or its
    account cannot be read, another program or account on its port too, other machines reach it, or (not Homebrew's
    or the app's, whose own signatures are named apart) its signature does not check out. These stop a re-run of
    setup too; Homebrew's, the app's and the other jobs are a "!" there."""
    if not found.program or found.uid is None or someone_else_runs(found, owner):
        return True
    if other_machines(found.addresses or ()):
        return True
    program = Path(found.program)
    return bool(found.signature) and not homebrews(program) and app_of(program) is None


def stopped(text: str, fix: str) -> str:
    """Setup's last words when it stops at Ollama: what is wrong, then the fix the person runs."""
    return f"✗ Ollama: {text}\n  Fix: {fix}"


class Refusal(SystemExit):
    """The Ollama step refuses what it found (`Install.refuse`): what is wrong and the fix. A first install stops
    there; a re-run of setup where she is set up already shows it as a "!" (`warned`) and goes on, unless it is
    `fatal`: an Ollama that someone other than you may control (`endangers`) stops every run."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


def warned(message: str) -> str:
    """A refusal's words as a "!" line: a re-run of setup goes on after it."""
    return f"! Ollama:{message.removeprefix('✗ Ollama:')}" if message.startswith("✗ Ollama:") else message


@dataclass(frozen=True)
class Job:
    """A launchd job that runs a program named ollama: its label, that program, its file (as shown), whether it is
    a LaunchDaemon (the system domain, from boot) or your LaunchAgent (your login's domain), and whether it starts
    Ollama through another program (a shell's -c, env), whose own words name the program as typed."""

    label: str
    program: str
    plist: Path
    system: bool
    wrapped: bool = False


def read_plist_bytes(path: Path) -> bytes | None:
    """A launchd job file's bytes: a regular file read without following a link, at most PLIST_BYTES; None
    otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with open(fd, "rb", closefd=False) as handle:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            data = handle.read(PLIST_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= PLIST_BYTES else None


def read_plist(path: Path) -> dict[str, Any] | None:
    """A launchd job file (`read_plist_bytes`), parsed; None when it cannot be read or is not a dictionary."""
    data = read_plist_bytes(path)
    if data is None:
        return None
    try:
        loaded = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, TypeError, OverflowError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _is_ollama(word: str) -> bool:
    return Path(word).name.lower() == "ollama"


def _serve_in(command: str) -> str:
    """The program a shell command starts as `ollama serve` (a word named ollama, then serve), as typed; "" for
    none. A command the shell's rules cannot split (an open quote) is split at its spaces and separators."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        words = [word for word in re.split(r"[\s;&|()]+", command) if word]
    return next((word for word, after in itertools.pairwise(words) if _is_ollama(word) and after == "serve"), "")


def started_ollama(service: Mapping[str, Any]) -> tuple[str, bool] | None:
    """The ollama program a launchd job starts, and whether through another program: its `Program` or first
    argument named ollama; else a later argument named ollama (`/usr/bin/env ollama serve`); else `ollama serve`
    inside an argument (a shell's -c). None when it starts none."""
    arguments = service.get("ProgramArguments")
    words = [word for word in arguments if isinstance(word, str)] if isinstance(arguments, list) else []
    first = service.get("Program") or (words[0] if words else "")
    if isinstance(first, str) and _is_ollama(first):
        return first, False
    for word in words[1:]:
        if _is_ollama(word):
            return word, True
    for word in words:
        if found := _serve_in(word):
            return found, True
    return None


def ollama_jobs(root: Path, agents: Path | None = None) -> list[Job]:
    """Every launchd job file that runs a program named ollama, directly or through another program
    (`started_ollama`): the LaunchDaemons under `root`, then your LaunchAgents in `agents`. Read-only, no link
    followed; a file that cannot be read is passed over."""
    jobs: list[Job] = []
    places = [(root / LAUNCH_DAEMONS.relative_to("/"), LAUNCH_DAEMONS, True)]
    if agents is not None:
        places.append((agents, agents, False))
    for folder, shown, system in places:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            service = read_plist(folder / name) if name.endswith(".plist") else None
            if service is None:
                continue
            started = started_ollama(service)
            if started is None:
                continue
            label = service.get("Label")
            program, wrapped = started
            jobs.append(
                Job(
                    label if isinstance(label, str) else name.removesuffix(".plist"),
                    program,
                    shown / name,
                    system,
                    wrapped,
                )
            )
    return jobs


def restart_fix(job: Job) -> str:
    """The commands that start a job again: kickstart, and bootstrap for one launchd does not have loaded."""
    if job.system:
        return prompts.OLLAMA_RESTART_SYSTEM.format(label=job.label, plist=job.plist)
    return prompts.OLLAMA_RESTART_AGENT.format(uid=os.getuid(), label=job.label, plist=job.plist)


def job_kind(job: Job, real: str) -> str:
    """Whose Ollama a job starts: "homebrew" (`brew services`), "app" (the Ollama app's) or "other"; `real` is its
    program with every link resolved. A job that starts it through another program is someone's own: "other"
    (`brew services stop` would not remove it), unless it is brew services' own file."""
    if job.label.startswith("homebrew.mxcl."):
        return "homebrew"
    if job.wrapped:
        return "other"
    if homebrews(Path(real)):
        return "homebrew"
    return "app" if app_of(Path(real)) is not None else "other"


def job_problem(job: Job, real: str) -> tuple[str, str]:
    """A job that runs Ollama and stops setup, with its fix: Homebrew's, the app's, or another."""
    kind = job_kind(job, real)
    if kind == "homebrew":
        return prompts.OLLAMA_JOB_HOMEBREW.format(plist=job.plist), prompts.OLLAMA_HOMEBREW_FIX
    app = app_of(Path(real))
    if kind == "app" and app is not None:
        return prompts.OLLAMA_JOB_APP.format(plist=job.plist), prompts.OLLAMA_APP_FIX.format(path=app)
    return (
        prompts.OLLAMA_JOB.format(label=job.label, plist=job.plist),
        prompts.OLLAMA_JOB_FIX.format(restart=restart_fix(job)),
    )


def daemon_plist(*, user: str, group: str, home: Path, num_ctx: int) -> bytes:
    """The LaunchDaemon: Ollama's official build from ~/opt/ollama/current, as you, from boot, on 127.0.0.1 only; one
    model loaded, one request at a time; its output in root's /var/log/estia."""
    service: dict[str, Any] = {
        "Label": LABEL,
        "UserName": user,
        "GroupName": group,
        "ProgramArguments": [str(home / "opt/ollama/current/ollama"), "serve"],
        "EnvironmentVariables": {
            "HOME": str(home),
            "OLLAMA_HOST": f"127.0.0.1:{PORT}",
            "OLLAMA_MODELS": str(home / ".ollama/models"),
            "OLLAMA_KEEP_ALIVE": KEEP_ALIVE,
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_CONTEXT_LENGTH": str(num_ctx),
        },
        "WorkingDirectory": str(home),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    return plistlib.dumps(service)


def service_file_changed(root: Path, num_ctx: int) -> bool | None:
    """Whether setup's own service file differs from the one setup writes now (`daemon_plist`) for the account it
    runs as (its UserName, its home and group from the user database); None when there is none or it cannot be read.
    Read-only, no link followed: doctor's question, as you or as her (the file is every account's to read)."""
    path = root / DAEMON.relative_to("/")
    data = read_plist_bytes(path)
    service = read_plist(path) if data is not None else None
    if data is None or service is None:
        return None
    user = service.get("UserName")
    try:
        account = pwd.getpwnam(user) if isinstance(user, str) else None
        group = grp.getgrgid(account.pw_gid).gr_name if account is not None else ""
    except KeyError:
        return True  # an account the user database does not know: never one setup writes
    if account is None or not isinstance(user, str):
        return True
    return data != daemon_plist(user=user, group=group, home=Path(account.pw_dir), num_ctx=num_ctx)


def login_profile(home: Path, shell: str) -> Path:
    """The file new Terminal windows read (setup.sh's choice for Homebrew's line)."""
    return home / (".bash_profile" if shell.endswith("/bash") else ".zprofile")


def non_mlx_source(model: str, entry: Mapping[str, Any] | None) -> str | None:
    """What a Gemma 4 model is made from when it is not one of the MLX builds setup installs (the tag, or "a llama.cpp
    (GGUF) build of Gemma 4"); None when it is one, is another family, or cannot be told from Ollama's list. A bare
    tag (gemma4:26b) is a list whose pick depends on Ollama's version, so it counts too."""
    name = model if ":" in model else f"{model}:latest"
    if name.startswith("gemma4:"):
        return None if name.endswith("-mlx") else model
    details = entry.get("details") if isinstance(entry, Mapping) else None
    if not isinstance(details, Mapping):
        return None
    parent = str(details.get("parent_model") or "").rsplit("/", 1)[-1]
    if parent.startswith("gemma4:"):
        return None if parent.endswith("-mlx") else parent
    families = [str(details.get("family") or ""), *(str(item) for item in details.get("families") or [])]
    if any(family.startswith("gemma4") for family in families) and details.get("format") == "gguf":
        return prompts.OLLAMA_GGUF_SOURCE
    return None


def _needs_signature(path: Path) -> bool:
    if path.name.endswith(_SIGNED_SUFFIXES):
        return True
    try:
        with path.open("rb") as handle:
            return handle.read(4) in _MACHO
    except OSError:
        return True  # unreadable: asked about, and codesign says why


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Tools:
    """The Mac for this step (replaced in tests): Ollama's answer, the program on its port, read-only tools, and
    changes, which run in the person's own terminal (sudo asks there). Off the live Mac (root not "/") nothing is read
    or run: no Ollama answers, no program listens, and every change is printed."""

    def __init__(
        self, root: Path | None = None, *, say: Callable[[str], None] = print, host: Host | None = None
    ) -> None:
        self.root = estia_root() if root is None else root
        self.say = say
        self.host = host or Host(self.root, say=say)

    @property
    def live(self) -> bool:
        return self.root == Path("/")

    def version(self) -> str | None:
        """/api/version's version ("" when the answer names none); None when nothing answers."""
        if not self.live:
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"{BASE}/api/version", timeout=3) as response:
                data = json.loads(response.read(100_000))
        except (OSError, ValueError):
            return None
        return str(data.get("version") or "") if isinstance(data, dict) else ""

    def listener(self, port: int = PORT) -> Listener:
        """The program listening on the port, where it listens and the account that runs it: netstat names every
        user's sockets with their pid and local address (lsof as a user only that user's), proc_pidpath the program
        and ps its account, both for any user's process; Listener("") when none."""
        if not self.live:
            return Listener("")
        sockets: dict[str, list[str]] = {}  # each listening pid and its local addresses on the port
        code, out = self.host.run([NETSTAT, "-anv", "-p", "tcp"], read_only=True)
        for line in out.splitlines() if code == 0 else []:
            listening = _LISTEN.match(line)
            if listening and listening[1].rpartition(".")[2] == str(port):
                sockets.setdefault(listening[3], []).append(listening[1])
        if not sockets:  # netstat started from some Pythons lists no internet sockets: your own, from lsof
            code, out = self.host.run([LSOF, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpn"], read_only=True)
            pid = ""
            for line in out.splitlines() if code == 0 else []:
                if line.startswith("p") and line[1:].isdecimal():
                    pid = line[1:]
                    sockets.setdefault(pid, [])
                elif line.startswith("n") and pid:  # lsof's "127.0.0.1:11434", "[::1]:11434", "*:11434": netstat's
                    host, _, at = line[1:].rpartition(":")
                    sockets[pid].append(f"{host.strip('[]')}.{at}")
        programs = {pid: self.host.program_path(pid) for pid in sockets}
        first = next((pid for pid in sockets if programs[pid]), None)
        if first is None:
            return Listener("")
        accounts = {pid: self.uid_of(pid) for pid in sockets}
        mine = (programs[first], accounts[first])
        others = tuple(
            other for other in dict.fromkeys((programs[pid], accounts[pid]) for pid in sockets) if other != mine
        )
        addresses = tuple(dict.fromkeys(address for found in sockets.values() for address in found))
        return Listener(mine[0], addresses, mine[1], others)

    def uid_of(self, pid: str) -> int | None:
        """The account (uid) that runs a process: `ps -o uid= -p <pid>` (fixed argv, read-only); None when it says
        nothing usable (the process is gone)."""
        code, out = self.host.run([PS, "-o", "uid=", "-p", pid], read_only=True)
        text = out.strip() if code == 0 else ""
        return int(text) if text.isdecimal() else None

    def read(self, argv: Sequence[str]) -> tuple[int, str]:
        """A read-only tool by its full path (codesign, spctl): (exit code, its output); never off the live Mac."""
        if not self.live:
            return 127, ""
        return self.host.run(argv, read_only=True)

    def change(self, argv: Sequence[str], *, input: bytes | None = None) -> int:
        """A change by its full path, in the person's terminal (sudo asks for the password there); with `input`, on
        the tool's input, its output dropped. Off the live Mac only printed."""
        if not argv or not argv[0].startswith("/"):
            raise ValueError(f"tools are run by their absolute path, not {list(argv[:1])}")
        if not self.live:
            self.say("would run: " + " ".join(argv))
            return 0
        try:
            done = subprocess.run(
                list(argv), input=input, stdout=subprocess.DEVNULL if input is not None else None, check=False
            )
        except OSError:
            return 127
        return done.returncode


class Install:
    """Setup's Ollama step: keep the official build that answers (updating setup's own in place to the pin), or
    install the pinned one and run it always on."""

    def __init__(
        self,
        tools: Tools,
        home: Path,
        *,
        dry_run: bool,
        say: Callable[[str], None] = print,
        release: Release = RELEASE,
        user: str | None = None,
        group: str | None = None,
        profile: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.tools = tools
        self.home = home
        self.say = say
        self.release = release
        self.user = user or pwd.getpwuid(os.getuid()).pw_name
        self.group = group or grp.getgrgid(os.getgid()).gr_name
        self.profile = profile or login_profile(home, os.environ.get("SHELL", ""))
        self.clock = clock
        self.sleep = sleep
        # off the live Mac (a test's root): nothing on this Mac is read, not even your home; every step only shown
        self.blind = not tools.live
        self.dry_run = dry_run or self.blind

    @property
    def folder(self) -> Path:
        return self.home / "opt" / "ollama"

    @property
    def dest(self) -> Path:
        return self.folder / f"v{self.release.version}"

    @property
    def program(self) -> Path:
        return self.folder / "current" / "ollama"

    def run(self, num_ctx: int) -> str:
        """The ollama program setup runs for the model step (any official one talks to the server that answers);
        SystemExit with what is wrong and the fix when it cannot go on. In a dry run, what would stop it is printed
        and the rest follows as it would run after the fix."""
        self.cap_log()
        found = self.running()
        jobs = self.jobs()
        if found is not None and self.keep(found, jobs):
            if self.ours(found, jobs) and found.version != self.release.version:
                return self.update(found, num_ctx)
            self.kept(found, jobs, num_ctx)
            return found.program
        if found is None:  # another always-on service would fight setup's for the port: Homebrew's or the app's first
            others = [job for job in jobs if job.label != LABEL]
            others.sort(key=lambda job: job_kind(job, self.real(job.program)) == "other")
            for job in others[:1]:
                self.refuse(stopped(*job_problem(job, self.real(job.program))))
            self.others(answering=False)
        self.unpack()
        self.link()
        self.on_path()
        self.daemon(num_ctx)
        self.wait()
        return str(self.program)

    def running(self) -> Found | None:
        """The Ollama that answers on this Mac, with its program, its signature and where it listens; None when none
        answers."""
        version = self.tools.version()
        if version is None:
            return None
        heard = self.tools.listener()
        if not heard.program:
            return Found("", version, addresses=heard.addresses)
        signature = signature_problem(self.tools.read, heard.program)
        return Found(heard.program, version, signature, heard.addresses, heard.uid, heard.others)

    def jobs(self) -> list[Job]:
        """The launchd jobs that run Ollama: the LaunchDaemons, and your LaunchAgents (not off the live Mac)."""
        return ollama_jobs(self.tools.root, None if self.blind else self.home / "Library/LaunchAgents")

    def real(self, path: str) -> str:
        return path if self.blind else os.path.realpath(path)

    def refuse(self, message: str, *, fatal: bool = False) -> None:
        """Setup stops here with the message (`Refusal`; `fatal`: a re-run stops too); a dry run prints it and goes
        on as it would after the fix."""
        if not self.dry_run:
            raise Refusal(message, fatal=fatal)
        self.say(message)
        self.say(prompts.OLLAMA_DRY_RUN_GOES_ON)

    def keep(self, found: Found, jobs: Sequence[Job]) -> bool:
        """Whether the Ollama that answers can be kept: the official build, its whole folder signed by Ollama's
        developer ID, run by your own account (one whose account cannot be read is not kept either), and no launchd
        job of Homebrew's or the app's that could take its port at a login. One that someone other than you may
        control (`endangers`) stops a re-run of setup too."""
        wrong = problem(found, owner=os.getuid())
        fatal = endangers(found, os.getuid())
        if wrong is None and found.program and found.uid is None:
            wrong = (
                prompts.OLLAMA_OTHER.format(program=found.program, why=prompts.OLLAMA_WHY_ACCOUNT_UNREAD),
                (prompts.OLLAMA_OTHER_FIX),
            )
        if wrong is None:
            why = self.check_tree(Path(found.program).parent)
            if why:  # a program, library or metallib there not signed by Ollama, or a link out: not its build
                wrong = prompts.OLLAMA_OTHER.format(program=found.program, why=why), prompts.OLLAMA_OTHER_FIX
                fatal = True
        if wrong is None:  # Homebrew's or the app's: started at a login, it can take the port first
            started = [job for job in jobs if job_kind(job, self.real(job.program)) != "other"]
            if started:
                wrong = job_problem(started[0], self.real(started[0].program))
        if wrong is None:
            return True
        self.refuse(stopped(*wrong), fatal=fatal)
        return False

    def ours(self, found: Found, jobs: Sequence[Job]) -> bool:
        """Setup's own service runs the Ollama that answers, from a folder in ~/opt/ollama: an update goes in place."""
        if not any(job.label == LABEL and job.system and job.program == str(self.program) for job in jobs):
            return False
        return Path(self.real(found.program)).parent.parent == Path(self.real(str(self.folder)))

    def kept(self, found: Found, jobs: Sequence[Job], num_ctx: int) -> None:
        """An official Ollama that answers and is not setup's to update: kept, with a "!" when its version is not the
        tested one or when nothing starts it with the Mac; the app or Homebrew's Ollama installed too, named.
        Setup's own service whose file is not the one this setup writes (an older release's, a setting changed by
        hand) gets it written again and starts again (`daemon`, `wait`)."""
        self.say(prompts.OLLAMA_KEPT.format(version=found.version, program=found.program))
        if found.version != self.release.version:
            text = prompts.OLLAMA_UNTESTED.format(version=found.version, pin=self.release.version)
            self.say(f"! Ollama: {text}\n  Fix: {prompts.OLLAMA_UNTESTED_FIX.format(pin=self.release.version)}")
        program = self.real(found.program)
        if not any(job.system and self.real(job.program) == program for job in jobs):
            self.say(f"! Ollama: {prompts.OLLAMA_NOT_AT_BOOT.format(program=found.program)}")
            self.say(f"  Fix: {prompts.OLLAMA_NOT_AT_BOOT_FIX}")
        if self.ours(found, jobs) and self.service_changed(num_ctx):
            self.say(prompts.OLLAMA_SERVICE_CHANGED.format(daemon=DAEMON))
            self.daemon(num_ctx)  # its log made private there too
            self.wait()
        else:
            log = self.log_steps() if any(job.label == LABEL for job in jobs) else []
            if log:
                self.say(prompts.OLLAMA_LOG_PRIVATE.format(log=LOG))
                for argv in log:
                    self.step(argv)
        self.others(answering=True)

    def service_changed(self, num_ctx: int) -> bool:
        """Setup's own service file is not the one `daemon` writes now (a missing or unreadable one counts)."""
        if self.blind:
            return False
        wanted = daemon_plist(user=self.user, group=self.group, home=self.home, num_ctx=num_ctx)
        return read_plist_bytes(self._system(DAEMON)) != wanted

    def update(self, found: Found, num_ctx: int) -> str:
        """Setup's own service on another version than the pin: the pinned release downloaded and checked while the
        old one runs, `current` switched, the service started again; when the new one does not answer, `current`
        points back at the old folder and the service starts again."""
        old = Path(self.real(found.program)).parent
        self.say(prompts.OLLAMA_UPDATE.format(version=found.version, pin=self.release.version))
        self.unpack()
        self.check_safe()  # before `current` moves: a refusal here leaves the old one running, untouched
        self.link()
        self.on_path()
        try:
            self.daemon(num_ctx)
            self.wait()
        except SystemExit as failed:
            if self.dry_run:
                raise
            self.point(old)
            back = prompts.OLLAMA_ROLLED_BACK.format(
                pin=self.release.version, folder=old, version=found.version, new=self.dest
            )
            try:
                self.restart()
            except SystemExit as again:  # its service did not start either: both reasons, `current` back on the old
                raise SystemExit(f"{failed}\n{again}\n{back}") from None
            raise SystemExit(f"{failed}\n{back}") from None
        return str(self.program)

    def _system(self, path: Path) -> Path:
        return self.tools.root / path.relative_to("/")

    def others(self, *, answering: bool) -> None:
        """The Ollama app (it stops other Ollamas when its own cannot start: setup stops before installing the
        service), and Homebrew's Ollama (named, with the fix)."""
        apps = ((self._system(APP), APP), *(() if self.blind else ((self.home / "Applications/Ollama.app", None),)))
        for app, shown in apps:
            if not os.path.lexists(app):
                continue
            path = shown or app
            text = prompts.OLLAMA_APP.format(state="is installed", path=path)
            fix = prompts.OLLAMA_APP_FIX.format(path=path)
            if not answering:
                self.refuse(stopped(text, fix))
            else:
                self.say(f"! Ollama: {text}\n  Fix: {fix}")
        for cellar in CELLARS:
            if os.path.lexists(self._system(cellar / "ollama")):
                self.say(
                    "! Ollama: "
                    + prompts.OLLAMA_HOMEBREW_THERE.format(path=cellar / "ollama")
                    + f"\n  Fix: {prompts.OLLAMA_HOMEBREW_REMOVE}"
                )

    def step(self, argv: Sequence[str], *, input: bytes | None = None, shown: str = "") -> int:
        """A change, printed first; in a dry run only printed."""
        self.say("  $ " + " ".join(argv) + (f"   ({shown})" if shown else ""))
        return 0 if self.dry_run else self.tools.change(argv, input=input)

    def unpack(self) -> None:
        """The pinned release in ~/opt/ollama/v<version>: a folder already there is checked and kept; else the
        download, checked in a new empty folder first, then moved into place whole. Nothing is ever deleted there."""
        dest = self.dest
        if not self.blind and os.path.lexists(dest):
            why = self.check_tree(dest) if dest.is_dir() and not dest.is_symlink() else prompts.OLLAMA_NOT_A_FOLDER
            if not why:
                self.say(prompts.OLLAMA_FOLDER_KEPT.format(folder=dest))
                return
            self.refuse(prompts.OLLAMA_FOLDER_REFUSED.format(folder=dest, why=why))
        self.say(
            prompts.OLLAMA_INSTALL.format(
                version=self.release.version, size=RELEASE_MB, team=TEAM_ID, folder=dest, needed=MIN_TEXT
            )
        )
        if self.dry_run:
            work = self.folder / ".download-…"
            self.step(self.curl(work / "ollama-darwin.tgz"))
            self.say(f"  (its sha256 must be {self.release.sha256}; then unpacked into a new empty folder)")
            self.step([TAR, "-xzf", str(work / "ollama-darwin.tgz"), "-C", str(work / "unpacked")])
            self.say(prompts.OLLAMA_CHECKS.format(team=TEAM_ID))
            self.say(f"  (then moved to {dest})")
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".download-", dir=self.folder))
        try:
            tgz = work / "ollama-darwin.tgz"
            if self.step(self.curl(tgz)) != 0 or not tgz.is_file():
                raise SystemExit(prompts.OLLAMA_DOWNLOAD_FAILED)
            digest = sha256_of(tgz)
            if digest != self.release.sha256:
                why = prompts.OLLAMA_WHY_FINGERPRINT.format(got=digest, want=self.release.sha256)
                raise SystemExit(prompts.OLLAMA_REFUSED.format(why=why))
            unpacked = work / "unpacked"
            unpacked.mkdir()
            if self.step([TAR, "-xzf", str(tgz), "-C", str(unpacked)]) != 0:
                raise SystemExit(prompts.OLLAMA_REFUSED.format(why=prompts.OLLAMA_WHY_UNPACKED))
            why = self.check_tree(unpacked)
            if why:
                raise SystemExit(prompts.OLLAMA_REFUSED.format(why=why))
            os.rename(unpacked, dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)  # the folder mkdtemp made for this download, and only that
        self.say(prompts.OLLAMA_INSTALLED.format(version=self.release.version, folder=dest))

    def curl(self, tgz: Path) -> list[str]:
        """The download: HTTPS only, redirects too (GitHub sends it on to its file host), never ~/.curlrc (-q, first);
        a stalled line ends it (under 10 KB/s for a minute), as does a file far larger than the release; a failure is
        an exit code."""
        return [
            CURL,
            "-q",
            "-fL",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--retry",
            "3",
            "--connect-timeout",
            "20",
            "--speed-limit",
            "10240",
            "--speed-time",
            "60",
            "--max-filesize",
            "300000000",
            "-o",
            str(tgz),
            self.release.url,
        ]

    def check_tree(self, folder: Path) -> str:
        """Why a release's folder cannot be used ("" when it can): every program, library and metallib signed by
        Ollama's developer ID (Apple's chain, `REQUIREMENT`), `ollama` notarized by Apple, both xgrammar libraries
        there, and no link out of the folder. At most five reasons, then a count."""
        problems: list[str] = []
        inside = folder.resolve()
        for top, folders, files in os.walk(folder):
            for name in sorted([*folders, *files]):
                path = Path(top) / name
                shown = path.relative_to(folder)
                if path.is_symlink():
                    if not path.resolve().is_relative_to(inside):
                        problems.append(prompts.OLLAMA_WHY_LINK.format(name=shown))
                    continue
                if name not in files or not path.is_file() or not _needs_signature(path):
                    continue
                why = signature_problem(self.tools.read, str(path))
                if why:
                    problems.append(f"{shown}: {why}")
        program = folder / "ollama"
        if program.is_symlink() or not program.is_file():
            problems.append(prompts.OLLAMA_WHY_NO_PROGRAM)
        else:
            code, out = self.tools.read([SPCTL, "--assess", "--type", "install", "-vv", str(program)])
            if code != 0 or "accepted" not in out or "source=Notarized Developer ID" not in out:
                problems.append(prompts.OLLAMA_WHY_NOT_NOTARIZED)
        missing, unread = libraries_in(folder)
        problems += [prompts.OLLAMA_WHY_LIBRARY.format(library=library) for library in (*missing, *unread)]
        more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
        return "; ".join(problems[:5]) + more

    def link(self) -> None:
        """~/opt/ollama/current points at this release (a rollback points it back at an older folder)."""
        self.point(self.dest)

    def point(self, target: Path) -> None:
        """~/opt/ollama/current points at `target`, swapped in one rename."""
        current = self.folder / "current"
        if self.blind:
            self.say(f"  $ ln -sfn {target} {current}")
            return
        if os.path.lexists(current) and not current.is_symlink():
            self.refuse(prompts.OLLAMA_CURRENT_NOT_LINK.format(path=current))
            return
        if current.is_symlink() and os.readlink(current) == str(target):
            return
        if self.dry_run:
            self.say(f"  $ ln -sfn {target} {current}")
            return
        temporary = self.folder / f".current-{os.getpid()}"
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        os.symlink(target, temporary)
        os.replace(temporary, current)

    def on_path(self) -> None:
        """Its folder in the login profile, so new Terminal windows find `ollama` (setup itself runs it by full path)."""
        try:
            text = "" if self.blind else self.profile.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        if "opt/ollama/current" in text:
            return
        if self.dry_run:
            self.say(prompts.OLLAMA_PATH_WOULD.format(line=PATH_LINE, profile=self.profile))
            return
        with self.profile.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{PATH_LINE}\n")
        self.say(prompts.OLLAMA_PATH_ADDED.format(line=PATH_LINE, profile=self.profile))

    def unsafe(self) -> str:
        """The first folder on the way from your home to the program, or the program itself, that another account
        owns or can change ("" when none): the service runs that program as you at every start. Read-only."""
        uid = os.getuid()
        on_the_way = [self.home, self.home / "opt", self.folder, self.dest, self.dest / "ollama"]
        for path in on_the_way:
            try:
                info = os.lstat(path) if path.name == "ollama" and path.parent == self.dest else os.stat(path)
            except FileNotFoundError:
                continue  # not there yet (a dry run): made by setup, yours
            except OSError:
                return prompts.OLLAMA_UNSAFE.format(path=path, why=prompts.OLLAMA_UNSAFE_UNREAD)
            if info.st_uid not in (uid, 0):
                return prompts.OLLAMA_UNSAFE.format(path=path, why=prompts.OLLAMA_UNSAFE_OWNER)
            if info.st_mode & 0o022:
                return prompts.OLLAMA_UNSAFE.format(path=path, why=prompts.OLLAMA_UNSAFE_WRITABLE)
        return ""

    def check_safe(self) -> None:
        """Stops before any sudo step when a folder on the way to the program is not yours alone (`unsafe`)."""
        unsafe = "" if self.blind else self.unsafe()
        if unsafe:
            self.refuse(unsafe)

    def log_steps(self) -> list[list[str]]:
        """Root makes the log yours, 0600, before launchd opens it (launchd appends to it as root): only when it is
        missing or not already your private, regular file with one name. install(1) replaces the name, never
        following a link there."""
        if not self.blind:
            try:
                info = os.lstat(self._system(LOG))
            except OSError:
                info = None
            if (
                info is not None
                and stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600
            ):
                return []
        return [[SUDO, INSTALL, "-m", "600", "-o", self.user, "-g", self.group, "/dev/null", str(LOG)]]

    def cap_log(self) -> None:
        """Your private log over LOG_CAP is emptied (no admin rights: it is yours; launchd goes on at the new end).
        One that is root's, a link or a second name is left alone."""
        if self.dry_run:
            return
        try:
            fd = os.open(self._system(LOG), os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return
        try:
            info = os.fstat(fd)
            if (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_uid == os.getuid()
                and info.st_size > LOG_CAP
            ):
                os.ftruncate(fd, 0)
                self.say(prompts.OLLAMA_LOG_EMPTIED.format(log=LOG))
        finally:
            os.close(fd)

    def restart(self) -> None:
        """The service started again: kickstart; one launchd does not have loaded (booted out), bootstrapped."""
        if self.step([SUDO, LAUNCHCTL, "kickstart", "-k", f"system/{LABEL}"]) == 0:
            return
        self.bootstrap()

    def bootstrap(self) -> None:
        argv = [SUDO, LAUNCHCTL, "bootstrap", "system", str(DAEMON)]
        if self.step(argv) != 0:
            self.sleep(2)
            if self.step(argv) != 0:
                raise SystemExit(prompts.OLLAMA_SERVICE_FAILED.format(label=LABEL))

    def daemon(self, num_ctx: int) -> None:
        """The LaunchDaemon: written by root from tee's input (root:wheel, 644), its log made yours and private, then
        loaded; one already in place with the same text is started again instead. Nothing runs as root while a
        folder on the way to the program is not yours alone."""
        wanted = daemon_plist(user=self.user, group=self.group, home=self.home, num_ctx=num_ctx)
        try:
            existing: bytes | None = None if self.blind else self._system(DAEMON).read_bytes()
        except OSError:
            existing = None
        self.say(prompts.OLLAMA_SERVICE.format(daemon=DAEMON, log=LOG))
        self.check_safe()
        log = self.log_steps()
        steps: list[list[str]] = []
        if existing != wanted or log:
            steps.append([SUDO, INSTALL, "-d", "-m", "755", "-o", "root", "-g", "wheel", str(LOG_DIR)])
        steps += log
        if existing != wanted:
            steps += [
                [SUDO, TEE, str(DAEMON)],
                [SUDO, CHMOD, "644", str(DAEMON)],
                [SUDO, CHOWN, "root:wheel", str(DAEMON)],
            ]
        for argv in steps:
            tee = argv[1] == TEE
            if self.step(argv, input=wanted if tee else None, shown=prompts.OLLAMA_ON_INPUT if tee else "") != 0:
                raise SystemExit(prompts.OLLAMA_SERVICE_FAILED.format(label=LABEL))
        if existing == wanted:
            self.restart()
            return
        if existing is not None:
            self.step([SUDO, LAUNCHCTL, "bootout", f"system/{LABEL}"])  # not loaded: it says so, and that is fine
            if not self.dry_run:
                self.sleep(1)  # launchd refuses a bootstrap right after a bootout (error 5)
        self.bootstrap()

    def wait(self) -> None:
        """Until the pinned version answers (WAIT_SECONDS at most), then the same checks as for one already there."""
        if self.dry_run:
            return
        deadline = self.clock() + WAIT_SECONDS
        while self.tools.version() != self.release.version:
            if self.clock() >= deadline:
                raise SystemExit(prompts.OLLAMA_NOT_UP.format(seconds=WAIT_SECONDS, label=LABEL, log=LOG))
            self.sleep(1)
        found = self.running()
        wrong = problem(found, owner=os.getuid()) if found is not None else None
        if wrong is not None:
            raise SystemExit(stopped(*wrong))
        self.say(prompts.OLLAMA_UP.format(version=self.release.version))


def main(argv: list[str] | None = None) -> int:
    """`scripts/install-ollama.sh`: setup's Ollama step on its own (by hand, or again after a failure). Without a
    terminal (an AI's command tool) it changes nothing: sudo needs the person's own window."""
    from local_assistant.setup import choose_model, has_terminal, memory_gb  # setup imports this module

    parser = argparse.ArgumentParser(
        description="Keep the official Ollama that answers, or install Ollama's official build, checked, always on."
    )
    parser.add_argument("--dry-run", action="store_true", help="read and print; install and start nothing")
    args = parser.parse_args(argv)
    if not args.dry_run and not has_terminal():
        print(prompts.OLLAMA_NO_TERMINAL, file=sys.stderr)
        return 1
    memory = memory_gb()
    choice = choose_model(memory)
    if choice is None:
        print(prompts.SETUP_TOO_SMALL.format(memory=memory, name="Estia"), file=sys.stderr)
        return 1
    tools = Tools()
    Install(tools, owner_home(), dry_run=args.dry_run or not tools.live).run(choice.num_ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
