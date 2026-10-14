"""The Mac's security settings around her: read-only checks, each with its fix and the commands that would apply it.

Every check reads through a `Host`: fixed argv with absolute tool paths, never a shell, and files under its root
(`ESTIA_ROOT`, default `/`), so tests and rehearsals give a fake or another root. Under another root the real `Host`
refuses the system tools (sudo, launchctl, the firewall, FileVault, the directory service, chown) and prints what it
would run, so a test run cannot change the Mac even by mistake.

A `Check` is ok (nothing to do), info (a fact, nothing to fix: a stricter setting that only `harden --nuclear` offers),
warn (worth fixing; she works) or fail (fix this first). `apply` holds the commands that fix it, in order: commands that
need root start with `sudo -n` (the caller asks for sudo once, before them). Doctor shows the checks of `doctor_checks`
and never applies anything; `harden` asks before it applies any.

The firewall, its stealth mode, the interpreters on its list and the keychain lock are the nuclear option
(`scripts/harden.sh --nuclear`): they may stop other services on this Mac, so outside it they are never a "!" or a "✗".

Nothing here returns or prints a secret: the Registration Lock is set or not set, never its PIN; the bot's number is only
used to find her account.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shlex
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["ok", "info", "warn", "fail"]

SUDO = "/usr/bin/sudo"
SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"
FDESETUP = "/usr/bin/fdesetup"
DEFAULTS = "/usr/bin/defaults"
SYSADMINCTL = "/usr/sbin/sysadminctl"
DSCL = "/usr/bin/dscl"
DSEDITGROUP = "/usr/sbin/dseditgroup"
LAUNCHCTL = "/bin/launchctl"
SECURITY = "/usr/bin/security"
SSHD = "/usr/sbin/sshd"
CHMOD = "/bin/chmod"
CHOWN = "/usr/sbin/chown"
INSTALL = "/usr/bin/install"
RM = "/bin/rm"
TAR = "/usr/bin/tar"
LSOF = "/usr/sbin/lsof"
PS = "/bin/ps"
NETSTAT = "/usr/sbin/netstat"
CODESIGN = "/usr/bin/codesign"
LIBPROC = "/usr/lib/libproc.dylib"
TAILSCALE_TOOLS = (
    "/opt/homebrew/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
)
# tools that change or read the system itself: refused (printed, never run) when the root is not "/"
PRIVILEGED = frozenset(
    {
        "sudo",
        "sysadminctl",
        "dscl",
        "dseditgroup",
        "launchctl",
        "socketfilterfw",
        "fdesetup",
        "chown",
        "sshd",
        "tailscale",
        "Tailscale",
    }
)
HARDEN = "scripts/harden.sh"
NUCLEAR = f"{HARDEN} --nuclear"
# one neutral line for the nuclear option, outside it (harden's default step; doctor with the firewall off)
NUCLEAR_ITEMS = "the firewall, stealth mode, python3 and ruby off the firewall's list, and the keychain lock"
NUCLEAR_LINE = f"{NUCLEAR_ITEMS}: only with {NUCLEAR}, which may stop other services on this Mac"
# the SSH drop-in that harden installs: read before macOS's own 100-macos.conf (sshd takes the first value it reads)
SSH_DROP_IN_PATH = Path("/etc/ssh/sshd_config.d/000-estia-keys-only.conf")
SSH_DROP_IN = (
    "# Installed by Estia's scripts/harden.sh: SSH with keys only. Remove this file to allow passwords again.\n"
    "PasswordAuthentication no\n"
    "KbdInteractiveAuthentication no\n"
    "PermitRootLogin no\n"
    "DenyUsers _estia\n"  # her role account: no password and no shell, and never an SSH login either
)
DROP_IN_SOURCE = "{drop-in}"  # in the SSH apply argv: harden puts the file it wrote there
INTERPRETERS = re.compile(r"^(python[0-9.]*|ruby)$")
KEYCHAIN_SECONDS = 900  # 15 minutes
_KEY_TYPES = re.compile(
    r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp\d+|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)$"
)


@dataclass(frozen=True)
class Check:
    key: str
    status: Status
    part: str
    text: str
    fix: str = ""
    apply: tuple[tuple[str, ...], ...] = ()


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def tail(output: str, lines: int = 5) -> str:
    """The last lines of a failing tool's output, on one line and without escape sequences (why it failed). Only for
    tools that never print a secret (git, uv, tar, dscl, sysadminctl, launchctl)."""
    kept = [line.strip() for line in output.splitlines() if line.strip()][-lines:]
    return _CONTROL.sub("?", " / ".join(kept))[:600]


def estia_root() -> Path:
    """The target root for system paths: ESTIA_ROOT (default "/"). Root ignores it: a sudo'd command never takes its
    targets from the environment."""
    if os.geteuid() == 0:
        return Path("/")
    value = os.environ.get("ESTIA_ROOT", "").strip()
    return Path(value) if value else Path("/")


class Host:
    """Runs fixed argv (absolute tools, no shell) and reads files under `root`. Under another root the system tools are
    refused and printed; in a dry run every change is printed instead of run (reads still run)."""

    def __init__(self, root: Path = Path("/"), *, dry_run: bool = False, say: Callable[[str], None] = print) -> None:
        self.root = root
        self.dry_run = dry_run
        self.say = say

    @property
    def live(self) -> bool:
        """The real Mac: root "/" and not a dry run (waits, health checks and the daemons only then)."""
        return self.root == Path("/") and not self.dry_run

    def path(self, system: str | Path) -> Path:
        """A system path under this root (/var/estia -> <root>/var/estia)."""
        target = Path(system)
        return self.root / target.relative_to("/") if target.is_absolute() else target

    def run(
        self,
        argv: Sequence[str],
        *,
        shown: Sequence[str] | None = None,
        input: str | None = None,
        read_only: bool = False,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 600.0,
    ) -> tuple[int, str]:
        """(exit code, stdout and stderr). `shown` replaces the argv in what is printed (a secret's placeholder)."""
        if not argv or not argv[0].startswith("/"):
            raise ValueError(f"tools are run by their absolute path, not {argv[:1]}")
        refused = self.root != Path("/") and Path(argv[0]).name in PRIVILEGED
        if refused or (self.dry_run and not read_only):
            self.say("would run: " + " ".join(shown if shown is not None else argv))
            return 0, ""
        try:
            done = subprocess.run(
                list(argv),
                input=input,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=timeout,
                check=False,
                stdin=None if input is not None else subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, type(exc).__name__
        return done.returncode, done.stdout + done.stderr

    def pipe(
        self, first: Sequence[str], second: Sequence[str], *, cwd: Path | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        """`first | second` without a shell (fixed argv, absolute tools): (the first non-zero exit code, both tools'
        stderr). Refused and printed like `run` (another root with a system tool, or a dry run). With `timeout`,
        both tools are killed when they are not done in that many seconds: (124, "timed out")."""
        for argv in (first, second):
            if not argv or not argv[0].startswith("/"):
                raise ValueError(f"tools are run by their absolute path, not {argv[:1]}")
        privileged = any(Path(argv[0]).name in PRIVILEGED for argv in (first, second))
        if self.dry_run or (self.root != Path("/") and privileged):
            self.say("would run: " + " ".join(first) + " | " + " ".join(second))
            return 0, ""
        with tempfile.TemporaryFile() as errors_one, tempfile.TemporaryFile() as errors_two:
            try:
                producer = subprocess.Popen(
                    list(first), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors_one, cwd=cwd
                )
            except OSError as exc:
                return 127, type(exc).__name__
            try:
                consumer = subprocess.Popen(
                    list(second), stdin=producer.stdout, stdout=subprocess.DEVNULL, stderr=errors_two, cwd=cwd
                )
            except OSError as exc:
                producer.kill()
                producer.wait()
                return 127, type(exc).__name__
            finally:
                if producer.stdout is not None:
                    producer.stdout.close()  # the consumer holds the pipe now; the producer sees it close
            deadline = None if timeout is None else time.monotonic() + timeout
            try:
                codes = tuple(
                    process.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))
                    for process in (producer, consumer)
                )
            except subprocess.TimeoutExpired:  # a reader that blocks (a FIFO swapped in) never hangs the caller
                for process in (producer, consumer):
                    process.kill()
                    process.wait()
                return 124, "timed out"
            except BaseException:  # an interruption: neither tool outlives the caller
                for process in (producer, consumer):
                    process.kill()
                    process.wait()
                raise
            errors_one.seek(0)
            errors_two.seek(0)
            said = (errors_one.read() + errors_two.read()).decode("utf-8", errors="replace")
        return next((code for code in codes if code != 0), 0), said

    def real_path(self, path: str) -> str:
        """A path with every link resolved (realpath), on the live Mac only; under another root it is left as is
        (a test never reads the live disk)."""
        return os.path.realpath(path) if self.root == Path("/") else path

    def program_path(self, pid: str) -> str:
        """A running process's program, by its pid: proc_pidpath, which names any user's process to any user (lsof
        names only your own). "" off the live Mac, or when the process is gone."""
        if self.root != Path("/") or not pid.isdecimal():
            return ""
        try:
            lib = ctypes.CDLL(LIBPROC, use_errno=True)
            lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            lib.proc_pidpath.restype = ctypes.c_int
            buffer = ctypes.create_string_buffer(4096)
            size = lib.proc_pidpath(int(pid), buffer, 4096)
        except (OSError, AttributeError, ValueError):
            return ""
        return buffer.raw[:size].decode("utf-8", errors="replace") if size > 0 else ""

    def read(self, path: Path) -> str | None:
        """A text file under this root; None when it is missing or cannot be read."""
        try:
            return self.path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def _sudo(*argv: str) -> tuple[str, ...]:
    return (SUDO, "-n", *argv)


# ------------------------------------------------------------------ the firewall


def _firewall_on(host: Host) -> tuple[bool | None, str]:
    """Whether the firewall is on (None: could not be read), and its state line."""
    code, state = host.run([SOCKETFILTERFW, "--getglobalstate"], read_only=True)
    return (None if code != 0 or "State =" not in state else "State = 0" not in state), state


def _stealth_on(host: Host) -> bool | None:
    _, stealth = host.run([SOCKETFILTERFW, "--getstealthmode"], read_only=True)
    if not stealth.strip():
        return None
    return bool(re.search(r"stealth mode is on|stealth mode enabled", stealth, re.I))


def firewall(host: Host) -> Check:
    """The application firewall (nuclear: programs that other machines reach stop answering them unless allowed)."""
    on, _ = _firewall_on(host)
    if on is None:
        return Check("firewall", "warn", "Firewall", "could not be read", "check System Settings → Network → Firewall")
    if on:
        return Check("firewall", "ok", "Firewall", "on")
    return Check(
        "firewall",
        "warn",
        "Firewall",
        "off: any program that listens can be reached from other machines",
        f"{NUCLEAR} turns it on (it asks first)",
        (_sudo(SOCKETFILTERFW, "--setglobalstate", "on"),),
    )


def stealth(host: Host) -> Check:
    """Stealth mode: no answer to ping or to probes of closed ports (nuclear: the Mac no longer answers ping)."""
    on = _stealth_on(host)
    if on is None:
        return Check("stealth", "warn", "Stealth mode", "could not be read")
    if on:
        return Check("stealth", "ok", "Stealth mode", "on: no answer to ping or to probes of closed ports")
    return Check(
        "stealth",
        "warn",
        "Stealth mode",
        "off: the Mac answers ping and probes of closed ports",
        f"{NUCLEAR} turns it on (it asks first)",
        (_sudo(SOCKETFILTERFW, "--setstealthmode", "on"),),
    )


def doctor_firewall(host: Host) -> Check:
    """Doctor's firewall line: on, it is fine (its listener lines follow); off or unreadable, one neutral line naming
    the nuclear option, never a "!" or a "✗" (it may stop other services on this Mac)."""
    on, _ = _firewall_on(host)
    if not on:
        state = "off" if on is False else "could not be read"
        return Check("firewall", "info", "Firewall", f"{state}. {NUCLEAR_LINE[0].upper()}{NUCLEAR_LINE[1:]}")
    hidden = _stealth_on(host)
    text = "on, with stealth mode: no answer to ping" if hidden else "on (stealth mode off: the Mac answers ping)"
    return Check("firewall", "ok", "Firewall", text)


def _listed_apps(out: str) -> list[tuple[str, bool]]:
    """(path, allowed) for each program in `socketfilterfw --listapps`."""
    apps: list[tuple[str, bool]] = []
    for line in out.splitlines():
        found = re.match(r"^\s*\d+\s*:\s*(/.*?)\s*$", line)
        if found:
            apps.append((found.group(1), False))
        elif apps and re.search(r"\(\s*allow incoming connections\s*\)", line, re.I):
            apps[-1] = (apps[-1][0], True)
    return apps


def firewall_apps(host: Host) -> Check:
    """Interpreters (python3, ruby) allowed to take incoming connections: any script could listen through them."""
    code, out = host.run([SOCKETFILTERFW, "--listapps"], read_only=True)
    if code != 0:
        return Check(
            "firewall_apps",
            "warn",
            "Firewall list",
            "could not be read",
            "check System Settings → Network → Firewall → Options",
        )
    allowed = [path for path, ok in _listed_apps(out) if ok and INTERPRETERS.match(Path(path).name)]
    if not allowed:
        return Check(
            "firewall_apps", "ok", "Firewall list", "no interpreter (python3, ruby) may take incoming connections"
        )
    return Check(
        "firewall_apps",
        "warn",
        "Firewall list",
        f"lets {', '.join(allowed)} take incoming connections: any script run with "
        f"{'it' if len(allowed) == 1 else 'one of them'} can listen on the network",
        f"{NUCLEAR} removes them from the list (it asks first). A local server of yours that others reach may then "
        "need its own entry: System Settings → Network → Firewall → Options",
        tuple(_sudo(SOCKETFILTERFW, "--remove", path) for path in allowed),
    )


def listeners(host: Host) -> list[tuple[str, str, str]]:
    """(name, port, program) for each of your programs that takes connections from other machines (not only from
    127.0.0.1 or ::1): what the firewall would stop answering unless it is allowed. Read with lsof as you (your own
    processes; nothing is changed)."""
    code, out = host.run([LSOF, "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn"], read_only=True)
    if code != 0:
        return []
    found: list[tuple[str, str, str]] = []
    pid = name = ""
    for line in out.splitlines():
        kind, value = line[:1], line[1:]
        if kind == "p":
            pid, name = value, ""
        elif kind == "c":
            name = value
        elif kind == "n" and pid.isdecimal():
            address, _, port = value.rpartition(":")
            if address in ("127.0.0.1", "[::1]", "localhost") or not port.isdecimal():
                continue
            if any(item[0] == name and item[1] == port for item in found):
                continue
            found.append((name, port, _executable(host, pid)))
    return found


# a listening socket in `netstat -anv -p tcp`: its local address, then (after four counters) its process and pid
_NETSTAT_LISTEN = re.compile(r"^tcp[46]*\s+\d+\s+\d+\s+(\S+)\s+\S+\s+LISTEN\s+(?:\d+\s+){4}(.+?):(\d+)\s")


def _only_this_mac(address: str) -> bool:
    return address in ("127.0.0.1", "::1", "localhost") or address.startswith("127.") or address.endswith("%lo0")


def everyones_listeners(host: Host) -> list[tuple[str, str, str]]:
    """(name, port, program) for each program of any user that takes connections from other machines: netstat names
    every socket's process (lsof as a user names only that user's, and doctor runs as her). The program comes from
    proc_pidpath, else lsof or ps; "" when none names it. The listeners of the user this runs as, from lsof
    (`listeners`), are added, since netstat started from some Pythons lists no internet sockets at all: as her (`estia
    doctor`) those are hers, so the owner's programs come from netstat alone; the owner's own from lsof only in
    scripts/doctor.sh before the move and in harden. Read-only, fixed argv."""
    found = _netstat_listeners(host)
    for name, port, program in listeners(host):
        if not any(
            port == seen_port and (program or name) == (seen or seen_name) for seen_name, seen_port, seen in found
        ):
            found.append((name, port, program))
    return found


def _netstat_listeners(host: Host) -> list[tuple[str, str, str]]:
    code, out = host.run([NETSTAT, "-anv", "-p", "tcp"], read_only=True)
    if code != 0:
        return []
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in out.splitlines():
        listening = _NETSTAT_LISTEN.match(line)
        if not listening:
            continue
        local, name, pid = listening.groups()
        address, _, port = local.rpartition(".")
        if not port.isdecimal() or _only_this_mac(address) or (pid, port) in seen:
            continue
        seen.add((pid, port))
        program = host.program_path(pid) or _executable(host, pid)
        found.append((Path(program).name if program else name.strip(), port, program))
    return found


def _allow_signed(host: Host) -> tuple[bool, bool]:
    """(built-in, downloaded): whether the firewall lets Apple's and other signed software through on its own
    (macOS's default: both; older macOS has one setting for both). Unreadable: the default."""
    _, out = host.run([SOCKETFILTERFW, "--getallowsigned"], read_only=True)
    single = re.search(r"allow signed software (ENABLED|DISABLED)", out, re.I)

    def setting(kind: str) -> bool:
        found = re.search(rf"{kind} signed software (ENABLED|DISABLED)", out, re.I) or single
        return found is None or found.group(1).upper() == "ENABLED"

    return setting("built-in"), setting("downloaded")


def _signer(host: Host, program: str) -> Literal["apple", "developer", "none", "unknown"]:
    """Who signed a program, read with codesign: Apple ("Software Signing"), a developer certificate, nobody (ad-hoc
    or unsigned) or unknown (codesign could not tell)."""
    code, out = host.run([CODESIGN, "-dvv", program], read_only=True)
    if "not signed at all" in out:
        return "none"
    if code != 0:
        return "unknown"
    authority = re.search(r"^Authority=(.+)$", out, re.MULTILINE)
    if authority is None or re.search(r"^Signature=adhoc", out, re.MULTILINE):
        return "none"
    return "apple" if authority.group(1).strip().endswith("Software Signing") else "developer"


def _ports(ports: Sequence[str]) -> str:
    ordered = sorted(set(ports), key=int)
    if len(ordered) == 1:
        return f"port {ordered[0]}"
    return f"ports {', '.join(ordered[:-1])} and {ordered[-1]}"


_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_TEMPORARY = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
_STARTED_IT = "if you started it and other machines should reach it:"


def _printable(text: str) -> str:
    """A process's name or path made safe for one doctor line: control characters (a newline, an escape) as "?"."""
    return _UNPRINTABLE.sub("?", text)


def _blocks_all(host: Host, state: str) -> bool:
    """Block all incoming connections: State = 2 on older macOS, else `--getblockall`."""
    if "State = 2" in state:
        return True
    _, out = host.run([SOCKETFILTERFW, "--getblockall"], read_only=True)
    return re.search(r"block all (state set to )?enabled", out, re.IGNORECASE) is not None


def _joined(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _opening_fix(program: str) -> str:
    """The two commands that let `program` through, after who should run them; none for a program in a temporary
    folder (something you did not start may run from there)."""
    if program.startswith(_TEMPORARY):
        return "it runs from a temporary folder: stop it if you did not start it; no firewall command is offered for it"
    return (
        f"{_STARTED_IT}\n"
        f"      {SUDO} {SOCKETFILTERFW} --add {shlex.quote(program)}\n"
        f"      {SUDO} {SOCKETFILTERFW} --unblockapp {shlex.quote(program)}"
    )


def firewall_listeners(host: Host) -> list[Check]:
    """Doctor's "!" line for each program that listens for other machines but that the firewall (when on) does not
    let through: not allowed on its list (`--listapps`, paths compared with links resolved) and not let through by
    its signature (ad-hoc or unsigned, or signed while that kind of signed software is not allowed on its own); one
    blocked on the list too. With block all on, one line names every listener. The fix is the two commands with the
    program's full path (none for one in a temporary folder); names and paths are printed without control
    characters. Nothing here changes the firewall. Nothing when the firewall is off or its list cannot be read."""
    code, state = host.run([SOCKETFILTERFW, "--getglobalstate"], read_only=True)
    if code != 0 or "State =" not in state or "State = 0" in state:
        return []
    code, out = host.run([SOCKETFILTERFW, "--listapps"], read_only=True)
    if code != 0:
        return []
    found = [(_printable(name), port, program) for name, port, program in everyones_listeners(host)]
    if _blocks_all(host, state):
        if not found:
            return []
        grouped: dict[str, tuple[str, list[str]]] = {}
        for name, port, program in found:
            grouped.setdefault(program or name, (name, []))[1].append(port)
        named = [f"{name} on {_ports(ports)}" for name, ports in grouped.values()]
        return [
            Check(
                "firewall_listener",
                "warn",
                "Firewall",
                f"block all incoming connections is on: {_joined(named)} cannot be reached from other machines",
                f"if other machines should reach them: {SUDO} {SOCKETFILTERFW} --setblockall off",
            )
        ]
    apps = [(host.real_path(path), ok) for path, ok in _listed_apps(out)]
    built_in, downloaded = _allow_signed(host)
    blocked: dict[str, tuple[str, list[str]]] = {}
    unknown: list[Check] = []
    for name, port, program in found:
        if not program or _UNPRINTABLE.search(program):
            why = "its program was not found" if not program else "its program's path has control characters"
            unknown.append(
                Check(
                    "firewall_listener",
                    "warn",
                    "Firewall",
                    f"{name} on port {port} may not be reachable from other machines ({why})",
                    f"find its program: {SUDO} {LSOF} -nP -iTCP:{port} -sTCP:LISTEN; {_STARTED_IT} "
                    f"{SUDO} {SOCKETFILTERFW} --add <its path>, then --unblockapp <its path>",
                )
            )
            continue
        real = host.real_path(program)
        listed = next(
            (ok for path, ok in apps if real == path or (path.endswith(".app") and real.startswith(f"{path}/"))),
            None,
        )
        if listed is None:
            signer = _signer(host, program)
            if signer == "unknown":
                continue  # cannot tell: nothing claimed
            listed = (signer == "apple" and built_in) or (signer == "developer" and downloaded)
        if not listed:
            blocked.setdefault(program, (name, []))[1].append(port)
    checks = [
        Check(
            "firewall_listener",
            "warn",
            "Firewall",
            f"{name} on {_ports(ports)} cannot be reached from other machines",
            _opening_fix(host.real_path(program)),
        )
        for program, (name, ports) in blocked.items()
    ]
    return checks + unknown


def _executable(host: Host, pid: str) -> str:
    """A process's program path: lsof's first txt entry (ps shows argv[0], only "node" for a program started by
    name); ps when lsof names none. "" when neither gives a path."""
    _, out = host.run([LSOF, "-nP", "-a", "-p", pid, "-d", "txt", "-Fn"], read_only=True)
    for line in out.splitlines():
        if line.startswith("n/"):
            return line[1:]
    _, program = host.run([PS, "-o", "comm=", "-p", pid], read_only=True)
    program = program.strip()
    return program if program.startswith("/") else ""


# ------------------------------------------------------------------ SSH


def sshd_settings(main: str, dropins: Sequence[tuple[str, str]]) -> dict[str, str]:
    """The settings sshd uses outside Match blocks: the first value read wins, and `Include` reads the drop-ins (in
    name order) where it stands. Keywords lower-case, values as written."""
    settings: dict[str, str] = {}
    stop = False

    def take(text: str, nested: bool) -> None:
        nonlocal stop
        for raw in text.splitlines():
            if stop:
                return
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            keyword, _, value = line.partition(" ")
            keyword = keyword.lower()
            if keyword == "match":
                stop = True
                return
            if keyword == "include" and not nested:
                for _name, included in sorted(dropins):
                    take(included, True)
                continue
            settings.setdefault(keyword, value.strip().strip('"'))

    take(main, False)
    return settings


def _remote_login(host: Host) -> bool | None:
    code, out = host.run([LAUNCHCTL, "print-disabled", "system"], read_only=True)
    found = re.search(r'"com\.openssh\.sshd"\s*=>\s*(enabled|disabled|true|false)', out) if code == 0 else None
    if found:
        return found.group(1) in ("enabled", "false")  # print-disabled: "false" means not disabled
    code, _ = host.run([LAUNCHCTL, "print", "system/com.openssh.sshd"], read_only=True)
    return code == 0


def has_key(text: str | None) -> bool:
    """Whether an authorized_keys file holds at least one public key (options before the key type are allowed)."""
    for line in (text or "").splitlines():
        words = line.strip().split()
        if not words or words[0].startswith("#"):
            continue
        for i, word in enumerate(words[:-1]):
            if _KEY_TYPES.match(word) and words[i + 1].startswith("AAAA"):
                return True
    return False


def _read_own(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def ssh_password(host: Host, key_home: Path | None = None) -> Check:
    """Remote Login with passwords (or keyboard-interactive, which PAM turns into a password). Key-only is offered only
    when Remote Login is on and `key_home`'s authorized_keys holds a key; doctor passes no home and never applies."""
    remote = _remote_login(host)
    if remote is False:
        return Check("ssh_password", "ok", "SSH", "Remote Login is off: no SSH")
    folder = host.path("/etc/ssh/sshd_config.d")
    try:
        names = sorted(p.name for p in folder.iterdir() if p.is_file())
    except OSError:
        names = []
    dropins = [(name, host.read(Path("/etc/ssh/sshd_config.d") / name) or "") for name in names]
    settings = sshd_settings(host.read(Path("/etc/ssh/sshd_config")) or "", dropins)
    methods = settings.get("authenticationmethods", "any").replace(" ", ",").split(",")
    keys_only = methods == ["publickey"] or (
        settings.get("passwordauthentication", "yes").lower() == "no"
        and settings.get("kbdinteractiveauthentication", settings.get("challengeresponseauthentication", "yes")).lower()
        == "no"
    )
    if keys_only:
        return Check("ssh_password", "ok", "SSH", "Remote Login is on, with keys only (no passwords)")
    text = "Remote Login is on and takes passwords: anyone who reaches the Mac can try them"
    if key_home is None:
        return Check("ssh_password", "warn", "SSH", text, f"{HARDEN} allows keys only (it asks first)")
    if not has_key(_read_own(key_home / ".ssh" / "authorized_keys")):  # the owner's home: never under the root
        return Check(
            "ssh_password",
            "warn",
            "SSH",
            text,
            "add your laptop's public key to ~/.ssh/authorized_keys and test a login with it; then "
            f"{HARDEN} allows keys only",
        )
    return Check(
        "ssh_password",
        "warn",
        "SSH",
        text,
        f"{HARDEN} allows keys only; this session stays open: test a new login before you close it",
        (
            _sudo(INSTALL, "-m", "644", "-o", "root", "-g", "wheel", DROP_IN_SOURCE, str(SSH_DROP_IN_PATH)),
            _sudo(SSHD, "-t"),
        ),
    )


# ------------------------------------------------------------------ the Mac's own settings (shown, never applied)


def filevault(host: Host) -> Check:
    code, out = host.run([FDESETUP, "status"], read_only=True)
    if code == 0 and "FileVault is On" in out:
        return Check("filevault", "ok", "FileVault", "on: with the Mac off, its disk cannot be read without a password")
    if code == 0 and "FileVault is Off" in out:
        return Check(
            "filevault",
            "warn",
            "FileVault",
            "off: anyone who carries the Mac away can read its disk, her Signal keys included",
            "System Settings → Privacy & Security → FileVault (keep the recovery key off the Mac)",
        )
    return Check("filevault", "warn", "FileVault", "could not be read", "run: fdesetup status")


def _defaults(host: Host, domain: str, key: str) -> str | None:
    prefs = host.path("/Library/Preferences") / domain
    code, out = host.run([DEFAULTS, "read", str(prefs), key], read_only=True)
    return out.strip() if code == 0 else None


def auto_login(host: Host) -> Check:
    user = _defaults(host, "com.apple.loginwindow", "autoLoginUser")
    if not user:
        return Check("auto_login", "ok", "Automatic login", "off: the Mac asks for a password at start")
    return Check(
        "auto_login",
        "warn",
        "Automatic login",
        f"on for {user}: the Mac starts into that account without a password",
        "System Settings → Users & Groups → Automatically log in as: Off. Then Ollama, a login item of yours, waits "
        'for a login after each restart, and she answers "isn\'t available" until then (setup_assistant.md, 2.8)',
    )


def screen_lock(host: Host) -> Check:
    code, out = host.run([SYSADMINCTL, "-screenLock", "status"], read_only=True)
    fix = "System Settings → Lock Screen → Require password after screen saver begins or display is turned off: Immediately"
    if code == 0 and "delay is immediate" in out:
        return Check("screen_lock", "ok", "Screen lock", "a password is asked at once after sleep or the screen saver")
    delay = re.search(r"delay is (\d+) seconds", out)
    if code == 0 and delay:
        return Check(
            "screen_lock", "warn", "Screen lock", f"a password is asked only after {delay.group(1)} seconds", fix
        )
    if code == 0 and "screenLock is off" in out:
        return Check("screen_lock", "warn", "Screen lock", "no password after sleep or the screen saver", fix)
    return Check("screen_lock", "warn", "Screen lock", "could not be read", fix)


def security_responses(host: Host) -> Check:
    if _defaults(host, "com.apple.SoftwareUpdate", "CriticalUpdateInstall") == "1":
        return Check("security_responses", "ok", "Security responses", "installed automatically")
    return Check(
        "security_responses",
        "warn",
        "Security responses",
        "not installed automatically: urgent macOS fixes wait for you",
        "System Settings → General → Software Update → (i) → Install Security Responses and system files: on",
    )


def sudoers(host: Host, contents: dict[str, str | None] | None = None) -> Check:
    """Rules in /etc/sudoers.d that let sudo run without a password. The files are readable only by root: pass
    `contents` (name -> text, None when unreadable) when they were read with sudo; names are shown, never rules."""
    if contents is None:
        folder = host.path("/etc/sudoers.d")
        try:
            names = sorted(p.name for p in folder.iterdir() if p.is_file())
        except OSError:
            names = []
        contents = {name: host.read(Path("/etc/sudoers.d") / name) for name in names}
    unread = sorted(name for name, text in contents.items() if text is None)
    rules = {
        name: [line.strip() for line in (text or "").splitlines() if line.strip() and not line.strip().startswith("#")]
        for name, text in contents.items()
    }
    passwordless = sorted(name for name, lines in rules.items() if any("NOPASSWD" in line for line in lines))
    if passwordless:
        removable = [name for name in passwordless if all("NOPASSWD" in line for line in rules[name])]
        return Check(
            "sudoers",
            "warn",
            "Passwordless sudo",
            f"{', '.join(passwordless)} in /etc/sudoers.d let sudo run without a password: any program you run "
            "can become root",
            f"{HARDEN} removes them once sudo works with your password (it asks first)",
            tuple(_sudo(RM, "-f", f"/etc/sudoers.d/{name}") for name in removable),
        )
    if unread:
        return Check(
            "sudoers",
            "warn",
            "Passwordless sudo",
            f"{len(unread)} rule file(s) in /etc/sudoers.d can be read only by root",
            f"{HARDEN} checks them, or run: sudo grep -l NOPASSWD /etc/sudoers.d/* (prints names only)",
        )
    return Check("sudoers", "ok", "Passwordless sudo", "no rule in /etc/sudoers.d skips the password")


def admins(host: Host, owner: str) -> Check:
    code, out = host.run([DSCL, ".", "-read", "/Groups/admin", "GroupMembership"], read_only=True)
    if code != 0:
        return Check("admins", "warn", "Admin accounts", "could not be read", "System Settings → Users & Groups")
    members = out.partition(":")[2].split()
    others = [name for name in members if name not in ("root", owner) and not name.startswith("_")]  # _: macOS's own
    if not others:
        return Check("admins", "ok", "Admin accounts", "no admin account besides yours")
    return Check(
        "admins",
        "warn",
        "Admin accounts",
        f"also {', '.join(others)}: each can become root and read her data",
        "give each a strong password, or take away the admin rights it does not need: System Settings → Users & Groups",
    )


def tailscale(
    host: Host, tools: Sequence[str] = TAILSCALE_TOOLS, exists: Callable[[str], bool] = os.path.exists
) -> Check:
    tool = next((path for path in tools if exists(path)), None)
    if tool is None:
        return Check("tailscale", "ok", "Tailscale", "not installed")
    code, out = host.run([tool, "debug", "prefs"], read_only=True)
    try:
        operator = str(json.loads(out).get("OperatorUser") or "") if code == 0 else ""
    except (ValueError, AttributeError):
        operator = ""
    _, funnel = host.run([tool, "funnel", "status"], read_only=True)
    if operator:
        return Check(
            "tailscale",
            "warn",
            "Tailscale",
            f"operator {operator}: that account can publish a port to the internet (Funnel) without sudo",
            f"/usr/bin/sudo {tool} set --operator=   then remove the funnel node attribute in the Tailscale admin console",
        )
    if funnel.strip() and "no serve config" not in funnel.lower():
        return Check(
            "tailscale",
            "warn",
            "Tailscale",
            "Serve or Funnel publishes a port of this Mac",
            f"{tool} funnel status; stop it with {tool} funnel reset",
        )
    return Check("tailscale", "ok", "Tailscale", "no operator account, no Funnel")


def keychain(host: Host, home: Path) -> Check:
    path = home / "Library" / "Keychains" / "login.keychain-db"
    if not path.exists():
        return Check("keychain", "ok", "Keychain", "no login keychain")
    code, out = host.run([SECURITY, "show-keychain-info", str(path)], read_only=True)
    timeout = re.search(r"timeout=(\d+)s", out)
    if code == 0 and timeout and 0 < int(timeout.group(1)) <= 3600:
        return Check("keychain", "ok", "Keychain", f"locks itself after {int(timeout.group(1)) // 60} minutes")
    return Check(
        "keychain",
        "warn",
        "Keychain",
        "your login keychain stays open once unlocked: any program you run can read the keys in it",
        f"{NUCLEAR} locks it after 15 minutes and at sleep (no sudo; macOS then asks for your password more often)",
        ((SECURITY, "set-keychain-settings", "-l", "-u", "-t", str(KEYCHAIN_SECONDS), str(path)),),
    )


# ------------------------------------------------------------------ her files and her Signal account

HER_FOLDERS = (
    ".config/local-assistant",
    "Library/Application Support/local-assistant",
    ".local/share/signal-cli",
    "Library/Logs/local-assistant",
)
VENDOR = "Library/Application Support/local-assistant/vendor"  # code, not her data: never copied into her home
LINK_OUT = "a link out of her folders"


def _leaves(path: Path, inside: str) -> bool:
    real = os.path.realpath(path)  # followed as the kernel does: a chain, `..` after a link, a dangling target
    return real != inside and not real.startswith(f"{inside}/")


def strays(top: Path, inside: Path, *, skip: Collection[Path] = ()) -> list[tuple[Path, str]]:
    """What under `top` (itself included) leads out of `inside` or is no file, folder or link, walked with lstat and
    nothing followed: a link whose target is not `inside` or under it (a dangling one too: whoever makes the target
    later owns it), a folder reached through such a link, a FIFO, a socket or a device. `inside` is a real path (no
    link in it); `skip`: folders left out (not copied)."""
    base = str(inside)
    left = {str(path) for path in skip}
    found: list[tuple[Path, str]] = []
    stack = [top]
    while stack:
        path = stack.pop()
        if str(path) in left:
            continue
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISLNK(mode):
            if _leaves(path, base):
                found.append((path, LINK_OUT))
        elif stat.S_ISFIFO(mode):
            found.append((path, "a FIFO (named pipe)"))
        elif stat.S_ISSOCK(mode):
            found.append((path, "a socket"))
        elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
            found.append((path, "a device"))
        elif stat.S_ISDIR(mode):
            if path == top and _leaves(path, base):  # a folder above it is a link
                found.append((path, LINK_OUT))
                continue
            try:
                names = sorted(os.listdir(path), reverse=True)
            except OSError:
                continue
            stack.extend(path / name for name in names)
    return found


def _links_out(home: Path) -> list[Path]:
    real = Path(os.path.realpath(home))
    return [
        path
        for name in HER_FOLDERS
        for path, what in strays(home / name, real / name, skip=[home / VENDOR])
        if what == LINK_OUT
    ]


def _counted(count: int, word: str) -> str:
    return f"{count} {word}" + ("s" if count != 1 else "")


def data_modes(home: Path, *, sudo: bool = False) -> Check:
    """Her config, data, Signal and log folders under `home`: folders 0700 and files 0600 (nothing for group or
    others), and no link out of them (top folder included: whoever owns where it leads reads and changes what she
    keeps there). Symlinks are never followed. `sudo`: the fix runs as root (her own user's folders)."""
    files: list[Path] = []
    folders: list[Path] = []
    roots: list[Path] = []
    links = _links_out(home)
    for name in HER_FOLDERS:
        top = home / name
        if not top.is_dir() or top.is_symlink():
            continue
        found = False
        for current, dirs, names in os.walk(top):
            here = Path(current)
            if stat.S_IMODE(here.lstat().st_mode) & 0o077:
                folders.append(here)
                found = True
            for entry in names:
                path = here / entry
                try:
                    info = path.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) & 0o077:
                    files.append(path)
                    found = True
            dirs[:] = [d for d in dirs if not (here / d).is_symlink()]
        if found:
            roots.append(top)
    if not files and not folders and not links:
        return Check(
            "data_modes", "ok", "Her files", "only their own account can read her folders and files (0700, 0600)"
        )
    texts: list[str] = []
    fixes: list[str] = []
    if files or folders:
        counted = " and ".join(
            part
            for part in (
                _counted(len(files), "file") if files else "",
                _counted(len(folders), "folder") if folders else "",
            )
            if part
        )
        texts.append(f"{counted} other accounts can read (first: {(files or folders)[0].relative_to(home)})")
        fixes.append(f"{HARDEN} makes them private (it asks first)")
    if links:
        texts.append(
            f"{_counted(len(links), 'link')} out of her folders (first: {links[0].relative_to(home)}): whoever owns "
            "where it leads reads and changes what she keeps there"
        )
        fixes.append("replace each link with the files it leads to")
    chmod = tuple((CHMOD, "-R", "go-rwx", str(top)) for top in roots)
    return Check(
        "data_modes",
        "fail",
        "Her files",
        "; ".join(texts),
        "; ".join(fixes),
        tuple(_sudo(*argv) for argv in chmod) if sudo else chmod,
    )


def registration_lock(signal_data: Path, number: str) -> bool | None:
    """Whether her Signal account has a Registration Lock PIN, from signal-cli's own account file. None: unreadable or
    no such account. The PIN itself is never returned. Without a number, the only account there."""
    try:
        listed = json.loads((signal_data / "data" / "accounts.json").read_text(encoding="utf-8")).get("accounts", [])
        entries = [a for a in listed if isinstance(a, dict)]
        matches = [a for a in entries if a.get("number") == number] if number else entries
        if len(matches) != 1 or not re.fullmatch(r"[0-9A-Za-z_.-]+", str(matches[0].get("path", ""))):
            return None
        account = json.loads((signal_data / "data" / str(matches[0]["path"])).read_text(encoding="utf-8"))
        return bool(account.get("registrationLockPin")) if isinstance(account, dict) else None
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return None


NO_ACCOUNT = "no Signal account for her number yet"


def registration_lock_state(signal_data: Path, number: str) -> Literal["on", "off", "none", "unknown"]:
    """ "on" or "off"; "none" when signal-cli holds no account for the number yet (nothing registered here);
    "unknown" when its account file cannot be read. Never the PIN."""
    try:
        listed = json.loads((signal_data / "data" / "accounts.json").read_text(encoding="utf-8")).get("accounts", [])
    except FileNotFoundError:
        return "none"
    except (OSError, ValueError, AttributeError):
        return "unknown"
    entries = [a for a in listed if isinstance(a, dict)] if isinstance(listed, list) else []
    if not (([a for a in entries if a.get("number") == number]) if number else entries):
        return "none"
    state = registration_lock(signal_data, number)
    return "unknown" if state is None else "on" if state else "off"


def registration_lock_check(home: Path, number: str = "") -> Check:
    state = registration_lock_state(home / ".local" / "share" / "signal-cli", number)
    if state == "on":
        return Check(
            "registration_lock",
            "ok",
            "Registration Lock",
            "on (the PIN is not shown). Keep the PIN in your password manager.",
        )
    if state == "off":
        return Check(
            "registration_lock",
            "warn",
            "Registration Lock",
            "off: whoever gets her SMS code can register her number elsewhere",
            f"{HARDEN} sets a PIN (keep it in your password manager)",
        )
    if state == "none":
        return Check(
            "registration_lock",
            "warn",
            "Registration Lock",
            f"{NO_ACCOUNT}: scripts/setup.sh registers it, then asks for the PIN",
        )
    return Check(
        "registration_lock",
        "warn",
        "Registration Lock",
        "her Signal account file could not be read",
        "/usr/bin/sudo /usr/local/estia/bin/estia doctor reads it as her; setup_assistant.md, 2.10",
    )


# ------------------------------------------------------------------ the sets


def doctor_checks(host: Host, home: Path, bot_number: str = "") -> list[Check]:
    """Doctor's lines: the firewall (on: fine, with a line for each listener it does not let through; off: one
    neutral line naming the nuclear option), SSH passwords, FileVault, her file modes and her Registration Lock.
    Read-only; the fixes name scripts/harden.sh, and nothing is applied."""
    return [
        doctor_firewall(host),
        *firewall_listeners(host),
        ssh_password(host),
        filevault(host),
        _shown(data_modes(home)),
        registration_lock_check(home, bot_number),
    ]


def _shown(check: Check) -> Check:
    return Check(check.key, check.status, check.part, check.text, check.fix)


def all_checks(
    host: Host, *, owner: str, owner_home: Path, her_home: Path, own_user: bool, bot_number: str = ""
) -> list[Check]:
    """Everything harden goes through, in its order: what it can apply first, then what it shows."""
    return [
        firewall(host),
        stealth(host),
        firewall_apps(host),
        ssh_password(host, key_home=owner_home),
        data_modes(her_home, sudo=own_user),
        keychain(host, owner_home),
        sudoers(host),
        registration_lock_check(her_home, bot_number),
        filevault(host),
        auto_login(host),
        screen_lock(host),
        security_responses(host),
        admins(host, owner),
        tailscale(host),
    ]
