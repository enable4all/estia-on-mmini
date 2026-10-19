"""The security step (harden): every check shown with why, applied only on a yes, sudo asked once, SSH tested before it
counts, passwordless sudo removed only while password sudo works, the Registration Lock set in a maintenance window
with the PIN only on stdin, and the phone checklist at the end. A fake Mac answers; nothing runs for real."""

import fnmatch
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from local_assistant import harden, hostsec
from local_assistant.hostsec import FDESETUP, LAUNCHCTL, SOCKETFILTERFW, SSHD, SUDO, Host

PIN = "731904"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleExample alex@example.com\n"


class FakeMac(Host):
    """Canned answers by argv (joined by spaces; "*" matches anything); argv and stdin recorded."""

    def __init__(self, root: Path, answers: dict[str, tuple[int, str]] | None = None, *, dry_run: bool = False) -> None:
        super().__init__(root, dry_run=dry_run, say=lambda text: None)
        self.answers = answers or {}
        self.asked: list[tuple[str, ...]] = []
        self.given: list[str | None] = []

    def run(self, argv: Sequence[str], **kwargs: Any) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        self.given.append(kwargs.get("input"))
        if self.dry_run and not kwargs.get("read_only"):
            return 0, ""
        joined = " ".join(argv)
        for pattern, answer in self.answers.items():
            if fnmatch.fnmatchcase(joined, pattern):
                return answer
        return 0, ""


def mac_state(root: Path, **overrides: tuple[int, str]) -> dict[str, tuple[int, str]]:
    state = {
        f"{SOCKETFILTERFW} --getglobalstate": (0, "Firewall is disabled. (State = 0)\n"),
        f"{SOCKETFILTERFW} --getstealthmode": (0, "Firewall stealth mode is off\n"),
        f"{SOCKETFILTERFW} --listapps": (
            0,
            "Total number of apps = 1 \n1 : /usr/bin/python3 \n (Allow incoming connections)\n",
        ),
        f"{LAUNCHCTL} print-disabled system": (0, '\t"com.openssh.sshd" => enabled\n'),
        f"{FDESETUP} status": (0, "FileVault is On.\n"),
        f"{hostsec.DSCL} . -read /Groups/admin GroupMembership": (0, "GroupMembership: root alex\n"),
        f"{SUDO} -n -l": (0, "User alex may run the following commands:\n    (ALL) ALL\n"),
        f"{SUDO} -n -k -l": (1, "sudo: a password is required\n"),
        f"{SSHD} -t*": (0, ""),
    }
    state.update(overrides)
    return state


def keychain(root: Path, home: Path) -> Path:
    """A login keychain that never locks itself (the fake Mac answers show-keychain-info)."""
    path = home / "Library" / "Keychains" / "login.keychain-db"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    return path


def mac(tmp_path: Path) -> tuple[Path, Path]:
    root, home = tmp_path / "root", tmp_path / "home"
    (root / "etc/ssh/sshd_config.d").mkdir(parents=True)
    (root / "etc/ssh/sshd_config").write_text("Include /etc/ssh/sshd_config.d/*\n")
    (root / "etc/ssh/sshd_config.d/100-macos.conf").write_text("UsePAM yes\n")
    (root / "etc/sudoers.d").mkdir(parents=True)
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh/authorized_keys").write_text(KEY)
    return root, home


def step(
    host: Host,
    home: Path,
    *,
    yes: Sequence[str] = (),
    pins: Sequence[str] = (),
    own_user: bool = False,
    said: list[str] | None = None,
    nuclear: bool = False,
    new_pin: bool = False,
    enter: bool = False,
) -> harden.Harden:
    """`yes`: words of the questions answered y; `enter`: every other question answered with Enter (its default)."""
    typed = iter(pins)
    asked: list[str] = []
    defaults: dict[str, bool] = {}

    def ask(question: str, default: bool = False) -> bool:
        asked.append(question)
        defaults[question] = default
        return any(word in question for word in yes) or (enter and default)

    run = harden.Harden(
        host,
        owner="alex",
        owner_home=home,
        own_user=own_user,
        ask=ask,
        hidden=lambda prompt: next(typed),
        say=(said.append if said is not None else lambda text: None),
        tailscale_tools=(),
        new_pin=new_pin,
        nuclear=nuclear,
    )
    run.questions = asked
    run.defaults = defaults  # type: ignore[attr-defined]
    return run


def test_only_what_was_said_yes_to_is_applied_after_one_sudo(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    host = FakeMac(root, mac_state(root))
    assert step(host, home, yes=["Turn on the firewall?", "stealth mode"], nuclear=True).run() == 0
    sudo_v = [i for i, argv in enumerate(host.asked) if argv == (SUDO, "-v")]
    applied = [argv for argv in host.asked if argv[:3] == (SUDO, "-n", SOCKETFILTERFW)]
    assert len(sudo_v) == 1 and applied == [
        (SUDO, "-n", SOCKETFILTERFW, "--setglobalstate", "on"),
        (SUDO, "-n", SOCKETFILTERFW, "--setstealthmode", "on"),
    ]
    assert sudo_v[0] < host.asked.index(applied[0])  # the password first, then the changes
    assert not any("--remove" in argv for argv in host.asked)  # python3 stays: no yes for it


def test_stealth_mode_is_its_own_question_and_stays_off_without_a_yes(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    host = FakeMac(root, mac_state(root))
    run = step(host, home, yes=["Turn on the firewall?"], nuclear=True)
    run.run()
    assert sum("stealth mode" in q for q in run.questions) == 1
    applied = [argv for argv in host.asked if argv[:3] == (SUDO, "-n", SOCKETFILTERFW)]
    assert applied == [(SUDO, "-n", SOCKETFILTERFW, "--setglobalstate", "on")]


def test_without_nuclear_the_strict_settings_are_one_neutral_line(tmp_path: Path) -> None:
    """The hardened level never asks about the firewall, stealth mode, the firewall's list or the keychain lock, never
    shows them as ! or ✗, and changes none of them: one neutral line names --nuclear."""
    root, home = mac(tmp_path)
    keychain(root, home)
    host = FakeMac(root, mac_state(root))
    said: list[str] = []
    run = step(host, home, yes=["firewall", "stealth", "python3", "keychain"], said=said)
    assert run.run() == 0
    strict = ("firewall", "stealth", "python3", "keychain")
    assert not any(word in q.casefold() for q in run.questions for word in strict)
    assert not any(SOCKETFILTERFW in argv or hostsec.SECURITY in argv for argv in host.asked if argv[0] == SUDO)
    assert not any(argv[:2] == (hostsec.SECURITY, "set-keychain-settings") for argv in host.asked)
    named = [line for line in said if "--nuclear" in line]
    assert len(named) == 1 and named[0].startswith("○ ") and "other services on this Mac" in named[0]
    marked = [line for line in said if line.startswith(("!", "✗"))]
    assert not any(word in line.casefold() for line in marked for word in strict), marked


def test_nuclear_says_first_that_it_may_stop_other_services(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    said: list[str] = []
    step(FakeMac(root, mac_state(root)), home, said=said, nuclear=True).run()
    first_check = next(i for i, line in enumerate(said) if line.startswith(("✓", "!", "✗")))
    warning = "\n".join(said[:first_check])
    assert "--nuclear" in warning and "other services on this Mac" in warning
    assert "stop answering" in warning and "ping" in warning and "password more often" in warning


def test_nuclear_takes_python3_off_the_list_and_locks_the_keychain_on_a_yes(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    path = keychain(root, home)
    host = FakeMac(root, mac_state(root))
    step(host, home, yes=["python3", "keychain"], nuclear=True).run()
    assert (SUDO, "-n", SOCKETFILTERFW, "--remove", "/usr/bin/python3") in host.asked
    assert (hostsec.SECURITY, "set-keychain-settings", "-l", "-u", "-t", "900", str(path)) in host.asked


def test_the_python3_question_says_it_holds_only_with_the_firewall_on() -> None:
    """Asked apart from the firewall: after a no to the firewall nothing is blocked, so the question says when."""
    question = harden.QUESTIONS["firewall_apps"]
    assert "(programs that run scripts)" in question and "With the firewall on, a script run with them" in question


def test_harden_takes_nuclear_from_its_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Recorded(harden.Harden):
        def __init__(self, host: Host, **kwargs: Any) -> None:
            seen.update(kwargs)
            super().__init__(host, **kwargs)

        def run(self) -> int:
            return 0

    monkeypatch.setattr(harden, "Harden", Recorded)
    assert harden.main(["--dry-run", "--nuclear"]) == 0 and seen["nuclear"] is True
    assert harden.main(["--dry-run"]) == 0 and seen["nuclear"] is False


def test_nothing_changes_without_a_yes_and_sudo_is_never_asked(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    host = FakeMac(root, mac_state(root))
    said: list[str] = []
    assert step(host, home, said=said, nuclear=True).run() == 0
    assert not any(argv[0] == SUDO and argv[1] != "-n" for argv in host.asked)
    assert not any(argv[:2] == (SUDO, "-n") and argv[2:] not in (("-l",), ("-k", "-l")) for argv in host.asked)
    text = "\n".join(said)
    assert "Firewall" in text and "FileVault" in text and "Signal → Settings → Linked devices" in text


def test_ssh_key_only_is_tested_and_taken_back_when_sshd_refuses_it(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    host = FakeMac(root, mac_state(root, **{f"{SUDO} -n {SSHD} -t": (1, "bad configuration")}))
    said: list[str] = []
    step(host, home, yes=["keys only"], said=said).run()
    install = next(argv for argv in host.asked if argv[:3] == (SUDO, "-n", hostsec.INSTALL))
    assert install[-1] == str(hostsec.SSH_DROP_IN_PATH)
    assert install.index(str(hostsec.SSH_DROP_IN_PATH)) == len(install) - 1 and hostsec.DROP_IN_SOURCE not in install
    assert (SUDO, "-n", hostsec.RM, "-f", str(hostsec.SSH_DROP_IN_PATH)) in host.asked  # taken back
    assert any("sshd refused" in line for line in said)


def test_the_ssh_drop_in_that_is_installed_holds_keys_only(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    seen: list[str] = []

    class Peek(FakeMac):
        def run(self, argv: Sequence[str], **kwargs: Any) -> tuple[int, str]:
            if tuple(argv[:3]) == (SUDO, "-n", hostsec.INSTALL):
                seen.append(Path(argv[-2]).read_text())
            return super().run(argv, **kwargs)

    host = Peek(root, mac_state(root))
    step(host, home, yes=["keys only"]).run()
    assert seen == [hostsec.SSH_DROP_IN]
    assert not any(argv[:4] == (SUDO, "-n", hostsec.RM, "-f") for argv in host.asked)  # sshd -t passed: it stays


def test_passwordless_sudo_is_offered_only_while_password_sudo_works(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    (root / "etc/sudoers.d/zz-alex-setup").write_text("alex ALL=(ALL) NOPASSWD: ALL\n")
    answers = mac_state(
        root,
        **{
            f"{SUDO} -n /bin/cat /etc/sudoers.d/zz-alex-setup": (0, "alex ALL=(ALL) NOPASSWD: ALL\n"),
            f"{SUDO} -n -k -l": (0, "User alex may run:\n    (ALL) ALL\n    (ALL) NOPASSWD: ALL\n"),
        },
    )
    host = FakeMac(root, answers)
    run = step(host, home, yes=["rule file(s)"])
    run.run()
    assert any("rule file(s)" in q for q in run.questions)
    assert (SUDO, "-n", hostsec.RM, "-f", "/etc/sudoers.d/zz-alex-setup") in host.asked
    assert host.asked.index((SUDO, "-k")) > host.asked.index(
        (SUDO, "-n", hostsec.RM, "-f", "/etc/sudoers.d/zz-alex-setup")
    )
    only_nopasswd = mac_state(
        root,
        **{
            f"{SUDO} -n /bin/cat /etc/sudoers.d/zz-alex-setup": (0, "alex ALL=(ALL) NOPASSWD: ALL\n"),
            f"{SUDO} -n -l": (0, "User alex may run:\n    (ALL) NOPASSWD: ALL\n"),
            f"{SUDO} -n -k -l": (0, "User alex may run:\n    (ALL) NOPASSWD: ALL\n"),
        },
    )
    host = FakeMac(root, only_nopasswd)
    run = step(host, home, yes=["rule file(s)"])
    run.run()
    assert not any("rule file(s)" in q for q in run.questions)  # removing it would lock you out of sudo
    assert not any(argv[:3] == (SUDO, "-n", hostsec.RM) for argv in host.asked)


def test_a_passwordless_rule_in_etc_sudoers_is_named_and_never_removed(tmp_path: Path) -> None:
    """Review of 15 Oct: a %admin NOPASSWD rule added with visudo sits in /etc/sudoers, not /etc/sudoers.d: it is
    found from what sudo itself lists, named, and its fix is visudo; harden never edits /etc/sudoers."""
    root, home = mac(tmp_path)
    listing = "User alex may run the following commands on mini:\n    (ALL) ALL\n    (ALL) NOPASSWD: ALL\n"
    host = FakeMac(root, mac_state(root, **{f"{SUDO} -n -k -l": (0, listing)}))
    said: list[str] = []
    step(host, home, yes=["rule file(s)"], said=said).run()
    assert "! Passwordless sudo: 1 sudo rule runs without your password (NOPASSWD)" in "\n".join(said)
    assert any("/usr/bin/sudo /usr/sbin/visudo" in line for line in said)
    assert not any(argv[:3] == (SUDO, "-n", hostsec.RM) for argv in host.asked)
    assert not any(argv[-1] == "/etc/sudoers" for argv in host.asked)


def test_with_your_password_harden_reads_the_sign_in_settings_too(tmp_path: Path) -> None:
    """One sudo sign-in for every program of yours (timestamp_type=global) is only listed with your password: harden
    reads it once sudo is asked (always in her own user)."""
    root, home = mac(tmp_path)
    listing = (
        "Matching Defaults entries for alex on mini:\n    env_reset, timestamp_type=global\n\n"
        "User alex may run the following commands on mini:\n    (ALL) ALL\n"
    )
    host = FakeMac(root, mac_state(root, **{f"{SUDO} -n -l": (0, listing)}))
    said: list[str] = []
    step(host, home, own_user=True, said=said).run()
    shown = "\n".join(said)
    assert "✓ Passwordless sudo: no sudo rule runs without your password" in shown  # the first look, without it
    assert "one sudo sign-in counts for every program of yours (timestamp_type=global)" in shown


def test_the_registration_lock_is_set_in_a_maintenance_window(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_text("x")
    her = {"data_modes": {"status": "ok"}, "registration_lock": {"status": "warn"}}
    answers = mac_state(
        root,
        **{
            f"{SUDO} -n -u _estia -H -- *--her-checks": (0, json.dumps(her)),
            f"{SUDO} -n -u _estia -H -- *--set-pin": (0, "Registration Lock: on\n"),
            f"{LAUNCHCTL} print system/com.local-assistant.bridge": (0, "pid = 77\n"),
        },
    )
    host = FakeMac(root, answers)
    said: list[str] = []
    run = step(host, home, yes=["Registration Lock"], pins=[PIN, PIN], own_user=True, said=said)
    assert run.run() == 0
    stop = host.asked.index((SUDO, "-n", LAUNCHCTL, "bootout", "system/com.local-assistant.bridge"))
    pin_call = next(i for i, argv in enumerate(host.asked) if argv[-1] == "--set-pin")
    start = host.asked.index(
        (SUDO, "-n", LAUNCHCTL, "bootstrap", "system", "/Library/LaunchDaemons/com.local-assistant.bridge.plist")
    )
    assert stop < pin_call < start
    assert "✓ Registration Lock: on; she runs again." in said
    assert host.given[pin_call] == f"{PIN}\n"  # stdin only
    assert all(PIN not in " ".join(argv) for argv in host.asked) and PIN not in "\n".join(said)
    assert any("password manager" in line for line in said)


def test_a_bridge_stopped_on_purpose_stays_stopped_after_a_new_pin(tmp_path: Path) -> None:
    """FULL3: after `/usr/bin/sudo /usr/local/estia/bin/estia stop` (step 1 of "If the Mac is compromised") her service
    file is still there, but launchd does not have her loaded. `--new-pin` (step 7) then neither stops nor starts her,
    and says she stays stopped, so step 8 (her linked devices) comes before she runs again."""
    root, home = mac(tmp_path)
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_text("x")
    her = {"data_modes": {"status": "ok"}, "registration_lock": {"status": "ok", "text": "on"}}
    stopped = 'Bad request.\nCould not find service "com.local-assistant.bridge" in domain for system\n'
    answers = {
        f"{SUDO} -n -u _estia -H -- *--her-checks": (0, json.dumps(her)),
        f"{SUDO} -n -u _estia -H -- *--set-pin": (0, "Registration Lock: on\n"),
        f"{LAUNCHCTL} print system/com.local-assistant.bridge": (113, stopped),
    }
    host = FakeMac(root, mac_state(root, **answers))
    said: list[str] = []
    run = step(host, home, yes=["Registration Lock"], pins=[PIN, PIN], own_user=True, new_pin=True, said=said)
    assert run.run() == 0
    assert any(argv[-1] == "--set-pin" for argv in host.asked)
    bridge = [
        argv for argv in host.asked if LAUNCHCTL in argv and argv[-1].endswith("com.local-assistant.bridge.plist")
    ]
    assert bridge == [] and (SUDO, "-n", LAUNCHCTL, "bootout", "system/com.local-assistant.bridge") not in host.asked
    assert "✓ Registration Lock: on; she stays stopped (she was stopped before)." in said


@pytest.mark.parametrize("answer", [(127, "TimeoutExpired"), (5, "Bad request.\n")], ids=["timeout", "other"])
def test_a_bridge_whose_state_cannot_be_read_is_stopped_for_the_pin_and_never_started(
    tmp_path: Path, answer: tuple[int, str]
) -> None:
    """Re-check of FULL3: launchctl's answer unread (a timeout, a message without "Could not find service") may be
    a bridge stopped on purpose. She is booted out (harmless when not loaded: one signal-cli per account), never
    started afterwards, and the line says how to start her."""
    root, home = mac(tmp_path)
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_text("x")
    her = {"data_modes": {"status": "ok"}, "registration_lock": {"status": "ok", "text": "on"}}
    answers = {
        f"{SUDO} -n -u _estia -H -- *--her-checks": (0, json.dumps(her)),
        f"{SUDO} -n -u _estia -H -- *--set-pin": (0, "Registration Lock: on\n"),
        f"{LAUNCHCTL} print system/com.local-assistant.bridge": answer,
    }
    host = FakeMac(root, mac_state(root, **answers))
    said: list[str] = []
    run = step(host, home, yes=["Registration Lock"], pins=[PIN, PIN], own_user=True, new_pin=True, said=said)
    assert run.run() == 0
    assert any(argv[-1] == "--set-pin" for argv in host.asked)
    assert (SUDO, "-n", LAUNCHCTL, "bootout", "system/com.local-assistant.bridge") in host.asked
    assert not any("bootstrap" in argv for argv in host.asked if argv[-1].endswith("com.local-assistant.bridge.plist"))
    line = next(line for line in said if line.startswith("✓ Registration Lock: on;"))
    assert "she stays stopped" in line and "/usr/bin/sudo /usr/local/estia/bin/estia start" in line, line


def test_a_new_pin_is_offered_with_the_lock_on_only_when_asked_for(tmp_path: Path) -> None:
    """After a compromise the PIN may be known: `scripts/harden.sh --new-pin` offers a new one also while the lock is
    on (SECURITY.md, "If the Mac is compromised", step 7); without it a lock that is on is left (review 15 Oct, 27)."""
    root, home = mac(tmp_path)
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_text("x")
    her = {"data_modes": {"status": "ok"}, "registration_lock": {"status": "ok", "text": "on"}}
    answers = {
        f"{SUDO} -n -u _estia -H -- *--her-checks": (0, json.dumps(her)),
        f"{SUDO} -n -u _estia -H -- *--set-pin": (0, "Registration Lock: on\n"),
        f"{LAUNCHCTL} print system/com.local-assistant.bridge": (0, "pid = 77\n"),
    }
    plain = step(FakeMac(root, mac_state(root, **answers)), home, yes=["Registration Lock"], own_user=True)
    plain.run()
    assert not any("Registration Lock" in q for q in plain.questions)
    host = FakeMac(root, mac_state(root, **answers))
    again = step(host, home, yes=["Registration Lock"], pins=[PIN, PIN], own_user=True, new_pin=True)
    assert again.run() == 0
    assert any("Registration Lock" in q for q in again.questions)
    assert any(argv[-1] == "--set-pin" for argv in host.asked)


def test_a_pin_typed_differently_twice_is_asked_again(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    run = step(FakeMac(root), home, pins=["1234", "1235", "q"])
    assert run.read_pin() is None
    run = step(FakeMac(root), home, pins=["12", "731904", "731904"])
    assert run.read_pin() == "731904"  # too short first, then a match


@pytest.mark.parametrize("nuclear", [False, True], ids=["default", "nuclear"])
def test_a_dry_run_shows_the_commands_and_asks_nothing(tmp_path: Path, nuclear: bool) -> None:
    root, home = mac(tmp_path)
    host = FakeMac(root, mac_state(root), dry_run=True)
    said: list[str] = []
    run = step(host, home, said=said, nuclear=nuclear)
    run.dry_run = True
    assert run.run() == 0
    assert run.questions == []
    text = "\n".join(said)
    assert (f"would run: {SUDO} -n {SOCKETFILTERFW} --setglobalstate on" in text) is nuclear
    assert (f"would run: {SUDO} -n {SOCKETFILTERFW} --setstealthmode on" in text) is nuclear
    assert "Signal → Settings → Linked devices" in text and "port-out" in text


class FakePipe:
    """signal-cli jsonRpc as a process: the request comes on stdin; the answer goes out on stdout."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.written: list[str] = []
        self.answer = answer
        self.argv: list[str] = []
        self.returncode = 0
        outer = self

        class In:
            def write(self, text: str) -> int:
                outer.written.append(text)
                return len(text)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                pass

        class Out:
            def readline(self) -> str:
                return json.dumps(outer.answer) + "\n" if outer.written else ""

        self.stdin = In()
        self.stdout = Out()

    def __call__(self, argv: list[str], **kwargs: Any) -> "FakePipe":
        self.argv = argv
        return self

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_set_pin_goes_over_json_rpc_on_stdin() -> None:
    pipe = FakePipe({"jsonrpc": "2.0", "result": {}, "id": 1})
    assert harden.set_pin("signal-cli", "+306900000000", PIN, popen=pipe) == (True, "")
    assert pipe.argv == [
        "signal-cli",
        "-Djava.net.preferIPv4Stack=true",  # one network path, as the bridge uses
        "--trust-new-identities",
        "on-first-use",
        "-a",
        "+306900000000",
        "jsonRpc",
        "--receive-mode",
        "manual",
    ]
    request = json.loads(pipe.written[0])
    assert request == {"jsonrpc": "2.0", "method": "setPin", "params": {"pin": PIN}, "id": 1}
    refused = FakePipe({"jsonrpc": "2.0", "error": {"code": -1, "message": "Rate limit"}, "id": 1})
    ok, why = harden.set_pin("signal-cli", "+306900000000", PIN, popen=refused)
    assert not ok and "Rate limit" in why and PIN not in why


def test_a_json_rpc_error_never_repeats_a_secret_sent_with_it() -> None:
    """setup's register and verify use the same one-request call: an error that echoes the code or the PIN shows a
    placeholder in its place."""
    echo = FakePipe({"jsonrpc": "2.0", "error": {"code": -1, "message": "code 111111 and pin 9876 refused"}, "id": 1})
    params = {"account": "+306900000000", "verificationCode": "111111", "pin": "9876"}
    secrets = {"111111": "<SMS code>", "9876": "<PIN>", "": "<empty>"}
    ok, why = harden.json_rpc(["signal-cli", "jsonRpc"], "verify", params, secrets=secrets, popen=echo)
    assert not ok and why == "code <SMS code> and pin <PIN> refused"
    assert json.loads(echo.written[0]) == {"jsonrpc": "2.0", "method": "verify", "params": params, "id": 1}


def test_a_json_rpc_call_without_an_answer_says_so() -> None:
    silent = FakePipe({"jsonrpc": "2.0", "result": {}, "id": 1})
    silent.written.append("x")  # readline answers nothing once the process is gone
    silent.stdout.readline = lambda: ""  # type: ignore[method-assign]
    ok, why = harden.json_rpc(["signal-cli", "jsonRpc"], "register", {"account": "+306900000000"}, popen=silent)
    assert not ok and "without an answer" in why


@pytest.mark.parametrize("pin", ["123", "12 34", "x" * 65])
def test_a_pin_must_be_4_to_64_characters_without_spaces(pin: str) -> None:
    assert harden.pin_problem(pin)
    assert harden.pin_problem("7319") == ""


# review fixes


def test_her_helper_is_read_as_data_and_never_runs_what_it_names(tmp_path: Path) -> None:
    """Her helper runs as _estia, which handles outside input: its answer gives a state, never a command. The fix is
    code's own, run as her (chmod as her cannot reach any file she does not own)."""
    root, home = mac(tmp_path)
    hostile = {
        "data_modes": {
            "status": "fail",
            "text": "2 files other accounts can read",
            "roots": [".config/local-assistant", "../../etc"],
            "apply": [[SUDO, "-n", "/bin/sh", "-c", "echo 'x ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/zz"]],
            "fix": "run this",
        },
        "registration_lock": {"status": "ok", "text": "on"},
    }
    host = FakeMac(root, mac_state(root, **{f"{SUDO} -n -u _estia -H -- *--her-checks": (0, json.dumps(hostile))}))
    step(host, home, yes=["private"], own_user=True).run()
    assert not any("/bin/sh" in argv or "-c" in argv[2:3] for argv in host.asked)
    applied = [argv for argv in host.asked if "/bin/chmod" in argv]
    assert applied == [(SUDO, "-n", "-u", "_estia", "/bin/chmod", "-R", "go-rwx", "/var/estia/.config/local-assistant")]


def test_her_files_are_asked_about_once(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    loose = home / ".config/local-assistant/config.toml"
    loose.parent.mkdir(parents=True)
    loose.write_text("x")
    loose.chmod(0o644)
    host = FakeMac(root, mac_state(root))
    run = step(host, home, yes=["private"])
    run.run()
    assert sum("private" in q for q in run.questions) == 1
    assert [argv for argv in host.asked if "/bin/chmod" in argv] == [
        ("/bin/chmod", "-R", "go-rwx", str(home / ".config/local-assistant"))
    ]  # once, as you: your own folders before the move


def test_no_signal_account_yet_skips_the_pin(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    run = step(FakeMac(root, mac_state(root)), home, yes=["Registration Lock"], pins=[PIN, PIN])
    run.run()
    assert not any("Registration Lock" in q for q in run.questions)


def test_she_is_started_again_when_the_pin_step_fails(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_text("x")

    class Broken(FakeMac):
        def run(self, argv: Sequence[str], **kwargs: Any) -> tuple[int, str]:
            if argv[-1] == "--set-pin":
                self.asked.append(tuple(argv))
                raise RuntimeError("the helper broke")
            return super().run(argv, **kwargs)

    host = Broken(root, mac_state(root))
    run = step(host, home, yes=["Registration Lock"], pins=[PIN, PIN], own_user=True)
    with pytest.raises(RuntimeError):
        run.registration_lock()
    assert host.asked[-1] == (
        SUDO,
        "-n",
        LAUNCHCTL,
        "bootstrap",
        "system",
        "/Library/LaunchDaemons/com.local-assistant.bridge.plist",
    )


def test_the_jvm_signal_cli_gets_ipv4_from_its_environment() -> None:
    pipe = FakePipe({"jsonrpc": "2.0", "result": {}, "id": 1})
    seen: dict[str, Any] = {}

    def popen(argv: list[str], **kwargs: Any) -> FakePipe:
        seen.update(kwargs)
        return pipe(argv)

    assert harden.set_pin("/x/jvm/bin/signal-cli", "+306900000000", PIN, popen=popen, jvm=True, java_home=Path("/j"))
    assert "-Djava.net.preferIPv4Stack=true" not in pipe.argv
    assert seen["env"]["JAVA_OPTS"] == "-Djava.net.preferIPv4Stack=true" and seen["env"]["JAVA_HOME"] == "/j"


def test_the_firewall_question_names_what_would_stop_answering(tmp_path: Path) -> None:
    """On a headless Mac nobody clicks "Allow": what listens on the network is named first, and each can be kept;
    doctor names a new one later."""
    root, home = mac(tmp_path)
    listening = "p501\ncnode\nf23\nn*:8170\n"
    answers = mac_state(
        root,
        **{
            f"{hostsec.LSOF} -nP -iTCP -sTCP:LISTEN -F pcn": (0, listening),
            "/bin/ps -o comm= -p 501": (0, "/opt/homebrew/Cellar/node/24.1.0/bin/node\n"),
        },
    )
    host = FakeMac(root, answers)
    run = step(host, home, yes=["Turn on the firewall?", "keep answering"], nuclear=True)
    run.run()
    question = next(q for q in run.questions if "Turn on the firewall?" in q)
    assert "node on port 8170" in question and "doctor" in question.casefold()
    node = "/opt/homebrew/Cellar/node/24.1.0/bin/node"
    assert (SUDO, "-n", SOCKETFILTERFW, "--add", node) in host.asked
    assert (SUDO, "-n", SOCKETFILTERFW, "--unblockapp", node) in host.asked


def test_the_firewall_question_without_listeners_says_doctor_names_them_later(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    run = step(FakeMac(root, mac_state(root)), home, nuclear=True)
    run.run()
    question = next(q for q in run.questions if "Turn on the firewall?" in q)
    assert "doctor" in question.casefold()


def test_a_listener_whose_program_is_unknown_is_named_with_the_way_to_allow_it(tmp_path: Path) -> None:
    """Never skipped silently (live 13 Oct): without its program's path it cannot be kept from here, so it is named."""
    root, home = mac(tmp_path)
    answers = mac_state(root, **{f"{hostsec.LSOF} -nP -iTCP -sTCP:LISTEN -F pcn": (0, "p501\ncnode\nf23\nn*:8170\n")})
    said: list[str] = []
    step(FakeMac(root, answers), home, yes=["Turn on the firewall?"], said=said, nuclear=True).run()
    line = next((text for text in said if "node" in text and "8170" in text and "Firewall" in text), "")
    assert "System Settings → Network → Firewall → Options" in line, said


def test_her_helpers_run_with_a_path_of_root_owned_folders_only(tmp_path: Path) -> None:
    """Homebrew's folders are yours: as her, nothing may be found there first."""
    root, home = mac(tmp_path)
    argv = step(FakeMac(root), home, own_user=True).as_her("--her-checks")
    path = next(arg for arg in argv if arg.startswith("PATH="))
    assert path == "PATH=/usr/local/estia/current/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def test_a_change_that_fails_says_why(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    answers = mac_state(
        root, **{f"{SUDO} -n {SOCKETFILTERFW} --setglobalstate on": (1, "Error: the firewall is managed\n")}
    )
    said: list[str] = []
    step(FakeMac(root, answers), home, yes=["Turn on the firewall?"], said=said, nuclear=True).run()
    assert any("not changed" in line and "the firewall is managed" in line for line in said)


@pytest.mark.parametrize("own_user", [False, True])
def test_a_link_out_of_her_folders_is_shown_and_no_chmod_is_offered_for_it(tmp_path: Path, own_user: bool) -> None:
    """No chmod fixes a link: the line shows ✗, and the question about her file modes is not asked."""
    root, home = mac(tmp_path)
    answers = mac_state(root)
    if own_user:
        her = {
            "data_modes": {"status": "fail", "text": "1 link out of her folders (first: x)", "roots": []},
            "registration_lock": {"status": "ok", "text": "on"},
        }
        answers[f"{SUDO} -n -u _estia -H -- *--her-checks"] = (0, json.dumps(her))
    else:
        data = home / ".local/share/signal-cli/data"
        data.mkdir(parents=True, mode=0o700)
        (home / ".local/share/signal-cli").chmod(0o700)  # private: only the link is wrong
        (data / "1.d").symlink_to(tmp_path / "elsewhere")
    said: list[str] = []
    run = step(FakeMac(root, answers), home, yes=["private"], own_user=own_user, said=said)
    run.sudo_ready = own_user
    run.run()
    assert any("link" in line and "out of her folders" in line for line in said)
    assert not any("private" in question for question in run.questions)


# Enter picks the safe answer (owner, 19 Oct): the hardened level's changes, none of which can lock anyone out; never
# the nuclear ones; keys only only once a key login is set up


def test_enter_takes_the_hardened_changes_and_never_the_nuclear_ones(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    keychain(root, home)
    loose = home / ".config/local-assistant/config.toml"
    loose.parent.mkdir(parents=True)
    loose.write_text("x")
    loose.chmod(0o644)
    (root / "etc/sudoers.d/zz-alex-setup").write_text("alex ALL=(ALL) NOPASSWD: ALL\n")
    answers = mac_state(
        root,
        **{
            f"{SUDO} -n /bin/cat /etc/sudoers.d/zz-alex-setup": (0, "alex ALL=(ALL) NOPASSWD: ALL\n"),
            f"{SUDO} -n -k -l": (0, "User alex may run:\n    (ALL) ALL\n    (ALL) NOPASSWD: ALL\n"),
            f"{SUDO} -n -l": (0, "User alex may run:\n    (ALL) ALL\n    (ALL) NOPASSWD: ALL\n"),
        },
    )
    host = FakeMac(root, answers)
    run = step(host, home, nuclear=True, enter=True)
    assert run.run() == 0
    defaults: dict[str, bool] = run.defaults  # type: ignore[attr-defined]
    words = ("keys only", "rule file(s)", "private")
    by_word = {word: [defaults[q] for q in defaults if word in q] for word in words}
    assert by_word == {"keys only": [True], "rule file(s)": [True], "private": [True]}
    strict = [q for q in defaults if any(word in q for word in ("firewall", "stealth", "python3", "keychain"))]
    assert strict and not any(defaults[q] for q in strict)  # the nuclear ones: Enter is a no
    assert any(argv[:3] == (SUDO, "-n", hostsec.INSTALL) for argv in host.asked)  # keys only
    assert (SUDO, "-n", hostsec.RM, "-f", "/etc/sudoers.d/zz-alex-setup") in host.asked
    assert ("/bin/chmod", "-R", "go-rwx", str(home / ".config/local-assistant")) in host.asked
    assert not any(argv[:3] == (SUDO, "-n", SOCKETFILTERFW) for argv in host.asked)
    assert not any(argv[:2] == (hostsec.SECURITY, "set-keychain-settings") for argv in host.asked)


def test_enter_offers_her_registration_lock(tmp_path: Path) -> None:
    root, home = mac(tmp_path)
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_text("x")
    her = {"data_modes": {"status": "ok"}, "registration_lock": {"status": "warn"}}
    answers = mac_state(
        root,
        **{
            f"{SUDO} -n -u _estia -H -- *--her-checks": (0, json.dumps(her)),
            f"{SUDO} -n -u _estia -H -- *--set-pin": (0, "Registration Lock: on\n"),
            f"{LAUNCHCTL} print system/com.local-assistant.bridge": (0, "pid = 77\n"),
        },
    )
    host = FakeMac(root, answers)
    run = step(host, home, pins=[PIN, PIN], own_user=True, enter=True)
    assert run.run() == 0
    defaults: dict[str, bool] = run.defaults  # type: ignore[attr-defined]
    assert [defaults[q] for q in defaults if "Registration Lock" in q] == [True]
    assert any(argv[-1] == "--set-pin" for argv in host.asked)


@pytest.mark.parametrize(
    ("sshd", "mode", "why"),
    [
        ("PubkeyAuthentication no\n", 0o644, "PubkeyAuthentication no"),
        ("AuthorizedKeysFile /etc/ssh/keys/%u\n", 0o644, "AuthorizedKeysFile"),
        ("", 0o664, "other accounts can change"),  # StrictModes: sshd ignores a key in a file others may change
    ],
    ids=["keys-off", "other-file", "loose-file"],
)
def test_keys_only_is_offered_only_once_ssh_takes_the_key(tmp_path: Path, sshd: str, mode: int, why: str) -> None:
    """Keys only with a key SSH would refuse locks every login out: never offered then; the line says what to fix,
    then to test a login with the key first."""
    root, home = mac(tmp_path)
    (root / "etc/ssh/sshd_config").write_text(sshd + "Include /etc/ssh/sshd_config.d/*\n")
    (home / ".ssh/authorized_keys").chmod(mode)
    host = FakeMac(root, mac_state(root))
    said: list[str] = []
    run = step(host, home, yes=["keys only"], enter=True, said=said)
    run.run()
    assert not any("keys only" in q for q in run.questions)
    assert not any(argv[:3] == (SUDO, "-n", hostsec.INSTALL) for argv in host.asked)
    fix = next(line for line in said if line.startswith("    → ") and "keys only" in line)
    assert why in fix and "test a login" in fix


def test_the_key_login_check_reads_what_sshd_reads(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh/authorized_keys").write_text(KEY)
    assert hostsec.key_login_problem(home, {}) == ""
    assert hostsec.key_login_problem(home, {"authorizedkeysfile": ".ssh/authorized_keys .ssh/authorized_keys2"}) == ""
    assert hostsec.key_login_problem(home, {"authorizedkeysfile": "%h/.ssh/authorized_keys"}) == ""
    assert hostsec.key_login_problem(home, {"strictmodes": "no", "pubkeyauthentication": "yes"}) == ""
    (home / ".ssh").chmod(0o777)
    assert "other accounts can change" in hostsec.key_login_problem(home, {})
    assert hostsec.key_login_problem(home, {"strictmodes": "no"}) == ""


def _key_home(tmp_path: Path, name: str = "home") -> Path:
    home = tmp_path / name
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh/authorized_keys").write_text(KEY)
    return home


def _linked_into_an_open_folder(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "open"
    folder.mkdir()
    (folder / "keys").write_text(KEY)
    folder.chmod(0o777)
    (home / ".ssh/authorized_keys").unlink()
    (home / ".ssh/authorized_keys").symlink_to(folder / "keys")


def _linked_to_an_open_file(tmp_path: Path, home: Path) -> None:
    (tmp_path / "keys").write_text(KEY)
    (tmp_path / "keys").chmod(0o666)
    (home / ".ssh/authorized_keys").unlink()
    (home / ".ssh/authorized_keys").symlink_to(tmp_path / "keys")


@pytest.mark.parametrize(
    ("settings", "change"),
    [
        ({"authorizedkeysfile": "~/.ssh/authorized_keys"}, None),  # sshd expands ~ as root: root's home
        ({"authorizedkeysfile": "~alex/.ssh/authorized_keys"}, None),
        ({}, _linked_into_an_open_folder),  # StrictModes checks the file a link leads to, and its folders
        ({}, _linked_to_an_open_file),
    ],
    ids=["tilde", "tilde-user", "link-into-open-folder", "link-to-open-file"],
)
def test_a_key_sshd_would_not_take_is_never_a_key_login(tmp_path: Path, settings: dict[str, str], change: Any) -> None:
    """Review, 19 Oct: each of these passed as set up, and a yes (Enter) turned passwords off while sshd ignored the
    key."""
    home = _key_home(tmp_path)
    if change is not None:
        change(tmp_path, home)
    assert hostsec.key_login_problem(home, settings, user="alex") != ""


def test_percent_u_is_the_account_name_not_the_home_folders_name(tmp_path: Path) -> None:
    home = _key_home(tmp_path, "home")  # a home folder named apart from its account
    named = {"authorizedkeysfile": f"{tmp_path}/%u/.ssh/authorized_keys"}
    assert "another file" in hostsec.key_login_problem(home, named, user="alex")
    assert hostsec.key_login_problem(home, named, user="home") == ""
    assert hostsec.key_login_problem(home, {"authorizedkeysfile": "%h/.ssh/authorized_keys"}, user="alex") == ""


@pytest.mark.parametrize(
    ("answer", "tty", "shown", "default", "said_yes", "hint"),
    [
        ("\n", True, True, True, True, "[Y/n]"),
        ("n\n", True, True, True, False, "[Y/n]"),
        ("\n", True, True, False, False, "[y/N]"),
        ("y\n", True, True, False, True, "[y/N]"),
        ("", True, True, True, False, "[Y/n]"),  # the end of input is never a yes
        ("\n", False, True, True, False, "[y/N]"),  # no terminal (a pipe): Enter keeps the no
        ("\n", True, False, True, False, "[y/N]"),  # the question sent to a file, unseen: Enter keeps the no
    ],
    ids=["enter", "n", "enter-no-default", "y", "end-of-input", "no-terminal", "output-to-a-file"],
)
def test_harden_asks_in_the_terminal_with_the_default_shown(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
    tty: bool,
    shown: bool,
    default: bool,
    said_yes: bool,
    hint: str,
) -> None:
    import io
    import sys

    class Typed(io.StringIO):
        def isatty(self) -> bool:
            return tty

    monkeypatch.setattr("sys.stdin", Typed(answer))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: shown)
    assert harden.terminal_ask("Safe?", default) is said_yes
    assert f"Safe? {hint} " in capsys.readouterr().out


@pytest.mark.parametrize(
    ("stdin", "stdout", "answers"), [(True, True, True), (True, False, False), (False, True, False)]
)
def test_a_terminal_answers_only_when_it_shows_the_question_too(
    monkeypatch: pytest.MonkeyPatch, stdin: bool, stdout: bool, answers: bool
) -> None:
    """The real check, unpatched (setup's Wizard.terminal and harden's terminal_ask both use it): output sent to a file
    hides the question, so Enter may mean yes only when both ends are the person's terminal."""

    class End:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    monkeypatch.setattr("sys.stdin", End(stdin))
    monkeypatch.setattr("sys.stdout", End(stdout))
    assert harden.in_terminal() is answers


def sudo_rule_mac(tmp_path: Path, rule: str) -> tuple[FakeMac, Path]:
    root, home = mac(tmp_path)
    (root / "etc/sudoers.d/zz-rule").write_text(rule)
    listing = "User alex may run:\n    (ALL) ALL\n    (ALL) NOPASSWD: ALL\n"
    answers = mac_state(
        root,
        **{
            f"{SUDO} -n /bin/cat /etc/sudoers.d/zz-rule": (0, rule),
            f"{SUDO} -n -k -l": (0, listing),
            f"{SUDO} -n -l": (0, listing),
        },
    )
    return FakeMac(root, answers), home


@pytest.mark.parametrize(
    ("rule", "enter"),
    [
        ("alex ALL=(ALL) NOPASSWD: ALL\n", True),
        ("%admin ALL = (ALL:ALL) NOPASSWD:ALL\n", True),
        ("zabbix ALL=(ALL) NOPASSWD: /usr/bin/true\n", False),  # another account's
        ("alex ALL=(root) NOPASSWD: /usr/bin/pmset\n", False),  # one program a tool of yours runs
        ("alex,ci ALL=(ALL) NOPASSWD: ALL\n", False),
        ("ALL ALL=(ALL) NOPASSWD: ALL\n", False),
    ],
    ids=["yours", "admins", "another-account", "one-program", "and-another-account", "every-account"],
)
def test_enter_removes_only_your_own_rule_for_every_program(tmp_path: Path, rule: str, enter: bool) -> None:
    """Review, 19 Oct: Enter = yes removed any rule file without a password, another account's or a tool's narrow one
    too, and what used it stopped. Enter takes yes only for a rule that gives you (or the admins) every program;
    another rule is asked about with Enter = no, and the question says what stops."""
    host, home = sudo_rule_mac(tmp_path, rule)
    run = step(host, home, enter=True)
    run.run()
    defaults: dict[str, bool] = run.defaults  # type: ignore[attr-defined]
    asked = [q for q in defaults if "zz-rule" in q]
    assert len(asked) == 1 and defaults[asked[0]] is enter
    assert ((SUDO, "-n", hostsec.RM, "-f", "/etc/sudoers.d/zz-rule") in host.asked) is enter
    if not enter:
        assert "stops at the password" in asked[0]


def test_the_password_asked_again_after_a_rule_is_removed_says_why_first(tmp_path: Path) -> None:
    """Review, 19 Oct: sudo -k, then sudo -v asks the Mac password at once; the line before it says why."""
    events: list[object] = []

    class Logged(FakeMac):
        def run(self, argv: Sequence[str], **kwargs: Any) -> tuple[int, str]:
            events.append(tuple(argv))
            return super().run(argv, **kwargs)

    host, home = sudo_rule_mac(tmp_path, "alex ALL=(ALL) NOPASSWD: ALL\n")
    host = Logged(host.root, host.answers)
    run = harden.Harden(
        host,
        owner="alex",
        owner_home=home,
        own_user=False,
        ask=lambda question, default: "zz-rule" in question,
        hidden=lambda prompt: "",
        say=events.append,
        tailscale_tools=(),
    )
    run.run()
    forget = events.index((SUDO, "-k"))
    check = events.index((SUDO, "-v"), forget)
    assert harden.PASSWORD_ONCE_MORE in events[forget:check]


def test_the_step_says_when_the_password_is_asked() -> None:
    """Asked when first needed and once more after a rule that skips it is removed; again only once 5 minutes have
    passed since admin rights were last used (sudo restarts its 5 minutes at each use, not only at the password)."""
    for text in (harden.PASSWORD_HERS, harden.PASSWORD_YOURS):
        assert "once 5 minutes have passed since admin rights were last used" in text
    assert "nothing shows while you type" in harden.PASSWORD_ONCE_MORE
    assert "once more after a rule that skips it is removed" in harden.PASSWORD_WHEN


@pytest.mark.parametrize("sshd_takes_it", [True, False], ids=["sshd-t-passes", "sshd-t-fails"])
def test_keys_only_prints_how_to_take_it_back_once_it_is_in_place(tmp_path: Path, sshd_takes_it: bool) -> None:
    """Review, 19 Oct: the undo line is the guard against a lockout, so it comes after the drop-in is installed and
    sshd -t passed, and never when sshd refused it (taken back already)."""
    root, home = mac(tmp_path)
    events: list[object] = []

    class Logged(FakeMac):
        def run(self, argv: Sequence[str], **kwargs: Any) -> tuple[int, str]:
            events.append(tuple(argv))
            return super().run(argv, **kwargs)

    host = Logged(root, mac_state(root, **({} if sshd_takes_it else {f"{SUDO} -n {SSHD} -t": (1, "bad")})))
    run = harden.Harden(
        host,
        owner="alex",
        owner_home=home,
        own_user=False,
        ask=lambda question, default: "keys only" in question,
        hidden=lambda prompt: "",
        say=events.append,
        tailscale_tools=(),
    )
    run.run()
    done = harden.SSH_DONE.format(undo=f"{SUDO} {hostsec.RM} {hostsec.SSH_DROP_IN_PATH}")
    assert (done in events) is sshd_takes_it
    if sshd_takes_it:
        install = next(
            i
            for i, event in enumerate(events)
            if isinstance(event, tuple) and event[:3] == (SUDO, "-n", hostsec.INSTALL)
        )
        check = events.index((SUDO, "-n", SSHD, "-t"))
        assert install < check < events.index(done)
        assert "type this here to take it back" in done


def test_the_keys_only_question_says_what_a_no_keeps() -> None:
    question = harden.QUESTIONS["ssh_password"]
    assert "No: passwords keep working." in question and not question.startswith(" ")


def test_each_hardened_question_says_what_a_no_does() -> None:
    """Enter takes yes at each of these (ENTER_YES): the question says what a no leaves, then the Recommendation."""
    for key in sorted(harden.ENTER_YES):
        question = harden.QUESTIONS[key]
        assert "No" in question.split("?")[-1] or "No:" in question, key
        assert "Recommendation: yes" in question, key
