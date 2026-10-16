"""Host checks (hostsec): canned tool output gives the expected Check, fix and apply argv; nothing runs for real."""

import json
import os
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from local_assistant import hostsec
from local_assistant.hostsec import (
    FDESETUP,
    SOCKETFILTERFW,
    SUDO,
    Check,
    Host,
    data_modes,
    doctor_checks,
    registration_lock,
)

ENABLED = "Firewall is enabled. (State = 1)\n"
DISABLED = "Firewall is disabled. (State = 0)\n"
STEALTH_ON = "Firewall stealth mode is on\n"
STEALTH_OFF = "Firewall stealth mode is off\n"
APPS = (
    "Total number of apps = 3 \n"
    "1 : /usr/libexec/remoted \n"
    "             (Allow incoming connections)\n"
    "2 : /usr/bin/python3 \n"
    "             (Allow incoming connections)\n"
    "3 : /usr/bin/ruby \n"
    "             (Block incoming connections)\n"
)
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleExample alex@example.com\n"


class FakeHost(Host):
    """Canned answers per argv (joined by spaces); every argv asked is recorded. Files are read under root."""

    def __init__(self, root: Path, answers: dict[str, tuple[int, str]] | None = None) -> None:
        super().__init__(root)
        self.answers = answers or {}
        self.asked: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        return self.answers.get(" ".join(argv), (1, ""))


def fw(state: str = ENABLED, stealth: str = STEALTH_ON, apps: str = APPS) -> dict[str, tuple[int, str]]:
    return {
        f"{SOCKETFILTERFW} --getglobalstate": (0, state),
        f"{SOCKETFILTERFW} --getstealthmode": (0, stealth),
        f"{SOCKETFILTERFW} --listapps": (0, apps),
    }


def by_key(checks: list[Check]) -> dict[str, Check]:
    return {check.key: check for check in checks}


def test_the_firewall_on_is_ok_and_off_is_turned_on_with_sudo(tmp_path: Path) -> None:
    on = hostsec.firewall(FakeHost(tmp_path, fw(stealth=STEALTH_OFF)))
    assert (on.status, on.apply) == ("ok", ())  # stealth mode is its own check
    off = hostsec.firewall(FakeHost(tmp_path, fw(DISABLED, STEALTH_OFF)))
    assert off.status == "warn" and off.apply == ((SUDO, "-n", SOCKETFILTERFW, "--setglobalstate", "on"),)
    assert "scripts/harden.sh --nuclear" in off.fix


def test_stealth_mode_is_its_own_check(tmp_path: Path) -> None:
    on = hostsec.stealth(FakeHost(tmp_path, fw()))
    assert (on.status, on.apply) == ("ok", ())
    off = hostsec.stealth(FakeHost(tmp_path, fw(stealth=STEALTH_OFF)))
    assert off.status == "warn" and off.apply == ((SUDO, "-n", SOCKETFILTERFW, "--setstealthmode", "on"),)
    assert "ping" in off.text and "scripts/harden.sh --nuclear" in off.fix
    unread = hostsec.stealth(FakeHost(tmp_path, {}))
    assert unread.status == "warn" and unread.apply == ()


def test_an_unreadable_firewall_is_a_warning_without_apply(tmp_path: Path) -> None:
    check = hostsec.firewall(FakeHost(tmp_path, {}))
    assert check.status == "warn" and check.apply == () and "could not be read" in check.text


@pytest.mark.parametrize(
    "answers", [fw(DISABLED, STEALTH_OFF), fw(DISABLED), {}], ids=["off", "off with stealth", "unreadable"]
)
def test_doctor_names_the_nuclear_option_for_a_firewall_that_is_off_never_a_warning(
    tmp_path: Path, answers: dict[str, tuple[int, str]]
) -> None:
    """The firewall is part of scripts/harden.sh --nuclear: off, doctor says so in one neutral line, never ! or ✗."""
    line = hostsec.doctor_firewall(FakeHost(tmp_path, answers))
    assert line.status == "info" and line.fix == "" and line.apply == ()
    assert "scripts/harden.sh --nuclear" in line.text and "other services on this Mac" in line.text


@pytest.mark.parametrize(("stealth", "said"), [(STEALTH_ON, "with stealth mode"), (STEALTH_OFF, "answers ping")])
def test_doctor_shows_a_firewall_that_is_on_as_fine(tmp_path: Path, stealth: str, said: str) -> None:
    line = hostsec.doctor_firewall(FakeHost(tmp_path, fw(ENABLED, stealth)))
    assert line.status == "ok" and said in line.text


def test_allowed_interpreters_are_removed_from_the_firewall_list(tmp_path: Path) -> None:
    check = hostsec.firewall_apps(FakeHost(tmp_path, fw()))
    assert check.status == "warn" and "/usr/bin/python3" in check.text and "/usr/bin/ruby" not in check.text
    assert "any script run with it can listen" in check.text
    both = APPS.replace("(Block incoming connections)", "(Allow incoming connections)")
    assert "any script run with one of them can listen" in hostsec.firewall_apps(FakeHost(tmp_path, fw(apps=both))).text
    assert check.apply == ((SUDO, "-n", SOCKETFILTERFW, "--remove", "/usr/bin/python3"),)
    assert "scripts/harden.sh --nuclear" in check.fix
    clean = hostsec.firewall_apps(FakeHost(tmp_path, fw(apps="Total number of apps = 0 \n")))
    assert (clean.status, clean.apply) == ("ok", ())


def sshd(root: Path, main: str, dropins: dict[str, str]) -> None:
    folder = root / "etc" / "ssh" / "sshd_config.d"
    folder.mkdir(parents=True)
    (root / "etc" / "ssh" / "sshd_config").write_text(main, encoding="utf-8")
    for name, text in dropins.items():
        (folder / name).write_text(text, encoding="utf-8")


MAIN = "Include /etc/ssh/sshd_config.d/*\nAuthorizedKeysFile .ssh/authorized_keys\n"
MACOS = {"100-macos.conf": "UsePAM yes\nAcceptEnv LANG LC_*\n"}
REMOTE_ON = {f"{hostsec.LAUNCHCTL} print-disabled system": (0, '\t"com.openssh.sshd" => enabled\n')}


def test_the_effective_sshd_settings_take_the_first_value_in_include_order() -> None:
    settings = hostsec.sshd_settings(
        "Include /etc/ssh/sshd_config.d/*\nPasswordAuthentication yes\nMatch User alex\n  PasswordAuthentication yes\n",
        [("000-estia.conf", "PasswordAuthentication no\n"), ("100-macos.conf", "PasswordAuthentication yes\n")],
    )
    assert settings["passwordauthentication"] == "no"


@pytest.mark.parametrize(
    ("dropins", "status"),
    [
        (MACOS, "warn"),  # macOS default: passwords and keyboard-interactive (PAM) allowed
        ({**MACOS, "000-estia-keys-only.conf": "PasswordAuthentication no\n"}, "warn"),  # PAM still asks for one
        (
            {**MACOS, "000-estia-keys-only.conf": "PasswordAuthentication no\nKbdInteractiveAuthentication no\n"},
            "ok",
        ),
        ({**MACOS, "000-x.conf": "AuthenticationMethods publickey\n"}, "ok"),
    ],
)
def test_ssh_password_login_is_found_in_the_config_and_its_drop_ins(
    tmp_path: Path, dropins: dict[str, str], status: str
) -> None:
    sshd(tmp_path, MAIN, dropins)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "authorized_keys").write_text(KEY, encoding="utf-8")
    check = hostsec.ssh_password(FakeHost(tmp_path, REMOTE_ON), key_home=home)
    assert check.status == status
    if status == "warn":
        assert check.apply and check.apply[0][:3] == (SUDO, "-n", hostsec.INSTALL)
        assert any(argv[2:] == (hostsec.SSHD, "-t") for argv in check.apply)  # tested before it counts
    else:
        assert check.apply == ()


def test_ssh_key_only_is_offered_only_with_a_key_in_authorized_keys(tmp_path: Path) -> None:
    sshd(tmp_path, MAIN, MACOS)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "authorized_keys").write_text("# no key yet\n", encoding="utf-8")
    check = hostsec.ssh_password(FakeHost(tmp_path, REMOTE_ON), key_home=home)
    assert check.status == "warn" and check.apply == () and "authorized_keys" in check.fix


def test_ssh_with_remote_login_off_is_ok_and_never_changed(tmp_path: Path) -> None:
    sshd(tmp_path, MAIN, MACOS)
    off = {f"{hostsec.LAUNCHCTL} print-disabled system": (0, '\t"com.openssh.sshd" => disabled\n')}
    check = hostsec.ssh_password(FakeHost(tmp_path, off), key_home=tmp_path)
    assert (check.status, check.apply) == ("ok", ())
    assert "Remote Login is off" in check.text


def test_the_ssh_drop_in_turns_off_passwords_and_keyboard_interactive() -> None:
    text = hostsec.SSH_DROP_IN
    for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitRootLogin no"):
        assert line in text.splitlines()
    assert "AllowUsers" not in text  # no guessing who may log in
    assert hostsec.SSH_DROP_IN_PATH.parent == Path("/etc/ssh/sshd_config.d")
    assert hostsec.SSH_DROP_IN_PATH.name < "100-macos.conf"  # read first: the first value wins


@pytest.mark.parametrize(
    ("out", "status"),
    [("FileVault is On.\n", "ok"), ("FileVault is Off.\n", "warn"), ("", "warn")],
)
def test_filevault(tmp_path: Path, out: str, status: str) -> None:
    check = hostsec.filevault(FakeHost(tmp_path, {f"{FDESETUP} status": (0 if out else 1, out)}))
    assert check.status == status and check.apply == ()  # shown with the fix, never applied


def test_auto_login_screen_lock_and_security_responses(tmp_path: Path) -> None:
    prefs = str(tmp_path / "Library" / "Preferences")
    answers = {
        f"{hostsec.DEFAULTS} read {prefs}/com.apple.loginwindow autoLoginUser": (0, "alex\n"),
        f"{hostsec.SYSADMINCTL} -screenLock status": (
            0,
            "2026-10-13 sysadminctl[1:2] screenLock delay is 300 seconds\n",
        ),
        f"{hostsec.DEFAULTS} read {prefs}/com.apple.SoftwareUpdate CriticalUpdateInstall": (1, "does not exist\n"),
    }
    host = FakeHost(tmp_path, answers)
    assert hostsec.auto_login(host).status == "warn"
    assert hostsec.screen_lock(host).status == "warn"
    assert hostsec.security_responses(host).status == "warn"
    answers = {
        f"{hostsec.DEFAULTS} read {prefs}/com.apple.loginwindow autoLoginUser": (1, "does not exist"),
        f"{hostsec.SYSADMINCTL} -screenLock status": (0, "sysadminctl[1:2] screenLock delay is immediate\n"),
        f"{hostsec.DEFAULTS} read {prefs}/com.apple.SoftwareUpdate CriticalUpdateInstall": (0, "1\n"),
    }
    host = FakeHost(tmp_path, answers)
    for check in (hostsec.auto_login(host), hostsec.screen_lock(host), hostsec.security_responses(host)):
        assert (check.status, check.apply) == ("ok", ()), check


def test_passwordless_sudo_rules(tmp_path: Path) -> None:
    folder = tmp_path / "etc" / "sudoers.d"
    folder.mkdir(parents=True)
    unreadable = hostsec.sudoers(FakeHost(tmp_path), contents={"zz-alex-setup": None})
    assert unreadable.status == "warn" and "sudo" in unreadable.fix and unreadable.apply == ()
    found = hostsec.sudoers(FakeHost(tmp_path), contents={"zz-alex-setup": "alex ALL=(ALL) NOPASSWD: ALL\n"})
    assert found.status == "warn" and "zz-alex-setup" in found.text
    assert found.apply == ((SUDO, "-n", hostsec.RM, "-f", "/etc/sudoers.d/zz-alex-setup"),)
    mixed = hostsec.sudoers(FakeHost(tmp_path), contents={"rules": "# NOPASSWD in a comment\nalex ALL=(ALL) ALL\n"})
    assert (mixed.status, mixed.apply) == ("ok", ())
    assert hostsec.sudoers(FakeHost(tmp_path), contents={}).status == "ok"


MACOS_LISTING = (
    "Matching Defaults entries for alex on mini:\n"
    '    env_reset, env_keep+=BLOCKSIZE, env_keep+="COLORFGBG COLORTERM", env_keep+=__CF_USER_TEXT_ENCODING,\n'
    '    env_keep+="CHARSET LANG LANGUAGE LC_ALL LC_COLLATE", lecture_file=/etc/sudo_lecture\n'
    "\n"
    "User alex may run the following commands on mini:\n"
    "    (ALL) ALL\n"
)


def test_sudo_as_macos_sets_it_up_asks_your_password() -> None:
    check = hostsec.sudo_listing(MACOS_LISTING)
    assert (check.key, check.status, check.part, check.unread) == ("sudoers", "ok", "Passwordless sudo", False)


@pytest.mark.parametrize(
    ("added", "named"),
    [
        ("    (ALL) NOPASSWD: ALL\n", "1 sudo rule runs without your password (NOPASSWD)"),
        ("    (root) NOPASSWD: /usr/bin/true, /bin/ls\n    (ALL) NOPASSWD: ALL\n", "2 sudo rules run without"),
    ],
)
def test_a_rule_without_your_password_is_named_wherever_it_is(added: str, named: str) -> None:
    """sudo -l lists what applies to you from /etc/sudoers and /etc/sudoers.d alike (a %admin rule added with
    visudo too): never read from one folder only."""
    check = hostsec.sudo_listing(MACOS_LISTING + added)
    assert check.status == "warn" and named in check.text and "any program you run" in check.text
    assert "/usr/bin/sudo /usr/sbin/visudo" in check.fix and check.apply == ()  # never removed from /etc/sudoers here
    assert "(ALL)" not in check.text  # kinds and counts, never the rules


@pytest.mark.parametrize(
    ("default", "named"),
    [
        ("timestamp_type=global", "one sudo sign-in counts for every program of yours (timestamp_type=global)"),
        ("!tty_tickets", "one sudo sign-in counts for every program of yours (!tty_tickets)"),
        ("!authenticate", "sudo asks no password at all (!authenticate)"),
    ],
)
def test_defaults_that_skip_or_share_your_password_are_named(default: str, named: str) -> None:
    listing = MACOS_LISTING.replace("lecture_file=/etc/sudo_lecture", f"lecture_file=/etc/sudo_lecture, {default}")
    check = hostsec.sudo_listing(listing)
    assert check.status == "warn" and named in check.text
    command_specific = (
        MACOS_LISTING + f"\nRunas and Command-specific defaults for alex:\n    Defaults!/bin/sh {default}\n"
    )
    assert named in hostsec.sudo_listing(command_specific).text


@pytest.mark.parametrize(
    ("default", "named"),
    [
        ("timestamp_timeout=-1", "one sudo sign-in never ends in that Terminal window (timestamp_timeout=-1)"),
        ("timestamp_timeout = -5", "one sudo sign-in never ends in that Terminal window (timestamp_timeout=-5)"),
        (
            "timestamp_timeout=30",
            "one sudo sign-in lasts 30 minutes in that Terminal window, not 5 (timestamp_timeout=30)",
        ),
        (
            'timestamp_timeout="7.5"',
            "one sudo sign-in lasts 7.5 minutes in that Terminal window, not 5 (timestamp_timeout=7.5)",
        ),
    ],
)
def test_a_sudo_sign_in_that_lasts_longer_than_5_minutes_is_named(default: str, named: str) -> None:
    """FULL4: with timestamp_timeout negative, one sudo sign-in never ends, and above sudo's 5 minutes it lasts
    longer: any later program in that Terminal window becomes root without the password. A warning, so the Mac is
    not called hardened."""
    listing = MACOS_LISTING.replace("env_reset,", f"env_reset, {default},")
    check = hostsec.sudo_listing(listing)
    assert check.status == "warn" and named in check.text, check.text
    assert "any program you run can become root" in check.text and check.fix == hostsec.SUDO_FIX


@pytest.mark.parametrize("default", ["timestamp_timeout=0", "timestamp_timeout=5", "timestamp_timeout=2.5"])
def test_a_sudo_sign_in_of_5_minutes_or_less_is_fine(default: str) -> None:
    listing = MACOS_LISTING.replace("env_reset,", f"env_reset, {default},")
    assert hostsec.sudo_listing(listing).status == "ok"


def test_the_last_timestamp_timeout_listed_counts_as_sudo_reads_it() -> None:
    longer = MACOS_LISTING.replace("env_reset,", "env_reset, timestamp_timeout=5, timestamp_timeout=-1,")
    assert "never ends" in hostsec.sudo_listing(longer).text
    shorter = MACOS_LISTING.replace("env_reset,", "env_reset, timestamp_timeout=-1, timestamp_timeout=5,")
    assert hostsec.sudo_listing(shorter).status == "ok"


def test_a_tools_tail_is_cleaned_like_the_deploys_fields() -> None:
    """FULL2 re-check: a build's tail reaches root's terminal in the deploy's lines; escape sequences, bidi overrides
    and zero-width characters are "?"s there, as in deploy.clean (now hostsec.clean, one rule)."""
    tail = hostsec.tail("first\nerror: \x1b[8mhidden\u202e\u200bend\t.\n")
    assert tail == "first / error: ?[8mhidden??end?."
    from local_assistant import deploy

    assert deploy.clean is hostsec.clean


def test_a_command_specific_timeout_never_hides_the_one_for_every_command() -> None:
    """Re-check of FULL4: `Defaults!/usr/bin/true timestamp_timeout=0` after a global -1 changes only that command's
    sign-in; every other command's still never ends. A command-specific long one counts on its own too."""
    specific = "\nRunas and Command-specific defaults for alex:\n    Defaults!/usr/bin/true timestamp_timeout=0\n"
    hidden = MACOS_LISTING.replace("env_reset,", "env_reset, timestamp_timeout=-1,") + specific
    check = hostsec.sudo_listing(hidden)
    assert check.status == "warn" and "never ends" in check.text, check.text
    longer = (
        MACOS_LISTING + "\nRunas and Command-specific defaults for alex:\n    Defaults!/bin/sh timestamp_timeout=60\n"
    )
    assert "lasts 60 minutes" in hostsec.sudo_listing(longer).text
    assert hostsec.sudo_listing(MACOS_LISTING + specific).status == "ok"


def test_a_listing_that_is_not_sudos_is_not_read() -> None:
    for listing in ("", "sudo: unable to resolve host\n"):
        check = hostsec.sudo_listing(listing)
        assert check.status == "warn" and check.unread and "could not be read" in check.text


def test_your_sudo_is_read_without_your_password(tmp_path: Path) -> None:
    """As you, with no password: sudo -n -k -l lists your rules only when one of them runs without the password
    (sudo's listpw default) or none needs one; "a password is required" means no rule skips it. -k ignores your
    cached sign-in, so a sudo used a minute ago proves nothing."""
    argv = f"{SUDO} -n -k -l"
    required = hostsec.owner_sudo(FakeHost(tmp_path, {argv: (1, "sudo: a password is required\n")}))
    assert required.status == "ok" and "no sudo rule runs without your password" in required.text
    listed = hostsec.owner_sudo(FakeHost(tmp_path, {argv: (0, MACOS_LISTING + "    (ALL) NOPASSWD: ALL\n")}))
    assert listed.status == "warn" and "NOPASSWD" in listed.text
    odd = hostsec.owner_sudo(FakeHost(tmp_path, {argv: (1, "sudo: unknown user\n")}))
    assert odd.status == "warn" and odd.unread
    dry = FakeHost(tmp_path, {argv: (1, "sudo: a password is required\n")})
    dry.dry_run = True  # a dry run never runs sudo, not even to list
    assert hostsec.owner_sudo(dry).unread and dry.asked == []


def test_passwordless_sudo_keeps_the_mac_from_the_hardened_level(tmp_path: Path) -> None:
    """Her own user holds only while no sudo rule skips your password: with one, the level is not hardened."""
    home = hardened_home(tmp_path)
    checks = hostsec.level_checks(level_host(tmp_path), home, "+306900000000")
    assert hostsec.security_level(checks) == "hardened"  # sudo not read (her bridge): the level as before
    passwordless = hostsec.sudo_listing(MACOS_LISTING + "    (ALL) NOPASSWD: ALL\n")
    assert hostsec.security_level([*checks, passwordless]) == ""
    line = hostsec.level_check([*checks, passwordless])
    assert line.status == "warn" and "Passwordless sudo" in line.text
    assert hostsec.security_level([*checks, hostsec.sudo_listing(MACOS_LISTING)]) == "hardened"
    assert hostsec.security_level([*checks, hostsec.sudo_listing("")]) is None  # not read: no level claimed
    found = doctor_checks(level_host(tmp_path), home, "+306900000000", sudo=passwordless)
    assert [check.key for check in found][-2:] == ["sudoers", "security_level"]
    assert found[-1].status == "warn"


def test_other_admin_accounts_are_named(tmp_path: Path) -> None:
    answers = {f"{hostsec.DSCL} . -read /Groups/admin GroupMembership": (0, "GroupMembership: root alex sam\n")}
    check = hostsec.admins(FakeHost(tmp_path, answers), owner="alex")
    assert check.status == "warn" and "sam" in check.text and "alex" not in check.text and check.apply == ()
    answers = {
        f"{hostsec.DSCL} . -read /Groups/admin GroupMembership": (0, "GroupMembership: root alex _mbsetupuser\n")
    }
    assert (
        hostsec.admins(FakeHost(tmp_path, answers), owner="alex").status == "ok"
    )  # macOS's own _ accounts are no person


def test_tailscale_operator_and_funnel(tmp_path: Path) -> None:
    tool = "/opt/homebrew/bin/tailscale"
    answers = {
        f"{tool} debug prefs": (0, json.dumps({"OperatorUser": "alex"})),
        f"{tool} funnel status": (0, "No serve config\n"),
    }
    check = hostsec.tailscale(FakeHost(tmp_path, answers), tools=(tool,), exists=lambda path: True)
    assert check.status == "warn" and "operator" in check.text and check.apply == ()
    answers[f"{tool} debug prefs"] = (0, json.dumps({"OperatorUser": ""}))
    assert hostsec.tailscale(FakeHost(tmp_path, answers), tools=(tool,), exists=lambda path: True).status == "ok"
    none = hostsec.tailscale(FakeHost(tmp_path), tools=(tool,), exists=lambda path: False)
    assert none.status == "ok" and "not installed" in none.text


def test_the_keychain_locks_itself_after_15_minutes(tmp_path: Path) -> None:
    keychain = tmp_path / "Library" / "Keychains" / "login.keychain-db"
    keychain.parent.mkdir(parents=True)
    keychain.write_bytes(b"")
    show = f"{hostsec.SECURITY} show-keychain-info {keychain}"
    never = hostsec.keychain(FakeHost(tmp_path, {show: (0, f'Keychain "{keychain}" no-timeout\n')}), tmp_path)
    assert never.status == "warn" and "scripts/harden.sh --nuclear" in never.fix
    assert never.apply == ((hostsec.SECURITY, "set-keychain-settings", "-l", "-u", "-t", "900", str(keychain)),)
    locked = hostsec.keychain(FakeHost(tmp_path, {show: (0, f'Keychain "{keychain}" timeout=900s\n')}), tmp_path)
    assert (locked.status, locked.apply) == ("ok", ())


def her_files(home: Path) -> list[Path]:
    made = []
    for folder in (
        ".config/local-assistant",
        "Library/Application Support/local-assistant",
        ".local/share/signal-cli/data",
    ):
        path = home / folder
        path.mkdir(parents=True)
        path.chmod(0o700)
        made.append(path)
    (home / ".local/share/signal-cli").chmod(0o700)
    return made


def test_her_files_must_be_private(tmp_path: Path) -> None:
    config, data, signal = her_files(tmp_path)
    (config / "config.toml").write_text("x")
    (config / "config.toml").chmod(0o600)
    assert data_modes(tmp_path).status == "ok"
    db = signal / "account.db"
    db.write_text("x")
    db.chmod(0o644)  # signal-cli's own default
    check = data_modes(tmp_path)
    assert check.status == "fail" and "1 file" in check.text and "account.db" in check.text
    assert check.apply == ((hostsec.CHMOD, "-R", "go-rwx", str(tmp_path / ".local/share/signal-cli")),)
    as_root = data_modes(tmp_path, sudo=True)
    assert as_root.apply == ((SUDO, "-n", hostsec.CHMOD, "-R", "go-rwx", str(tmp_path / ".local/share/signal-cli")),)
    (data / "status.json").symlink_to("assistant.db")  # links are never followed: a link inside is no file of others
    db.chmod(0o600)
    assert data_modes(tmp_path).status == "ok"


def test_a_link_out_of_her_folders_is_a_failure(tmp_path: Path) -> None:
    """A link out of her folders keeps her data where its owner reads and changes it: doctor shows it, top folder
    included. No chmod fixes a link, so none is offered."""
    config, _data, signal = her_files(tmp_path)
    (signal / "latest").symlink_to("../data")  # inside her folder: fine
    assert data_modes(tmp_path).status == "ok"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (signal / "1.d").symlink_to(elsewhere)
    check = data_modes(tmp_path)
    assert check.status == "fail" and "link" in check.text and ".local/share/signal-cli/data/1.d" in check.text
    assert check.apply == () and data_modes(tmp_path, sudo=True).apply == ()
    host = FakeHost(tmp_path, {**fw(), f"{FDESETUP} status": (0, "FileVault is On.\n")})
    assert by_key(doctor_checks(host, tmp_path))["data_modes"].status == "fail"
    (signal / "1.d").unlink()
    (signal / "gone").symlink_to(tmp_path / "nowhere")  # a dangling link out: whoever makes it later owns it
    assert data_modes(tmp_path).status == "fail"
    (signal / "gone").unlink()
    config.rmdir()
    config.symlink_to(elsewhere)  # the top folder itself
    check = data_modes(tmp_path)
    assert check.status == "fail" and ".config/local-assistant" in check.text


def account(signal: Path, number: str, path: str, pin: str | None) -> None:
    data = signal / "data"
    data.mkdir(parents=True, exist_ok=True)
    listed = json.loads((data / "accounts.json").read_text()) if (data / "accounts.json").exists() else {}
    accounts = [*listed.get("accounts", []), {"path": path, "number": number, "uuid": "x"}]
    (data / "accounts.json").write_text(json.dumps({"accounts": accounts, "version": 2}))
    (data / path).write_text(json.dumps({"version": 9, "registrationLockPin": pin, "number": number}))


@pytest.mark.parametrize("swap", ["accounts.json", "111111"])
@pytest.mark.parametrize("kind", ["fifo", "link"])
def test_her_signal_account_files_are_read_never_through_a_link_or_a_fifo(tmp_path: Path, swap: str, kind: str) -> None:
    """The move's preflight reads your accounts.json and the account file it names as root: a FIFO swapped in must
    not hang root, and a link of yours is never followed (review 15 Oct, 6)."""
    account(tmp_path, "+306900000000", "111111", "731904")
    target = tmp_path / "data" / swap
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(target.read_bytes())
    target.unlink()
    if kind == "fifo":
        os.mkfifo(target)
    else:
        target.symlink_to(elsewhere)
    started = time.monotonic()
    assert hostsec.signal_account(tmp_path, "+306900000000") is None
    assert hostsec.registration_lock_state(tmp_path, "+306900000000") == "unknown"
    assert time.monotonic() - started < 5


def test_an_account_file_named_by_a_path_is_never_read(tmp_path: Path) -> None:
    account(tmp_path, "+306900000000", "111111", "731904")
    data = tmp_path / "data"
    listed = {"accounts": [{"path": str(data / "111111"), "number": "+306900000000"}], "version": 2}
    (data / "accounts.json").write_text(json.dumps(listed))
    assert hostsec.signal_account(tmp_path, "+306900000000") is None


def test_registration_lock_is_read_and_its_pin_never_returned(tmp_path: Path) -> None:
    account(tmp_path, "+306900000000", "111111", "731904")
    account(tmp_path, "+306900000001", "222222", None)
    assert registration_lock(tmp_path, "+306900000000") is True
    assert registration_lock(tmp_path, "+306900000001") is False
    assert registration_lock(tmp_path, "+306900000002") is None  # not registered here
    assert registration_lock(tmp_path / "nowhere", "+306900000000") is None
    (tmp_path / "data" / "111111").write_text("{broken")
    assert registration_lock(tmp_path, "+306900000000") is None


def test_with_one_account_the_number_may_be_left_out(tmp_path: Path) -> None:
    account(tmp_path, "+306900000000", "111111", "731904")
    assert registration_lock(tmp_path, "") is True


def test_the_doctor_checks_have_their_keys_and_never_print_the_pin(tmp_path: Path) -> None:
    home = tmp_path / "home"
    her_files(home)
    account(home / ".local/share/signal-cli", "+306900000000", "111111", "731904")
    sshd(tmp_path, MAIN, MACOS)
    host = FakeHost(tmp_path, {**fw(), f"{FDESETUP} status": (0, "FileVault is On.\n"), **REMOTE_ON})
    checks = doctor_checks(host, home, "+306900000000")
    assert [check.key for check in checks] == [
        "firewall",
        "ssh_password",
        "filevault",
        "data_modes",
        "registration_lock",
        "security_level",
    ]
    found = by_key(checks)
    assert found["registration_lock"].status == "ok" and found["filevault"].status == "ok"
    assert found["ssh_password"].status == "warn" and found["ssh_password"].apply == ()  # doctor never applies
    for check in checks:
        assert "731904" not in check.text + check.fix and "+306900000000" not in check.text + check.fix
    assert all(arg[0].startswith("/") for arg in host.asked)  # absolute tools only
    assert doctor_checks(host, home)[4].status == "ok"  # one account: the number may be left out


def test_a_registration_lock_that_is_on_says_where_the_pin_belongs(tmp_path: Path) -> None:
    """The ✓ line shows no fix: the reminder is in its text, in setup's words."""
    home = tmp_path / "home"
    account(home / ".local/share/signal-cli", "+306900000000", "111111", "731904")
    check = hostsec.registration_lock_check(home, "+306900000000")
    assert check.status == "ok"
    assert check.text == "on (the PIN is not shown). Keep the PIN in your password manager."


def test_a_missing_registration_lock_is_a_warning_with_the_harden_step(tmp_path: Path) -> None:
    home = tmp_path / "home"
    account(home / ".local/share/signal-cli", "+306900000000", "111111", None)
    check = hostsec.registration_lock_check(home, "+306900000000")
    assert check.status == "warn" and "scripts/harden.sh" in check.fix


def test_the_real_host_refuses_privileged_tools_under_another_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = Host(tmp_path)
    for tool in (SUDO, hostsec.LAUNCHCTL, SOCKETFILTERFW, FDESETUP, hostsec.DSCL, hostsec.SYSADMINCTL, hostsec.CHOWN):
        assert host.run([tool, "--anything"]) == (0, "")
    out = capsys.readouterr().out
    assert out.count("would run: ") == 7
    with pytest.raises(ValueError, match="absolute"):
        host.run(["sudo", "-v"])


def test_the_real_host_reads_under_its_root_and_runs_without_a_shell(tmp_path: Path) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "x.conf").write_text("hello\n")
    host = Host(tmp_path)
    assert host.read(Path("/etc/x.conf")) == "hello\n" and host.read(Path("/etc/none")) is None
    assert host.path("/var/estia") == tmp_path / "var" / "estia"
    assert host.run(["/bin/echo", "a;b", "$HOME"]) == (0, "a;b $HOME\n")  # no shell: nothing expanded


def test_a_dry_run_host_prints_changes_and_still_reads(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    host = Host(Path("/"), dry_run=True)
    assert host.run(["/bin/echo", "change"]) == (0, "")
    assert host.run(["/bin/echo", "state"], read_only=True) == (0, "state\n")
    assert host.run(["/bin/echo", "secret-123"], shown=["/bin/echo", "<secret>"]) == (0, "")
    out = capsys.readouterr().out
    assert "would run: /bin/echo change" in out and "secret-123" not in out and "<secret>" in out


def test_estia_root_is_ignored_for_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ESTIA_ROOT", str(tmp_path))
    assert hostsec.estia_root() == tmp_path
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert hostsec.estia_root() == Path("/")
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    monkeypatch.delenv("ESTIA_ROOT")
    assert hostsec.estia_root() == Path("/")


# review fixes: no account yet is not "unreadable", her role account cannot log in over SSH, listeners are named


def test_no_signal_account_yet_is_told_apart_from_an_unreadable_one(tmp_path: Path) -> None:
    assert hostsec.registration_lock_state(tmp_path / "nowhere", "+306900000000") == "none"
    account(tmp_path, "+306900000000", "111111", "731904")
    assert hostsec.registration_lock_state(tmp_path, "+306900000002") == "none"  # another number: not here
    assert hostsec.registration_lock_state(tmp_path, "+306900000000") == "on"
    (tmp_path / "data" / "111111").write_text("{broken")
    assert hostsec.registration_lock_state(tmp_path, "+306900000000") == "unknown"
    home = tmp_path / "home"
    check = hostsec.registration_lock_check(home, "+306900000000")
    assert check.status == "warn" and "scripts/setup.sh registers it" in check.text + check.fix
    assert "harden.sh checks it" not in check.fix


def test_the_ssh_drop_in_keeps_her_role_account_out() -> None:
    assert "\nDenyUsers _estia\n" in hostsec.SSH_DROP_IN


LISTENING = (
    "p501\ncnode\nf23\nn*:8170\n"
    "p502\ncollama\nf5\nn127.0.0.1:11434\n"
    "p503\ncPython\nf7\nn[::1]:8150\n"
    "p504\ncrapportd\nf9\nn100.64.0.7:5000\n"
)


def test_the_programs_that_listen_on_the_network_are_named(tmp_path: Path) -> None:
    host = FakeHost(
        tmp_path,
        {
            f"{hostsec.LSOF} -nP -iTCP -sTCP:LISTEN -F pcn": (0, LISTENING),
            "/bin/ps -o comm= -p 501": (0, "/opt/homebrew/Cellar/node/24.1.0/bin/node\n"),
            "/bin/ps -o comm= -p 504": (0, "/usr/libexec/rapportd\n"),
        },
    )
    found = hostsec.listeners(host)
    assert found == [
        ("node", "8170", "/opt/homebrew/Cellar/node/24.1.0/bin/node"),
        ("rapportd", "5000", "/usr/libexec/rapportd"),
    ]  # 127.0.0.1 and ::1 only answer this Mac: the firewall never stops them


def test_a_program_started_by_name_is_found_by_its_executable(tmp_path: Path) -> None:
    """ps shows argv[0] ("node" when started by name: live 13 Oct, the firewall's keep question never came); lsof's
    txt entry names the executable."""
    host = FakeHost(
        tmp_path,
        {
            f"{hostsec.LSOF} -nP -iTCP -sTCP:LISTEN -F pcn": (0, "p501\ncnode\nf23\nn100.64.0.7:8170\n"),
            f"{hostsec.LSOF} -nP -a -p 501 -d txt -Fn": (
                0,
                "p501\nftxt\nn/opt/example/node/bin/node\nftxt\nn/usr/lib/dyld\n",
            ),
            "/bin/ps -o comm= -p 501": (0, "node\n"),
        },
    )
    assert hostsec.listeners(host) == [("node", "8170", "/opt/example/node/bin/node")]


def test_the_automatic_login_fix_names_its_trade_off(tmp_path: Path) -> None:
    """Off is safer, but Ollama (a login item of yours) then waits for a login after every restart."""
    host = FakeHost(
        tmp_path,
        {f"{hostsec.DEFAULTS} read {tmp_path}/Library/Preferences/com.apple.loginwindow autoLoginUser": (0, "alex\n")},
    )
    check = hostsec.auto_login(host)
    assert check.status == "warn" and "Ollama" in check.fix and "2.8" in check.fix


# ------------------------------------------------------------------ doctor: what the firewall does not let through


NETSTAT_ARGV = f"{hostsec.NETSTAT} -anv -p tcp"
NETSTAT_HEAD = (
    "Active Internet connections (including servers)\n"
    "Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)          rxbytes      txbytes  "
    "rhiwat  shiwat          process:pid    state  options           gencnt    flags   flags1 usecnt rtncnt fltrs\n"
)


def listen(proto: str, local: str, process: str, pid: int) -> str:
    return (
        f"{proto}       0      0  {local:<22} *.*                    LISTEN                 0            0  131072  "
        f"131072 {process}:{pid}  00100 00000006 00000000006b60c4 00000000 00000800      1      0 000000\n"
    )


NODE = "/opt/example/node/bin/node"
APPLE_SIGNED = "Executable=/sbin/launchd\nAuthority=macOS Software Signing\nAuthority=Apple Root CA\n"
DEVELOPER_SIGNED = "Executable=/x\nAuthority=Developer ID Application: Example (EXAMPLE123)\nAuthority=Apple Root CA\n"
ADHOC = "Executable=/x\nSignature=adhoc\nTeamIdentifier=not set\n"
ALLOW_SIGNED = (
    "Automatically allow built-in signed software ENABLED.\nAutomatically allow downloaded signed software ENABLED.\n"
)
NO_DOWNLOADED = (
    "Automatically allow built-in signed software ENABLED.\nAutomatically allow downloaded signed software DISABLED.\n"
)
NO_APPS = "Total number of apps = 0 \n"


class ProcessHost(FakeHost):
    """FakeHost with each pid's program, as proc_pidpath gives it for any user's process."""

    def __init__(self, root: Path, answers: dict[str, tuple[int, str]], programs: dict[str, str]) -> None:
        super().__init__(root, answers)
        self.programs = programs

    def program_path(self, pid: str) -> str:
        return self.programs.get(pid, "")


def firewall_host(
    tmp_path: Path,
    sockets: str,
    programs: dict[str, str],
    signatures: dict[str, tuple[int, str]],
    *,
    apps: str = NO_APPS,
    allow: str = ALLOW_SIGNED,
    state: str = ENABLED,
) -> ProcessHost:
    answers = {
        **fw(state=state, apps=apps),
        f"{SOCKETFILTERFW} --getallowsigned": (0, allow),
        NETSTAT_ARGV: (0, NETSTAT_HEAD + sockets),
        **{f"{hostsec.CODESIGN} -dvv {path}": answer for path, answer in signatures.items()},
    }
    return ProcessHost(tmp_path, answers, programs)


def test_a_program_the_firewall_does_not_let_through_gets_a_warning_with_the_two_commands(tmp_path: Path) -> None:
    """Live 13 Oct: node on port 8170 (ad-hoc signed, not on the firewall's list) answered nobody else. Any user's
    program is seen (doctor runs as her: netstat names every socket's process)."""
    sockets = (
        listen("tcp4", "*.8170", "node", 501)
        + listen("tcp6", "*.8170", "node", 501)  # the same program and port twice: one line
        + listen("tcp4", "127.0.0.1.11434", "ollama", 502)  # only this Mac: never stopped
        + listen("tcp6", "::1.5432", "postgres", 503)
        + listen("tcp4", "*.22", "launchd", 1)  # Apple's, with built-in signed software allowed
    )
    host = firewall_host(
        tmp_path,
        sockets,
        {"501": NODE, "502": "/opt/ollama", "503": "/opt/pg", "1": "/sbin/launchd"},
        {NODE: (0, ADHOC), "/sbin/launchd": (0, APPLE_SIGNED)},
    )
    checks = hostsec.firewall_listeners(host)
    assert len(checks) == 1
    [check] = checks
    assert (check.status, check.part) == ("warn", "Firewall")  # a "!" line, never a "✗"
    assert check.text == "node on port 8170 cannot be reached from other machines"
    assert check.fix.splitlines() == [
        "if you started it and other machines should reach it:",
        f"      /usr/bin/sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add {NODE}",
        f"      /usr/bin/sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp {NODE}",
    ]
    assert check.apply == ()  # doctor never changes the firewall
    changes = [argv for argv in host.asked if argv[0] == SUDO or "--add" in argv or "--unblockapp" in argv]
    assert changes == []
    assert (hostsec.CODESIGN, "-dvv", NODE) in host.asked and all(arg[0].startswith("/") for arg in host.asked)


@pytest.mark.parametrize(
    ("apps", "signature", "allow", "blocked"),
    [
        (
            f"Total number of apps = 1 \n1 : {NODE} \n             (Allow incoming connections)\n",
            ADHOC,
            ALLOW_SIGNED,
            False,
        ),
        (
            f"Total number of apps = 1 \n1 : {NODE} \n             (Block incoming connections)\n",
            DEVELOPER_SIGNED,
            ALLOW_SIGNED,
            True,
        ),
        (NO_APPS, DEVELOPER_SIGNED, ALLOW_SIGNED, False),
        (NO_APPS, DEVELOPER_SIGNED, NO_DOWNLOADED, True),
        (NO_APPS, APPLE_SIGNED, NO_DOWNLOADED, False),
        (NO_APPS, ADHOC, ALLOW_SIGNED, True),
    ],
    ids=["allowed-on-the-list", "blocked-on-the-list", "signed", "signed-not-allowed", "apple", "ad-hoc"],
)
def test_what_the_firewall_lets_through(tmp_path: Path, apps: str, signature: str, allow: str, blocked: bool) -> None:
    host = firewall_host(
        tmp_path, listen("tcp4", "*.8170", "node", 501), {"501": NODE}, {NODE: (0, signature)}, apps=apps, allow=allow
    )
    assert bool(hostsec.firewall_listeners(host)) is blocked


def test_an_unsigned_program_is_not_let_through_and_an_unreadable_signature_is_not_guessed(tmp_path: Path) -> None:
    sockets = listen("tcp4", "*.8170", "node", 501) + listen("tcp4", "*.9000", "tool", 502)
    host = firewall_host(
        tmp_path,
        sockets,
        {"501": NODE, "502": "/opt/tool"},
        {NODE: (1, f"{NODE}: code object is not signed at all\n"), "/opt/tool": (1, "/opt/tool: No such file\n")},
    )
    assert [check.text for check in hostsec.firewall_listeners(host)] == [
        "node on port 8170 cannot be reached from other machines"
    ]


def test_a_program_inside_an_allowed_app_is_let_through(tmp_path: Path) -> None:
    program = "/Applications/Example.app/Contents/MacOS/Example"
    apps = "Total number of apps = 1 \n1 : /Applications/Example.app \n             (Allow incoming connections)\n"
    host = firewall_host(
        tmp_path, listen("tcp4", "*.7000", "Example", 501), {"501": program}, {program: (0, ADHOC)}, apps=apps
    )
    assert hostsec.firewall_listeners(host) == []


def test_one_program_on_two_ports_is_one_line_and_a_path_with_spaces_is_quoted(tmp_path: Path) -> None:
    program = "/Applications/My Server/bin/serve"
    sockets = listen("tcp4", "100.64.0.7.62954", "serve", 501) + listen("tcp6", "fd7a:115c::.56746", "serve", 501)
    host = firewall_host(tmp_path, sockets, {"501": program}, {program: (0, ADHOC)})
    [check] = hostsec.firewall_listeners(host)
    assert check.text == "serve on ports 56746 and 62954 cannot be reached from other machines"
    assert "--add '/Applications/My Server/bin/serve'" in check.fix


def test_a_listener_whose_program_is_not_found_is_named_with_how_to_find_it(tmp_path: Path) -> None:
    """A Mac without a screen cannot show System Settings: the fix is a command that names the program."""
    host = firewall_host(tmp_path, listen("tcp4", "*.8170", "node", 501), {}, {})
    [check] = hostsec.firewall_listeners(host)
    assert check.status == "warn" and "node on port 8170" in check.text and "may not" in check.text
    assert f"/usr/bin/sudo {hostsec.LSOF} -nP -iTCP:8170 -sTCP:LISTEN" in check.fix
    assert "--add" in check.fix and "System Settings" not in check.fix


def test_with_the_firewall_off_or_unreadable_lists_there_are_no_listener_lines(tmp_path: Path) -> None:
    sockets = listen("tcp4", "*.8170", "node", 501)
    off = firewall_host(tmp_path, sockets, {"501": NODE}, {NODE: (0, ADHOC)}, state=DISABLED)
    assert hostsec.firewall_listeners(off) == []  # the firewall's own line says it is off
    assert not any(argv[0] == hostsec.NETSTAT for argv in off.asked)
    unreadable = firewall_host(tmp_path, sockets, {"501": NODE}, {NODE: (0, ADHOC)})
    unreadable.answers[f"{SOCKETFILTERFW} --listapps"] = (1, "")
    assert hostsec.firewall_listeners(unreadable) == []  # cannot tell: nothing claimed


def test_doctor_shows_the_listener_lines_after_the_firewall(tmp_path: Path) -> None:
    home = tmp_path / "home"
    her_files(home)
    host = firewall_host(tmp_path, listen("tcp4", "*.8170", "node", 501), {"501": NODE}, {NODE: (0, ADHOC)})
    host.answers[f"{FDESETUP} status"] = (0, "FileVault is On.\n")
    keys = [check.key for check in doctor_checks(host, home)]
    assert keys[:2] == ["firewall", "firewall_listener"]


def test_the_real_host_finds_no_program_off_the_live_mac(tmp_path: Path) -> None:
    assert Host(tmp_path).program_path(str(os.getpid())) == ""


def test_your_own_listeners_are_found_with_lsof_when_netstat_names_none(tmp_path: Path) -> None:
    """Seen here: netstat started from some Pythons lists no internet sockets at all. lsof still names your own."""
    host = firewall_host(tmp_path, "", {}, {NODE: (0, ADHOC)})
    host.answers[f"{hostsec.LSOF} -nP -iTCP -sTCP:LISTEN -F pcn"] = (0, "p501\ncnode\nf23\nn*:8170\n")
    host.answers[f"{hostsec.LSOF} -nP -a -p 501 -d txt -Fn"] = (0, f"p501\nftxt\nn{NODE}\n")
    assert [check.text for check in hostsec.firewall_listeners(host)] == [
        "node on port 8170 cannot be reached from other machines"
    ]


def test_a_listener_both_tools_name_is_one_line(tmp_path: Path) -> None:
    host = firewall_host(tmp_path, listen("tcp4", "*.8170", "node", 501), {"501": NODE}, {NODE: (0, ADHOC)})
    host.answers[f"{hostsec.LSOF} -nP -iTCP -sTCP:LISTEN -F pcn"] = (0, "p501\ncnode\nf23\nn*:8170\n")
    host.answers[f"{hostsec.LSOF} -nP -a -p 501 -d txt -Fn"] = (0, f"p501\nftxt\nn{NODE}\n")
    assert len(hostsec.firewall_listeners(host)) == 1


def test_a_pipe_with_a_time_limit_stops_both_tools(tmp_path: Path) -> None:
    """A reader that blocks (a FIFO) never hangs the caller: both tools are killed at the limit."""
    import time

    started = time.monotonic()
    code, said = Host(tmp_path / "unused").pipe(["/bin/sleep", "30"], ["/bin/cat"], timeout=0.3)
    assert code != 0 and "timed out" in said
    assert time.monotonic() - started < 10


@pytest.mark.parametrize(
    "blockall",
    [
        {f"{SOCKETFILTERFW} --getblockall": (0, "Firewall has block all state set to enabled.\n")},
        {f"{SOCKETFILTERFW} --getglobalstate": (0, "Firewall is enabled. (State = 2)\n")},
    ],
    ids=["getblockall", "state-2"],
)
def test_block_all_stops_every_listener_in_one_line(tmp_path: Path, blockall: dict[str, tuple[int, str]]) -> None:
    """Block all incoming connections: signed or allowed, no program is reached; one line, no per-program commands."""
    sockets = listen("tcp4", "*.8170", "node", 501) + listen("tcp4", "*.22", "launchd", 1)
    apps = f"Total number of apps = 1 \n1 : {NODE} \n             (Allow incoming connections)\n"
    host = firewall_host(
        tmp_path,
        sockets,
        {"501": NODE, "1": "/sbin/launchd"},
        {NODE: (0, ADHOC), "/sbin/launchd": (0, APPLE_SIGNED)},
        apps=apps,
    )
    host.answers.update(blockall)
    [check] = hostsec.firewall_listeners(host)
    assert check.status == "warn"
    assert check.text == (
        "block all incoming connections is on: node on port 8170 and launchd on port 22 cannot be reached from other "
        "machines"
    )
    assert check.fix == (
        "if other machines should reach them: /usr/bin/sudo /usr/libexec/ApplicationFirewall/socketfilterfw "
        "--setblockall off"
    )
    assert "--add" not in check.fix and check.apply == ()


def test_block_all_off_changes_nothing(tmp_path: Path) -> None:
    host = firewall_host(tmp_path, listen("tcp4", "*.8170", "node", 501), {"501": NODE}, {NODE: (0, ADHOC)})
    host.answers[f"{SOCKETFILTERFW} --getblockall"] = (0, "Firewall has block all state set to disabled.\n")
    assert [check.text for check in hostsec.firewall_listeners(host)] == [
        "node on port 8170 cannot be reached from other machines"
    ]


class LinkHost(ProcessHost):
    """ProcessHost whose paths resolve through links, as realpath sees them on the live Mac."""

    def __init__(self, root: Path, answers: dict[str, tuple[int, str]], programs: dict[str, str]) -> None:
        super().__init__(root, answers, programs)
        self.links: dict[str, str] = {}

    def real_path(self, path: str) -> str:
        return self.links.get(path, path)


def test_an_entry_added_through_a_link_path_lets_the_program_through(tmp_path: Path) -> None:
    """socketfilterfw --add /opt/homebrew/bin/node keeps that path; the process's path is the real one."""
    link = "/opt/example/bin/node"
    apps = f"Total number of apps = 1 \n1 : {link} \n             (Allow incoming connections)\n"
    answers = {
        **fw(apps=apps),
        f"{SOCKETFILTERFW} --getallowsigned": (0, ALLOW_SIGNED),
        NETSTAT_ARGV: (0, NETSTAT_HEAD + listen("tcp4", "*.8170", "node", 501)),
        f"{hostsec.CODESIGN} -dvv {NODE}": (0, ADHOC),
    }
    host = LinkHost(tmp_path, answers, {"501": NODE})
    host.links[link] = NODE
    assert hostsec.firewall_listeners(host) == []


def test_the_real_host_resolves_paths_only_on_the_live_mac(tmp_path: Path) -> None:
    assert Host(tmp_path).real_path("/tmp") == "/tmp"  # a test root: the live disk is never read


def test_a_program_path_with_control_characters_cannot_forge_a_doctor_line(tmp_path: Path) -> None:
    forged = "/opt/x\n✓ Firewall: everything is fine\x1b[0m"
    host = firewall_host(tmp_path, listen("tcp4", "*.8170", "node", 501), {"501": forged}, {forged: (0, ADHOC)})
    checks = hostsec.firewall_listeners(host)
    assert checks
    for check in checks:
        assert not any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in check.text)
        assert "everything is fine" not in check.fix and "\x1b" not in check.fix


@pytest.mark.parametrize("folder", ["/tmp", "/private/tmp", "/var/folders/ab/T", "/private/var/folders/ab/T"])
def test_a_program_in_a_temporary_folder_gets_no_command_that_opens_the_firewall(tmp_path: Path, folder: str) -> None:
    program = f"{folder}/serve"
    host = firewall_host(tmp_path, listen("tcp4", "*.9000", "serve", 501), {"501": program}, {program: (0, ADHOC)})
    [check] = hostsec.firewall_listeners(host)
    assert check.text == "serve on port 9000 cannot be reached from other machines"
    assert "--add" not in check.fix and "--unblockapp" not in check.fix
    assert "temporary folder" in check.fix


# owner, 14 Oct: the Mac's security has two levels, named in every text a person reads: hardened (setup's security
# step) and nuclear (scripts/harden.sh --nuclear); never "default"
REMOTE_OFF = {f"{hostsec.LAUNCHCTL} print-disabled system": (0, '\t"com.openssh.sshd" => disabled\n')}


# the firewall's list as --nuclear leaves it: no interpreter may take incoming connections
NO_INTERPRETER = "Total number of apps = 1 \n1 : /usr/bin/python3 \n             (Block incoming connections)\n"


def level_host(
    root: Path,
    *,
    firewall: str = DISABLED,
    remote: dict[str, tuple[int, str]] = REMOTE_OFF,
    apps: str = NO_INTERPRETER,
) -> FakeHost:
    answers = {**fw(firewall, STEALTH_OFF, apps), **remote, f"{FDESETUP} status": (0, "FileVault is On.\n")}
    return FakeHost(root, answers)


def hardened_home(tmp_path: Path, pin: str | None = "731904") -> Path:
    home = tmp_path / "home"
    her_files(home)
    account(home / ".local/share/signal-cli", "+306900000000", "111111", pin)
    for written in (home / ".local/share/signal-cli/data").iterdir():
        written.chmod(0o600)  # private, as the security step leaves them
    return home


@pytest.mark.parametrize(
    ("firewall", "pin", "level"),
    [(ENABLED, "731904", "nuclear"), (DISABLED, "731904", "hardened"), (ENABLED, None, ""), (DISABLED, None, "")],
)
def test_the_security_level_is_read_from_what_the_security_step_sets(
    tmp_path: Path, firewall: str, pin: str | None, level: str
) -> None:
    """hardened: SSH with keys only or off, her files private, her Registration Lock; nuclear: that, the firewall on
    and no interpreter on its list."""
    home = hardened_home(tmp_path, pin)
    checks = hostsec.level_checks(level_host(tmp_path, firewall=firewall), home, "+306900000000")
    keys = ["firewall", "ssh_password", "data_modes", "registration_lock", "firewall_apps"]
    assert [check.key for check in checks] == keys
    assert hostsec.security_level(checks) == level


def test_the_firewall_alone_is_not_nuclear(tmp_path: Path) -> None:
    """Review of 14 Oct: a firewall the owner turned on with python3 still on its list is the hardened level."""
    home = hardened_home(tmp_path)
    for apps in (APPS, None):  # python3 allowed; the list not readable
        host = level_host(tmp_path, firewall=ENABLED, apps=APPS)
        if apps is None:
            host.answers[f"{SOCKETFILTERFW} --listapps"] = (1, "")
        checks = hostsec.level_checks(host, home, "+306900000000")
        assert hostsec.security_level(checks) == "hardened"
        line = hostsec.level_check(checks)
        assert line.status == "ok" and line.text.startswith("hardened")
        assert "python3 and ruby off the firewall's list" in line.text and "scripts/harden.sh --nuclear" in line.text


def test_a_setting_that_could_not_be_read_gives_no_level(tmp_path: Path) -> None:
    """Review of 14 Oct: her Signal account file not readable (doctor as the owner) is no proof of "not hardened"."""
    home = hardened_home(tmp_path)
    (home / ".local/share/signal-cli/data/accounts.json").write_text("{broken")
    checks = hostsec.level_checks(level_host(tmp_path, firewall=ENABLED), home, "+306900000000")
    assert hostsec.security_level(checks) is None
    line = hostsec.level_check(checks)
    assert (line.status, line.text) == ("warn", "not read: Registration Lock could not be read")


def test_a_setting_read_as_not_held_wins_over_one_not_read(tmp_path: Path) -> None:
    home = hardened_home(tmp_path)
    (home / ".local/share/signal-cli/data/accounts.json").write_text("{broken")
    sshd(tmp_path, MAIN, MACOS)  # Remote Login on, passwords allowed
    checks = hostsec.level_checks(level_host(tmp_path, remote=REMOTE_ON), home, "+306900000000")
    assert hostsec.security_level(checks) == ""


def test_ssh_with_passwords_is_not_hardened(tmp_path: Path) -> None:
    home = hardened_home(tmp_path)
    sshd(tmp_path, MAIN, MACOS)  # Remote Login on, passwords allowed
    checks = hostsec.level_checks(level_host(tmp_path, firewall=ENABLED, remote=REMOTE_ON), home, "+306900000000")
    assert hostsec.security_level(checks) == ""


@pytest.mark.parametrize(
    ("firewall", "pin", "status", "words"),
    [
        (ENABLED, "731904", "ok", "nuclear"),
        (DISABLED, "731904", "ok", "hardened"),
        (DISABLED, None, "warn", "not hardened yet"),
    ],
)
def test_doctor_names_the_security_level_last(
    tmp_path: Path, firewall: str, pin: str | None, status: str, words: str
) -> None:
    home = hardened_home(tmp_path, pin)
    line = doctor_checks(level_host(tmp_path, firewall=firewall), home, "+306900000000")[-1]
    assert (line.key, line.part, line.status) == ("security_level", "Security level", status)
    assert line.text.startswith(words)
    assert "default" not in (line.text + line.fix).lower()
    if status == "warn":
        assert "Registration Lock" in line.text and "scripts/harden.sh" in line.fix and "hardened" in line.fix
    else:
        assert "hardened" in line.text and "nuclear" in line.text  # both levels named, the current one first
    if words == "nuclear":
        assert "stealth mode and the keychain lock do not count" in line.text


def test_the_neutral_line_names_the_nuclear_level() -> None:
    assert hostsec.NUCLEAR_LINE.startswith("the nuclear level: the firewall,")
    assert "default" not in hostsec.NUCLEAR_LINE
