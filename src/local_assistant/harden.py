"""scripts/harden.sh: the Mac's security around her, one setting at a time.

Each setting is shown with its state and one line on why it matters. What can be applied is applied only on a yes, and
sudo is asked once, before the first change (sudo's own cached sign-in; fixed argv, never a shell). The hardened level:
SSH goes to keys only only when Remote Login is on and your authorized_keys holds a key: a drop-in file, checked with
`sshd -t` and taken back if sshd refuses it; this session stays open. Passwordless sudo is read from what sudo lists
for you (/etc/sudoers and /etc/sudoers.d alike; with your password also the Defaults that share one sign-in with every
program of yours); a file in /etc/sudoers.d holding only such rules is offered for removal only while sudo also works
with your password, anything else is shown with its visudo fix. Her files are made private. What only you can change (FileVault,
automatic login, the screen lock, security responses, other admins, Tailscale) is shown with its fix, never applied.
The Registration Lock for her number is set in a short maintenance window: she is stopped, the PIN goes to signal-cli
over JSON-RPC on stdin (never argv, never printed), and she is started again. The phone checklist comes last.

`--nuclear`, the nuclear level, adds the firewall (each program that listens for other machines now is named, and may
keep answering), stealth mode (its own question), python3 and ruby off the firewall's list, and the keychain lock, after
saying first that they may stop other services on this Mac. Without it they are one neutral line. Nothing here turns
any of them off again: what a Mac already has stays. The run says its level first and ends with where doctor names the
level the Mac reached (hostsec.security_level).

`--set-pin` and `--her-checks` are the helpers that run as her own user (_estia) when she has one: the PIN on stdin.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import pwd
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from local_assistant import hostsec, ownuser
from local_assistant.hostsec import CHMOD, LAUNCHCTL, SOCKETFILTERFW, SUDO, Check, Host, estia_root

REPO = Path(__file__).resolve().parents[2]
TRUST = ("--trust-new-identities", "on-first-use")  # the identity rule the bridge pins too
IPV4 = "-Djava.net.preferIPv4Stack=true"  # signal_io.IPV4: one network path, as the bridge uses
PLIST = ownuser.DAEMONS / f"{ownuser.BRIDGE}.plist"
NOT_LOADED = "Could not find service"  # launchctl print's answer for a service launchd does not have (estia stop)
QUESTIONS = {
    "firewall": "Turn on the firewall?",
    "stealth": "Turn on stealth mode? The Mac then no longer answers ping or probes of closed ports.",
    "firewall_apps": (
        "Take python3 and ruby (programs that run scripts) off the firewall's list? With the firewall on, a script "
        "run with them then no longer takes connections from other machines."
    ),
    "ssh_password": "Allow SSH with keys only? This session stays open: test a new login before you close it.",
    "data_modes": "Make her files private (folders 0700, files 0600)?",
    "keychain": "Lock your login keychain after 15 minutes and at sleep? macOS then asks for your password more often.",
    "registration_lock": "Set a Registration Lock PIN for her number now? She is stopped for about a minute.",
}
NUCLEAR_KEYS = ("firewall", "stealth", "firewall_apps", "keychain")  # only with --nuclear
NUCLEAR_WARNING = (
    "--nuclear, the nuclear option: the strictest settings, the nuclear level. They may stop other services on this "
    "Mac:",
    "  • Programs that other machines reach (a file share, a media server, a page you open from your laptop) stop "
    "answering them unless you allow each one; the ones listening now are asked about.",
    "  • With stealth mode, the Mac no longer answers ping.",
    "  • With the keychain lock, macOS asks for your password more often (keychain prompts).",
    "Each one is asked first and changed only on your yes. Nothing here turns a setting off again.",
)
# the run's level, said first, and where the level reached is named (owner, 14 Oct: by name, never "default")
LEVEL_HARDENED = f"Security level: {hostsec.HARDENED} (the security step; {hostsec.NUCLEAR_LEVEL} is the other level)."
LEVEL_NUCLEAR = f"Security level: {hostsec.NUCLEAR_LEVEL} ({hostsec.HARDENED}, plus the settings above)."
LEVEL_REACHED = "Doctor names the level reached: {doctor}"
LOCK_NOTE = (
    "Registration Lock: a PIN Signal asks for before her number can be registered on another device. Store it in your "
    "password manager: without it and her data, the number is locked for 7 days."
)
PHONE_CHECKLIST = (
    "On your phone and the device with her SIM (no Mac needed):",
    "  • Signal → Settings → Linked devices: only devices you know.",
    "  • Signal → Settings → Account → Registration Lock: on, for your own number too.",
    "  • Signal → Settings → Privacy → Screen lock: on, 1 minute or less.",
    "  • Her chat → her name → View safety number: compare it with `/usr/bin/sudo /usr/local/estia/bin/estia signal "
    "listIdentities` on the Mac (setup_assistant.md, 7), then Mark as verified.",
    "  • The device with her SIM: a SIM PIN (not 0000), a screen lock, notifications that hide their content.",
    "  • Her carrier: a port-out or SIM-swap PIN; for a prepaid number, a reminder to top it up before it lapses.",
    "  • A Signal code by SMS that nobody asked for means someone tries to register her number.",
)


# ------------------------------------------------------------------ the Registration Lock over JSON-RPC


def pin_problem(pin: str) -> str:
    """Why a PIN cannot be used ("" when it can): 4 to 64 characters, no spaces."""
    if len(pin) < 4:
        return "at least 4 characters"
    if len(pin) > 64:
        return "at most 64 characters"
    if any(ch.isspace() for ch in pin):
        return "no spaces"
    return ""


def set_pin_argv(signal_cli: str, number: str, *, jvm: bool = False) -> list[str]:
    """IPv4 as the bridge uses it: an argument for Homebrew's native build, JAVA_OPTS for the JVM build."""
    return [signal_cli, *([] if jvm else [IPV4]), *TRUST, "-a", number, "jsonRpc", "--receive-mode", "manual"]


def json_rpc(
    argv: Sequence[str],
    method: str,
    params: Mapping[str, object],
    *,
    secrets: Mapping[str, str] | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    timeout: float = 90.0,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """One request to `signal-cli … jsonRpc` on stdin, its answer read from stdout: a secret goes in `params`, never
    in argv (ps shows a program's arguments to every user of the Mac). (done, why not); `secrets` maps each secret to
    the placeholder shown in its place, should signal-cli's error repeat it."""
    request = json.dumps({"jsonrpc": "2.0", "method": method, "params": dict(params), "id": 1})
    proc = popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=None if env is None else dict(env),
    )
    expired = threading.Event()

    def stop() -> None:
        expired.set()
        proc.kill()

    watchdog = threading.Timer(timeout, stop)
    watchdog.start()
    try:
        proc.stdin.write(request + "\n")
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            if not line:
                return (
                    False,
                    f"no answer within {timeout:.0f} s" if expired.is_set() else "signal-cli stopped without an answer",
                )
            try:
                answer = json.loads(line)
            except ValueError:
                continue
            if not isinstance(answer, dict) or answer.get("id") != 1:
                continue
            if "error" in answer:
                message = str((answer.get("error") or {}).get("message", "refused"))
                for secret, placeholder in sorted((secrets or {}).items(), key=lambda item: -len(item[0])):
                    if secret:
                        message = message.replace(secret, placeholder)
                return False, message[:200]
            return True, ""
    finally:
        watchdog.cancel()
        with contextlib.suppress(OSError):
            proc.stdin.close()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def set_pin(
    signal_cli: str,
    number: str,
    pin: str,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    timeout: float = 90.0,
    jvm: bool = False,
    java_home: Path | None = None,
) -> tuple[bool, str]:
    """Set her Registration Lock: signal-cli's JSON-RPC on stdin (receiving nothing meanwhile). (done, why not)."""
    env = (
        {**os.environ, "JAVA_OPTS": IPV4, **({"JAVA_HOME": str(java_home)} if java_home is not None else {})}
        if jvm
        else None
    )
    argv = set_pin_argv(signal_cli, number, jvm=jvm)
    return json_rpc(argv, "setPin", {"pin": pin}, secrets={pin: "<PIN>"}, popen=popen, timeout=timeout, env=env)


def _helper_set_pin() -> int:
    """--set-pin: the PIN on stdin; her number and signal-cli from her config (run as her user, or as you)."""
    from local_assistant.config import DEFAULT_CONFIG_PATH, ConfigError, find_java_home, load_config

    pin = sys.stdin.readline().strip()
    if pin_problem(pin):
        print(f"Registration Lock: not set ({pin_problem(pin)})")
        return 1
    try:
        cfg = load_config(DEFAULT_CONFIG_PATH)
    except ConfigError as exc:
        print(f"Registration Lock: not set (config: {exc})")
        return 1
    if cfg.calls_enabled:  # with calls she runs the JVM build: its Java, IPv4 in JAVA_OPTS
        java = cfg.java_home or find_java_home()
        done, why = set_pin(str(cfg.call_signal_cli), cfg.bot_number, pin, jvm=True, java_home=java)
    else:
        done, why = set_pin(cfg.signal_cli, cfg.bot_number, pin)
    print("Registration Lock: on" if done else f"Registration Lock: not set ({why})")
    return 0 if done else 1


def _helper_her_checks() -> int:
    """--her-checks: her file modes and her Registration Lock, as JSON (states, texts and folder names: no value, no
    PIN, no command), run as her user."""
    from local_assistant.config import DEFAULT_CONFIG_PATH, ConfigError, load_config

    try:
        number = load_config(DEFAULT_CONFIG_PATH).bot_number
    except ConfigError:
        number = ""
    home = Path.home()
    modes = hostsec.data_modes(home)
    lock = hostsec.registration_lock_check(home, number)
    roots = [str(Path(argv[-1]).relative_to(home)) for argv in modes.apply]
    print(
        json.dumps(
            {
                "data_modes": {"status": modes.status, "text": modes.text, "roots": roots},
                "registration_lock": {"status": lock.status, "text": lock.text},
            }
        )
    )
    return 0


HER_PARTS = {"data_modes": "Her files", "registration_lock": "Registration Lock"}


def her_check(key: str, data: dict[str, Any]) -> Check:
    """Her helper's answer, read as data only: a state and a text. It runs as _estia, which handles outside input,
    so a command or fix in it is never used: her files' fix is code's own, run as her (her chmod cannot reach a file
    she does not own), only for her known folders."""
    status = data.get("status") if data.get("status") in ("ok", "warn", "fail") else "warn"
    text = hostsec.tail(str(data.get("text", "")), 1)[:300]
    apply: tuple[tuple[str, ...], ...] = ()
    if key == "data_modes" and status != "ok":
        roots = data.get("roots")
        named = [str(name) for name in roots if str(name) in hostsec.HER_FOLDERS] if isinstance(roots, list) else []
        apply = tuple(
            (SUDO, "-n", "-u", ownuser.ROLE_USER, CHMOD, "-R", "go-rwx", str(ownuser.HOME / name))
            for name in (hostsec.HER_FOLDERS if roots is None else named)  # none named (a link only): no chmod
        )
    fix = f"{hostsec.HARDEN} makes them private (it asks first)" if apply else ""
    return Check(key, status, HER_PARTS[key], text, fix, apply)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the step


class Harden:
    def __init__(
        self,
        host: Host,
        *,
        owner: str,
        owner_home: Path,
        own_user: bool,
        ask: Callable[[str], bool],
        hidden: Callable[[str], str],
        say: Callable[[str], None],
        dry_run: bool | None = None,
        tailscale_tools: Sequence[str] = hostsec.TAILSCALE_TOOLS,
        checkout: Path = REPO,
        sleep: Callable[[float], None] = time.sleep,
        nuclear: bool = False,
        new_pin: bool = False,
    ) -> None:
        self.host = host
        self.new_pin = new_pin  # a new Registration Lock PIN also while the lock is on (after a compromise)
        self.owner = owner
        self.owner_home = owner_home
        self.own_user = own_user
        self.ask = ask
        self.hidden = hidden
        self.say = say
        self.dry_run = host.dry_run if dry_run is None else dry_run
        self.tailscale_tools = tailscale_tools
        self.checkout = checkout
        self.sleep = sleep
        self.nuclear = nuclear
        self.sudo_ready = False
        self.questions: list[str] = []

    # ---------------------------------------------------------------- small parts

    def show(self, check: Check) -> None:
        mark = {"ok": "✓", "info": "○", "warn": "!", "fail": "✗"}[check.status]
        self.say(f"{mark} {check.part}: {check.text}")
        if check.status != "ok" and check.fix:
            self.say(f"    → {check.fix}")

    def offer(self, question: str, commands: Sequence[Sequence[str]] = ()) -> bool:
        if self.dry_run:
            self.say(f"  would ask: {question}")
            for argv in commands:
                self.say("  would run: " + " ".join(argv))
            return False
        return self.ask(question)

    def sudo(self) -> bool:
        """sudo's password once (its own cached sign-in covers the rest)."""
        if self.sudo_ready:
            return True
        self.say("sudo asks for your Mac password once now, for the changes you said yes to.")
        code, _ = self.host.run([SUDO, "-v"])
        self.sudo_ready = code == 0
        if not self.sudo_ready:
            self.say("✗ sudo did not work: nothing that needs it was changed.")
        return self.sudo_ready

    def as_her(self, *argv: str) -> list[str]:
        python = str(ownuser.CURRENT / ".venv" / "bin" / "python")
        path = f"PATH={ownuser.HER_PATH}"  # root-owned folders only: Homebrew's are yours
        env = ["/usr/bin/env", "-i", f"HOME={ownuser.HOME}", path]
        return [
            SUDO,
            "-n",
            "-u",
            ownuser.ROLE_USER,
            "-H",
            "--",
            *env,
            python,
            "-I",
            "-m",
            "local_assistant.harden",
            *argv,
        ]

    @staticmethod
    def question(check: Check, listening: Sequence[tuple[str, str, str]]) -> str:
        """The yes/no question; the firewall's names what would stop answering other machines (on a Mac without a
        screen nobody can click "Allow"), and that doctor names a new one later."""
        if check.key != "firewall":
            return QUESTIONS[check.key]
        if not listening:
            return f"{QUESTIONS['firewall']} Nothing listens for other machines now; doctor names any program that does later."
        named = ", ".join(f"{name} on port {port}" for name, port, _program in listening)
        return (
            f"{QUESTIONS['firewall']} Listening for other machines now: {named}. Each may stop answering them unless "
            "you keep it (asked next); doctor names any new one later."
        )

    def keep_listening(self, listening: Sequence[tuple[str, str, str]]) -> None:
        """Each program that listens for other machines may stay reachable through the firewall, on a yes."""
        for name, port, program in listening:
            if not program:
                self.say(
                    f"! Firewall: {name} on port {port} may stop answering other machines (its program was not "
                    "found): allow it in System Settings → Network → Firewall → Options, or on the Mac when macOS asks"
                )
                continue
            argv = (
                (SUDO, "-n", SOCKETFILTERFW, "--add", program),
                (SUDO, "-n", SOCKETFILTERFW, "--unblockapp", program),
            )
            if self.offer(f"Let {name} keep answering other machines on port {port} ({program})?", argv):
                failed = [cmd for cmd in argv if self.host.run(list(cmd))[0] != 0]
                self.say(f"{'✗' if failed else '✓'} Firewall: {name} {'not changed' if failed else 'allowed'}")

    @staticmethod
    def root_free(check: Check) -> bool:
        return not any(argv[0] == SUDO for argv in check.apply)

    def apply(self, check: Check) -> None:
        if check.key == "ssh_password":
            self.ssh_keys_only(check)
            return
        why = ""
        for argv in check.apply:
            code, out = self.host.run(list(argv))
            if code != 0:
                why = hostsec.tail(out, 2) or f"exit {code}"
                break
        self.say(f"✗ {check.part}: not changed ({why})" if why else f"✓ {check.part}: done")

    def ssh_keys_only(self, check: Check) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "drop-in.conf"
            source.write_text(hostsec.SSH_DROP_IN, encoding="utf-8")
            source.chmod(0o644)
            install = [arg.replace(hostsec.DROP_IN_SOURCE, str(source)) for arg in check.apply[0]]
            if self.host.run(install)[0] != 0:
                self.say("✗ SSH: the drop-in could not be installed; nothing changed")
                return
            code, _ = self.host.run([SUDO, "-n", hostsec.SSHD, "-t"])
            if code != 0:
                self.host.run([SUDO, "-n", hostsec.RM, "-f", str(hostsec.SSH_DROP_IN_PATH)])
                self.say("✗ SSH: sshd refused the new setting, so it was taken back; passwords still work")
                return
        self.say(
            "✓ SSH: keys only from the next login on. Test a new login from your laptop before you close this one."
        )

    # ---------------------------------------------------------------- sudoers

    def sudoers(self, check: Check) -> None:
        """Passwordless sudo. With your password at hand (sudo asked), every rule and Default that applies to you is
        read again (sudo -n -l: the sign-in settings too, which the first look cannot read). A file in /etc/sudoers.d
        that holds only rules without the password is offered for removal while a rule with your password remains;
        anything else (a rule in /etc/sudoers, a Default) keeps its visudo fix: never edited here."""
        rules = ""
        if self.sudo_ready:
            code, rules = self.host.run([SUDO, "-n", "-l"], read_only=True)
            full = hostsec.sudo_listing(rules) if code == 0 else check
            if (full.status, full.text) != (check.status, check.text):
                self.show(full)
            check = full
        if check.status == "ok":
            return
        folder = self.host.path("/etc/sudoers.d")
        try:
            names = sorted(p.name for p in folder.iterdir() if p.is_file())
        except OSError:
            names = []
        contents: dict[str, str | None] = {name: self.host.read(Path("/etc/sudoers.d") / name) for name in names}
        if any(text is None for text in contents.values()) and self.sudo_ready:
            for name, text in list(contents.items()):
                if text is None:
                    code, out = self.host.run([SUDO, "-n", "/bin/cat", f"/etc/sudoers.d/{name}"], read_only=True)
                    contents[name] = out if code == 0 else None
        check = hostsec.sudoers(self.host, contents=contents)
        if not check.apply:
            return
        if not rules:
            _, rules = self.host.run([SUDO, "-n", "-l"], read_only=True)
        with_password = any(
            line.strip().startswith("(") and line.strip().endswith(") ALL") and "NOPASSWD" not in line
            for line in rules.splitlines()
        )
        if not with_password:
            self.say(
                "  Kept: no rule gives you sudo with your password, so removing it would lock you out of sudo. "
                "Add your account to the admins first (System Settings → Users & Groups)."
            )
            return
        rule_files = ", ".join(Path(argv[-1]).name for argv in check.apply)
        question = f"Remove the passwordless sudo rule(s) {rule_files}? sudo then asks for your password, as it should."
        if not self.offer(question, check.apply) or not self.sudo():
            return
        self.apply(check)
        self.host.run([SUDO, "-k"])
        code, _ = self.host.run([SUDO, "-v"])
        self.say(
            "✓ sudo asks for your password now." if code == 0 else "! sudo -v failed: check sudo in a new terminal."
        )

    # ---------------------------------------------------------------- the Registration Lock

    def read_pin(self) -> str | None:
        """A PIN typed twice, hidden; q (or no terminal) stops."""
        while True:
            pin = self.hidden("Registration Lock PIN (hidden; q to skip): ").strip()
            if pin.lower() == "q":
                return None
            problem = pin_problem(pin)
            if problem:
                self.say(f"  The PIN needs {problem}.")
                continue
            if self.hidden("The same PIN again (hidden): ").strip() != pin:
                self.say("  The two did not match; type it again.")
                continue
            return pin

    def registration_lock(self) -> None:
        self.say(LOCK_NOTE)
        steps = (
            [SUDO, "-n", LAUNCHCTL, "bootout", f"system/{ownuser.BRIDGE}"],
            [*self.pin_helper(), "(the PIN on stdin)"],
            [SUDO, "-n", LAUNCHCTL, "bootstrap", "system", str(PLIST)],
        )
        if not self.offer(QUESTIONS["registration_lock"], steps):
            return
        pin = self.read_pin()
        if pin is None:
            self.say("  Skipped: scripts/harden.sh offers it again.")
            return
        daemon = self.host.path(PLIST).exists()
        if daemon and not self.sudo():
            return
        # she runs again afterwards only when launchd had her before: one stopped on purpose (`estia stop`, the first
        # step after a compromise) stays stopped; an answer that cannot be read (a timeout, another message) may be
        # that too, so she is stopped for the PIN (harmless when not loaded: one signal-cli per account) and never
        # started again
        shown = self.host.run([LAUNCHCTL, "print", f"system/{ownuser.BRIDGE}"], read_only=True) if daemon else (1, "")
        running = daemon and shown[0] == 0
        unread = daemon and not running and NOT_LOADED not in shown[1]
        if (
            not daemon
            and self.host.run(["/usr/bin/pgrep", "-f", r"\.venv/bin/local-assistant"], read_only=True)[0] == 0
        ):
            self.say("✗ A bridge runs in a terminal: stop it (Ctrl-C), then run scripts/harden.sh again.")
            return
        if running or unread:
            self.host.run([SUDO, "-n", LAUNCHCTL, "bootout", f"system/{ownuser.BRIDGE}"])
            if self.host.live:
                for _ in range(50):  # one signal-cli per account: wait until hers is gone
                    if self.host.run([LAUNCHCTL, "print", f"system/{ownuser.BRIDGE}"], read_only=True)[0] != 0:
                        break
                    self.sleep(0.2)
        try:
            code, out = self.host.run(self.pin_helper(), input=f"{pin}\n", timeout=180, cwd=Path("/"))
        finally:
            pin = ""
            if running:  # she runs again, whatever happened to the PIN
                self.host.run([SUDO, "-n", LAUNCHCTL, "bootstrap", "system", str(PLIST)])
                if self.host.live:
                    for _ in range(30):
                        if (
                            "pid = "
                            in self.host.run([LAUNCHCTL, "print", f"system/{ownuser.BRIDGE}"], read_only=True)[1]
                        ):
                            break
                        self.sleep(2)
        result = out.strip().splitlines()[-1] if out.strip() else "Registration Lock: no answer"
        start = f"{ownuser.ESTIA} start" if self.own_user else f"{SUDO} {LAUNCHCTL} bootstrap system {PLIST}"
        after = (
            "; she runs again."
            if running
            else f"; she stays stopped (whether she ran before could not be read): start her with {start}"
            if unread
            else "; she stays stopped (she was stopped before)."
            if daemon
            else ""
        )
        self.say(("✓ " if code == 0 else "✗ ") + result + after)

    def pin_helper(self) -> list[str]:
        if self.own_user:
            return self.as_her("--set-pin")
        return [str(self.checkout / ".venv" / "bin" / "python"), "-I", "-m", "local_assistant.harden", "--set-pin"]

    # ---------------------------------------------------------------- the whole step

    def run(self) -> int:
        if self.nuclear:
            for line in NUCLEAR_WARNING:
                self.say(line)
            self.say("")
        self.say(LEVEL_NUCLEAR if self.nuclear else LEVEL_HARDENED)
        self.say(
            "The Mac's security around her: each setting, why it matters, and on your yes the fix. "
            "sudo is asked once, before the first change."
        )
        strict = (
            [
                hostsec.firewall(self.host),
                hostsec.stealth(self.host),
                hostsec.firewall_apps(self.host),
                hostsec.keychain(self.host, self.owner_home),
            ]
            if self.nuclear
            else []
        )
        visible = [
            *strict,
            hostsec.ssh_password(self.host, key_home=self.owner_home),
            hostsec.owner_sudo(self.host),  # what sudo lists for you: /etc/sudoers and /etc/sudoers.d alike
        ]
        if not self.own_user:
            visible += [hostsec.data_modes(self.owner_home), hostsec.registration_lock_check(self.owner_home)]
        shown_only = [
            hostsec.filevault(self.host),
            hostsec.auto_login(self.host),
            hostsec.screen_lock(self.host),
            hostsec.security_responses(self.host),
            hostsec.admins(self.host, self.owner),
            hostsec.tailscale(self.host, tools=self.tailscale_tools),
        ]
        for check in [*visible, *shown_only]:
            self.show(check)
        if not self.nuclear:  # never a "!" or a "✗": one neutral line
            self.show(
                Check("nuclear", "info", "Nuclear level", hostsec.NUCLEAR_LINE.removeprefix("the nuclear level: "))
            )
        listening = hostsec.listeners(self.host) if self.nuclear else []
        accepted = [
            check
            for check in visible
            if check.status != "ok"
            and check.apply
            and check.key not in ("sudoers", "data_modes", "registration_lock")  # her two: below, asked once
            and self.offer(self.question(check, listening), check.apply)
        ]
        wants_lock = False
        needs_root = any(argv[0] == SUDO for check in accepted for argv in check.apply)
        if (needs_root or self.own_user) and not self.dry_run and not self.sudo():
            accepted = [check for check in accepted if not any(argv[0] == SUDO for argv in check.apply)]
        for check in accepted:
            self.apply(check)
            if check.key == "firewall":
                self.keep_listening(listening)
        sudoers = next(check for check in visible if check.key == "sudoers")
        if sudoers.status != "ok" or self.sudo_ready:
            self.sudoers(sudoers)
        her = [check for check in visible if check.key in ("data_modes", "registration_lock")]
        if self.own_user and self.sudo_ready:
            her = self.her_checks()
        elif self.own_user and self.dry_run:
            self.say(f"  would read her file modes and her Registration Lock as {ownuser.ROLE_USER}:")
            self.say("  would run: " + " ".join(self.as_her("--her-checks")))
        for check in her:
            if self.own_user:
                self.show(check)
            private = check.key == "data_modes" and check.status != "ok" and bool(check.apply)  # a link: no chmod
            if private and self.offer(QUESTIONS["data_modes"], check.apply) and (self.root_free(check) or self.sudo()):
                self.apply(check)
            no_account = check.text.startswith(hostsec.NO_ACCOUNT)
            if check.key == "registration_lock" and (check.status != "ok" or self.new_pin) and not no_account:
                wants_lock = True
        if wants_lock or (self.dry_run and self.own_user):
            self.registration_lock()
        self.say(LEVEL_REACHED.format(doctor=ownuser.ESTIA + " doctor" if self.own_user else "scripts/doctor.sh"))
        self.say("")
        for line in PHONE_CHECKLIST:
            self.say(line)
        return 0

    def her_checks(self) -> list[Check]:
        code, out = self.host.run(self.as_her("--her-checks"), read_only=True, cwd=Path("/"))
        try:
            data = json.loads(out.strip().splitlines()[-1]) if code == 0 and out.strip() else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not data:
            self.say(f"! Her files and Registration Lock: could not be read as {ownuser.ROLE_USER}")
            return []
        return [her_check(key, value) for key, value in data.items() if key in HER_PARTS and isinstance(value, dict)]


def own_user_installed(host: Host) -> bool:
    """She runs as her own user: the bridge's service file says so (after a rollback it names you again)."""
    return ownuser.moved(host.root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harden", description="The Mac's security step around her.")
    parser.add_argument("--dry-run", action="store_true", help="show every check and command; change nothing")
    parser.add_argument(
        "--nuclear",
        action="store_true",
        help="also the firewall, stealth mode, python3 and ruby off the firewall's list and the keychain lock "
        "(may stop other services on this Mac)",
    )
    parser.add_argument(
        "--new-pin",
        action="store_true",
        help="offer a new Registration Lock PIN also while the lock is on (after a compromise: the old one may be known)",
    )
    parser.add_argument("--set-pin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--her-checks", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.set_pin:
        return _helper_set_pin()
    if args.her_checks:
        return _helper_her_checks()
    host = Host(estia_root(), dry_run=args.dry_run)
    me = pwd.getpwuid(os.getuid())

    def ask(question: str) -> bool:
        try:
            return input(f"{question} [y/N] ").strip().lower().startswith("y")
        except EOFError:
            print()
            return False

    def hidden(prompt: str) -> str:
        try:
            return getpass.getpass(prompt)
        except EOFError:
            return "q"

    step = Harden(
        host,
        owner=me.pw_name,
        owner_home=Path(os.environ.get("HOME") or me.pw_dir),
        own_user=own_user_installed(host),
        ask=ask,
        hidden=hidden,
        say=lambda text: print(text, flush=True),
        nuclear=args.nuclear,
        new_pin=args.new_pin,
    )
    return step.run()


if __name__ == "__main__":
    raise SystemExit(main())
