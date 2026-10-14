"""Her own user (ownuser): the layout, the role account's plan, the plist values, doctor's install checks and the
migration, all against a temporary root (ESTIA_ROOT) with a fake runner: no user is created, nothing runs as root."""

import json
import os
import plistlib
import shutil
import stat
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from local_assistant import hostsec, ownuser
from local_assistant.hostsec import Check
from local_assistant.ownuser import CODE, HOME, ROLE_GROUP, ROLE_USER

needs_plutil = pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
ROOT = Path(__file__).resolve().parent.parent


def test_the_layout_is_fixed() -> None:
    assert ("_estia", "_estia", Path("/var/estia"), Path("/usr/local/estia")) == (ROLE_USER, ROLE_GROUP, HOME, CODE)
    assert ownuser.RELEASES == CODE / "releases" and ownuser.CURRENT == CODE / "current"
    assert ownuser.BIN == CODE / "bin"
    assert [old for old, _ in ownuser.DATA_MAP] == [
        ".config/local-assistant",
        "Library/Application Support/local-assistant",
        ".local/share/signal-cli",
        "Library/Logs/local-assistant",
        "Library/Caches/voice-service",
        ".cache/fluidaudio",  # FluidAudio's speech models (Kokoro, Supertonic): offline after the move too
    ]
    assert all(old == new for old, new in ownuser.DATA_MAP)  # the same place under her home


def test_a_free_id_in_the_role_account_range() -> None:
    assert ownuser.free_id(set(), set()) == 499
    assert ownuser.free_id({499, 498}, {497}) == 496  # free as a user and as a group
    with pytest.raises(RuntimeError, match="no free") as raised:
        ownuser.free_id(set(range(450, 500)), set())
    # sysadminctl ignores -UID (macOS 27, live: uid 503): only her group surely gets the number
    assert "her group" in str(raised.value) and "user and group" not in str(raised.value)


def test_the_ids_in_use_are_read_from_the_directory_listing() -> None:
    listing = "_www                     70\nalex                     501\n_oahd                    441\n"
    assert ownuser.ids_in(listing) == {70, 501, 441}


def test_the_account_plan_is_a_hidden_role_account_with_its_own_group_and_no_login() -> None:
    plan = ownuser.account_plan(499)
    joined = [" ".join(argv) for argv in plan]
    assert joined[0] == "/usr/sbin/dseditgroup -o create -i 499 -r Estia _estia"
    add = next(argv for argv in plan if "-addUser" in argv)
    assert add[0] == "/usr/sbin/sysadminctl" and "-roleAccount" in add
    assert add[add.index("-UID") + 1] == "499" and add[add.index("-GID") + 1] == "499"
    assert add[add.index("-shell") + 1] == "/usr/bin/false" and add[add.index("-home") + 1] == "/var/estia"
    assert "-password" not in add and "-admin" not in add
    assert not any("staff" in line for line in joined)
    record = "/Users/" + ROLE_USER  # the directory-service record, not a home folder
    settings = [" ".join(argv) for argv in ownuser.account_settings()]
    assert f"/usr/bin/dscl . -create {record} IsHidden 1" in settings
    assert f"/usr/bin/dscl . -create {record} UserShell /usr/bin/false" in settings
    assert f"/usr/bin/dscl . -delete {record} AuthenticationAuthority" in settings  # no shadow hash to log in with
    assert f"/usr/bin/dscl . -delete {record} ShadowHashData" in settings
    # live 13 Oct (macOS 27): the record keeps /var/empty as her home and root cannot change it (eDSPermissionError):
    # her services get the home explicitly instead (HOME, CFFIXED_USER_HOME, signal-cli --config, -Duser.home)
    assert not any("NFSHomeDirectory" in line for line in settings)
    assert settings[-1] == f"/usr/bin/dscl . -create {record} Password *"  # no password can log in
    assert not any("dseditgroup" in line for line in map(" ".join, ownuser.account_plan(499, group=False)))


def test_the_plist_values_are_her_user_and_the_root_owned_code() -> None:
    values = ownuser.plist_values()
    assert values == {
        "RUN_USER": "_estia",
        "RUN_GROUP": "_estia",
        "RUN_HOME": "/var/estia",
        "CODE": "/usr/local/estia/current",
    }  # no Homebrew: its folders are yours, and what she runs comes from the release


def test_render_fills_placeholders_and_refuses_unknown_ones() -> None:
    text = ownuser.render("<string>@RUN_USER@</string> @CODE@", {"RUN_USER": "_estia", "CODE": "/a&b"}, plist=True)
    assert text == "<string>_estia</string> /a&amp;b"
    with pytest.raises(ValueError, match="@NOPE@"):
        ownuser.render("@NOPE@", {}, plist=True)
    with pytest.raises(ValueError, match="empty"):
        ownuser.render("@CODE@", {"CODE": ""}, plist=True)


@needs_plutil
@pytest.mark.parametrize("label", ["bridge", "voice"])
def test_the_plists_render_with_her_values(tmp_path: Path, label: str) -> None:
    template = ROOT / "launchd" / f"com.local-assistant.{label}.plist.in"
    out = tmp_path / f"{label}.plist"
    ownuser.render_file(template, out, ownuser.plist_values())
    plist = plistlib.loads(out.read_bytes())
    assert (plist["UserName"], plist["GroupName"]) == ("_estia", "_estia")
    assert plist["EnvironmentVariables"]["HOME"] == "/var/estia"
    assert plist["EnvironmentVariables"]["CFFIXED_USER_HOME"] == "/var/estia"  # Swift and Foundation read this home
    assert plist["ProgramArguments"][0].startswith("/usr/local/estia/current/")
    assert plist["WorkingDirectory"].startswith("/usr/local/estia/current")
    assert plist["StandardErrorPath"].startswith("/var/estia/Library/Logs/local-assistant/")
    assert plist["Umask"] == 63
    if label == "bridge":
        assert (
            plist["EnvironmentVariables"]["PATH"] == "/usr/local/estia/current/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        )
    assert ownuser.plist_problem(plist, f"com.local-assistant.{label}") is None
    assert subprocess.run(["plutil", "-lint", str(out)], capture_output=True, check=False).returncode == 0
    assert (out.stat().st_mode & 0o777) == 0o644


# ------------------------------------------------------------------ doctor's install checks


def installed(root: Path, *, user: str = ROLE_USER, program: str = "/usr/local/estia/current/.venv/bin/x") -> Path:
    """A finished own-user install under `root` (files owned by whoever runs the test)."""
    release = root / "usr/local/estia/releases/20261013T120000Z-abc123"
    (release / "src").mkdir(parents=True)
    (release / "src" / "x.py").write_text("x")
    (root / "usr/local/estia/current").symlink_to("releases/20261013T120000Z-abc123")
    (root / "var/estia").mkdir(parents=True)
    (root / "var/estia").chmod(0o700)
    daemons = root / "Library/LaunchDaemons"
    daemons.mkdir(parents=True)
    plist = {
        "Label": "com.local-assistant.bridge",
        "UserName": user,
        "GroupName": user,
        "ProgramArguments": [program],
        "EnvironmentVariables": {"HOME": "/var/estia", "CFFIXED_USER_HOME": "/var/estia"},
    }
    (daemons / "com.local-assistant.bridge.plist").write_bytes(plistlib.dumps(plist))
    (daemons / "com.local-assistant.bridge.plist").chmod(0o644)
    return release


@pytest.fixture
def as_her(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test user stands in for root (the code's owner) and for _estia (her home's owner)."""
    me = os.getuid()
    monkeypatch.setattr(ownuser, "CODE_UID", me)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: me if name == ROLE_USER else None)


def by_key(checks: Sequence[Check]) -> dict[str, Check]:
    return {check.key: check for check in checks}


def test_no_install_at_all_gives_no_line(tmp_path: Path) -> None:
    assert ownuser.install_checks(tmp_path) == []


def test_a_daemon_that_runs_as_the_owner_is_a_warning_with_the_migration(tmp_path: Path) -> None:
    daemons = tmp_path / "Library/LaunchDaemons"
    daemons.mkdir(parents=True)
    plist = {"Label": "x", "UserName": "alex", "ProgramArguments": ["/Users/alex/estia/.venv/bin/local-assistant"]}
    (daemons / "com.local-assistant.bridge.plist").write_bytes(plistlib.dumps(plist))
    checks = ownuser.install_checks(tmp_path)
    assert [(c.key, c.status) for c in checks] == [("own_user", "warn")]
    assert "scripts/migrate-to-own-user.sh --dry-run" in checks[0].fix


def test_a_finished_install_is_ok(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    found = by_key(ownuser.install_checks(tmp_path))
    assert {key: check.status for key, check in found.items()} == {"own_user": "ok", "code": "ok", "plists": "ok"}


def test_code_the_owner_can_change_is_a_failure(tmp_path: Path, as_her: None) -> None:
    release = installed(tmp_path)
    (release / "src" / "x.py").chmod(0o666)
    check = by_key(ownuser.install_checks(tmp_path))["code"]
    assert check.status == "fail" and "estia-deploy" in check.fix


def test_code_owned_by_another_account_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed(tmp_path)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: os.getuid())
    monkeypatch.setattr(ownuser, "CODE_UID", 0)  # the files belong to the test user, not root
    assert by_key(ownuser.install_checks(tmp_path))["code"].status == "fail"


def test_a_plist_that_still_runs_from_the_checkout_is_a_failure(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path, program="/Users/alex/estia/.venv/bin/local-assistant")
    check = by_key(ownuser.install_checks(tmp_path))["plists"]
    assert check.status == "fail" and "bridge" in check.text


def test_a_missing_role_user_is_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed(tmp_path)
    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid())
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: None)
    assert by_key(ownuser.install_checks(tmp_path))["own_user"].status == "fail"


@pytest.mark.parametrize(("days", "status", "word"), [(2, "ok", "estia rollback"), (9, "warn", "estia remove-old")])
def test_the_rollback_window(tmp_path: Path, as_her: None, days: int, status: str, word: str) -> None:
    installed(tmp_path)
    marker = {"owner": "alex", "moved_at": time.time() - days * 86400, "folders": ["/x"], "removed": False}
    (tmp_path / "usr/local/estia/migration.json").write_text(json.dumps(marker))
    check = by_key(ownuser.install_checks(tmp_path))["rollback"]
    assert check.status == status and word in check.text + check.fix
    marker["removed"] = True
    (tmp_path / "usr/local/estia/migration.json").write_text(json.dumps(marker))
    assert "rollback" not in by_key(ownuser.install_checks(tmp_path))


def test_her_home_must_be_hers_alone(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    (tmp_path / "var/estia").chmod(0o755)
    check = by_key(ownuser.install_checks(tmp_path))["own_user"]
    assert check.status == "fail" and "/var/estia" in check.text


def test_the_checks_read_only(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    before = sorted((str(p), p.lstat().st_mode, p.lstat().st_mtime_ns) for p in tmp_path.rglob("*"))
    ownuser.install_checks(tmp_path)
    assert sorted((str(p), p.lstat().st_mode, p.lstat().st_mtime_ns) for p in tmp_path.rglob("*")) == before
    assert stat.S_IMODE((tmp_path / "var/estia").stat().st_mode) == 0o700


# ------------------------------------------------------------------ the migration


class RecordingHost(ownuser.Host):
    """The real Host under a temporary root (system tools refused and printed), with every argv recorded."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, say=lambda text: None)
        self.asked: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        return super().run(argv, **kwargs)  # type: ignore[arg-type]

    def pipe(self, first: Sequence[str], second: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append((*first, "|", *second))
        return super().pipe(first, second, **kwargs)  # type: ignore[arg-type]


OLD_PLIST = b"<plist><dict><key>UserName</key><string>alex</string></dict></plist>"
NUMBER = "+306900000000"


def her_old_setup(home: Path, root: Path, *, services: bool = True) -> None:
    """The owner's install as it is today: her folders in the owner's home, both services, the newsyslog rule."""
    files = {
        ".config/local-assistant/config.toml": (
            f'bot_number = "{NUMBER}"\n[calls]\nsignal_cli = "~/dev/vendor/signal-cli-0.14.9-jvm/x/bin/signal-cli"\n'
            '[projects]\nroot = "~/dev"\n',
            0o600,
        ),
        ".config/local-assistant/web.env": ("BRAVE_API_KEY=abc\n", 0o600),
        "Library/Application Support/local-assistant/assistant.db": ("db", 0o600),
        "Library/Application Support/local-assistant/vendor/big.bin": ("x", 0o755),
        ".local/share/signal-cli/data/accounts.json": (json.dumps({"accounts": [{"number": NUMBER}]}), 0o644),
        ".local/share/signal-cli/data/111111.d/account.db": ("signal", 0o644),  # signal-cli's own default
        "Library/Logs/local-assistant/bridge.log": ("log", 0o600),
        "Library/Caches/voice-service/cache.bin": ("ane", 0o644),
        ".cache/fluidaudio/Models/kokoro/model.bin": ("tts", 0o644),
    }
    for name, (text, mode) in files.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
    daemons = root / "Library/LaunchDaemons"
    daemons.mkdir(parents=True, exist_ok=True)
    if services:
        for label in ownuser.LABELS:
            (daemons / f"{label}.plist").write_bytes(OLD_PLIST)
        (root / "etc/newsyslog.d").mkdir(parents=True)
        (root / "etc/newsyslog.d/local-assistant.conf").write_text("old rule\n")


def a_checkout(tmp_path: Path) -> Path:
    """What the move reads from the checkout: the service templates and the voice service's build."""
    repo = tmp_path / "checkout"
    (repo / "launchd").mkdir(parents=True)
    for template in (ROOT / "launchd").glob("*.plist.in"):
        shutil.copy(template, repo / "launchd" / template.name)
    voice = repo / "tools/voice/.build/release/voice-service"
    voice.parent.mkdir(parents=True)
    voice.write_text("binary")
    return repo


def fake_deploy(root: Path, result: int = 0, seen: list[str] | None = None) -> ownuser.DeployStep:
    def run() -> int:
        if seen is not None:
            seen.append("deploy")
        if result:
            return result
        release = root / "usr/local/estia/releases/r1"
        (release / "launchd").mkdir(parents=True, exist_ok=True)
        for template in (ROOT / "launchd").glob("*.plist.in"):
            shutil.copy(template, release / "launchd" / template.name)
        jvm = release / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
        jvm.parent.mkdir(parents=True, exist_ok=True)
        jvm.write_text("#!/bin/sh\n")
        current = root / "usr/local/estia/current"
        if not current.is_symlink():
            current.symlink_to("releases/r1")
        return result

    return run


def migration(
    tmp_path: Path,
    *,
    mode: str = "real",
    result: int = 0,
    said: list[str] | None = None,
    seen: list[str] | None = None,
) -> tuple[ownuser.Migration, RecordingHost, Path, Path]:
    home, root = tmp_path / "home", tmp_path / "root"
    host = RecordingHost(root)
    owner = ownuser.Owner("alex", home, os.getuid())
    checkout = tmp_path / "checkout"
    if not checkout.exists():
        a_checkout(tmp_path)
    move = ownuser.Migration(
        host,
        owner,
        checkout=checkout,
        mode=mode,
        deploy=fake_deploy(root, result, seen),
        say=(said.append if said is not None else lambda text: None),
    )
    return move, host, home, root


def modes(folder: Path) -> dict[str, int]:
    return {str(p.relative_to(folder)): stat.S_IMODE(p.lstat().st_mode) for p in folder.rglob("*")}


SECRET_FOLDERS = (
    ".config/local-assistant",
    "Library/Application Support/local-assistant",
    ".local/share/signal-cli",
    "Library/Logs/local-assistant",
)


@pytest.fixture
def no_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: None)  # no _estia yet


@needs_plutil
def test_the_migration_moves_her_data_into_her_home_with_private_modes(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    assert stat.S_IMODE(her.stat().st_mode) == 0o700
    copied = modes(her)
    assert copied[".local/share/signal-cli/data/111111.d/account.db"] == 0o600
    assert copied[".config/local-assistant/web.env"] == 0o600
    assert copied["Library/Caches/voice-service/cache.bin"] == 0o600  # the voice service's compile cache
    assert (her / ".cache/fluidaudio/Models/kokoro/model.bin").read_text() == "tts"  # its speech models
    assert all(mode & 0o077 == 0 for mode in copied.values())
    assert not (her / "Library/Application Support/local-assistant/vendor").exists()  # code goes into the release
    held = root / "usr/local/estia/rollback/data"
    assert stat.S_IMODE(held.stat().st_mode) == 0o700  # root only: your programs cannot reach them
    for old in SECRET_FOLDERS:
        assert not (home / old).exists(), old  # moved out of your home, by rename: nothing re-owned
    assert (home / "Library/Caches/voice-service/cache.bin").exists()  # caches hold no secret: left
    plist = plistlib.loads((root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes())
    assert plist["UserName"] == "_estia" and plist["ProgramArguments"][0].startswith("/usr/local/estia/current/")
    assert (root / "Library/LaunchDaemons/com.local-assistant.voice.plist").exists()
    assert not (root / "etc/newsyslog.d/local-assistant.conf").exists()
    assert (root / "usr/local/estia/rollback/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    marker = json.loads((root / "usr/local/estia/migration.json").read_text())
    assert marker["owner"] == "alex" and marker["fresh"] is False
    sources = {source: Path(target) for source, target in marker["held"]}
    config_held = sources[str(home / ".config/local-assistant")]
    assert config_held.parent == held and (config_held / "config.toml").exists()
    tools = [Path(argv[0]).name for argv in host.asked]
    assert tools.index("launchctl") < tools.index("sysadminctl")  # stopped before anything moves
    assert ("/usr/sbin/chown", "_estia:_estia", str(her)) in host.asked
    copy = next(argv for argv in host.asked if "|" in argv)
    assert copy[copy.index("|") + 1 :][:2] == ("/usr/bin/tar", "-x")  # read as you, written as her: no root copy
    assert (
        ownuser.LAUNCHCTL,
        "bootstrap",
        "system",
        "/Library/LaunchDaemons/com.local-assistant.bridge.plist",
    ) in host.asked
    text = (her / ".config/local-assistant/config.toml").read_text()
    assert '# signal_cli = "~/dev/vendor' in text and "\nsignal_cli" not in text  # the release's vendor/ is used
    assert "signal_cli = " in (config_held / "config.toml").read_text()  # the original is untouched
    assert any("projects.root" in line for line in said)  # a path that now means her home is named
    assert "abc" not in "\n".join(said)  # no value is printed
    assert any("before the move" in line and "SECURITY.md" in line for line in said)


def test_the_release_is_built_before_she_is_stopped(tmp_path: Path, no_user: None) -> None:
    seen: list[str] = []
    move, _host, home, root = migration(tmp_path, seen=seen)
    her_old_setup(home, root)
    real = move.host.run

    def run(argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        if argv[:2] == [ownuser.LAUNCHCTL, "bootout"]:
            seen.append("stop")
        return real(argv, **kwargs)  # type: ignore[arg-type]

    move.host.run = run  # type: ignore[method-assign]
    assert move.run() == 0
    assert seen[:2] == ["deploy", "stop"]


def test_a_failed_build_changes_nothing(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, host, home, root = migration(tmp_path, result=1, said=said)
    her_old_setup(home, root)
    assert move.run() == 1
    assert not any(argv[0] == ownuser.LAUNCHCTL for argv in host.asked)  # she was never stopped
    assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    assert (home / ".config/local-assistant/config.toml").exists()
    assert not (root / "usr/local/estia/migration.json").exists()
    assert any("Nothing changed" in line for line in said)


@pytest.mark.parametrize("missing", ["config", "account", "voice"])
def test_the_preflight_stops_before_anything_changes(tmp_path: Path, no_user: None, missing: str) -> None:
    said: list[str] = []
    seen: list[str] = []
    move, host, home, root = migration(tmp_path, said=said, seen=seen)
    her_old_setup(home, root)
    if missing == "config":
        (home / ".config/local-assistant/config.toml").unlink()
    elif missing == "account":
        (home / ".local/share/signal-cli/data/accounts.json").unlink()
    else:
        (tmp_path / "checkout/tools/voice/.build/release/voice-service").unlink()
    assert move.run() == 1
    assert seen == [] and host.asked == []  # nothing built, nothing run
    text = "\n".join(said)
    assert "Nothing changed" in text
    assert {"config": "scripts/setup.sh", "account": "registers", "voice": "swift build -c release"}[missing] in text


@needs_plutil
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, RuntimeError])
def test_an_interruption_after_the_stop_brings_her_old_setup_back(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException]
) -> None:
    """A Ctrl-C, or a hang-up from a dropped SSH session, never leaves her stopped."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)

    def broken() -> None:
        raise interruption("gone")

    monkeypatch.setattr(move, "_install_plists", lambda voice: broken())
    assert move.run() == 1
    assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    for old in SECRET_FOLDERS:
        assert (home / old).is_dir(), old  # back in your home
    assert stat.S_IMODE((home / ".config/local-assistant/config.toml").stat().st_mode) == 0o600  # as it was
    assert (
        "/bin/launchctl",
        "bootstrap",
        "system",
        "/Library/LaunchDaemons/com.local-assistant.bridge.plist",
    ) in host.asked
    assert any("Her old setup comes back" in line for line in said)


def test_a_hang_up_becomes_an_interruption() -> None:
    import signal

    with pytest.raises(KeyboardInterrupt), ownuser.interruptions_raise():
        os.kill(os.getpid(), signal.SIGHUP)
        time.sleep(1)
    assert signal.getsignal(signal.SIGHUP) is not None


@needs_plutil
def test_a_fresh_install_that_fails_says_she_does_not_run_yet(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    monkeypatch.setattr(move, "_install_plists", lambda voice: (_ for _ in ()).throw(RuntimeError("no plist")))
    assert move.run() == 1
    text = "\n".join(said)
    assert "Nothing was installed: she does not run yet" in text and "comes back" not in text
    assert (home / ".config/local-assistant/config.toml").exists()


@needs_plutil
def test_a_fresh_install_keeps_no_old_copy(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    assert not (root / "usr/local/estia/rollback/data").exists()  # her only copy is in /var/estia
    marker = json.loads((root / "usr/local/estia/migration.json").read_text())
    assert marker["fresh"] is True
    assert migration(tmp_path, mode="rollback")[0].rollback() == 1  # no earlier setup to go back to


@needs_plutil
def test_a_second_run_changes_nothing(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    before = modes(root)
    again, host2, _, _ = migration(tmp_path)
    assert again.run() == 0
    assert modes(root) == before and not any("sysadminctl" in argv[0] for argv in host2.asked)


@needs_plutil
def test_the_rollback_brings_back_her_old_setup_with_what_she_saved_since(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    (her / ".local/share/signal-cli/data/111111.d/account.db").write_text("ratchet moved on")  # newer sessions
    (her / "Library/Application Support/local-assistant/assistant.db").write_text("a week of messages")
    (her / ".config/local-assistant/config.toml").write_text("edited as her\n")
    assert ownuser.moved(root)
    back, host2, _, _ = migration(tmp_path, mode="rollback")
    assert back.rollback() == 0
    for label in ownuser.LABELS:
        assert (root / f"Library/LaunchDaemons/{label}.plist").read_bytes() == OLD_PLIST
    assert (root / "etc/newsyslog.d/local-assistant.conf").read_text() == "old rule\n"
    assert (home / ".local/share/signal-cli/data/111111.d/account.db").read_text() == "ratchet moved on"
    assert (home / "Library/Application Support/local-assistant/assistant.db").read_text() == "a week of messages"
    assert (home / "Library/Application Support/local-assistant/vendor/big.bin").exists()  # your old folder is back
    assert "signal_cli" in (home / ".config/local-assistant/config.toml").read_text()  # your config, not hers
    assert os.access(home / ".config/local-assistant/config.toml", os.W_OK)
    assert stat.S_IMODE((home / ".local/share/signal-cli/data/111111.d/account.db").stat().st_mode) == 0o600
    assert json.loads((root / "usr/local/estia/migration.json").read_text())["rolled_back"] is True
    assert (ownuser.LAUNCHCTL, "bootout", "system/com.local-assistant.bridge") in host2.asked
    assert not ownuser.moved(root)  # the scripts take the owner's path again
    assert migration(tmp_path, mode="rollback")[0].rollback() == 1  # once only


@needs_plutil
def test_no_rollback_after_the_window(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    marker = root / "usr/local/estia/migration.json"
    data = json.loads(marker.read_text())
    data["moved_at"] -= 8 * 86400
    marker.write_text(json.dumps(data))
    assert migration(tmp_path, mode="rollback")[0].rollback() == 1


@needs_plutil
def test_a_move_after_a_rollback_keeps_her_earlier_copy_aside(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path, mode="rollback")[0].rollback() == 0
    again, _, _, _ = migration(tmp_path, said=said)
    assert again.run() == 0
    kept = [p for p in (root / "var/estia").iterdir() if p.name.startswith(".before-")]
    assert kept and (kept[0] / ".local/share/signal-cli").is_dir()  # never deleted
    assert any("kept her earlier copy" in line for line in said)


@needs_plutil
def test_her_chmod_walks_only_the_folders_just_copied(tmp_path: Path, no_user: None) -> None:
    """Root makes the folders that hold an earlier copy (live 13 Oct): her chmod over her whole home stopped on them.
    It names only the folders just copied, the ones doctor checks."""
    move, host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    chmods = [argv for argv in host.asked if hostsec.CHMOD in argv and "go-rwx" in argv]
    assert chmods and all(str(her) not in argv for argv in chmods)
    assert {str(her / new) for _old, new in ownuser.DATA_MAP} <= {arg for argv in chmods for arg in argv}


def test_a_dry_run_after_an_earlier_try_needs_nothing_of_hers(tmp_path: Path, no_user: None) -> None:
    """After a first try her home is hers alone (0700, live 13 Oct): the dry run, run as you, stopped on it."""
    home, root = tmp_path / "home", tmp_path / "root"
    her_old_setup(home, root)
    her = root / "var/estia"
    (her / ".config/local-assistant").mkdir(parents=True)
    said: list[str] = []
    move = ownuser.Migration(
        ownuser.Host(root, dry_run=True, say=said.append),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=a_checkout(tmp_path),
        mode="dry",
        deploy=lambda: 0,
        say=said.append,
    )
    her.chmod(0)
    try:
        result = move.run()
    finally:
        her.chmod(0o700)
    text = "\n".join(said)
    assert result == 0, text
    assert "earlier copy" in text and "Nothing changed (a dry run)" in text


@needs_plutil
def test_remove_old_deletes_the_held_copies(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path, mode="remove-old")[0].remove_old() == 0
    assert not (root / "usr/local/estia/rollback/data").exists()
    assert json.loads((root / "usr/local/estia/migration.json").read_text())["removed"] is True


def test_a_rehearsal_leaves_her_folders_alone(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path, mode="rehearse")
    her_old_setup(home, root)
    before = modes(home)
    assert move.run() == 0
    assert modes(home) == before  # untouched
    assert (root / "var/estia/.config/local-assistant/config.toml").exists()
    assert (root / "Library/LaunchDaemons/com.local-assistant.voice.plist").read_bytes() != OLD_PLIST


def test_a_dry_run_writes_nothing(tmp_path: Path, no_user: None) -> None:
    home, root = tmp_path / "home", tmp_path / "root"
    her_old_setup(home, root)
    before = (modes(home), modes(root))
    said: list[str] = []
    host = ownuser.Host(root, dry_run=True, say=said.append)
    move = ownuser.Migration(
        host,
        ownuser.Owner("alex", home, os.getuid()),
        checkout=a_checkout(tmp_path),
        mode="dry",
        deploy=lambda: 0,
        say=said.append,
    )
    assert move.run() == 0
    assert (modes(home), modes(root)) == before
    text = "\n".join(said)
    assert "would run: /usr/sbin/sysadminctl -addUser _estia" in text and "would copy" in text
    assert "BRAVE_API_KEY" not in text and "abc" not in text
    assert "Nothing changed (a dry run)" in text


def test_the_first_deploy_runs_while_your_old_folders_are_still_readable(tmp_path: Path, no_user: None) -> None:
    """The build reads vendor builds and the voice binary from your folders: they are moved only after it."""
    home, root = tmp_path / "home", tmp_path / "root"
    her_old_setup(home, root)
    seen: list[bool] = []
    deploy = fake_deploy(root)

    def checked() -> int:
        seen.append(os.access(home / "Library/Application Support/local-assistant/vendor", os.W_OK))
        return deploy()

    move = ownuser.Migration(
        RecordingHost(root),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=a_checkout(tmp_path),
        mode="real",
        deploy=checked,
    )
    assert move.run() == 0 and seen == [True]


# her account: made again when a first run stopped half way, and checked


class DirectoryHost(RecordingHost):
    """A directory service where an earlier run made the group (gid 497) but not the user."""

    def run(self, argv: Sequence[str], **kwargs: object) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        if argv[:3] == [hostsec.DSCL, ".", "-read"] and argv[3] == "/Groups/_estia":
            return 0, "PrimaryGroupID: 497\n"
        if argv[:3] == [hostsec.DSCL, ".", "-list"]:
            return 0, "_www 70\nalex 501\n" if argv[3] == "/Users" else "_estia 497\nstaff 20\n"
        if "-delete" in argv:
            return 182, "eDSAttributeNotFound"  # not there: expected
        return 0, ""


def test_a_second_try_uses_the_group_an_earlier_run_made(tmp_path: Path, no_user: None) -> None:
    host = DirectoryHost(tmp_path / "root")
    move = ownuser.Migration(
        host, ownuser.Owner("alex", tmp_path / "home", os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    move._make_user()
    joined = [" ".join(argv) for argv in host.asked]
    assert not any("dseditgroup" in line for line in joined)
    add = next(argv for argv in host.asked if "-addUser" in argv)
    assert add[add.index("-UID") + 1] == "497" and add[add.index("-GID") + 1] == "497"
    assert joined[-2].endswith("Password *") or any(line.endswith("Password *") for line in joined)


def test_the_account_settings_are_set_again_when_the_user_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: 497)
    host = DirectoryHost(tmp_path / "root")
    move = ownuser.Migration(
        host, ownuser.Owner("alex", tmp_path / "home", os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    move._make_user()
    joined = [" ".join(argv) for argv in host.asked]
    assert any(line.endswith("IsHidden 1") for line in joined) and any(line.endswith("Password *") for line in joined)
    assert not any("sysadminctl" in line for line in joined)


@pytest.mark.parametrize(
    ("record", "problem"),
    [
        ("NFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n", None),
        ("NFSHomeDirectory: /var/empty\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n", None),  # macOS keeps it
        ("UserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n", None),
        ("NFSHomeDirectory: /var/estia\nUserShell: /bin/zsh\nIsHidden: 1\nPassword: *\n", "shell"),
        ("NFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 0\nPassword: *\n", "login window"),
        (
            "AuthenticationAuthority: ;ShadowHash;HASHLIST:<SALTED-SHA512-PBKDF2>\nUserShell: /usr/bin/false\n"
            "IsHidden: 1\nPassword: ********\nNFSHomeDirectory: /var/estia\n",
            "AuthenticationAuthority",
        ),
        ("NFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: ********\n", "password"),
    ],
)
def test_her_account_record_is_checked(record: str, problem: str | None) -> None:
    found = ownuser.account_problem(record)
    assert (found is None) if problem is None else (found is not None and problem in found)


def test_doctor_shows_an_account_with_a_password_as_a_failure(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    record = "AuthenticationAuthority: ;ShadowHash;\nNFSHomeDirectory: /var/estia\nUserShell: /usr/bin/false\nIsHidden: 1\nPassword: ********\n"
    check = by_key(ownuser.install_checks(tmp_path, account=lambda: record))["own_user"]
    assert check.status == "fail" and "AuthenticationAuthority" in check.text


# her service files: only hers


GOOD: dict[str, Any] = {
    "Label": "com.local-assistant.bridge",
    "UserName": "_estia",
    "GroupName": "_estia",
    "ProgramArguments": ["/usr/local/estia/current/.venv/bin/local-assistant"],
    "EnvironmentVariables": {
        "HOME": "/var/estia",
        "CFFIXED_USER_HOME": "/var/estia",
        "PATH": "/usr/local/estia/current/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
    },
    "WorkingDirectory": "/usr/local/estia/current",
    "StandardOutPath": "/var/estia/Library/Logs/local-assistant/launchd.log",
}


@pytest.mark.parametrize(
    ("change", "word"),
    [
        ({"UserName": "root"}, "UserName"),
        ({"ProgramArguments": ["/Users/alex/estia/.venv/bin/local-assistant"]}, "program"),
        ({"EnvironmentVariables": {**GOOD["EnvironmentVariables"], "DYLD_INSERT_LIBRARIES": "/tmp/x"}}, "Environment"),
        ({"EnvironmentVariables": {**GOOD["EnvironmentVariables"], "PATH": "/opt/homebrew/bin:/usr/bin"}}, "PATH"),
        ({"Sockets": {}}, "Sockets"),
        (
            {"EnvironmentVariables": {**GOOD["EnvironmentVariables"], "CFFIXED_USER_HOME": "/Users/alex"}},
            "CFFIXED_USER_HOME",
        ),
        ({"StandardOutPath": "/etc/sudoers.d/zz"}, "log folder"),
        ({"Label": "com.example.other"}, "Label"),
    ],
)
def test_a_service_file_that_is_not_only_hers_is_named(change: dict[str, object], word: str) -> None:
    assert ownuser.plist_problem(GOOD, "com.local-assistant.bridge") is None
    problem = ownuser.plist_problem({**GOOD, **change}, "com.local-assistant.bridge")
    assert problem is not None and word in problem


def test_a_service_file_others_can_change_is_a_failure(tmp_path: Path, as_her: None) -> None:
    installed(tmp_path)
    (tmp_path / "Library/LaunchDaemons/com.local-assistant.bridge.plist").chmod(0o666)
    check = by_key(ownuser.install_checks(tmp_path))["plists"]
    assert check.status == "fail" and "estia-deploy" in check.fix


def test_moved_follows_the_bridges_service_file_and_the_rollback(tmp_path: Path) -> None:
    assert not ownuser.moved(tmp_path)
    installed(tmp_path)
    assert ownuser.moved(tmp_path)
    (tmp_path / "usr/local/estia/migration.json").write_text(json.dumps({"rolled_back": True}))
    assert not ownuser.moved(tmp_path)


def test_a_link_chain_out_of_the_code_is_a_failure(tmp_path: Path, as_her: None) -> None:
    release = installed(tmp_path)
    (release / "src" / "b").symlink_to(".")
    (release / "b").symlink_to("..")
    (release / "src" / "evil.pth").symlink_to("b/b/../../../../../../etc/hosts")
    check = by_key(ownuser.install_checks(tmp_path))["code"]
    assert check.status == "fail" and "lead out" in check.text


# what she runs: root's alone


def test_her_programs_must_be_roots_alone(tmp_path: Path, as_her: None) -> None:
    mine = tmp_path / "opt/homebrew/bin"
    mine.mkdir(parents=True)
    (mine / "signal-cli").write_text("x")
    ok = tmp_path / "usr/bin"
    ok.mkdir(parents=True)
    (ok / "true").write_text("x")
    assert ownuser.program_check([("tool", ok / "true")], tmp_path).status == "ok"
    mine.chmod(0o775)  # the owner's group may write: any program of yours can swap it
    check = ownuser.program_check([("signal-cli", mine / "signal-cli")], tmp_path)
    assert check.status == "fail" and "signal-cli" in check.text and "estia-deploy" in check.fix


def test_her_programs_owned_by_someone_else_are_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ownuser, "CODE_UID", os.getuid() + 1)
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/signal-cli").write_text("x")
    assert ownuser.program_check([("signal-cli", tmp_path / "bin/signal-cli")], tmp_path).status == "fail"


# her config: the programs she runs point into the release


def test_her_program_keys_are_taken_out_or_pointed_at_the_release(tmp_path: Path) -> None:
    vendor = tmp_path / "vendor"
    for path in (
        "signal-cli-native/bin/signal-cli",
        "openjdk.jdk/Contents/Home/bin/java",
        "signal-cli-0.14.10-jvm/signal-cli-0.14.10/bin/signal-cli",
        "signal-call-tunnel/signal-call-tunnel/target/release/signal-call-tunnel",
    ):
        (vendor / path).parent.mkdir(parents=True, exist_ok=True)
        (vendor / path).write_text("x")
    home = Path("/Users/alex")
    text = (
        'signal_cli = "/opt/homebrew/bin/signal-cli"\n'
        "[calls]\n"
        'signal_cli = "/Users/alex/dev/vendor/signal-cli-0.14.10-jvm/signal-cli-0.14.10/bin/signal-cli"\n'
        'java_home = "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"\n'
        'tunnel = "~/dev/vendor/signal-call-tunnel/x"\n'
        "[health]\n"
        'watch_logs = ["~/Library/Logs/x.log", "/var/log/system.log"]\n'
    )
    out, notes = ownuser.config_notes(text, home, vendor)
    assert '# signal_cli = "/opt/homebrew/bin/signal-cli"' in out  # the default finds her own copy
    assert (
        'signal_cli = "/usr/local/estia/current/vendor/signal-cli-0.14.10-jvm/signal-cli-0.14.10/bin/signal-cli"' in out
    )  # the same version, from the release
    assert '# java_home = "/opt/homebrew' in out and '# tunnel = "~/dev' in out
    joined = "\n".join(notes)
    assert "health.watch_logs" in joined  # a home path inside a list is named
    assert "/opt/homebrew" not in joined and "~/" not in joined  # keys only, never values


def test_without_her_own_copy_a_program_key_is_named_not_changed(tmp_path: Path) -> None:
    text = 'signal_cli = "/opt/homebrew/bin/signal-cli"\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out.startswith(text) and any("signal_cli" in note and "no copy" in note for note in notes)


# her projects folder: the owner's, by its absolute path (~ in her config is her own home)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ('bot_number = "+306900000000"\n[projects]\nroot = "~/dev"  # yours\ndocs = ["alpha"]\n', "  # yours"),
        ('bot_number = "+306900000000"\n[projects]\n#root = "~/dev"\ndefault = "summary"\n', "default"),
        ('bot_number = "+306900000000"\n[projects]\n[[tapo.devices]]\nname = "lamp"\n', "lamp"),
    ],
    ids=["set", "commented", "array-after"],
)
def test_her_config_names_your_projects_folder_by_its_absolute_path(text: str, kept: str) -> None:
    import tomllib

    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    data = tomllib.loads(out)
    assert data["projects"]["root"] == "/Users/alex/dev" and kept in out
    assert any(note.startswith('projects.root: set to "/Users/alex/dev"') for note in notes), notes
    assert ownuser.her_projects_root(text, Path("/Users/alex")) == Path("/Users/alex/dev")


@pytest.mark.parametrize(
    ("root", "expected", "word"),
    [
        ("~/code/work", "/Users/alex/code/work", "set to"),
        ("/Volumes/Work/dev", "/Volumes/Work/dev", "outside your home"),
    ],
)
def test_her_projects_folder_from_your_config(root: str, expected: str, word: str) -> None:
    import tomllib

    out, notes = ownuser.config_notes(f'[projects]\nroot = "{root}"\n', Path("/Users/alex"), None)
    assert tomllib.loads(out)["projects"]["root"] == expected
    assert any(note.startswith("projects.root:") and word in note for note in notes), notes


def test_without_projects_her_config_gets_no_folder_of_yours() -> None:
    """Projects are an extra: without [projects] in your config, none is added to hers and nothing is on record."""
    text = 'bot_number = "+306900000000"\n[health]\ninterval_hours = 4\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out == text and ownuser.her_projects_root(text, Path("/Users/alex")) is None
    assert notes == ["projects: off (no [projects] in your config): no folder of yours is shared with her"]


def test_a_relative_projects_folder_is_named_not_guessed() -> None:
    text = '[projects]\nroot = "dev"\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out == text and ownuser.her_projects_root(text, Path("/Users/alex")) is None
    assert any(note.startswith("projects.root:") and "absolute" in note for note in notes)


def test_a_projects_root_that_cannot_be_written_is_named() -> None:
    text = "[projects]\nroot = '~/dev'\n"  # a literal string: the line is not rewritten in place
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    assert out == text
    assert any(note.startswith("projects.root:") and 'root = "/Users/alex/dev"' in note for note in notes)


@needs_plutil
def test_the_move_records_her_projects_folder(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    assert move.run() == 0
    marker = json.loads((root / "usr/local/estia/migration.json").read_text())
    assert marker["projects_root"] == str(home / "dev")
    import tomllib

    her = tomllib.loads((root / "var/estia/.config/local-assistant/config.toml").read_text())
    assert her["projects"]["root"] == str(home / "dev")
    assert any(line.startswith(f'config: projects.root: set to "{home / "dev"}"') for line in said)


def test_code_under_a_folder_others_can_change_is_a_failure(tmp_path: Path, as_her: None) -> None:
    """Whoever can rename /usr/local/estia can put other code (and another `estia`) in its place."""
    installed(tmp_path)
    (tmp_path / "usr/local").chmod(0o777)
    check = by_key(ownuser.install_checks(tmp_path))["code"]
    assert check.status == "fail" and "/usr/local" in check.text


@needs_plutil
def test_a_leftover_in_the_holding_folder_never_stops_the_move(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    leftover = root / "usr/local/estia/rollback/data/0"
    leftover.mkdir(parents=True)
    (leftover / "x").write_text("from an earlier move")
    assert move.run() == 0
    assert (leftover / "x").exists()  # untouched
    held = json.loads((root / "usr/local/estia/migration.json").read_text())["held"]
    assert len({target for _source, target in held}) == len(held) == 4 and str(leftover) not in dict(held).values()


def test_a_folder_given_back_gets_its_missing_parents_as_yours(tmp_path: Path) -> None:
    """The rollback renames a held folder back; parents you deleted meanwhile are made again, and yours."""
    home, root = tmp_path / "home", tmp_path / "root"
    held = root / "usr/local/estia/rollback/data/1-0"
    held.mkdir(parents=True)
    (held / "account.db").write_text("x")
    move = ownuser.Migration(
        RecordingHost(root), ownuser.Owner("alex", home, os.getuid()), checkout=tmp_path, mode="real", deploy=lambda: 0
    )
    move._unhold([(str(home / ".local/share/signal-cli"), str(held))])
    assert (home / ".local/share/signal-cli/account.db").read_text() == "x"
    assert (home / ".local").stat().st_uid == os.getuid()


def test_a_hang_up_while_the_release_builds_changes_nothing(tmp_path: Path, no_user: None) -> None:
    """A dropped SSH session during the build: an interruption, so the half-built release is removed and she keeps
    running (nothing was stopped yet)."""
    import signal

    seen: list[object] = []
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)

    def building() -> int:
        seen.append(signal.getsignal(signal.SIGHUP))
        raise KeyboardInterrupt("signal 1")

    move.deploy = building
    assert move.run() == 1
    assert seen and seen[0] not in (signal.SIG_DFL, signal.SIG_IGN, None)  # a hang-up raises, it does not kill
    assert not any(argv[0] == ownuser.LAUNCHCTL for argv in host.asked) and any("Nothing changed" in s for s in said)


def test_a_folder_reached_through_a_link_out_of_your_home_is_never_moved(tmp_path: Path) -> None:
    """Root renames only folders that are really in your home: a parent you swapped for a link to elsewhere is left."""
    home, elsewhere = tmp_path / "home", tmp_path / "system"
    (elsewhere / "signal-cli").mkdir(parents=True)
    (home / ".local").mkdir(parents=True)
    (home / ".local/share").symlink_to(elsewhere)
    (home / ".config/local-assistant").mkdir(parents=True)
    move = ownuser.Migration(
        RecordingHost(tmp_path / "root"),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=tmp_path,
        mode="real",
        deploy=lambda: 0,
    )
    assert [str(p.relative_to(home)) for p in move._old_folders()] == [".config/local-assistant"]


def test_her_config_is_rewritten_from_the_copy_you_could_read(tmp_path: Path, no_user: None) -> None:
    """Root never opens your config through a path you control: it rewrites the copy your tar made for her."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    opened: list[str] = []
    real_open = os.open

    def watch(path: object, *args: object, **kwargs: object) -> int:
        opened.append(str(path))
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    import unittest.mock

    with unittest.mock.patch.object(os, "open", watch):
        move._make_user()
        move._copy_data()
    assert str(home / ".config/local-assistant/config.toml") not in opened
    assert str(root / "var/estia/.config/local-assistant/config.toml") in opened


def test_her_programs_name_what_she_runs(tmp_path: Path) -> None:
    from types import SimpleNamespace

    installed(tmp_path)
    plain = SimpleNamespace(
        calls_enabled=False, signal_cli="/usr/local/estia/current/vendor/signal-cli-native/bin/signal-cli"
    )
    names = dict(ownuser.her_programs(plain, tmp_path))
    assert names["signal-cli"] == Path(plain.signal_cli)
    jdk = Path("/usr/local/estia/current/vendor/openjdk.jdk/Contents/Home")
    calls = SimpleNamespace(
        calls_enabled=True,
        call_signal_cli=Path("/usr/local/estia/current/vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"),
        call_tunnel=Path("/usr/local/estia/current/vendor/signal-call-tunnel/x"),
        calls_java_home=None,
        java_home=jdk,  # the release's own JDK: the default when the config names none
    )
    names = dict(ownuser.her_programs(calls, tmp_path))
    assert names["Java"] == jdk / "bin" / "java" and "the call helper" in names and "signal-cli (calls)" in names


# once moved, the fixes never send you back to the move (it would run your checkout's Python as root)

GOOD_RECORD = "UserShell: /usr/bin/false\nIsHidden: 1\nPassword: *\n"


@pytest.mark.parametrize(
    ("state", "fix"),
    [("no user", "estia account"), ("no home", "estia rollback"), ("a password", "estia account")],
)
def test_once_moved_the_own_user_fix_never_names_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, fix: str
) -> None:
    installed(tmp_path)
    me = os.getuid()
    monkeypatch.setattr(ownuser, "CODE_UID", me)
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: None if state == "no user" else me)
    if state == "no home":
        shutil.rmtree(tmp_path / "var/estia")
    record = "UserShell: /usr/bin/false\nIsHidden: 1\nPassword: ********\n" if state == "a password" else GOOD_RECORD
    check = by_key(ownuser.install_checks(tmp_path, account=lambda: record))["own_user"]
    assert check.status == "fail"
    assert "migrate-to-own-user" not in check.fix and fix in check.fix


def test_estia_account_sets_her_account_again_with_fixed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ownuser, "lookup_uid", lambda name: 497)
    monkeypatch.setenv("ESTIA_ROOT", str(tmp_path))
    assert ownuser.main(["account"]) == 0
    out = capsys.readouterr().out
    record = f"/Users/{ownuser.ROLE_USER}"  # her user's record in Directory Services
    for line in (
        f"would run: /usr/bin/dscl . -create {record} UserShell /usr/bin/false",
        f"would run: /usr/bin/dscl . -create {record} IsHidden 1",
        f"would run: /usr/bin/dscl . -delete {record} AuthenticationAuthority",
        f"would run: /usr/bin/dscl . -delete {record} ShadowHashData",
        f"would run: /usr/bin/dscl . -create {record} Password *",
    ):
        assert line in out, line
    assert "sysadminctl" not in out  # she is there: only her settings


def test_estia_account_needs_root(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("ESTIA_ROOT", raising=False)
    if os.geteuid() == 0:
        pytest.skip("runs as root")
    assert ownuser.main(["account"]) == 2
    assert "/usr/bin/sudo /usr/local/estia/bin/estia account" in capsys.readouterr().err


# links and special files: refused before she stops, and again in her copy before she starts


@pytest.mark.parametrize("planted", ["a link out", "a dangling link out", "a FIFO"])
def test_the_preflight_refuses_a_link_out_or_a_special_file(tmp_path: Path, no_user: None, planted: str) -> None:
    said: list[str] = []
    seen: list[str] = []
    move, host, home, root = migration(tmp_path, said=said, seen=seen)
    her_old_setup(home, root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    data = home / ".local/share/signal-cli/data"
    if planted == "a FIFO":
        os.mkfifo(data / "pipe")
        name = "pipe"
    else:
        (data / "1.d").symlink_to(elsewhere if planted == "a link out" else tmp_path / "nowhere")
        name = "1.d"
    assert move.run() == 1
    assert seen == [] and host.asked == []  # nothing built, nothing run
    text = "\n".join(said)
    assert str(data / name) in text and "Nothing changed" in text


@needs_plutil
def test_a_link_inside_her_folders_is_copied(tmp_path: Path, no_user: None) -> None:
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    (home / ".local/share/signal-cli/data/latest").symlink_to("111111.d")
    assert move.run() == 0
    assert (root / "var/estia/.local/share/signal-cli/data/latest").is_symlink()


@needs_plutil
def test_a_link_planted_during_the_build_stops_the_move_before_she_starts(tmp_path: Path, no_user: None) -> None:
    """The preflight saw none; a link made while her release builds is found in her copy, and her old setup comes
    back before anything of hers starts from there."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    build = move.deploy

    def planting() -> int:
        (home / ".local/share/signal-cli/data/1.d").symlink_to(elsewhere)
        return build()

    move.deploy = planting
    assert move.run() == 1
    text = "\n".join(said)
    assert "/var/estia/.local/share/signal-cli/data/1.d" in text and "Her old setup comes back" in text
    assert not any(argv[0] == ownuser.LAUNCHCTL and "_estia" in " ".join(argv) for argv in host.asked)
    assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    assert (home / ".config/local-assistant/config.toml").exists()


# the restore and the rollback: failure-safe


def restoring(tmp_path: Path, path: str, said: list[str]) -> tuple[ownuser.Migration, RecordingHost, Path, Path]:
    """A move that fails after her old folders are held (so the restore runs), or a finished move to roll back."""
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    if path == "restore":

        def broken(voice: bool) -> None:
            raise RuntimeError("no plist")

        move._install_plists = broken  # type: ignore[method-assign]
        return move, host, home, root
    assert move.run() == 0
    said.clear()
    back, host2, _, _ = migration(tmp_path, mode="rollback", said=said)
    return back, host2, home, root


def go(move: ownuser.Migration, path: str) -> int:
    try:
        return move.run() if path == "restore" else move.rollback()
    except BaseException as exc:  # a signal or a failed step got through: she would stay stopped
        pytest.fail(f"the {path} was interrupted: {exc!r}")


@needs_plutil
@pytest.mark.parametrize("path", ["restore", "rollback"])
def test_a_failed_unhold_still_brings_her_services_back_and_prints_each_held_folder(
    tmp_path: Path, no_user: None, path: str
) -> None:
    import shlex

    said: list[str] = []
    move, host, home, root = restoring(tmp_path, path, said)

    def stuck(held: Sequence[tuple[str, str]]) -> None:
        raise OSError(16, "Resource busy")

    move._unhold = stuck  # type: ignore[method-assign]
    assert go(move, path) == 1
    for label in ownuser.LABELS:
        assert (root / f"Library/LaunchDaemons/{label}.plist").read_bytes() == OLD_PLIST  # put back
        assert (ownuser.LAUNCHCTL, "bootstrap", "system", f"/Library/LaunchDaemons/{label}.plist") in host.asked
    lines = [shlex.split(line) for line in said if line.strip().startswith("/usr/bin/sudo mv ")]
    held = root / "usr/local/estia/rollback/data"
    assert sorted(words[3] for words in lines) == sorted(str(home / old) for old in SECRET_FOLDERS)
    assert all(words[:2] == ["/usr/bin/sudo", "mv"] and Path(words[2]).parent == held for words in lines)
    assert all(Path(words[2]).is_dir() for words in lines)  # each really still held
    if path == "rollback":
        assert json.loads((root / "usr/local/estia/migration.json").read_text())["rolled_back"] is False


@needs_plutil
@pytest.mark.parametrize("path", ["restore", "rollback"])
def test_hang_ups_terms_and_ctrl_c_are_ignored_while_her_old_setup_comes_back(
    tmp_path: Path, no_user: None, path: str
) -> None:
    import signal

    said: list[str] = []
    move, _host, home, root = restoring(tmp_path, path, said)
    unhold = move._unhold
    finished: list[bool] = []

    def interrupted(held: Sequence[tuple[str, str]]) -> None:
        for number in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            os.kill(os.getpid(), number)
            time.sleep(0.05)
        unhold(held)
        finished.append(True)

    move._unhold = interrupted  # type: ignore[method-assign]
    assert go(move, path) == (1 if path == "restore" else 0)
    assert finished == [True]
    for old in SECRET_FOLDERS:
        assert (home / old).is_dir(), old
    assert (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").read_bytes() == OLD_PLIST
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler  # as it was


STALE = (
    ".local/share/signal-cli/data/111111.d/account.db-wal",
    ".local/share/signal-cli/data/111111.d/account.db-shm",
    "Library/Application Support/local-assistant/assistant.db-wal",
    "Library/Application Support/local-assistant/assistant.db-shm",
)


@needs_plutil
@pytest.mark.parametrize("path", ["restore", "rollback"])
def test_a_stale_wal_never_lands_next_to_her_newer_database(tmp_path: Path, no_user: None, path: str) -> None:
    """SQLite replays a -wal into whatever database sits next to it: an old one beside her newer copy corrupts it."""
    move, _host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    for name in STALE:  # left by an unclean stop before the move
        (home / name).write_text("old frames")
        (home / name).chmod(0o600)
    her = root / "var/estia"

    def newer_and_clean() -> None:  # her bridge ran and closed cleanly: newer databases, no -wal
        for name in STALE:
            (her / name).unlink(missing_ok=True)
        (her / ".local/share/signal-cli/data/111111.d/account.db").write_text("newer")
        (her / "Library/Application Support/local-assistant/assistant.db").write_text("newer")

    if path == "restore":
        start = move._start

        def started_then_failed(labels: list[str]) -> None:
            start(labels)
            newer_and_clean()
            raise RuntimeError("the bridge did not stay up")

        move._start = started_then_failed  # type: ignore[method-assign]
        assert move.run() == 1
    else:
        assert move.run() == 0
        newer_and_clean()
        assert migration(tmp_path, mode="rollback")[0].rollback() == 0
    assert (home / ".local/share/signal-cli/data/111111.d/account.db").read_text() == "newer"
    assert (home / "Library/Application Support/local-assistant/assistant.db").read_text() == "newer"
    for name in STALE:
        assert not (home / name).exists(), name


# ------------------------------------------------------------------ when calls work after a move or a deploy


@pytest.mark.parametrize(
    ("health", "line"),
    [
        ({"stt": "ready", "tts": "ready", "vad": "loading"}, "voice: ready"),
        ({"stt": "loading", "tts": "ready"}, ownuser.VOICE_LOADING),
        (None, ownuser.VOICE_LOADING),  # not answering yet
    ],
)
def test_the_voice_line_names_the_real_state_only_when_ready(health: dict[str, str] | None, line: str) -> None:
    assert ownuser.voice_line(health) == line
    assert ownuser.VOICE_LOADING == ("Calls work once her voice is ready (up to about 15 minutes); she texts you then.")


class VoiceClock:
    """A fake clock whose sleep moves it on, and a voice that turns ready at a given time (None: never)."""

    def __init__(self, ready_at: float | None) -> None:
        self.now = 0.0
        self.ready_at = ready_at
        self.reads: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def health(self) -> dict[str, str]:
        self.reads.append(self.now)
        state = "ready" if self.ready_at is not None and self.now >= self.ready_at else "loading"
        return {"stt": state, "tts": state}


def test_the_voice_end_waits_for_the_bridges_long_load_rule_before_it_promises_a_text() -> None:
    """The bridge texts only after a long load (a not-ready read 2 minutes after its start): the terminal says "she
    texts you then" only when the voice is still loading that long after the bridge was seen up."""
    from local_assistant import voice_ready

    assert ownuser.VOICE_WAIT_SECONDS >= voice_ready.LONG_LOAD_SECONDS + 2 * voice_ready.POLL_SECONDS
    said: list[str] = []
    soon = VoiceClock(ready_at=60.0)
    line = ownuser.voice_end(soon.health, calls=True, clock=soon, sleep=soon.sleep, say=said.append)
    assert line == "voice: ready" and soon.now < 70
    late = VoiceClock(ready_at=None)
    line = ownuser.voice_end(late.health, calls=True, clock=late, sleep=late.sleep, say=said.append)
    assert line == ownuser.VOICE_LOADING and late.reads[-1] >= ownuser.VOICE_WAIT_SECONDS
    assert said == [ownuser.VOICE_WAITING, ownuser.VOICE_WAITING]


def test_a_ctrl_c_during_the_voice_wait_ends_only_the_wait() -> None:
    """The move, the deploy and setup are done by then: an interruption ends the wait with a line, never a trace."""
    said: list[str] = []
    voice = VoiceClock(ready_at=None)

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    line = ownuser.voice_end(voice.health, calls=True, clock=voice, sleep=sleep, say=said.append)
    assert line == ownuser.VOICE_STOPPED and "http://127.0.0.1:8150/health" in line and "texts" not in line


def test_without_calls_the_voice_end_promises_no_text_and_does_not_wait() -> None:
    said: list[str] = []
    voice = VoiceClock(ready_at=None)
    line = ownuser.voice_end(voice.health, calls=False, clock=voice, sleep=voice.sleep, say=said.append)
    assert line == ownuser.VOICE_LOADING_NO_CALLS == "voice: loading (up to about 15 minutes)"
    assert "texts" not in line and voice.reads == [0.0] and said == []
    ready = VoiceClock(ready_at=0.0)
    assert ownuser.voice_end(ready.health, calls=False, clock=ready, sleep=ready.sleep, say=said.append) == (
        "voice: ready"
    )


def test_calls_are_on_only_with_a_calls_table_next_to_voice() -> None:
    assert ownuser.calls_in('[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\n')
    assert not ownuser.calls_in('[voice]\nurl = "http://127.0.0.1:8150"\n')
    assert not ownuser.calls_in("[calls]\n")  # [calls] needs [voice]: the bridge would not start
    assert not ownuser.calls_in("not = toml = at all")


def test_the_end_of_the_move_says_when_calls_work(
    tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 13 Oct: "voice: ?" (the health has no "state"): now the real state, or when calls work; the text is
    promised only with [calls] in her config and a voice still loading when the bridge counts a long load."""
    from local_assistant import deploy

    said: list[str] = []
    move, _host, _home, _root = migration(tmp_path, said=said)
    monkeypatch.setattr(deploy.Deploy, "health", lambda self, since, old_pid=None: None)
    voice = VoiceClock(ready_at=None)
    move.clock, move.sleep = voice, voice.sleep
    move.voice_health = voice.health
    move.calls = True
    move._check(0.0)
    assert said == []  # the health check alone: the voice is read once the move is done
    move._voice_end(True)
    assert said == [ownuser.VOICE_WAITING, ownuser.VOICE_LOADING]
    said.clear()
    move.voice_health = lambda: {"stt": "ready", "tts": "ready"}
    move._voice_end(True)
    assert said == ["voice: ready"]
    said.clear()
    move.calls = False
    move.voice_health = lambda: {"stt": "loading", "tts": "loading"}
    move._voice_end(True)
    assert said == [ownuser.VOICE_LOADING_NO_CALLS]
    said.clear()
    move._voice_end(False)  # no voice service: no line
    assert said == []


def test_a_ctrl_c_while_waiting_for_her_voice_leaves_the_finished_move_alone(tmp_path: Path, no_user: None) -> None:
    """The wait comes after the move is done: an interruption there never brings her old setup back."""
    said: list[str] = []
    move, _host, _home, _root = migration(tmp_path, said=said)
    move.calls = True

    def interrupted() -> dict[str, str]:
        raise KeyboardInterrupt

    move.voice_health = interrupted
    move._voice_end(True)  # no exception: the move stays done
    assert said == [ownuser.VOICE_STOPPED]


def test_the_voice_health_of_a_test_root_is_never_the_live_service(tmp_path: Path) -> None:
    move, _host, _home, _root = migration(tmp_path)
    assert move.voice_health() is None  # a fake root never asks 127.0.0.1:8150


# ------------------------------------------------------------------ the copies kept aside in her home (remove-old)


def kept_aside(her: Path, stamp: str) -> Path:
    """An earlier copy of hers that a retried move kept aside: a direct child of her home named .before-<stamp>."""
    folder = her / f".before-{stamp}"
    (folder / ".config/local-assistant").mkdir(parents=True)
    (folder / ".config/local-assistant/web.env").write_text("BRAVE_API_KEY=abc\n")
    return folder


@needs_plutil
def test_remove_old_deletes_her_copies_kept_aside_and_names_each(tmp_path: Path, no_user: None) -> None:
    """Live 13 Oct: four tries left copies in her home that needed hand-typed paths. Only real folders named
    .before-YYYYMMDD-HHMMSS right in her home: a link is never followed (nor deleted), nothing else is touched."""
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    her = root / "var/estia"
    first, second = kept_aside(her, "20261013-204501"), kept_aside(her, "20261013-210012")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "keep").mkdir(parents=True)
    link = her / ".before-20261013-220000"
    link.symlink_to(elsewhere)
    (her / ".before-20261013-230000").write_text("a file, not a folder")
    (her / ".before-notes").mkdir()
    (her / "Library/.before-20261013-204501").mkdir(parents=True)
    said: list[str] = []
    assert migration(tmp_path, said=said)[0].remove_old() == 0
    assert not first.exists() and not second.exists()
    assert link.is_symlink() and (elsewhere / "keep").is_dir()
    assert (her / ".before-20261013-230000").is_file() and (her / ".before-notes").is_dir()
    assert (her / "Library/.before-20261013-204501").is_dir()
    assert not (root / "usr/local/estia/rollback/data").exists()  # the held folders too, as before
    for name in (first.name, second.name):
        assert any(f"{HOME}/{name}" in line for line in said), said
    assert not any("220000" in line or "230000" in line for line in said)
    assert "abc" not in "\n".join(said)


@needs_plutil
def test_remove_old_again_deletes_copies_left_since(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path)[0].remove_old() == 0  # the held folders: gone, on record
    copy = kept_aside(root / "var/estia", "20261014-080000")
    said: list[str] = []
    assert migration(tmp_path, said=said)[0].remove_old() == 0
    assert not copy.exists() and any(copy.name in line for line in said)
    assert migration(tmp_path)[0].remove_old() == 1  # nothing left: said so


def test_after_a_fresh_install_remove_old_deletes_the_copies_kept_aside(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    copy = kept_aside(root / "var/estia", "20261013-204501")
    assert migration(tmp_path)[0].remove_old() == 0
    assert not copy.exists()


def test_a_dry_run_of_remove_old_names_the_copies_and_keeps_them(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    copy = kept_aside(root / "var/estia", "20261013-204501")
    said: list[str] = []
    dry, _, _, _ = migration(tmp_path, mode="dry", said=said)
    assert dry.remove_old() == 0
    assert copy.is_dir() and any(f"would delete {HOME}/{copy.name}" in line for line in said), said


@needs_plutil
def test_a_successful_move_lists_the_copies_kept_aside_and_the_command_that_deletes_them(
    tmp_path: Path, no_user: None
) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    assert move.run() == 0
    assert migration(tmp_path, mode="rollback")[0].rollback() == 0
    said: list[str] = []
    again, _, _, _ = migration(tmp_path, said=said)
    assert again.run() == 0
    kept = sorted(p.name for p in (root / "var/estia").iterdir() if p.name.startswith(".before-"))
    assert kept
    line = next((text for text in said if text.startswith("Earlier copies of hers kept aside")), "")
    assert "/usr/bin/sudo /usr/local/estia/bin/estia remove-old deletes them" in line
    assert all(f"{HOME}/{name}" in line for name in kept), said
    assert not any(" sudo /usr/local" in text or text.startswith("sudo ") for text in said)  # one form only


def test_a_move_without_copies_kept_aside_names_none(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    assert move.run() == 0
    assert not any(".before-" in line for line in said)


def test_without_a_move_on_record_remove_old_still_deletes_the_copies_kept_aside(tmp_path: Path) -> None:
    """Every try failed: no record, but a later try may have kept an earlier copy aside."""
    move, _, _, root = migration(tmp_path)
    copy = kept_aside(root / "var/estia", "20261013-204501")
    assert move.remove_old() == 0
    assert not copy.exists()
    assert migration(tmp_path)[0].remove_old() == 1


# ------------------------------------------------------------------ the releases of earlier tries (a successful move)


def earlier_releases(root: Path) -> list[Path]:
    """Releases an earlier try built that never went live: 2 MB each."""
    found = []
    for name in ("20261013T190000Z-aaaaaaaaaaaa", "20261013T193000Z-bbbbbbbbbbbb"):
        release = root / "usr/local/estia/releases" / name
        (release / "src").mkdir(parents=True)
        (release / "src/big.bin").write_bytes(b"x" * 2_000_000)
        found.append(release)
    return found


@needs_plutil
def test_a_successful_move_deletes_the_releases_she_does_not_run(tmp_path: Path, no_user: None) -> None:
    said: list[str] = []
    move, _, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    old = earlier_releases(root)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "keep").mkdir(parents=True)
    (root / "usr/local/estia/releases/link").symlink_to(elsewhere)
    assert move.run() == 0
    assert not any(release.exists() for release in old)
    assert (root / "usr/local/estia/current").resolve().is_dir()  # the one she runs stays
    assert (elsewhere / "keep").is_dir()  # a link there is never followed
    line = next((text for text in said if "release" in text and "freed" in text), "")
    assert "2 releases" in line and "4.0 MB freed" in line, said


def test_a_current_that_points_nowhere_deletes_no_release(tmp_path: Path, no_user: None) -> None:
    """A dangling `current` (or one pointing outside releases/) names no live release: nothing is deleted as root."""
    said: list[str] = []
    move, _, _home, root = migration(tmp_path, said=said)
    old = earlier_releases(root)
    current = root / "usr/local/estia/current"
    current.symlink_to("releases/20261013T200000Z-cccccccccccc")  # gone
    move._prune_releases()
    assert all(release.is_dir() for release in old) and not any("freed" in text for text in said)
    outside = tmp_path / "outside-release"
    outside.mkdir()
    current.unlink()
    current.symlink_to(outside)
    move._prune_releases()
    assert all(release.is_dir() for release in old)


def test_a_failed_move_keeps_every_release(tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch) -> None:
    move, _, home, root = migration(tmp_path)
    her_old_setup(home, root)
    old = earlier_releases(root)
    monkeypatch.setattr(move, "_install_plists", lambda voice: (_ for _ in ()).throw(RuntimeError("no plist")))
    assert move.run() == 1
    assert all(release.is_dir() for release in old)


def test_a_rehearsal_and_a_dry_run_keep_every_release(tmp_path: Path, no_user: None) -> None:
    move, _, home, root = migration(tmp_path, mode="rehearse")
    her_old_setup(home, root)
    old = earlier_releases(root)
    assert move.run() == 0
    assert all(release.is_dir() for release in old)
    said: list[str] = []
    dry = ownuser.Migration(
        ownuser.Host(root, dry_run=True, say=said.append),
        ownuser.Owner("alex", home, os.getuid()),
        checkout=tmp_path / "checkout",  # the rehearsal made it
        mode="dry",
        deploy=lambda: 0,
        say=said.append,
    )
    assert dry.run() == 0
    assert all(release.is_dir() for release in old)


def test_sizes_are_said_in_decimal_units() -> None:
    assert [ownuser.human_size(n) for n in (0, 999, 12_300, 4_000_000, 1_400_000_000)] == [
        "0 bytes",
        "999 bytes",
        "12 kB",
        "4.0 MB",
        "1.4 GB",
    ]


# ------------------------------------------------------------------ her config: the picture and the watched logs


def picture(path: Path, size: int = 2000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8))
    return path


def with_config(home: Path, extra: str) -> None:
    config = home / ".config/local-assistant/config.toml"
    config.write_text(extra + config.read_text())


@needs_plutil
def test_her_picture_in_your_home_is_copied_into_her_config_folder(tmp_path: Path, no_user: None) -> None:
    """Live 13 Oct: her avatar in your Pictures folder stopped working (your home is closed to her)."""
    said: list[str] = []
    move, host, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root)
    source = picture(home / "Pictures/her picture.png")
    with_config(home, 'avatar = "~/Pictures/her picture.png"\n')
    assert move.run() == 0
    copy = root / "var/estia/.config/local-assistant/avatar.png"
    assert copy.read_bytes() == source.read_bytes() and stat.S_IMODE(copy.stat().st_mode) == 0o600
    text = (root / "var/estia/.config/local-assistant/config.toml").read_text()
    assert f'avatar = "{HOME}/.config/local-assistant/avatar.png"' in text
    reads = [argv for argv in host.asked if "|" in argv and str(source) in argv]
    assert reads, "read as you, written as her: never root reading your path"
    assert any("avatar" in line and "copied" in line for line in said)
    assert not any("Pictures" in line for line in said)  # keys, never values


@pytest.mark.parametrize("kind", ["link", "too large", "outside your home", "missing", "dot-dot", "linked folder"])
def test_a_picture_that_cannot_be_copied_keeps_the_note(tmp_path: Path, no_user: None, kind: str) -> None:
    said: list[str] = []
    move, _, home, root = migration(tmp_path, said=said)
    her_old_setup(home, root, services=False)
    real = picture(tmp_path / "elsewhere/real.png")
    target = home / "Pictures/her.png"
    value = "~/Pictures/her.png"
    if kind == "link":
        target.parent.mkdir(parents=True)
        target.symlink_to(real)
    elif kind == "too large":
        picture(target, 5 * 1024 * 1024 + 1)
    elif kind == "outside your home":
        target = real
        value = str(target)
    elif kind == "dot-dot":  # in your home as text only
        value = "~/../elsewhere/real.png"
    elif kind == "linked folder":  # a folder on the way is a link out of your home
        (home / "Pictures").symlink_to(tmp_path / "elsewhere")
        value = "~/Pictures/real.png"
    with_config(home, f'avatar = "{value}"\n')
    assert move.run() == 0
    assert not list((root / "var/estia/.config/local-assistant").glob("avatar*"))
    text = (root / "var/estia/.config/local-assistant/config.toml").read_text()
    assert f'avatar = "{value}"' in text  # left as it was
    named = [line for line in said if "config: avatar" in line]
    if kind == "outside your home":
        assert named == []  # not in your home: nothing to say
    else:
        assert named and "home" in named[0] and "copied" not in named[0]


@needs_plutil
def test_the_picture_is_read_with_a_time_limit(tmp_path: Path, no_user: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A FIFO swapped in after the check would block `head` (as you) while she is stopped: the pipe has a limit."""
    move, host, home, root = migration(tmp_path)
    her_old_setup(home, root)
    picture(home / "Pictures/her.png")
    with_config(home, 'avatar = "~/Pictures/her.png"\n')
    limits: list[object] = []
    real = host.pipe

    def pipe(first: list[str], second: list[str], **kwargs: object) -> tuple[int, str]:
        if ownuser.HEAD in first:
            limits.append(kwargs.get("timeout"))
        return real(first, second, **kwargs)

    monkeypatch.setattr(host, "pipe", pipe)
    assert move.run() == 0
    assert limits == [ownuser.AVATAR_SECONDS] and ownuser.AVATAR_SECONDS <= 60


def test_watched_log_folders_in_your_home_are_left_out_of_her_copy_and_counted() -> None:
    home = Path("/Users/alex")
    text = (
        'bot_number = "+306900000000"\n'
        "[health]\n"
        'watch_logs = ["~/Library/Logs/x", "/var/log/estia", "/Users/alex/Logs/y"]  # watched\n'
        "[projects]\n"
        'docs = ["~/notes"]\n'
    )
    out, notes = ownuser.config_notes(text, home, None)
    import tomllib

    data = tomllib.loads(out)
    assert data["health"]["watch_logs"] == ["/var/log/estia"]
    assert "# watched" in out and 'docs = ["~/notes"]' in out  # only [health] watch_logs changes
    [note] = [note for note in notes if note.startswith("health.watch_logs")]
    assert "2 folders" in note and "left out" in note
    assert "~/" not in note and "/Users/alex" not in note and "/var/log" not in note  # keys, never values


def test_when_every_watched_log_folder_is_yours_her_copy_watches_none() -> None:
    out, notes = ownuser.config_notes('[health]\nwatch_logs = ["~/Library/Logs/x"]\n', Path("/Users/alex"), None)
    import tomllib

    assert tomllib.loads(out)["health"]["watch_logs"] == []
    assert any("1 folder " in note for note in notes)


def test_a_watched_log_list_over_several_lines_is_handled_too() -> None:
    text = '[health]\nwatch_logs = [\n  "~/Library/Logs/x",  # mine\n  "/var/log/estia",\n]\ninterval_hours = 4\n'
    out, notes = ownuser.config_notes(text, Path("/Users/alex"), None)
    import tomllib

    data = tomllib.loads(out)
    assert data["health"] == {"watch_logs": ["/var/log/estia"], "interval_hours": 4}
    assert any(note.startswith("health.watch_logs: 1 folder ") for note in notes)
