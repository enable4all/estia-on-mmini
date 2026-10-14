"""estia-deploy: put a checkout's commit live as a root-owned release, or switch back.

    /usr/bin/sudo /usr/local/estia/bin/estia-deploy [checkout] [--voice] [--keep N] [--dry-run]
    /usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools [--dry-run]

0. Review: the commits since the live one and the files they change are shown, and nothing happens without a yes.
1. Build, as the owner (SUDO_USER), at the release's final path /usr/local/estia/releases/<UTC stamp>-<commit>: the
   commit's files (git archive), a Python interpreter of its own (uv python install into the release, never uv's copy
   in the owner's home), the editable .venv and the tools' venvs (uv sync, with bytecode), the voice service and the
   programs she runs: the JVM signal-cli and the call helper, Homebrew's native signal-cli and, with the JVM build, a
   JDK (vendor/), so nothing she runs stays in a folder the owner can change.
2. Freeze, as root: the build is copied into fresh root-owned files at the same path (0755 folders and programs, 0644
   the rest, times kept so the bytecode stays valid). Hard links, links that leave the release (also through a chain
   of links), links to nothing, set-id bits and files the owner did not make stop the deploy.
3. The service files are rendered from the root-run tools' templates and checked (her user, her home, programs under
   `current`, no other keys); one that changed is reloaded, the others restarted (the voice service only when its
   program changed, or with --voice).
4. Switch `current` atomically, then check within 120 s: a new bridge process that stays the same, signal-cli running
   as _estia, and last_alive written after the restart. On failure `current` goes back to the previous release.

Root never runs code from the checkout or a release: `bin/estia`, `bin/estia-deploy` and this module run from
/usr/local/estia/tools (its own Python, this file, ownuser, hostsec and the service templates), installed with the
first release and changed only by `--update-tools`, which shows their diff and asks first.

Prints metadata only (names, commits, versions, file names), never file contents or secrets.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import plistlib
import pwd
import re
import shutil
import sqlite3
import stat
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from local_assistant import ownuser
from local_assistant.hostsec import CHOWN, LAUNCHCTL, RM, SUDO, Host, estia_root, tail

ENV = "/usr/bin/env"
GIT = "/usr/bin/git"
TAR = "/usr/bin/tar"
CP = "/bin/cp"
MKDIR = "/bin/mkdir"
PS = "/bin/ps"
KEEP = 3
PYTHON_SERIES = "3.13"
HEALTH_SECONDS = 120.0
STABLE_SECONDS = 10.0
POLL_SECONDS = 2.0
ALIVE_KEY = "last_alive"  # app.ALIVE_KEY: written after signal-cli subscribes, then every 60 s
OWNER_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TUNNEL = Path("signal-call-tunnel/signal-call-tunnel/target/release/signal-call-tunnel")
NATIVE = Path("signal-cli-native")  # Homebrew's signal-cli keg, copied (config.DEFAULT_SIGNAL_CLI)
JDK = Path("openjdk.jdk")  # a JDK for the JVM signal-cli (config: [calls] java_home's default)
DB = ownuser.HOME / "Library" / "Application Support" / "local-assistant" / "assistant.db"
DEPLOY = ownuser.DEPLOY_CMD
# what root runs: copied from a release into /usr/local/estia/tools only with the first release or --update-tools
TOOL_FILES = (
    "scripts/estia",
    "scripts/estia-deploy",
    "src/local_assistant/__init__.py",
    "src/local_assistant/hostsec.py",
    "src/local_assistant/ownuser.py",
    "src/local_assistant/deploy.py",
    f"launchd/{ownuser.BRIDGE}.plist.in",
    f"launchd/{ownuser.VOICE}.plist.in",
)
PROGRAMS = frozenset({"scripts/estia", "scripts/estia-deploy"})
VOICE_BINARY = Path("tools/voice/.build/release/voice-service")


class DeployError(Exception):
    """The deploy stopped; nothing was switched."""


Builder = ownuser.Owner  # the owner who builds: SUDO_USER under sudo, else whoever runs it


def builder() -> Builder:
    if os.geteuid() == 0:
        name = os.environ.get("SUDO_USER", "")
        if not name or name == "root":
            raise DeployError(f"run it with sudo from your own account: {DEPLOY} [checkout]")
        entry = pwd.getpwnam(name)
        return Builder(entry.pw_name, Path(entry.pw_dir), entry.pw_uid)
    entry = pwd.getpwuid(os.getuid())
    return Builder(entry.pw_name, Path(os.environ.get("HOME") or entry.pw_dir), entry.pw_uid)


def find_uv(who: Builder) -> str:
    for path in (
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
        str(who.home / ".local" / "bin" / "uv"),
        str(who.home / ".cargo" / "bin" / "uv"),
    ):
        if Path(path).is_file():
            return path
    raise DeployError("uv not found (brew install uv)")


def default_checkout(code: Path) -> Path:
    """The checkout the current release was built from."""
    try:
        source = json.loads((code / "current" / "release.json").read_text(encoding="utf-8")).get("source", "")
    except (OSError, ValueError, AttributeError):
        source = ""
    if not source:
        raise DeployError(f"name the checkout: {DEPLOY} ~/estia")
    return Path(source)


def read_alive(db: Path) -> str | None:
    """The bridge's last_alive, read-only (no -wal or -shm is made); None when there is none."""
    if not db.is_file():
        return None
    uri = f"file:{urllib.parse.quote(str(db))}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (ALIVE_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row and row[0] is not None else None


def read_calls(config: Path) -> bool:
    """Whether a config turns calls on (run as her: `--read-calls`); False when it cannot be read."""
    try:
        fd = os.open(config, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return False
            text = handle.read(1_000_000).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return ownuser.calls_in(text)


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    """Owner-written text (a commit subject, a file name) made safe to print: no escape sequences."""
    return _CONTROL.sub("?", text)


def ask_terminal(question: str) -> bool:
    """A yes from the terminal; without one (no terminal, end of input) it is a no."""
    if not sys.stdin.isatty():
        print("✗ run it in a terminal: it shows what changes and asks before it does anything", flush=True)
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower().startswith("y")
    except EOFError:
        return False


def _inside(link_dir: str, target: str, top: Path, final: Path) -> bool:
    """Whether a link's text stays inside the release: absolute under its final path, relative within the tree."""
    if os.path.isabs(target):
        return os.path.normpath(target) == str(final) or os.path.normpath(target).startswith(f"{final}/")
    resolved = os.path.normpath(os.path.join(link_dir, target))
    return resolved == str(top) or resolved.startswith(f"{top}/")


def _check_links(tree: Path) -> None:
    """Every link in the tree, followed as the kernel does (a chain of links, `..` after a link), ends at an existing
    file or folder inside it."""
    real = os.path.realpath(tree)
    for folder, dirs, files in os.walk(tree):
        for name in [*dirs, *files]:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                continue
            rel = os.path.relpath(path, tree)
            target = os.path.realpath(path)
            if target != real and not target.startswith(f"{real}/"):
                raise DeployError(f"{rel}: a link out of the release")
            if not os.path.exists(path):
                raise DeployError(f"{rel}: a link to nothing")


def _walk_build(top: Path, uid: int, final: Path, copy_to: Path | None) -> None:
    """Check every entry of the build (and copy it into `copy_to`): folders, regular files with one link, made by
    `uid`, without set-id bits, and links that stay inside. Folders are opened without following links (fwalk) and
    files are checked on the open file itself (opened without waiting, so a FIFO swapped in cannot hang root), so
    nothing can be swapped in between. A copy keeps each file's times (bytecode checks them)."""
    for folder, dirs, files, dir_fd in os.fwalk(top, follow_symlinks=False):
        here = Path(folder)
        rel = here.relative_to(top)
        info = os.fstat(dir_fd)
        if info.st_uid != uid:
            raise DeployError(f"{rel or '.'}: another owner than the builder")
        if copy_to is not None:
            (copy_to / rel).mkdir(mode=0o755, exist_ok=rel == Path("."))
        for name in [*dirs, *files]:
            entry = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            path = rel / name
            if stat.S_ISLNK(entry.st_mode):
                target = os.readlink(name, dir_fd=dir_fd)
                if not _inside(str(here), target, top, final):
                    raise DeployError(f"{path}: a link out of the release")
                if copy_to is not None:
                    os.symlink(target, copy_to / path)
                    if name in dirs:
                        dirs.remove(name)
                continue
            if stat.S_ISDIR(entry.st_mode):
                continue
            if not stat.S_ISREG(entry.st_mode):
                raise DeployError(f"{path}: not a regular file")
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
            except OSError as exc:
                raise DeployError(f"{path}: cannot be opened ({type(exc).__name__})") from exc
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode):
                    raise DeployError(f"{path}: not a regular file")
                if opened.st_nlink != 1:
                    raise DeployError(f"{path}: a hard link")
                if opened.st_uid != uid:
                    raise DeployError(f"{path}: another owner than the builder")
                if opened.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    raise DeployError(f"{path}: a set-id bit")
                if copy_to is not None:
                    mode = 0o755 if opened.st_mode & 0o111 else 0o644
                    out = os.open(copy_to / path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
                    with os.fdopen(out, "wb") as target_file, os.fdopen(os.dup(fd), "rb") as source_file:
                        shutil.copyfileobj(source_file, target_file, 1 << 20)
                    os.chmod(copy_to / path, mode)
                    os.utime(copy_to / path, ns=(opened.st_atime_ns, opened.st_mtime_ns))
            finally:
                os.close(fd)
    if copy_to is not None:
        for folder, _dirs, _files in os.walk(copy_to):
            os.chmod(folder, 0o755)
    _check_links(copy_to if copy_to is not None else top)


def verify_build(tree: Path, uid: int, final: Path | None = None) -> None:
    """Raise DeployError when the build holds anything the freeze would refuse."""
    _walk_build(tree, uid, final or tree, None)


def copy_regular(source: Path, target: Path, mode: int) -> None:
    """One regular file, copied by root without following a link or waiting on a FIFO."""
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise DeployError(f"{source.name}: cannot be opened ({type(exc).__name__})") from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise DeployError(f"{source.name}: not a regular file")
        data = handle.read()
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(out, "wb") as handle:
        handle.write(data)
    os.chmod(target, mode)


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class Deploy:
    def __init__(
        self,
        host: Host,
        who: Builder,
        checkout: Path,
        *,
        uv: str,
        keep: int = KEEP,
        voice: bool = False,
        vendor: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        as_root: bool | None = None,
        check_health: bool | None = None,
        say: Callable[[str], None] | None = None,
        confirm: Callable[[str], bool] = ask_terminal,
        brew: Path | None = None,
        voice_health: Callable[[], Mapping[str, object] | None] | None = None,
        her_calls: Callable[[], bool] | None = None,
    ) -> None:
        self.host = host
        # the voice service's health after a restart of it (never the running service off the live Mac)
        self.voice_health = voice_health or (lambda: ownuser.voice_health(host.live))
        self.her_calls = her_calls or self._her_calls  # her config has [calls] (read as her)
        self.who = who
        self.checkout = checkout
        self.uv = uv
        self.keep = max(2, keep)
        self.voice = voice
        self.vendor = vendor
        self.clock = clock
        self.sleep = sleep
        self.as_root = os.geteuid() == 0 if as_root is None else as_root
        self.check_health = host.live if check_health is None else check_health
        self.say = say or (lambda text: print(text, flush=True))
        self.confirm = confirm
        self.brew = brew or Path(ownuser.find_brew_prefix())
        self.code = host.path(ownuser.CODE)
        self.releases = self.code / "releases"
        self.current = self.code / "current"
        self.tools = host.path(ownuser.TOOLS)
        self.dry = host.dry_run

    # ---------------------------------------------------------------- helpers

    def owner(self, argv: Sequence[str], *env: str) -> list[str]:
        """A command run as the builder, with a clean environment (HOME, a fixed PATH)."""
        inner = [ENV, "-i", f"HOME={self.who.home}", f"PATH={OWNER_PATH}", "LANG=en_US.UTF-8", *env, *argv]
        return [SUDO, "-u", self.who.name, "-H", "--", *inner] if self.as_root else inner

    def build_step(self, argv: Sequence[str], *env: str, read_only: bool = False) -> str:
        code, out = self.host.run(self.owner(argv, *env), read_only=read_only)
        if code != 0:
            words = " ".join(Path(argv[0]).name if i == 0 else a for i, a in enumerate(argv[:3]))
            why = tail(out)
            raise DeployError(f"{words} … failed (exit {code})" + (f": {why}" if why else ""))
        return out

    def note(self, text: str) -> None:
        self.say(f"would {text}" if self.dry else text)

    def live_meta(self) -> dict[str, object]:
        try:
            data = json.loads((self.current / "release.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------- review

    def commit(self) -> tuple[str, bool]:
        out = self.build_step([GIT, "-C", str(self.checkout), "rev-parse", "--verify", "HEAD"], read_only=True)
        sha = out.strip() if re.fullmatch(r"[0-9a-f]{40,64}\s*", out) else ""
        dirty = bool(
            self.build_step(
                [GIT, "-C", str(self.checkout), "status", "--porcelain", "--untracked-files=no"], read_only=True
            ).strip()
        )
        if not sha and not self.dry:
            raise DeployError("the checkout has no commit")
        return sha or "0" * 40, dirty

    def review(self, sha: str) -> bool:
        """What goes live: the commits since the live one and the files they change, then a yes."""
        live = str(self.live_meta().get("sha", ""))
        if not re.fullmatch(r"[0-9a-f]{7,64}", live):
            self.say(f"First release from {self.checkout}: commit {sha[:12]}.")
        elif sha.startswith(live):
            self.say(f"Commit {sha[:12]} is live already: this builds it again.")
        else:
            code, log = self.host.run(
                self.owner([GIT, "-C", str(self.checkout), "log", "--oneline", "--no-decorate", f"{live}..{sha}"]),
                read_only=True,
            )
            if code != 0:
                self.say(f"The live commit {live} is not in {self.checkout}: what changed cannot be shown.")
            else:
                lines = log.splitlines()
                self.say(f"{len(lines)} new commit(s) since the live {live}:")
                for line in lines[:50]:
                    self.say(f"  {clean(line)}")
                _, files = self.host.run(
                    self.owner([GIT, "-C", str(self.checkout), "diff", "--stat=120", f"{live}..{sha}"]),
                    read_only=True,
                )
                for line in files.splitlines()[:200]:
                    self.say(f"  {clean(line)}")
        if self.dry:
            self.say("would ask: Deploy it?")
            return True
        return self.confirm("Deploy it? Read the list above first: the release runs as her, with her data.")

    # ---------------------------------------------------------------- build

    def vendor_items(self) -> list[tuple[Path, Path]]:
        """(source, path inside vendor/): each JVM signal-cli, the call helper, the native signal-cli and (with a JVM
        signal-cli) a JDK, from the first place that has it; Homebrew's come before the live release's."""
        roots = [
            *([self.vendor] if self.vendor else []),
            self.checkout / "vendor",
            self.checkout.parent / "vendor",
            self.who.home / "Library" / "Application Support" / "local-assistant" / "vendor",
            None,  # Homebrew
            self.current / "vendor",
        ]
        found: dict[Path, Path] = {}
        for root in roots:
            if root is None:
                keg = (self.brew / "bin" / "signal-cli").resolve().parent.parent
                if (keg / "bin" / "signal-cli").is_file() and "Cellar" in keg.parts:
                    found.setdefault(NATIVE, keg)
                jdk = (self.brew / "opt" / "openjdk" / "libexec" / "openjdk.jdk").resolve()
                if (jdk / "Contents" / "Home" / "bin" / "java").is_file():
                    found.setdefault(JDK, jdk)
                continue
            if not root.is_dir():
                continue
            for jvm in sorted(root.glob("signal-cli-*-jvm/signal-cli-*")):
                if (jvm / "bin" / "signal-cli").is_file():
                    found.setdefault(jvm.relative_to(root), jvm)
            if (root / TUNNEL).is_file():
                found.setdefault(TUNNEL, root / TUNNEL)
            if (root / NATIVE / "bin" / "signal-cli").is_file():
                found.setdefault(NATIVE, root / NATIVE)
            if (root / JDK / "Contents" / "Home" / "bin" / "java").is_file():
                found.setdefault(JDK, root / JDK)
        if not any(rel.parts[0].endswith("-jvm") for rel in found):
            found.pop(JDK, None)  # Java only for the JVM signal-cli
        return [(source, rel) for rel, source in found.items()]

    def voice_source(self) -> Path | None:
        for folder in (self.checkout / "tools/voice/.build/release", self.current / "tools/voice/.build/release"):
            if (folder / "voice-service").is_file():
                return folder
        return None

    def build(self, release: Path, sha: str) -> dict[str, object]:
        """Everything the owner builds into `release` (owned by the owner meanwhile)."""
        tar = release / ".source.tar"
        self.build_step([GIT, "-C", str(self.checkout), "archive", "--format=tar", f"--output={tar}", sha])
        self.build_step([TAR, "-x", "-f", str(tar), "-C", str(release)])
        self.build_step([RM, "-f", str(tar)])
        self.build_step(
            [self.uv, "python", "install", PYTHON_SERIES, "--install-dir", str(release / "python"), "--no-bin"]
        )
        found = sorted(release.glob(f"python/cpython-{PYTHON_SERIES}*/bin/python{PYTHON_SERIES}"))
        interp = found[-1] if found else release / f"python/cpython-{PYTHON_SERIES}-…/bin/python{PYTHON_SERIES}"
        if not found and not self.dry:
            raise DeployError("uv installed no Python into the release")
        for project in (release, release / "tools" / "tapo", release / "tools" / "ical"):
            self.build_step(
                [
                    self.uv,
                    "--no-python-downloads",
                    "--directory",
                    str(project),
                    "sync",
                    "--frozen",
                    "--no-dev",
                    "--python",
                    str(interp),
                ],
                "UV_LINK_MODE=copy",
                "UV_COMPILE_BYTECODE=1",  # her user cannot write __pycache__ in the release: compiled now
            )
        self.build_step([str(interp), "-I", "-m", "compileall", "-q", "-j", "0", str(release / "src")])
        voice = self.voice_source()
        if voice is not None:
            target = release / "tools/voice/.build/release"
            self.build_step([MKDIR, "-p", str(target)])
            self.build_step([CP, "-p", str(voice / "voice-service"), str(target / "voice-service")])
            for bundle in sorted(voice.glob("*.bundle")):
                self.build_step([CP, "-R", "-p", str(bundle), f"{target}/"])
        vendored: list[str] = []
        for source, rel in self.vendor_items():
            self.build_step([MKDIR, "-p", str(release / "vendor" / rel.parent)])
            if source.name == rel.name:
                self.build_step([CP, "-R", "-p", str(source), f"{release / 'vendor' / rel.parent}/"])
            else:  # a folder under another name (Homebrew's keg is named by its version)
                self.build_step([CP, "-R", "-p", f"{source}/", str(release / "vendor" / rel)])
            vendored.append(rel.parts[0])
        checks = (
            (release / ".venv/bin/python", "import local_assistant.app, local_assistant.doctor"),
            (release / "tools/tapo/.venv/bin/python", "import kasa"),
            (release / "tools/ical/.venv/bin/python", "import icalendar, recurring_ical_events"),
        )
        prefix = self.build_step([str(interp), "-I", "-c", "import sys; print(sys.prefix)"]).strip()
        if not self.dry and not os.path.realpath(prefix).startswith(os.path.realpath(release)):  # /tmp is a link
            raise DeployError("the release's Python is not its own")
        for python, imports in checks:
            self.build_step([str(python), "-I", "-c", imports])
        version = re.search(r"cpython-([0-9.]+)", str(interp))
        return {
            "python": version.group(1) if version else PYTHON_SERIES,
            "voice": voice is not None,
            "vendor": vendored,
        }

    def freeze(self, release: Path) -> None:
        """Copy the owner's build into fresh root-owned files at the same path, after checking every entry."""
        if self.dry:
            self.note(f"copy the build into root-owned files: {release}")
            return
        staging = release.with_name(f".build-{release.name}")
        os.rename(release, staging)
        try:
            _walk_build(staging, self.who.uid, release, release)
        except BaseException:
            shutil.rmtree(release, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        if self.as_root:
            self.host.run([CHOWN, "-R", "-h", "root:wheel", str(release)])

    def build_release(self, sha: str, dirty: bool) -> Path:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        release = self.releases / f"{stamp}-{sha[:12]}"
        if dirty:
            self.say(f"note: uncommitted changes in {self.checkout} are not deployed (only commit {sha[:12]})")
        if release.exists():
            raise DeployError(f"{release.name} exists already")
        if self.dry:
            self.note(f"make {release} (owned by {self.who.name} while it is built)")
        else:
            for folder in (self.code, self.releases):
                folder.mkdir(mode=0o755, parents=True, exist_ok=True)
                folder.chmod(0o755)
            release.mkdir(mode=0o755)
            if self.as_root:
                os.chown(release, self.who.uid, pwd.getpwnam(self.who.name).pw_gid)
        try:
            meta = self.build(release, sha)
            self.freeze(release)
        except BaseException:
            if not self.dry:
                shutil.rmtree(release, ignore_errors=True)
            raise
        meta = {
            "name": release.name,
            "sha": sha[:12],
            "dirty": dirty,
            "built_at": int(self.clock()),
            "source": str(self.checkout),
            "builder": self.who.name,
            **meta,
        }
        if not self.dry:
            (release / "release.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
            (release / "release.json").chmod(0o644)
        vendor = ", ".join(str(item) for item in meta["vendor"]) or "none"  # type: ignore[attr-defined]
        voice = "yes" if meta["voice"] else "no"
        self.say(f"release {release.name}: commit {sha[:12]}, Python {meta['python']}, voice {voice}, vendor {vendor}")
        return release

    # ---------------------------------------------------------------- the root-run tools

    def tool_changes(self, release: Path) -> list[str]:
        """The tool files whose copy in `release` differs from the installed tools (all of them before the first)."""
        return [rel for rel in TOOL_FILES if _digest(release / rel) != _digest(self.tools / rel)]

    def install_tools(self, release: Path) -> None:
        """The tools from a root-owned release: its tool files, its Python (only when the tools have none yet) and
        bin/estia, bin/estia-deploy. Swapped in whole."""
        if self.dry:
            self.note(f"install the root-run tools from {release.name} into {self.tools} and {self.code / 'bin'}")
            return
        new = self.code / ".tools.new"
        old = self.code / ".tools.old"
        for leftover in (new, old):
            shutil.rmtree(leftover, ignore_errors=True)
        new.mkdir(mode=0o755)
        for rel in TOOL_FILES:
            copy_regular(release / rel, new / rel, 0o755 if rel in PROGRAMS else 0o644)
        keep_python = (self.tools / "python").is_dir()
        if not keep_python:
            shutil.copytree(release / "python", new / "python", symlinks=True)
        if self.tools.exists():
            os.rename(self.tools, old)
        os.rename(new, self.tools)
        if keep_python:
            os.rename(old / "python", self.tools / "python")
        shutil.rmtree(old, ignore_errors=True)
        found = sorted(self.tools.glob(f"python/cpython-{PYTHON_SERIES}*/bin/python{PYTHON_SERIES}"))
        if found:
            os.symlink(os.path.relpath(found[-1], self.tools), self.tools / "python3")
        record = {"release": release.name, "installed_at": int(self.clock())}
        (self.tools / "tools.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        (self.tools / "tools.json").chmod(0o644)
        for folder in [self.tools, *(p for p in self.tools.rglob("*") if p.is_dir() and not p.is_symlink())]:
            folder.chmod(0o755)
        bin_folder = self.code / "bin"
        bin_folder.mkdir(mode=0o755, exist_ok=True)
        bin_folder.chmod(0o755)
        for name in ("estia", "estia-deploy"):
            tmp = bin_folder / f".{name}.new"
            tmp.unlink(missing_ok=True)
            copy_regular(self.tools / "scripts" / name, tmp, 0o755)
            os.replace(tmp, bin_folder / name)
        if self.as_root:
            self.host.run([CHOWN, "-R", "-h", "root:wheel", str(self.tools), str(bin_folder)])

    def update_tools(self) -> int:
        """--update-tools: the current release's tool files, after their diff was shown and a yes."""
        release = self.current.resolve() if self.current.is_symlink() else None
        if release is None or not release.is_dir():
            self.say(f"✗ No release is live yet: {DEPLOY} <checkout> makes the first.")
            return 1
        changed = self.tool_changes(release)
        if not changed:
            self.say(f"✓ The root-run tools are those of the live release {release.name}.")
            return 0
        for rel in changed:
            old = (
                (self.tools / rel).read_text(encoding="utf-8", errors="replace") if (self.tools / rel).is_file() else ""
            )
            new = (release / rel).read_text(encoding="utf-8", errors="replace")
            diff = difflib.unified_diff(
                old.splitlines(), new.splitlines(), f"tools/{rel}", f"{release.name}/{rel}", lineterm=""
            )
            for line in diff:
                self.say(clean(line))
        if self.dry:
            self.say(f"would ask: Install these {len(changed)} file(s)? They run as root.")
            return 0
        if not self.confirm(f"Install these {len(changed)} file(s)? They run as root."):
            self.say("Kept the installed tools.")
            return 1
        self.install_tools(release)
        self.say(f"✓ Root-run tools from {release.name}: {', '.join(changed)}. The next deploy renders the services.")
        return 0

    # ---------------------------------------------------------------- the services

    def installed(self, label: str) -> bool:
        return (self.host.path(ownuser.DAEMONS) / f"{label}.plist").exists()

    def services(self, release: Path) -> dict[str, bytes]:
        """The service files as the root-run tools' templates render them, each checked: the bridge (when installed)
        and the voice service (when installed, or with --voice). A template with anything else stops the deploy."""
        folder = self.tools / "launchd" if (self.tools / "launchd").is_dir() else release / "launchd"
        rendered: dict[str, bytes] = {}
        for label in ownuser.LABELS:
            if not (self.installed(label) or (label == ownuser.VOICE and self.voice)):
                continue
            try:
                text = ownuser.render(
                    (folder / f"{label}.plist.in").read_text(encoding="utf-8"), ownuser.plist_values(), plist=True
                )
                data = plistlib.loads(text.encode("utf-8"))
            except (OSError, ValueError, plistlib.InvalidFileException) as exc:
                raise DeployError(f"{label}.plist: cannot be rendered ({exc})") from exc
            problem = ownuser.plist_problem(data, label) if isinstance(data, dict) else "not a dictionary"
            if problem:
                raise DeployError(f"{label}.plist: {problem}")
            rendered[label] = text.encode("utf-8")
        return rendered

    def wait_gone(self, label: str) -> None:
        if self.host.live:
            for _ in range(50):  # bootout is asynchronous; bootstrapping before it ends fails with error 5
                if self.host.run([LAUNCHCTL, "print", f"system/{label}"], read_only=True)[0] != 0:
                    return
                self.sleep(0.2)

    def go(self, plists: dict[str, bytes], voice_changed: bool) -> None:
        """A service file that changed is written and reloaded; the bridge is restarted; the voice service only when
        its program changed (or with --voice)."""
        for label, content in plists.items():
            path = self.host.path(ownuser.DAEMONS) / f"{label}.plist"
            system = str(ownuser.DAEMONS / f"{label}.plist")
            try:
                before: bytes | None = path.read_bytes()
            except OSError:
                before = None
            if before != content:
                if self.dry:
                    self.note(f"write {system} (from the tools' template)")
                else:
                    tmp = path.with_name(f".{path.name}.new")
                    tmp.unlink(missing_ok=True)
                    tmp.write_bytes(content)
                    tmp.chmod(0o644)
                    os.replace(tmp, path)
                    if self.as_root:
                        self.host.run([CHOWN, "root:wheel", str(path)])
                if before is not None:
                    self.host.run([LAUNCHCTL, "bootout", f"system/{label}"])
                    self.wait_gone(label)
                self.host.run([LAUNCHCTL, "bootstrap", "system", system])
            elif label == ownuser.BRIDGE or voice_changed or self.voice:
                self.host.run([LAUNCHCTL, "kickstart", "-k", f"system/{label}"])

    def restart(self) -> None:
        if self.installed(ownuser.BRIDGE) or self.dry:
            self.host.run([LAUNCHCTL, "kickstart", "-k", f"system/{ownuser.BRIDGE}"])

    def bridge_pid(self) -> int | None:
        _, out = self.host.run([LAUNCHCTL, "print", f"system/{ownuser.BRIDGE}"], read_only=True)
        found = re.search(r"^\s*pid = (\d+)", out, re.MULTILINE)
        return int(found.group(1)) if found else None

    def signal_cli_running(self) -> bool:
        uid = ownuser.lookup_uid(ownuser.ROLE_USER)
        _, out = self.host.run([PS, "-axo", "uid=,command="], read_only=True)
        for line in out.splitlines():
            words = line.split(None, 1)
            if len(words) == 2 and words[0] == str(uid) and "signal-cli" in words[1] and "jsonRpc" in words[1]:
                return True
        return False

    def last_alive(self) -> float | None:
        python = self.host.path(ownuser.CURRENT) / ".venv" / "bin" / "python"
        argv = [SUDO, "-n", "-u", ownuser.ROLE_USER, "-H", "--", ENV, "-i", f"HOME={ownuser.HOME}"]
        code, out = self.host.run(
            [*argv, str(python), "-I", "-m", "local_assistant.deploy", "--read-alive", str(DB)],
            read_only=True,
            cwd=Path("/"),  # her user cannot enter the folder sudo was started from
        )
        try:
            return float(out.strip()) if code == 0 else None
        except ValueError:
            return None

    def _her_calls(self) -> bool:
        """Whether her config turns calls on, read as her by her release's Python (root never reads her files)."""
        python = self.host.path(ownuser.CURRENT) / ".venv" / "bin" / "python"
        argv = [SUDO, "-n", "-u", ownuser.ROLE_USER, "-H", "--", ENV, "-i", f"HOME={ownuser.HOME}"]
        config = ownuser.HOME / ownuser.HER_CONFIG
        code, out = self.host.run(
            [*argv, str(python), "-I", "-m", "local_assistant.deploy", "--read-calls", str(config)],
            read_only=True,
            cwd=Path("/"),  # her user cannot enter the folder sudo was started from
        )
        return code == 0 and out.strip() == "on"

    def health(self, since: float, old_pid: int | None = None) -> str | None:
        """None when, within 120 s of `since` (the restart), a bridge process other than `old_pid` stays the same for
        10 s while signal-cli runs as _estia and the bridge wrote last_alive after `since`; else what was missing."""
        deadline = since + HEALTH_SECONDS
        steady: tuple[int, float] | None = None
        problem = "the bridge did not start"
        while self.clock() < deadline:
            pid = self.bridge_pid()
            if pid is None:
                problem, steady = "the bridge did not stay up", None
            elif old_pid is not None and pid == old_pid:
                problem, steady = "the bridge did not restart (the old process still runs)", None
            elif not self.signal_cli_running():
                problem, steady = f"signal-cli did not start as {ownuser.ROLE_USER}", None
            elif (self.last_alive() or 0) < since:
                problem, steady = "the bridge wrote no sign of life (last_alive) since the restart", None
            elif steady is None or steady[0] != pid:
                steady = (pid, self.clock())
            elif self.clock() - steady[1] >= STABLE_SECONDS:
                return None
            self.sleep(POLL_SECONDS)
        return problem

    # ---------------------------------------------------------------- go live

    def switch(self, release: Path) -> Path | None:
        """Point `current` at `release` in one rename; the release it pointed at before, if any."""
        previous = self.current.resolve() if self.current.is_symlink() else None
        if self.dry:
            self.note(f"switch {self.current} to releases/{release.name}")
            return previous
        link = self.code / ".current.new"
        link.unlink(missing_ok=True)
        os.symlink(f"releases/{release.name}", link)
        os.replace(link, self.current)
        return previous if previous != release else None

    def prune(self, keep_also: Path | None) -> None:
        if self.dry or not self.releases.is_dir():
            return
        current = self.current.resolve() if self.current.is_symlink() else None
        names = sorted(p for p in self.releases.iterdir() if p.is_dir() and not p.name.startswith("."))
        protected = {p for p in (current, keep_also) if p is not None}
        extra = len(names) - self.keep
        for old in names:
            if extra <= 0:
                break
            if old.resolve() in protected:
                continue
            shutil.rmtree(old)
            extra -= 1
        for leftover in self.releases.glob(".build-*"):
            shutil.rmtree(leftover, ignore_errors=True)

    def tools_step(self, release: Path) -> None:
        """The first release installs the tools; a later one whose tool files differ only says so."""
        if not (self.tools / "tools.json").exists():
            self.install_tools(release)
            return
        if self.dry:  # nothing was built to compare with
            self.say("would compare the root-run tools with the new release's (--update-tools takes changed ones)")
            return
        changed = self.tool_changes(release)
        if changed:
            self.say(
                f"The root-run tools differ in this release ({', '.join(changed)}): the installed ones stay. "
                f"Review and take them: {DEPLOY} --update-tools"
            )

    def run(self, *, restart: bool = True) -> int:
        """Review, build, render, switch, restart, check; switch back on failure. Exit code 0 or 1. Without
        `restart` (the move into her own user) no review and no service: the release is built and switched."""
        try:
            sha, dirty = self.commit()
            if restart and not self.review(sha):
                self.say("Nothing deployed.")
                return 1
            release = self.build_release(sha, dirty)
        except DeployError as exc:
            self.say(f"✗ deploy stopped, nothing switched: {exc}")
            return 1
        if not restart:
            self.switch(release)
            self.install_tools(release)  # the move runs this checkout as root: a retry replaces an earlier try's
            self.prune(None)
            self.say(f"release ready: {release.name}")
            return 0
        try:
            plists = self.services(release)
        except DeployError as exc:
            self.say(f"✗ deploy stopped, nothing switched: {exc}")
            if not self.dry:
                shutil.rmtree(release, ignore_errors=True)
            return 1
        old_pid = self.bridge_pid() if self.check_health else None
        previous = self.switch(release)
        built = self.voice_source() if self.dry else release / VOICE_BINARY.parent  # a dry run built nothing
        new_voice = _digest(built / VOICE_BINARY.name) if built is not None else None
        voice_changed = previous is None or new_voice != _digest(previous / VOICE_BINARY)
        restarted = self.clock()
        self.go(plists, voice_changed)
        problem = self.health(restarted, old_pid) if self.check_health else None
        if problem is not None:
            self.say(f"✗ {release.name}: {problem}")
            if previous is not None:
                self.switch(previous)
                self.restart()
                self.say(f"switched back to {previous.name}; logs: {ownuser.ESTIA} logs launchd")
            return 1
        self.tools_step(release)
        self.prune(previous)
        lead = "would go live" if self.dry else "✓ live"
        self.say(f"{lead}: {release.name}" + (f" (before: {previous.name})" if previous else ""))
        if not self.dry and (voice_changed or self.voice) and (self.voice or self.installed(ownuser.VOICE)):
            # a new voice compiles again: when calls work (a text promised only when the bridge will send it)
            line = ownuser.voice_end(
                self.voice_health, calls=self.her_calls(), clock=self.clock, sleep=self.sleep, say=self.say
            )
            self.say(line)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="estia-deploy", description="Put a checkout's commit live as a release.")
    parser.add_argument("checkout", nargs="?", type=Path, help="default: the one the current release came from")
    parser.add_argument("--voice", action="store_true", help="also install and start the voice service")
    parser.add_argument("--vendor", type=Path, help="a folder with the JVM signal-cli and the call helper builds")
    parser.add_argument("--keep", type=int, default=KEEP, help=f"releases to keep (default {KEEP})")
    parser.add_argument("--dry-run", action="store_true", help="print every step and command, change nothing")
    parser.add_argument(
        "--update-tools", action="store_true", help="show the root-run tools' diff, then take the live release's"
    )
    parser.add_argument("--read-alive", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--read-calls", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.read_alive is not None:
        value = read_alive(args.read_alive)
        print(value or "")
        return 0 if value else 1
    if args.read_calls is not None:
        print("on" if read_calls(args.read_calls) else "off")
        return 0
    host = Host(estia_root(), dry_run=args.dry_run)
    if os.geteuid() != 0 and host.root == Path("/") and not args.dry_run:
        print(f"run it with sudo: {DEPLOY} [checkout]", file=sys.stderr)
        return 2
    try:
        who = builder()
        if args.update_tools:
            return Deploy(host, who, Path("/"), uv="").update_tools()
        checkout = args.checkout or default_checkout(host.path(ownuser.CODE))
        if not (checkout / ".git").exists():
            raise DeployError(f"{checkout} is not a git checkout")
        try:
            uv = find_uv(who)
        except DeployError:
            if not args.dry_run:
                raise
            uv = "/opt/homebrew/bin/uv"
        run = Deploy(host, who, checkout, uv=uv, keep=args.keep, voice=args.voice, vendor=args.vendor)
    except DeployError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    return run.run()


if __name__ == "__main__":
    raise SystemExit(main())
