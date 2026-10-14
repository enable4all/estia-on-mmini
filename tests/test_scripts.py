"""The launchd templates and the shell scripts: no host-specific values, rendering works, every script parses."""

import json
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RENDER = ROOT / "scripts" / "render-launchd.sh"
BRIDGE = ROOT / "launchd" / "com.local-assistant.bridge.plist.in"
VOICE = ROOT / "launchd" / "com.local-assistant.voice.plist.in"
SHELL_SCRIPTS = sorted(
    [
        *(ROOT / "scripts").glob("*.sh"),
        ROOT / "scripts" / "estia",
        ROOT / "scripts" / "estia-deploy",
        *(ROOT / "tools" / "call-tunnel").glob("*.sh"),
    ]
)

needs_plutil = pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")


def render(template: Path, out: Path, **values: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin"}
    for key in ("RUN_USER", "RUN_GROUP", "RUN_HOME", "CODE"):
        if key.lower() in values:
            env[f"LA_{key}"] = values[key.lower()]
    return subprocess.run([str(RENDER), str(template), str(out)], env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("template", [BRIDGE, VOICE], ids=lambda p: p.name)
def test_templates_hold_placeholders_not_host_values(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    assert "/Users/" not in text and "/opt/homebrew" not in text and "/usr/local/estia" not in text
    assert "@RUN_HOME@" in text and "@CODE@" in text
    assert "<string>@RUN_USER@</string>" in text and "<string>@RUN_GROUP@</string>" in text
    assert "staff" not in text  # her own group: never the owner's group-readable files


def test_bridge_template_placeholders() -> None:
    text = BRIDGE.read_text(encoding="utf-8")
    assert "<string>@CODE@/.venv/bin/local-assistant</string>" in text
    assert "<string>@CODE@/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>" in text  # root-owned folders only
    assert "<string>@RUN_HOME@/Library/Logs/local-assistant/launchd.log</string>" in text


@needs_plutil
@pytest.mark.parametrize("template", [BRIDGE, VOICE], ids=lambda p: p.name)
def test_plist_templates_render_to_valid_plists(template: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.plist"
    result = render(template, out)
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert not re.search(r"@[A-Z_]+@", text)
    assert text.count("<string>_estia</string>") == 2  # UserName and GroupName
    assert "<string>/var/estia</string>" in text
    assert "/usr/local/estia/current/" in text and "/var/estia/Library/Logs/local-assistant/" in text
    assert subprocess.run(["plutil", "-lint", str(out)], capture_output=True, check=False).returncode == 0


@needs_plutil
def test_plist_values_are_xml_escaped(tmp_path: Path) -> None:
    out = tmp_path / "out.plist"
    result = render(BRIDGE, out, code="/opt/a&b|c <d>\\e")
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "/opt/a&amp;b|c &lt;d&gt;\\e/.venv/bin/local-assistant" in text
    assert subprocess.run(["plutil", "-lint", str(out)], capture_output=True, check=False).returncode == 0


def test_unknown_placeholder_is_an_error(tmp_path: Path) -> None:
    template = tmp_path / "x.conf.in"
    template.write_text("@RUN_HOME@ @NOPE@\n", encoding="utf-8")
    result = render(template, tmp_path / "x.conf")
    assert result.returncode != 0
    assert "@NOPE@" in result.stderr
    assert not (tmp_path / "x.conf").exists()


@needs_plutil
def test_no_output_left_when_the_plist_is_invalid(tmp_path: Path) -> None:
    template = tmp_path / "bad.plist.in"
    template.write_text("<plist><dict>@RUN_HOME@</plist>\n", encoding="utf-8")
    result = render(template, tmp_path / "bad.plist")
    assert result.returncode != 0
    assert not (tmp_path / "bad.plist").exists() and sorted(p.name for p in tmp_path.iterdir()) == ["bad.plist.in"]


def test_logs_rotate_in_process_so_there_is_no_newsyslog_rule() -> None:
    """The bridge and the voice service cap their own logs (merged with that change): no root rule over her folder."""
    assert not (ROOT / "launchd" / "local-assistant.newsyslog.conf.in").exists()
    for script in SHELL_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert "newsyslog" not in text or script.name == "migrate-to-own-user.sh", script.name


def test_the_old_installers_are_thin_entries_into_her_own_user() -> None:
    daemon = (ROOT / "scripts" / "install-daemon.sh").read_text(encoding="utf-8")
    assert "scripts/migrate-to-own-user.sh" in daemon and "/usr/local/estia/bin/estia-deploy" in daemon
    voice = (ROOT / "scripts" / "install-voice-daemon.sh").read_text(encoding="utf-8")
    assert "swift build -c release --force-resolved-versions" in voice
    assert '"$sudo" "$estia_deploy" --voice "$repo"' in voice and "/usr/local/estia/bin/estia-deploy --voice" in voice
    for text in (daemon, voice):
        body = text.split("\nset -euo pipefail\n", 1)[1]
        assert "/Library/LaunchDaemons/" not in body and "launchctl bootstrap" not in body  # nothing installed by hand


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_script_parses(script: Path) -> None:
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_script_holds_no_personal_paths(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "dev/vendor" not in text


def stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def setup_dry_run(tmp_path: Path, *, tools: bool, script: Path = ROOT / "scripts" / "setup.sh") -> tuple[str, Path]:
    """scripts/setup.sh --dry-run in a fresh clone (no .venv) on a pretend Apple-silicon Mac, with only stubs on
    PATH: with `tools`, brew, uv, ollama, signal-cli and curl are there but log and fail when called."""
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    shutil.copy2(script, clone / "scripts" / "setup.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "called.log"
    stub(bin_dir, "uname", 'case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; esac')
    dirname = shutil.which("dirname")
    assert dirname is not None
    (bin_dir / "dirname").symlink_to(dirname)
    if tools:
        for name in ("brew", "uv", "ollama", "signal-cli", "curl"):
            stub(bin_dir, name, f'echo "{name} $*" >> "{called}"; exit 1')
    result = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(clone / "scripts" / "setup.sh"), "--dry-run"],
        env={"PATH": str(bin_dir), "HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not called.exists(), called.read_text(encoding="utf-8")  # no tool was run
    assert [p.name for p in clone.iterdir()] == ["scripts"]  # no .venv, nothing else
    assert not (tmp_path / "home").exists()
    return result.stdout, clone


@pytest.mark.parametrize("tools", [False, True], ids=["fresh-mac", "tools-there"])
def test_setup_dry_run_installs_runs_and_creates_nothing(tmp_path: Path, tools: bool) -> None:
    """The first step SETUP-WITH-YOUR-AI.md gives: it only prints, even on a fresh clone."""
    out, _ = setup_dry_run(tmp_path, tools=tools)
    assert "$ uv python install 3.13" in out and "$ uv sync --frozen" in out and "Then: the model" in out
    assert ("Homebrew: would offer to install it" in out) is not tools
    assert ("$ brew install signal-cli ollama uv" in out) is not tools


def test_setup_runs_homebrew_without_its_analytics(tmp_path: Path) -> None:
    """Homebrew sends usage analytics unless told not to: every brew call setup makes carries HOMEBREW_NO_ANALYTICS=1
    (a real run on a pretend Mac whose tools are stubs: two missing, Ollama not answering yet)."""
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "setup.sh", clone / "scripts" / "setup.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "called.log"
    stub(bin_dir, "uname", 'case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; esac')
    dirname = shutil.which("dirname")
    assert dirname is not None
    (bin_dir / "dirname").symlink_to(dirname)
    stub(bin_dir, "brew", f'echo "${{HOMEBREW_NO_ANALYTICS:-unset}} brew $*" >> "{called}"')
    stub(bin_dir, "uv", f'echo "uv $*" >> "{called}"')
    stub(bin_dir, "curl", "exit 1")
    stub(bin_dir, "seq", "echo 1")
    stub(bin_dir, "sleep", "exit 0")
    result = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(clone / "scripts" / "setup.sh")],
        env={"PATH": str(bin_dir), "HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    brew = [line for line in called.read_text(encoding="utf-8").splitlines() if " brew " in f" {line}"]
    assert brew == ["1 brew install signal-cli ollama", "1 brew services start ollama"]


SET_CALENDAR = ROOT / "scripts" / "set-calendar-url.sh"
FAMILY_LINK = "https://calendar.google.com/calendar/ical/alex%40example.com/private-0123456789abcdef/basic.ics"
SCHOOL_LINK = "https://calendar.google.com/calendar/ical/school%40example.com/public/basic.ics"


def set_calendar(home: Path, link: str, *args: str) -> subprocess.CompletedProcess[str]:
    """scripts/set-calendar-url.sh with the link typed at its hidden prompt, under the macOS bash (3.2)."""
    return subprocess.run(
        ["/bin/bash", str(SET_CALENDAR), *args],
        input=f"  {link}  \n",
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "ESTIA_TEST_ROOT": str(home / "no-own-user")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_set_calendar_url_merges_one_key_and_keeps_the_others(tmp_path: Path) -> None:
    folder = tmp_path / ".config" / "local-assistant"
    env = folder / "calendar.env"
    began = int(time.time())
    first = set_calendar(tmp_path, FAMILY_LINK)
    assert first.returncode == 0, first.stderr
    lines = env.read_text().splitlines()
    assert lines[0] == f"CALENDAR_ICS_URL={FAMILY_LINK}" and lines[1].startswith("CALENDAR_SAVED_AT=")
    assert began <= int(lines[1].partition("=")[2]) <= time.time() and len(lines) == 2  # for doctor: when saved
    assert (env.stat().st_mode & 0o777, folder.stat().st_mode & 0o777) == (0o600, 0o700)
    env.write_text("CALENDAR_SAVED_AT=1\n" + env.read_text() + "# a note\nOTHER_KEY = kept\n")
    named = set_calendar(tmp_path, SCHOOL_LINK, "school-days")
    assert named.returncode == 0, named.stderr
    again = set_calendar(tmp_path, SCHOOL_LINK.replace("school%40", "school2%40"))  # the default calendar, replaced
    assert again.returncode == 0, again.stderr
    assert [line.partition("=")[0] if "SAVED_AT" in line else line for line in env.read_text().splitlines()] == [
        "# a note",
        "OTHER_KEY = kept",
        f"CALENDAR_ICS_URL_SCHOOL_DAYS={SCHOOL_LINK}",
        "CALENDAR_SAVED_AT_SCHOOL_DAYS",
        f"CALENDAR_ICS_URL={SCHOOL_LINK.replace('school%40', 'school2%40')}",
        "CALENDAR_SAVED_AT",
    ]
    assert env.stat().st_mode & 0o777 == 0o600 and sorted(p.name for p in folder.iterdir()) == ["calendar.env"]
    for result in (first, named, again):  # the link is never shown
        assert "calendar.google.com" not in result.stdout + result.stderr
    assert "CALENDAR_ICS_URL_SCHOOL_DAYS" in named.stdout


@pytest.mark.parametrize(
    ("args", "link", "code"),
    [
        (["Work"], FAMILY_LINK, 2),
        (["../work"], FAMILY_LINK, 2),
        (["work", "extra"], FAMILY_LINK, 2),
        (["work"], "https://calendar.example.com/calendar/ical/x/basic.ics", 1),
        ([], "https://calendar.google.com/calendar/ical/x/basic.html", 1),
    ],
)
def test_set_calendar_url_refuses_bad_names_and_links(tmp_path: Path, args: list[str], link: str, code: int) -> None:
    env = tmp_path / ".config" / "local-assistant" / "calendar.env"
    env.parent.mkdir(parents=True)
    env.write_text("CALENDAR_ICS_URL=old\n")
    result = set_calendar(tmp_path, link, *args)
    assert result.returncode == code
    assert env.read_text() == "CALENDAR_ICS_URL=old\n" and sorted(p.name for p in env.parent.iterdir()) == [
        "calendar.env"
    ]


@pytest.mark.parametrize(
    ("args", "code", "passed"),
    [
        (["info@example.com"], 0, "run --quiet local-assistant --mail-login info@example.com"),
        (
            ["info@example.com", "--mail-only"],
            0,
            "run --quiet local-assistant --mail-login info@example.com --mail-only",
        ),
        ([], 2, ""),
        (["info@example.com", "--other"], 2, ""),
    ],
)
def test_mail_login_passes_mail_only_on(tmp_path: Path, args: list[str], code: int, passed: str) -> None:
    stub(tmp_path, "uv", 'echo "$*"')
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts" / "mail-login.sh"), *args],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_TEST_ROOT": str(tmp_path / "none")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, result.stdout.strip()) == (code, passed)
    if code:
        assert "--mail-only" in result.stderr  # the usage names it


# ------------------------------------------------------------------ her own user: the wrappers and the owner's scripts

SYSTEM_TOOLS = ("sudo", "launchctl", "sysadminctl", "dscl", "socketfilterfw", "fdesetup", "sshd")


def root_sudo(root: Path, body: str) -> None:
    """The owner's scripts run sudo by its full path, /usr/bin/sudo: under a test's fake tree (ESTIA_TEST_ROOT) that
    is <root>/usr/bin/sudo, this stub."""
    (root / "usr/bin").mkdir(parents=True, exist_ok=True)
    stub(root / "usr/bin", "sudo", body)


def system_stubs(tmp_path: Path, *, sudo: str = "exit 99") -> tuple[Path, Path]:
    """A stub folder first on PATH: sudo (exits 99 unless told otherwise) and the system tools log their argv; sudo
    also in the fake tree tmp_path/root, where the owner's scripts find it."""
    folder = tmp_path / "stubs"
    folder.mkdir()
    log = tmp_path / "called.log"
    for name in SYSTEM_TOOLS:
        body = f'echo "{name} $*" >> "{log}"'
        stub(folder, name, f"{body}\n{sudo}" if name == "sudo" else body)
    root_sudo(tmp_path / "root", f'echo "sudo $*" >> "{log}"\n{sudo}')
    return folder, log


def snapshot(folder: Path, *skip: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (str(p), p.lstat().st_mode, p.lstat().st_size)
        for p in folder.rglob("*")
        if not any(p == s or s in p.parents for s in skip)
    )


def own_user(root: Path, user: str = "_estia") -> Path:
    """An own-user install under a test root: her bin/estia (a stub) and the bridge's service file, which says who she
    runs as (after a rollback: you again)."""
    estia = root / "usr/local/estia/bin/estia"
    estia.parent.mkdir(parents=True, exist_ok=True)
    estia.write_text("#!/bin/sh\nexit 0\n")
    estia.chmod(0o755)
    plist = root / "Library/LaunchDaemons/com.local-assistant.bridge.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps({"Label": "com.local-assistant.bridge", "UserName": user}))
    return estia


NO_TREE = "/var/empty/estia-test-root"  # can never exist (root's empty folder): no bin/estia, no sudo


def run_script(script: str, *args: str, env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Always under a fake tree: without ESTIA_TEST_ROOT the owner's scripts would read the live Mac (where she is
    moved) and run the real /usr/bin/sudo."""
    return subprocess.run(
        ["/bin/bash", str(ROOT / "scripts" / script), *args],
        input=stdin,
        env={"ESTIA_TEST_ROOT": NO_TREE} | env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["doctor"], ".venv/bin/python -I -m local_assistant.doctor"),
        (["logs", "voice", "-n", "20"], "/usr/bin/tail -n 20 /var/estia/Library/Logs/local-assistant/voice.log"),
        (["logs"], "/usr/bin/tail -n 50 /var/estia/Library/Logs/local-assistant/bridge.log"),
        (["check"], "/bin/bash"),
        (["mail-login", "info@example.com", "--mail-only"], "--mail-login info@example.com --mail-only"),
        (["spotify-login"], ".venv/bin/local-assistant --spotify-login"),
        (["secret", "set", "web.env", "BRAVE_API_KEY"], "-m local_assistant.secretfile set web.env BRAVE_API_KEY"),
        (["tapo-discover"], "scripts/tapo-discover.sh"),
    ],
)
def test_the_estia_wrapper_runs_each_command_as_her_user(tmp_path: Path, args: list[str], expected: str) -> None:
    stubs, log = system_stubs(tmp_path)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "ESTIA_ROOT": str(tmp_path / "root")}
    before = snapshot(tmp_path)
    done = run_script("../scripts/estia", "--dry-run", *args, env=env)
    assert done.returncode == 0, done.stderr
    line = done.stdout.strip()
    assert line.startswith(
        "would run: /usr/bin/sudo -u _estia -H -- /usr/bin/env -i HOME=/var/estia "
        "PATH=/usr/local/estia/current/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 "
    )  # no Homebrew: a folder you can change never comes before her programs
    assert expected in line
    assert not log.exists() and snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "args",
    [
        ["logs", "-n", "1;rm"],
        ["logs", "other"],
        ["mail-login"],
        ["doctor", "x"],
        ["nothing"],
        ["secret", "get", "a", "b"],
        ["signal", "send", "-m", "hi"],
        ["signal"],
        ["rollback", "now"],
        ["restart", "x"],
    ],
)
def test_the_estia_wrapper_refuses_anything_else(tmp_path: Path, args: list[str]) -> None:
    done = run_script("estia", "--dry-run", *args, env={"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path)})
    assert done.returncode == 2 and "would run" not in done.stdout


def test_a_secret_on_the_wrappers_command_line_is_refused(tmp_path: Path) -> None:
    done = run_script(
        "estia",
        "--dry-run",
        "secret",
        "set",
        "web.env",
        "BRAVE_API_KEY",
        "sk-visible-value",
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 2 and "never the command line" in done.stderr
    assert "sk-visible-value" not in done.stdout + done.stderr


def test_the_wrappers_need_root_without_a_dry_run(tmp_path: Path) -> None:
    for script in ("estia", "estia-deploy"):
        done = run_script(script, "doctor", env={"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path)})
        assert done.returncode == 2 and "/usr/bin/sudo /usr/local/estia/bin/" in done.stderr


def fake_release(root: Path, checkout: Path) -> None:
    """A current release, and the root-run tools: their Python is this test run's and their code this checkout's (the
    deploy module itself runs in its dry-run mode). The release's own Python is never run."""
    release = root / "usr/local/estia/releases/20261013T120000Z-abc"
    release.mkdir(parents=True)
    (release / "release.json").write_text(json.dumps({"source": str(checkout)}))
    (root / "usr/local/estia/current").symlink_to("releases/20261013T120000Z-abc")
    tools = root / "usr/local/estia/tools"
    tools.mkdir()
    (tools / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (tools / "python3").chmod(0o755)
    (tools / "src").symlink_to(ROOT / "src")


def test_estia_deploy_dry_run_changes_nothing(tmp_path: Path) -> None:
    stubs, log = system_stubs(tmp_path)
    root = tmp_path / "root"
    checkout = tmp_path / "checkout"
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "x.py").write_text("x")
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=A", "-c", "user.email=a@example.com", "commit", "-qm", "x"],
    ):
        subprocess.run(argv, cwd=checkout, check=True)
    fake_release(root, checkout)
    before = snapshot(tmp_path)
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "ESTIA_ROOT": str(root),
        "ESTIA_TEST_ROOT": str(root),
    }
    done = run_script("estia-deploy", "--dry-run", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "would run:" in done.stdout and "would switch" in done.stdout and "uv python install 3.13" in done.stdout
    assert not log.exists() and snapshot(tmp_path) == before


def fake_home(home: Path, *, projects: bool = False) -> None:
    config = 'bot_number = "+306900000000"\n' + ("[projects]\n" if projects else "")
    files = {
        ".config/local-assistant/config.toml": (config, 0o600),
        ".config/local-assistant/web.env": ("BRAVE_API_KEY=sk-made-up-0000\n", 0o600),
        ".local/share/signal-cli/data/111111.d/account.db": ("x", 0o644),
        ".local/share/signal-cli/data/accounts.json": ('{"accounts": [{"number": "+306900000000"}]}', 0o644),
    }
    for name, (text, mode) in files.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)


def test_the_migration_dry_run_changes_nothing_and_never_calls_sudo(tmp_path: Path) -> None:
    stubs, log = system_stubs(tmp_path)
    home, root = tmp_path / "home", tmp_path / "root"
    fake_home(home, projects=True)
    (home / "dev" / "alpha").mkdir(parents=True)
    before = snapshot(tmp_path)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(home), "ESTIA_ROOT": str(root), "ESTIA_TEST_ROOT": str(root)}
    done = run_script("migrate-to-own-user.sh", "--dry-run", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    # her user may already exist on the Mac running the tests (an earlier move): then only its settings are set again
    made = "would run: /usr/sbin/sysadminctl -addUser _estia" in done.stdout
    record = "/Users/" + "_estia"  # the directory-service record, not a home folder
    settings = f"would run: /usr/bin/dscl . -create {record} IsHidden 1" in done.stdout
    assert (made or settings) and "would copy" in done.stdout
    assert f'config: projects.root: set to "{home / "dev"}"' in done.stdout
    tree = "'user:_estia allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit'"
    assert f"would run: /bin/chmod -h +a {tree} {home / 'dev'} … and 1 more" in done.stdout  # as you, after the move
    assert f"would run: /bin/chmod +a 'user:_estia allow search' {home}" in done.stdout
    assert "sk-made-up-0000" not in done.stdout + done.stderr
    assert not log.exists() and snapshot(tmp_path) == before
    assert (
        subprocess.run(["/bin/ls", "-led", str(home / "dev")], capture_output=True, text=True).stdout.count("\n") == 1
    )


def test_the_migration_dry_run_without_projects_shares_nothing(tmp_path: Path) -> None:
    """Projects are an extra: without [projects] the move names no folder of yours and nothing is shared."""
    stubs, log = system_stubs(tmp_path)
    home, root = tmp_path / "home", tmp_path / "root"
    fake_home(home)
    (home / "dev" / "alpha").mkdir(parents=True)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(home), "ESTIA_ROOT": str(root), "ESTIA_TEST_ROOT": str(root)}
    done = run_script("migrate-to-own-user.sh", "--dry-run", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "config: projects: off (no [projects] in your config)" in done.stdout
    assert "+a" not in done.stdout  # no ACL entry for her, and no folder suggested
    assert "Projects are off (no [projects] in the config): nothing to share." in done.stdout
    assert not log.exists()


def test_the_migration_refuses_root_and_unknown_flags(tmp_path: Path) -> None:
    done = run_script("migrate-to-own-user.sh", "--now", env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert done.returncode == 2 and "usage" in done.stderr


def test_the_real_migration_goes_through_sudo(tmp_path: Path) -> None:
    stubs, log = system_stubs(tmp_path, sudo="exit 0")
    marker = tmp_path / "root/usr/local/estia/migration.json"  # what the move (as root) records
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"projects_root": None, "rolled_back": False}))
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_TEST_ROOT": str(tmp_path / "root")}
    done = run_script("migrate-to-own-user.sh", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert log.read_text().startswith(
        f"sudo {ROOT}/.venv/bin/python -I -m local_assistant.ownuser migrate --checkout {ROOT}"
    )
    assert "Projects are off (no [projects] in the config): nothing to share." in done.stdout  # the move recorded none
    rollback = run_script("migrate-to-own-user.sh", "--rollback", env=env)  # no move: nothing to go back from
    assert rollback.returncode == 1 and "no move" in rollback.stderr
    assert len(log.read_text().splitlines()) == 1


def owner_stubs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """sudo records its argv and what came on stdin, then succeeds; security answers each keychain service."""
    folder = tmp_path / "stubs"
    folder.mkdir()
    log, given = tmp_path / "sudo.log", tmp_path / "stdin.log"
    stub(folder, "sudo", f'echo "sudo $*" >> "{log}"; cat >> "{given}"; exit 0')
    root_sudo(tmp_path / "root", f'echo "sudo $*" >> "{log}"; cat >> "{given}"; exit 0')
    stub(
        folder,
        "security",
        'case "$*" in *"-s brave-search"*) echo sk-brave-made-up ;; *"-s ollama"*) echo ollama-made-up ;; *) exit 44 ;; esac',
    )
    return folder, log, given


def owner_env(tmp_path: Path, stubs: Path) -> dict[str, str]:
    """ESTIA_TEST_ROOT, never ESTIA_ROOT: an ESTIA_ROOT left from a rehearsal must not change what sudo runs."""
    return {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "ESTIA_TEST_ROOT": str(tmp_path / "root")}


def test_web_keys_go_through_a_pipe_into_her_secret_helper(tmp_path: Path) -> None:
    stubs, log, given = owner_stubs(tmp_path)
    estia = own_user(tmp_path / "root")
    done = run_script("set-web-keys.sh", env=owner_env(tmp_path, stubs))
    assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == [
        f"sudo {estia} secret set web.env BRAVE_API_KEY",
        f"sudo {estia} secret set web.env OLLAMA_API_KEY",
    ]
    assert given.read_text().splitlines() == ["sk-brave-made-up", "ollama-made-up"]
    assert "made-up" not in done.stdout + done.stderr and "Brave key 16" in done.stdout
    assert not (tmp_path / "home/.config").exists()  # nothing written in your home


def test_a_calendar_address_goes_through_a_pipe(tmp_path: Path) -> None:
    stubs, log, given = owner_stubs(tmp_path)
    estia = own_user(tmp_path / "root")
    done = run_script("set-calendar-url.sh", "school-days", env=owner_env(tmp_path, stubs), stdin=f"{FAMILY_LINK}\n")
    assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == [f"sudo {estia} secret set calendar.env CALENDAR_ICS_URL_SCHOOL_DAYS"]
    assert given.read_text() == f"{FAMILY_LINK}\n" and "calendar.google.com" not in done.stdout + done.stderr


def test_the_tp_link_login_goes_through_pipes(tmp_path: Path) -> None:
    stubs, log, given = owner_stubs(tmp_path)
    estia = own_user(tmp_path / "root")
    done = run_script(
        "set-tapo-credentials.sh", env=owner_env(tmp_path, stubs), stdin="alex@example.com\nhunter2-made-up\n"
    )
    assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == [
        f"sudo {estia} secret set tapo.env TAPO_USERNAME",
        f"sudo {estia} secret set tapo.env TAPO_PASSWORD",
    ]
    assert given.read_text() == "alex@example.com\nhunter2-made-up\n" and "hunter2" not in done.stdout + done.stderr


@pytest.mark.parametrize(
    ("script", "args", "passed"),
    [
        ("mail-login.sh", ["info@example.com", "--mail-only"], "mail-login info@example.com --mail-only"),
        ("spotify-login.sh", [], "spotify-login"),
        ("tapo-discover.sh", [], "tapo-discover"),
    ],
)
def test_sign_ins_run_as_her_through_the_wrapper(tmp_path: Path, script: str, args: list[str], passed: str) -> None:
    stubs, log, _ = owner_stubs(tmp_path)
    estia = own_user(tmp_path / "root")
    done = run_script(script, *args, env=owner_env(tmp_path, stubs))
    assert done.returncode == 0, done.stderr
    assert log.read_text().strip() == f"sudo {estia} {passed}"


@pytest.mark.parametrize(
    ("script", "says"),
    [
        ("doctor.sh", "/usr/bin/sudo /usr/local/estia/bin/estia doctor"),
        ("check.sh", "/usr/bin/sudo /usr/local/estia/bin/estia check"),
        ("run.sh", "sudo launchctl kickstart -k system/com.local-assistant.bridge"),
    ],
)
def test_the_owners_checks_point_to_her_wrapper(tmp_path: Path, script: str, says: str) -> None:
    stubs, log, _ = owner_stubs(tmp_path)
    own_user(tmp_path / "root")
    done = run_script(script, env=owner_env(tmp_path, stubs))
    assert done.returncode == 1 and says in done.stdout + done.stderr and "_estia" in done.stdout + done.stderr
    assert not log.exists()  # it says what to run; it never runs sudo itself


@pytest.mark.parametrize("nuclear", [False, True], ids=["default", "nuclear"])
def test_the_harden_dry_run_changes_nothing_and_never_calls_sudo(tmp_path: Path, nuclear: bool) -> None:
    stubs, log = system_stubs(tmp_path)
    home, root = tmp_path / "home", tmp_path / "root"
    fake_home(home)
    before = snapshot(tmp_path)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(home), "ESTIA_ROOT": str(root), "ESTIA_TEST_ROOT": str(root)}
    done = run_script("harden.sh", "--dry-run", *(["--nuclear"] if nuclear else []), env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Linked devices" in done.stdout and "would ask" in done.stdout
    assert ("other services on this Mac:" in done.stdout.splitlines()[0]) is nuclear  # said first
    assert (" Firewall: " in done.stdout) is nuclear
    assert ("○ Nuclear option: " in done.stdout) is not nuclear  # without --nuclear: one neutral line
    assert "would run: /usr/bin/sudo -n /bin/chmod -R go-rwx" not in done.stdout  # not her own user: no sudo for it
    assert not log.exists() and snapshot(tmp_path) == before


def test_an_estia_root_left_from_a_rehearsal_never_reaches_sudo(tmp_path: Path) -> None:
    """A rehearsal's tree (ESTIA_ROOT) holds a stub bin/estia: the owner's scripts never pipe a key into it."""
    stubs, log, _ = owner_stubs(tmp_path)
    own_user(tmp_path / "rehearsal")
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "ESTIA_ROOT": str(tmp_path / "rehearsal")}
    done = run_script("set-web-keys.sh", env=env)
    assert done.returncode == 0, done.stderr
    assert not log.exists() or "rehearsal" not in log.read_text()


@pytest.mark.parametrize("script", ["set-web-keys.sh", "doctor.sh", "run.sh"])
def test_after_a_rollback_the_scripts_take_your_path_again(tmp_path: Path, script: str) -> None:
    """bin/estia stays after a rollback; the bridge's service file says who she runs as: you again."""
    stubs, log, _ = owner_stubs(tmp_path)
    own_user(tmp_path / "root", user="alex")
    done = run_script(script, "--help" if script == "doctor.sh" else "--dry-run", env=owner_env(tmp_path, stubs))
    assert "/usr/local/estia/bin/estia" not in done.stdout + done.stderr
    assert not log.exists() or "/usr/local/estia/bin/estia" not in log.read_text()


@pytest.mark.parametrize(
    ("args", "lines"),
    [
        (["restart"], ["would run: /bin/launchctl kickstart -k system/com.local-assistant.bridge"]),
        (
            ["signal", "listIdentities"],
            [
                "would run: /bin/launchctl bootout system/com.local-assistant.bridge",
                "-m local_assistant.admin signal listIdentities",
                "would run: /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.bridge.plist",
            ],
        ),
        (["unlock"], ["would run: /bin/launchctl bootout", "-m local_assistant.admin unlock", "bootstrap system"]),
        (["rollback"], ["tools/python3 -I -c"]),
        (["remove-old"], ["tools/python3 -I -c"]),
    ],
)
def test_the_wrappers_maintenance_commands(tmp_path: Path, args: list[str], lines: list[str]) -> None:
    stubs, log = system_stubs(tmp_path)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "ESTIA_ROOT": str(tmp_path / "root")}
    done = run_script("estia", "--dry-run", *args, env=env)
    assert done.returncode == 0, done.stderr
    out = done.stdout.splitlines()
    assert len(out) == len(lines) and all(want in line for want, line in zip(lines, out, strict=True))
    if args[0] in ("rollback", "remove-old"):
        assert done.stdout.rstrip().endswith(f"local_assistant.ownuser migrate --{args[0]}")
        assert "/usr/local/estia/current" not in done.stdout  # root runs the tools, never a release
    assert not log.exists()


def test_rollback_and_remove_old_after_the_move_run_from_the_root_owned_tools(tmp_path: Path) -> None:
    stubs, log, _ = owner_stubs(tmp_path)
    estia = own_user(tmp_path / "root")
    for flag in ("--rollback", "--remove-old"):
        done = run_script("migrate-to-own-user.sh", flag, env=owner_env(tmp_path, stubs))
        assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == [f"sudo {estia} rollback", f"sudo {estia} remove-old"]
    assert ".venv/bin/python" not in log.read_text()  # never your checkout's Python as root


def test_after_the_move_the_migration_stops_before_any_sudo(tmp_path: Path) -> None:
    """Run again after the move, it would run your checkout's Python as root: it stops first and names the deploy."""
    stubs, log, _ = owner_stubs(tmp_path)
    own_user(tmp_path / "root")
    done = run_script("migrate-to-own-user.sh", env=owner_env(tmp_path, stubs))
    assert done.returncode == 0, done.stderr
    assert "already in her own user" in done.stdout
    assert "/usr/bin/sudo /usr/local/estia/bin/estia-deploy" in done.stdout
    assert not log.exists()  # sudo never called
    header = (ROOT / "scripts" / "migrate-to-own-user.sh").read_text(encoding="utf-8").split("set -euo", 1)[0]
    assert "Safe to run again" not in header and "/usr/bin/sudo /usr/local/estia/bin/estia-deploy" in header


def test_the_wrapper_sets_her_account_again_from_the_root_owned_tools(tmp_path: Path) -> None:
    stubs, log = system_stubs(tmp_path)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "ESTIA_ROOT": str(tmp_path / "root")}
    done = run_script("estia", "--dry-run", "account", env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("would run: ") and "tools/python3 -I -c" in done.stdout
    assert done.stdout.rstrip().endswith("local_assistant.ownuser account")
    assert "/usr/local/estia/current" not in done.stdout  # root runs the tools, never a release
    assert run_script("estia", "--dry-run", "account", "now", env=env).returncode == 2
    assert not log.exists()


# her read-only access to your projects folder: set as you, no sudo


def projects_on(home: Path) -> None:
    """Your config with [projects] (the extra on, its folder ~/dev)."""
    config = home / ".config/local-assistant/config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('bot_number = "+306900000000"\n[projects]\n')


def test_share_projects_dry_run_prints_the_commands_and_changes_nothing(tmp_path: Path) -> None:
    stubs, log, _ = owner_stubs(tmp_path)
    home = tmp_path / "home"
    projects_on(home)
    (home / "dev" / "alpha").mkdir(parents=True)
    (home / "dev" / "alpha" / "README.md").write_text("# alpha\n")
    (home / "dev" / "elsewhere").symlink_to(tmp_path)
    before = snapshot(tmp_path)
    done = run_script("share-projects.sh", "--dry-run", env=owner_env(tmp_path, stubs))
    assert done.returncode == 0, done.stdout + done.stderr
    tree = "'user:_estia allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit'"
    assert done.stdout.splitlines()[:2] == [
        f"would run: /bin/chmod -h +a {tree} {home / 'dev'} … and 2 more (in {home / 'dev'}; no link)",
        f"would run: /bin/chmod +a 'user:_estia allow search' {home}",
    ]
    assert not log.exists() and snapshot(tmp_path) == before
    revoke = run_script("share-projects.sh", "--dry-run", "--revoke", env=owner_env(tmp_path, stubs))
    assert revoke.returncode == 0 and "take back" in revoke.stdout


def test_share_projects_refuses_your_home_and_a_folder_outside_it(tmp_path: Path) -> None:
    stubs, log, _ = owner_stubs(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    for folder, why in ((home, "your home itself"), (tmp_path, "outside your home")):
        done = run_script("share-projects.sh", "--dry-run", str(folder), env=owner_env(tmp_path, stubs))
        assert done.returncode == 1 and why in done.stdout, done.stdout
    assert not log.exists()


def test_share_projects_without_her_user_changes_nothing(tmp_path: Path) -> None:
    import pwd

    try:
        pwd.getpwnam("_estia")
        pytest.skip("her user exists on this Mac: the share would set real entries")
    except KeyError:
        pass
    stubs, _log, _ = owner_stubs(tmp_path)
    projects_on(tmp_path / "home")
    (tmp_path / "home" / "dev").mkdir(parents=True)
    done = run_script("share-projects.sh", env=owner_env(tmp_path, stubs))
    assert done.returncode == 1 and "does not exist yet" in done.stdout
