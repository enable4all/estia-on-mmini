"""The launchd templates and the shell scripts: no host-specific values, rendering works, every script parses."""

import json
import os
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
    assert "<string>@LOG_DIR@/launchd.log</string>" in text  # a root-owned folder: never one of hers


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
    assert "/usr/local/estia/current/" in text and "<string>/var/log/estia/" in text
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
    # estia-deploy builds her voice service from the commit's own sources: a build in the checkout would never be used
    assert "swift build" not in voice.split("\nset -euo pipefail\n", 1)[1]
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


REAL_TOOLS = ("dirname", "grep", "mktemp", "rm", "chmod", "cat", "seq", "touch", "mkdir", "cp")


def fake_mac(tmp_path: Path, *, git: bool = True) -> tuple[Path, Path, Path, dict[str, str]]:
    """A fresh clone of scripts/setup.sh on a pretend Apple-silicon Mac: the clone (a git clone unless `git` is
    false), the stub folder (the only folder on PATH, with a few real tools linked), the call log and the
    environment. ESTIA_TEST_ROOT is a fake tree: the Mac's own /opt/homebrew is never found or run."""
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "setup.sh", clone / "scripts" / "setup.sh")
    if git:
        (clone / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "uname", 'case "$1" in -s) echo Darwin ;; -m) echo arm64 ;; esac')
    stub(bin_dir, "sleep", "exit 0")
    stub(bin_dir, "open", f'echo "open $*" >> "{tmp_path / "called.log"}"')
    for name in REAL_TOOLS:
        real = shutil.which(name, path="/usr/bin:/bin")
        assert real is not None, name
        (bin_dir / name).symlink_to(real)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path / "home"), "ESTIA_TEST_ROOT": str(tmp_path / "root")}
    return clone, bin_dir, tmp_path / "called.log", env


def in_a_terminal(argv: list[str], env: dict[str, str], typed: str = "") -> subprocess.CompletedProcess[str]:
    """Run with a terminal on stdin (a pty, `typed` already in it), as in the person's own Terminal window."""
    master, slave = os.openpty()
    try:
        if typed:
            os.write(master, typed.encode())
        return subprocess.run(argv, stdin=slave, env=env, capture_output=True, text=True, check=False, timeout=60)
    finally:
        os.close(master)
        os.close(slave)


def setup_sh(clone: Path) -> list[str]:
    return [shutil.which("bash") or "/bin/bash", str(clone / "scripts" / "setup.sh")]


def setup_dry_run(tmp_path: Path, *, tools: bool) -> tuple[str, Path]:
    """scripts/setup.sh --dry-run in a fresh clone (no .venv) on a pretend Apple-silicon Mac, with only stubs on
    PATH: with `tools`, brew, uv, ollama, signal-cli and curl are there but log and fail when called."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    if tools:
        for name in ("brew", "uv", "ollama", "signal-cli", "curl"):
            stub(bin_dir, name, f'echo "{name} $*" >> "{called}"; exit 1')
    result = subprocess.run(
        [*setup_sh(clone), "--dry-run"], env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not called.exists(), called.read_text(encoding="utf-8")  # no tool was run
    assert sorted(p.name for p in clone.iterdir()) == [".git", "scripts"]  # no .venv, nothing else
    assert not (tmp_path / "home").exists()
    return result.stdout, clone


@pytest.mark.parametrize("tools", [False, True], ids=["fresh-mac", "tools-there"])
def test_setup_dry_run_installs_runs_and_creates_nothing(tmp_path: Path, tools: bool) -> None:
    """The first step SETUP-WITH-YOUR-AI.md gives: it only prints, even on a fresh clone, and needs no terminal."""
    out, _ = setup_dry_run(tmp_path, tools=tools)
    assert "$ uv python install 3.13" in out and "$ uv sync --frozen" in out and "Then: Ollama's official build" in out
    assert "the model for this Mac's memory" in out
    assert ("Homebrew: would offer to install it" in out) is not tools
    assert ("$ brew install signal-cli uv" in out) is not tools
    assert "brew install signal-cli ollama" not in out and "brew services" not in out  # never Homebrew's Ollama
    assert "Ollama's official build" in out


def tools_stubs(bin_dir: Path, called: Path, *, signal_version: str = "0.14.9", ollama_formula: bool = True) -> None:
    """brew logs every call: `install` puts the tools it names on PATH, `services start` brings Ollama up, `list
    --formula ollama` says whether Homebrew installed it; curl answers once Ollama is up; uv logs."""
    up = bin_dir.parent / "ollama-up"
    tool = {
        "signal-cli": f'printf "#!/bin/sh\\necho signal-cli {signal_version}\\n" > "{bin_dir}/signal-cli"',
        "ollama": f'printf "#!/bin/sh\\nexit 0\\n" > "{bin_dir}/ollama"',
        "uv": f'printf "#!/bin/sh\\necho \\"uv \\$*\\" >> {called}\\n" > "{bin_dir}/uv"',
    }
    installs = "\n".join(f'    {name}) {make}; chmod +x "{bin_dir}/{name}" ;;' for name, make in tool.items())
    stub(
        bin_dir,
        "brew",
        f'echo "${{HOMEBREW_NO_ANALYTICS:-unset}} brew $*" >> "{called}"\n'
        'case "$1" in\n'
        f'  install) shift; for t in "$@"; do case "$t" in\n{installs}\n  esac; done ;;\n'
        f'  services) touch "{up}" ;;\n'
        f"  list) exit {0 if ollama_formula else 1} ;;\n"
        "esac",
    )
    stub(bin_dir, "curl", f'[ -e "{up}" ]')


def test_setup_runs_homebrew_without_its_analytics(tmp_path: Path) -> None:
    """Homebrew sends usage analytics unless told not to: every brew call setup makes carries HOMEBREW_NO_ANALYTICS=1
    (a real run on a pretend Mac whose tools are stubs: two missing, Ollama not answering yet)."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    tools_stubs(bin_dir, called)
    stub(bin_dir, "uv", f'echo "uv $*" >> "{called}"')
    result = in_a_terminal(setup_sh(clone), env)
    assert result.returncode == 0, result.stdout + result.stderr
    brew = [line for line in called.read_text(encoding="utf-8").splitlines() if " brew " in f" {line}"]
    assert brew == ["1 brew install signal-cli"]  # uv is there; Ollama is never Homebrew's (setup's Ollama step)


def test_setup_without_a_terminal_stops_before_anything(tmp_path: Path) -> None:
    """An AI's command tool has no terminal: setup asks questions and Signal's one-time codes, so it says where to run
    it and runs nothing (finding 19, 38)."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    tools_stubs(bin_dir, called)
    result = subprocess.run(setup_sh(clone), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 1
    assert "run scripts/setup.sh in your own Terminal window" in result.stderr
    assert not called.exists()


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_setup_refuses_a_folder_that_is_not_a_git_clone(tmp_path: Path, dry: bool) -> None:
    """Every update is built from git: a folder from a ZIP download can never be installed, so setup says so first."""
    clone, bin_dir, called, env = fake_mac(tmp_path, git=False)
    tools_stubs(bin_dir, called)
    result = in_a_terminal([*setup_sh(clone), *(["--dry-run"] if dry else [])], env)
    assert result.returncode == 1
    assert "git clone https://github.com/enable4all/estia-one-mmini.git ~/estia" in result.stderr
    assert not called.exists()


def homebrew_in(root: Path, called: Path) -> Path:
    """Homebrew installed in the fake tree's /opt/homebrew, which is not on PATH (a new Terminal window without its
    line in ~/.zprofile); its brew logs and puts its folder on PATH for `shellenv`."""
    folder = root / "opt/homebrew/bin"
    folder.mkdir(parents=True)
    stub(
        folder,
        "brew",
        f'echo "${{HOMEBREW_NO_ANALYTICS:-unset}} brew $*" >> "{called}"\n'
        f'[ "$1" = shellenv ] && echo \'export PATH="{folder}:$PATH"\'\nexit 0',
    )
    for name in ("ollama", "uv"):
        stub(folder, name, f'echo "{name} $*" >> "{called}"')
    stub(folder, "signal-cli", "echo signal-cli 0.14.9")
    return folder


def test_homebrew_off_path_is_found_and_added_to_new_terminal_windows(tmp_path: Path) -> None:
    """Homebrew installed but not on PATH: setup finds it (never offers to install it again) and adds its line to
    ~/.zprofile once, so the next Terminal window finds brew, uv and ollama (finding 5, 52)."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    homebrew_in(tmp_path / "root", called)
    stub(bin_dir, "curl", "exit 0")
    (tmp_path / "home").mkdir()
    said = []
    for _ in range(2):
        result = in_a_terminal(setup_sh(clone), env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Install Homebrew now?" not in result.stdout + result.stderr
        said.append(result.stdout)
    profile = (tmp_path / "home" / ".zprofile").read_text(encoding="utf-8")
    assert profile.count('eval "$(/opt/homebrew/bin/brew shellenv)"') == 1
    assert "1 brew shellenv" in called.read_text(encoding="utf-8")
    assert "added to new Terminal windows" in said[0] and "added to new Terminal windows" not in said[1]


def test_a_failed_homebrew_download_names_the_network_and_runs_nothing(tmp_path: Path) -> None:
    """curl fails (offline): setup says so and stops, instead of running an empty installer (finding 6, 62)."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    stub(bin_dir, "curl", f'echo "curl $*" >> "{called}"; exit 7')
    result = in_a_terminal(setup_sh(clone), env, typed="y\n")
    assert result.returncode == 1
    assert "Could not download Homebrew's installer: check the internet connection" in result.stderr
    assert "brew: command not found" not in result.stderr


def test_the_homebrew_question_says_why_and_what_is_recommended(tmp_path: Path) -> None:
    """What Homebrew is for, what a no does, that nothing shows at the password, and "Recommendation: yes" before the
    hint; Enter keeps the no (not a safety question: a no only stops setup)."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    stub(bin_dir, "curl", f'echo "curl $*" >> "{called}"; exit 7')
    result = in_a_terminal(setup_sh(clone), env, typed="\n")
    out = result.stdout + result.stderr
    assert result.returncode == 1 and "curl" not in (called.read_text() if called.exists() else "")
    assert "signal-cli (for Signal) and uv (for Python)" in out and "nothing shows while you type" in out
    assert "No stops setup." in out and "Install Homebrew now? Recommendation: yes. [y/N] " in out
    assert all(len(line) <= 135 for line in out.splitlines() if "Homebrew" in line)


def test_the_homebrew_installer_is_shown_as_one_line_never_its_text(tmp_path: Path) -> None:
    """The installer is downloaded first, then run: the screen shows the one command, not its thousand lines
    (finding 6, 45, 62)."""
    clone, bin_dir, _called, env = fake_mac(tmp_path)
    folder = tmp_path / "root/opt/homebrew/bin"
    installer = (
        "# INSTALLER-BODY: Homebrew's own script, about a thousand lines\n"
        f'mkdir -p "{folder}"\n'
        f'printf \'#!/bin/sh\\n[ "$1" = shellenv ] && echo export PATH="{folder}:$PATH"\\nexit 0\\n\' > "{folder}/brew"\n'
        f'chmod +x "{folder}/brew"\n'
    )
    script = tmp_path / "install.sh"
    script.write_text(installer, encoding="utf-8")
    stub(bin_dir, "curl", f'for a; do [ "$prev" = -o ] && cp "{script}" "$a"; prev="$a"; done')
    result = in_a_terminal(setup_sh(clone), env, typed="y\n")
    out = result.stdout + result.stderr
    assert '$ /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' in out
    assert "INSTALLER-BODY" not in out
    assert (folder / "brew").exists()


def test_setup_sh_never_installs_or_starts_ollama_itself(tmp_path: Path) -> None:
    """Owner, 15 Oct: only Ollama's official build. setup.sh neither installs Homebrew's nor starts the app or a brew
    service, even with Ollama not answering: setup's Python step keeps the official one or installs it, checked."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    tools_stubs(bin_dir, called, ollama_formula=False)
    (tmp_path / "root/Applications/Ollama.app").mkdir(parents=True)
    stub(bin_dir, "curl", f'echo "curl $*" >> "{called}"; exit 7')  # nothing answers anywhere
    result = in_a_terminal(setup_sh(clone), env)
    assert result.returncode == 0, result.stdout + result.stderr
    log = called.read_text(encoding="utf-8")
    assert "ollama" not in log.replace("local_assistant", "") and "open " not in log and "curl" not in log
    assert "uv run --quiet python -m local_assistant.setup" in log
    script = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "brew services" not in script and "open -a Ollama" not in script


def test_a_signal_cli_of_another_version_is_refused(tmp_path: Path) -> None:
    """Registration uses signal-cli 0.14's requests: another version is named before it fails obscurely (finding 20)."""
    clone, bin_dir, called, env = fake_mac(tmp_path)
    tools_stubs(bin_dir, called)
    stub(bin_dir, "signal-cli", "echo signal-cli 0.13.24")
    stub(bin_dir, "ollama", "exit 0")
    stub(bin_dir, "uv", "exit 0")
    result = in_a_terminal(setup_sh(clone), env)
    assert result.returncode == 1
    assert "signal-cli 0.14" in result.stderr and "0.13.24" in result.stderr


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


SET_PASSWORD = ROOT / "scripts" / "set-calendar-password.sh"
APP_PASSWORD = "abcd-efgh-ijkl-mnop"  # privacy-ok: made up, an app-specific password's shape


def set_password(home: Path, password: str, *args: str) -> subprocess.CompletedProcess[str]:
    """scripts/set-calendar-password.sh with the password typed at its hidden prompt, under the macOS bash (3.2)."""
    return subprocess.run(
        ["/bin/bash", str(SET_PASSWORD), *args],
        input=f"  {password}  \n",
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "ESTIA_TEST_ROOT": str(home / "no-own-user")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_set_calendar_password_merges_one_key_and_keeps_the_others(tmp_path: Path) -> None:
    folder = tmp_path / ".config" / "local-assistant"
    env = folder / "calendar.env"
    folder.mkdir(parents=True)
    env.write_text(f"CALENDAR_ICS_URL={FAMILY_LINK}\nCALENDAR_SAVED_AT=1\nCALDAV_PASSWORD_FAMILY=old\n")
    began = int(time.time())
    named = set_password(tmp_path, APP_PASSWORD, "family")
    assert named.returncode == 0, named.stderr
    default = set_password(tmp_path, APP_PASSWORD.upper())  # the calendar named calendar: CALDAV_PASSWORD
    assert default.returncode == 0, default.stderr
    lines = env.read_text().splitlines()
    assert [
        line.partition("=")[0] if "SAVED_AT_" in line or line.startswith("CALENDAR_SAVED_AT=") else line
        for line in lines
    ] == [
        f"CALENDAR_ICS_URL={FAMILY_LINK}",
        f"CALDAV_PASSWORD_FAMILY={APP_PASSWORD}",
        "CALENDAR_SAVED_AT_FAMILY",
        f"CALDAV_PASSWORD={APP_PASSWORD.upper()}",
        "CALENDAR_SAVED_AT",
    ]
    assert began <= int(lines[2].partition("=")[2]) <= time.time()  # for doctor: when saved
    assert (env.stat().st_mode & 0o777, folder.stat().st_mode & 0o777) == (0o600, 0o700)
    assert sorted(p.name for p in folder.iterdir()) == ["calendar.env"]
    for result in (named, default):  # the password is never shown
        assert APP_PASSWORD.lower() not in (result.stdout + result.stderr).lower()
    assert "CALDAV_PASSWORD_FAMILY" in named.stdout


@pytest.mark.parametrize(
    ("args", "password", "code"),
    [(["Family"], APP_PASSWORD, 2), (["../x"], APP_PASSWORD, 2), (["a", "b"], APP_PASSWORD, 2), (["family"], "", 1)],
)
def test_set_calendar_password_refuses_bad_names_and_an_empty_password(
    tmp_path: Path, args: list[str], password: str, code: int
) -> None:
    env = tmp_path / ".config" / "local-assistant" / "calendar.env"
    env.parent.mkdir(parents=True)
    env.write_text("CALDAV_PASSWORD_FAMILY=old\n")
    result = set_password(tmp_path, password, *args)
    assert result.returncode == code
    assert env.read_text() == "CALDAV_PASSWORD_FAMILY=old\n" and [p.name for p in env.parent.iterdir()] == [
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
        (["logs", "voice", "-n", "20"], "/usr/bin/tail -n 20 /var/log/estia/voice.log"),
        (["logs", "launchd"], "/usr/bin/tail -n 50 /var/log/estia/launchd.log"),
        (["logs"], "/usr/bin/tail -n 50 /var/estia/Library/Logs/local-assistant/bridge.log"),
        (["check"], "/bin/bash"),
        (["mail-login", "info@example.com", "--mail-only"], "--mail-login info@example.com --mail-only"),
        (["spotify-login"], ".venv/bin/local-assistant --spotify-login"),
        (["secret", "set", "web.env", "BRAVE_API_KEY"], "-m local_assistant.secretfile set web.env BRAVE_API_KEY"),
        (["tapo-discover"], "scripts/tapo-discover.sh"),
        (["hue-pair"], "scripts/hue-pair.sh"),
        (["hue-pair", "192.168.1.40"], "scripts/hue-pair.sh 192.168.1.40"),
        (["config"], "TERM=xterm-256color /usr/bin/nano /var/estia/.config/local-assistant/config.toml"),
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


def test_estia_config_runs_nano_as_her_so_no_copy_of_her_config_lands_in_your_account(tmp_path: Path) -> None:
    """sudoedit would copy her config into your account and run the editor as you: a program of yours could read it
    or add an allowed number before it is written back. `estia config` runs /usr/bin/nano by its full path as her, in
    a clean environment; `config soul` copies the example as her (0600) the first time (review 15 Oct, 2)."""
    env = {"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path), "TERM": "xterm-ghostty"}
    done = run_script("estia", "--dry-run", "config", env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith(
        "LANG=en_US.UTF-8 TERM=xterm-ghostty /usr/bin/nano /var/estia/.config/local-assistant/config.toml"
    )
    assert "/usr/bin/sudo -u _estia -H -- /usr/bin/env -i HOME=/var/estia " in done.stdout
    odd = run_script("estia", "--dry-run", "config", env=env | {"TERM": "x;rm -rf ~"})
    assert "TERM=xterm-256color /usr/bin/nano" in odd.stdout  # an odd TERM is never passed on
    soul = run_script("estia", "--dry-run", "config", "soul", env=env)
    lines = soul.stdout.strip().splitlines()
    assert soul.returncode == 0 and len(lines) == 2, soul.stdout
    assert lines[0].endswith(
        "/usr/bin/install -m 600 /usr/local/estia/current/soul.example.md /var/estia/.config/local-assistant/soul.md"
        " (only when she has none)"
    )
    assert lines[1].endswith("TERM=xterm-ghostty /usr/bin/nano /var/estia/.config/local-assistant/soul.md")
    assert run_script("estia", "--dry-run", "config", "other", env=env).returncode == 2


# what may follow sudo before the program: options, and those that take a value; -l, -v, -k and -e run no program here
_SUDO_FLAGS = {"-n", "-H", "-i", "-s", "-b", "-S", "-E", "-P", "--"}
_SUDO_VALUED = {"-u", "-U", "-g", "-p", "-C", "-D", "-h", "-r", "-t", "-T"}
_NO_PROGRAM = {"-l", "-v", "-k", "-e"}
# a bare sudo is caught by tests/test_docs.py; with one of these after it, it is a command too
_SYSTEM_TOOLS = r"grep|cat|rm|mv|cp|tee|kill|launchctl|chown|chmod|pmset|fdesetup|install|lsof|visudo|mkdir|tail|dscl"
_SUDO_AT = re.compile(rf'/usr/bin/sudo\b|\{{SUDO\}}|"\$sudo"|\$sudo\b|(?<![/\w-])sudo(?= +(?:{_SYSTEM_TOOLS})\b)')


def programs_after_sudo(line: str) -> list[str]:
    """The program each sudo in `line` runs, when it is named without its full path ("" for none)."""
    bare: list[str] = []
    for found in _SUDO_AT.finditer(line):
        words = [word.strip("`'\"(),;") for word in line[found.end() :].split()]
        index = 0
        while index < len(words) and (words[index] in _SUDO_FLAGS or words[index] in _SUDO_VALUED):
            index += 2 if words[index] in _SUDO_VALUED else 1
        word = words[index] if index < len(words) else ""
        if word not in _NO_PROGRAM and re.fullmatch(r"[a-z][a-z0-9_.+-]*", word):
            bare.append(word)
    return bare


def test_every_program_after_sudo_is_named_by_its_full_path() -> None:
    """sudo looks a bare name up in your PATH (macOS sets no secure_path), where Homebrew's folder and ~/.local/bin
    come first and are yours: a planted `launchctl` there runs as root the moment you type your password. Every
    command code prints, every fix line and every command a script shows or runs names the program by its full path
    (or goes through /usr/local/estia/bin/estia)."""
    assert programs_after_sudo("/usr/bin/sudo launchctl kickstart -k x") == ["launchctl"]
    assert programs_after_sudo("{SUDO} mv a b; sudo grep -l x") == ["mv", "grep"]
    assert programs_after_sudo("/usr/bin/sudo -u _estia -e /var/estia/x; /usr/bin/sudo -n -l -U alex (as root)") == []
    assert programs_after_sudo('"$sudo" "$estia" doctor; /usr/bin/sudo /bin/launchctl print x') == []
    sources = [
        *sorted((ROOT / "src/local_assistant").rglob("*.py")),  # the prompts package's files too
        *sorted((ROOT / "scripts").glob("*.sh")),
        ROOT / "scripts/estia",
        ROOT / "scripts/estia-deploy",
    ]
    found = [
        f"{path.relative_to(ROOT)}:{number}: {programs_after_sudo(line)}"
        for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if programs_after_sudo(line)
    ]
    assert found == []


def test_every_sudo_a_script_shows_or_runs_is_named_by_its_full_path() -> None:
    """A usage line copied from a script's head runs whatever `sudo` comes first on your PATH: a planted one catches
    the password. The scripts name it /usr/bin/sudo (or "$sudo" from own-user.sh), in comments too."""
    bare = re.compile(r'(?:^|[^/\w$"-])sudo +[/-]')
    sources = [*sorted((ROOT / "scripts").glob("*.sh")), ROOT / "scripts/estia", ROOT / "scripts/estia-deploy"]
    found = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if bare.search(line)
    ]
    assert found == []


@pytest.mark.parametrize(
    ("args", "line"),
    [
        (["stop"], "would run: /bin/launchctl bootout system/com.local-assistant.bridge"),
        (["stop", "voice"], "would run: /bin/launchctl bootout system/com.local-assistant.voice"),
        (
            ["start"],
            "would run: /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.bridge.plist",
        ),
        (
            ["start", "voice"],
            "would run: /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.voice.plist",
        ),
    ],
)
def test_estia_stops_and_starts_her_services_as_root(tmp_path: Path, args: list[str], line: str) -> None:
    """A stop the docs give (her compromise steps, the voice service's undo) goes through her wrapper, by its full
    path: never a launchctl found on your PATH."""
    done = run_script("estia", "--dry-run", *args, env={"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path)})
    assert done.returncode == 0 and done.stdout.strip() == line
    refused = run_script(
        "estia", "--dry-run", args[0], "other", env={"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path)}
    )
    assert refused.returncode == 2


def test_estia_usage_names_every_command_with_the_full_sudo_path(tmp_path: Path) -> None:
    done = run_script("estia", "--dry-run", "nothing", env={"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path)})
    commands = [line for line in done.stderr.splitlines() if re.match(r"\s*\S*sudo /usr/local/estia/bin/estia ", line)]
    assert len(commands) >= 15 and all(line.lstrip().startswith("/usr/bin/sudo ") for line in commands), done.stderr
    assert "--dry-run (first)" in done.stderr  # the notes at the end of the header are shown too


@pytest.mark.parametrize("started_as", ["./estia", "estia", "../scripts/estia"])
def test_estia_usage_reads_its_own_header_when_started_by_a_relative_path(tmp_path: Path, started_as: str) -> None:
    """SEC7: the script goes to / before anything else (her user cannot enter your folders); its usage still finds
    its own header when it was started by a path relative to the folder you were in."""
    done = subprocess.run(
        ["/bin/bash", started_as, "--dry-run", "nothing"],
        cwd=ROOT / "scripts",
        env={"ESTIA_TEST_ROOT": NO_TREE, "PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2 and "awk" not in done.stderr, done.stderr
    assert "--dry-run (first)" in done.stderr and "/usr/bin/sudo /usr/local/estia/bin/estia doctor" in done.stderr


def test_estia_doctor_hands_her_doctor_your_sudo_rules_as_root_lists_them(tmp_path: Path) -> None:
    """Her user cannot read sudo's rules: root lists yours (sudo -l -U, no password for root) and her doctor reads the
    list from stdin, so passwordless sudo counts against the hardened level in her own user too."""
    env = {"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(tmp_path), "SUDO_USER": "alex"}
    done = run_script("estia", "--dry-run", "doctor", env=env)
    lines = done.stdout.strip().splitlines()
    assert done.returncode == 0 and lines[0].endswith("-I -m local_assistant.doctor")
    assert " LANG=en_US.UTF-8 ESTIA_OWNER_SUDO=stdin /" in lines[0]  # a release's doctor from before ignores it
    assert lines[1] == "would run: /usr/bin/sudo -n -l -U alex (as root: your sudo rules, read by her doctor)"


@pytest.mark.skipif(not Path("/usr/libexec/PlistBuddy").exists(), reason="PlistBuddy is macOS only")
def test_estia_logs_reads_her_output_where_her_service_file_puts_it(tmp_path: Path) -> None:
    """An install from before /var/log/estia keeps its output in her folder until the tools' update and a deploy."""
    root = tmp_path / "root"
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    old = "/var/estia/Library/Logs/local-assistant/voice.log"
    plist = {"Label": "com.local-assistant.voice", "StandardOutPath": old}
    (root / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_bytes(plistlib.dumps(plist))
    env = {"PATH": "/usr/bin:/bin", "ESTIA_ROOT": str(root)}
    done = run_script("estia", "--dry-run", "logs", "voice", env=env)
    assert done.returncode == 0 and done.stdout.strip().endswith(f"/usr/bin/tail -n 50 {old}")
    done = run_script("estia", "--dry-run", "logs", "launchd", env=env)  # no service file names another: the new one
    assert done.stdout.strip().endswith("/usr/bin/tail -n 50 /var/log/estia/launchd.log")


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
        ["hue-pair", "192.168.1.40;rm"],
        ["hue-pair", "bridge.example.com"],
        ["hue-pair", "192.168.1.40", "192.168.1.41"],
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
    uv = fake_uv(root)
    before = snapshot(tmp_path)
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "ESTIA_ROOT": str(root),
        "ESTIA_TEST_ROOT": str(root),
    }
    done = run_script("estia-deploy", "--dry-run", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "would run:" in done.stdout and "would switch" in done.stdout and f"{uv} python install 3.13" in done.stdout
    assert not log.exists() and snapshot(tmp_path) == before


def fake_uv(root: Path) -> Path:
    """uv at Homebrew's fixed path in the fake tree: the build steps name it, so no test needs the Mac's own uv."""
    folder = root / "opt/homebrew/bin"
    folder.mkdir(parents=True, exist_ok=True)
    stub(folder, "uv", "exit 0")
    return folder / "uv"


def fake_home(home: Path, *, projects: bool = False) -> None:
    config = 'bot_number = "+306900000000"\n' + ("[projects]\n" if projects else "")
    files = {
        ".config/local-assistant/config.toml": (config, 0o600),
        ".config/local-assistant/web.env": ("BRAVE_API_KEY=sk-made-up-0000\n", 0o600),
        ".local/share/signal-cli/data/111111.d/account.db": ("x", 0o644),
        ".local/share/signal-cli/data/accounts.json": (
            '{"accounts": [{"path": "111111", "number": "+306900000000"}]}',
            0o644,
        ),
        ".local/share/signal-cli/data/111111": ('{"registered": true, "number": "+306900000000"}', 0o600),
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
    uv = fake_uv(root)
    before = snapshot(tmp_path)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(home), "ESTIA_ROOT": str(root), "ESTIA_TEST_ROOT": str(root)}
    done = run_script("migrate-to-own-user.sh", "--dry-run", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{uv} python install" in done.stdout  # the fake tree's uv, never the Mac's own
    # her user may already exist on the Mac running the tests (an earlier move): then only its settings are set again
    made = "would run: /usr/sbin/sysadminctl -addUser _estia" in done.stdout
    record = "/Users/" + "_estia"  # the directory-service record, not a home folder
    settings = f"would run: /usr/bin/dscl . -create {record} IsHidden 1" in done.stdout
    assert (made or settings) and "would copy" in done.stdout
    assert f'config: projects.root: set to "{home / "dev"}"' in done.stdout
    tree = "'user:_estia allow list,search,readattr,readextattr,readsecurity,read,file_inherit,directory_inherit'"
    assert f"would run: /bin/chmod -h +a {tree} {home / 'dev'} … and 1 more" in done.stdout  # as you, after the move
    assert f"would run: /bin/chmod +a 'user:_estia allow search' {home}" in done.stdout
    assert "Her projects folder, read-only for her, at the end of the move (no admin rights needed):" in done.stdout
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
    uv = fake_uv(root)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(home), "ESTIA_ROOT": str(root), "ESTIA_TEST_ROOT": str(root)}
    done = run_script("migrate-to-own-user.sh", "--dry-run", env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{uv} python install" in done.stdout
    assert "config: projects: off (no [projects] in your config)" in done.stdout
    assert "+a" not in done.stdout  # no ACL entry for her, and no folder suggested
    assert "Projects are off (no [projects] in the config): nothing to share." in done.stdout
    assert "Her projects folder" not in done.stdout  # no heading over nothing to share
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


def test_a_calendar_password_goes_through_a_pipe(tmp_path: Path) -> None:
    stubs, log, given = owner_stubs(tmp_path)
    estia = own_user(tmp_path / "root")
    done = run_script("set-calendar-password.sh", "family", env=owner_env(tmp_path, stubs), stdin=f"{APP_PASSWORD}\n")
    assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == [f"sudo {estia} secret set calendar.env CALDAV_PASSWORD_FAMILY"]
    assert given.read_text() == f"{APP_PASSWORD}\n" and APP_PASSWORD not in done.stdout + done.stderr


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
        ("hue-pair.sh", [], "hue-pair"),
        ("hue-pair.sh", ["192.168.1.40"], "hue-pair 192.168.1.40"),
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
        ("run.sh", "/usr/bin/sudo /usr/local/estia/bin/estia restart"),
    ],
)
def test_the_owners_checks_point_to_her_wrapper(tmp_path: Path, script: str, says: str) -> None:
    stubs, log, _ = owner_stubs(tmp_path)
    own_user(tmp_path / "root")
    done = run_script(script, env=owner_env(tmp_path, stubs))
    assert done.returncode == 1 and says in done.stdout + done.stderr and "_estia" in done.stdout + done.stderr
    assert not log.exists()  # it says what to run; it never runs sudo itself


@pytest.mark.parametrize("nuclear", [False, True], ids=["hardened", "nuclear"])
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
    assert ("○ Nuclear level: " in done.stdout) is not nuclear  # without --nuclear: one neutral line
    # the level by name, never "default" (owner, 14 Oct): hardened is setup's security step, nuclear is --nuclear
    assert f"Security level: {'nuclear' if nuclear else 'hardened'}" in done.stdout
    assert "default" not in done.stdout.lower()
    assert "Doctor names the level reached: scripts/doctor.sh" in done.stdout  # not her own user here
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


@pytest.mark.parametrize(("root_part", "code"), [(0, 3), (1, 1), (3, 1)], ids=["share-fails", "move-fails", "move-3"])
def test_only_a_failed_share_after_the_move_ends_the_script_with_its_own_code(
    tmp_path: Path, root_part: int, code: int
) -> None:
    """SEC1 re-check: setup goes on past a failed share only on the share's own exit code (3); a move that fails, in
    any way, ends 1, so setup never reads it as moved (a failed restore can leave her service file in place)."""
    stubs, log, _ = owner_stubs(tmp_path)
    root_sudo(tmp_path / "root", f'echo "sudo $*" >> "{log}"; exit {root_part}')
    marker = tmp_path / "root/usr/local/estia/migration.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"projects_root": str(tmp_path / "elsewhere/projects")}), encoding="utf-8")
    (tmp_path / "home").mkdir()
    done = run_script("migrate-to-own-user.sh", env=owner_env(tmp_path, stubs))
    assert done.returncode == code, done.stdout + done.stderr
    assert ("her projects folder is not shared yet" in done.stderr) is (code == 3)


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


def test_share_documents_dry_run_names_your_documents_folder_and_changes_nothing(tmp_path: Path) -> None:
    stubs, log, _ = owner_stubs(tmp_path)
    home = tmp_path / "home"
    folder = home / "Estia-KB"
    folder.mkdir(parents=True)
    (folder / "lease.pdf").write_bytes(b"%PDF-1.7\n")
    before = snapshot(tmp_path)
    done = run_script("share-documents.sh", "--dry-run", env=owner_env(tmp_path, stubs))
    assert done.returncode == 0, done.stdout + done.stderr
    add = "'user:_estia allow add_file'"
    assert f"would run: /bin/chmod -h +a {add} {folder}" in done.stdout
    assert f"would write: {folder / '.estia-share'}" in done.stdout
    assert done.stdout.splitlines()[-1].startswith("Nothing changed (a dry run)")
    assert not log.exists() and snapshot(tmp_path) == before
    revoke = run_script("share-documents.sh", "--dry-run", "--revoke", env=owner_env(tmp_path, stubs))
    assert revoke.returncode == 0 and "take back" in revoke.stdout
    other = run_script("share-documents.sh", "--dry-run", str(tmp_path), env=owner_env(tmp_path, stubs))
    assert other.returncode == 2 and "always your ~/Estia-KB" in other.stderr


def test_share_documents_without_her_user_changes_nothing(tmp_path: Path) -> None:
    import pwd

    try:
        pwd.getpwnam("_estia")
        pytest.skip("her user exists on this Mac: the share would set real entries")
    except KeyError:
        pass
    stubs, _log, _ = owner_stubs(tmp_path)
    (tmp_path / "home").mkdir()
    done = run_script("share-documents.sh", env=owner_env(tmp_path, stubs))
    assert done.returncode == 1 and "does not exist yet" in done.stdout
    assert not (tmp_path / "home" / "Estia-KB").exists()


def test_the_documents_helper_is_built_and_tested() -> None:
    """tools/documents (Swift): run.sh builds it where the config's default looks before the bridge starts; verify.sh
    builds it before the tests (the end-to-end test reads PDFs with it, never skipped there) and runs its own tests.
    estia-deploy builds it into a release (tests/test_deploy.py)."""
    run, verify = (ROOT / "scripts/run.sh").read_text(), (ROOT / "scripts/verify.sh").read_text()
    build = "(cd tools/documents && swift build -c release --quiet)"
    assert build in run and run.index(build) < run.index("exec .venv/bin/local-assistant")
    # documents are an extra: a failed build (an older Swift) is said, and the bridge still starts
    assert f"{build} ||" in run and "Swift 6.0 or later" in run[run.index(build) :].splitlines()[0]
    assert verify.index(build) < verify.index("export DOCUMENTS_HELPER_REQUIRED=1") < verify.index("uv run pytest -q")
    # one test at a time, as the helper reads: parallel Vision tests deadlocked a 4-core Mac (Vision's own queue
    # waits for a thread of Swift's pool, all of them in Vision), fresh-install test, 16 Oct
    assert verify.index("uv run pytest -q") < verify.index("(cd tools/documents && swift test --quiet --no-parallel)")


def test_every_python_helper_is_synced_tested_and_built() -> None:
    """Each helper in its own uv environment (tools/<name>/pyproject.toml): run.sh syncs it, verify.sh runs its tests
    and mypy, and estia-deploy builds it into the release and checks that it imports."""
    run, verify = (ROOT / "scripts/run.sh").read_text(), (ROOT / "scripts/verify.sh").read_text()
    deploy = (ROOT / "src/local_assistant/deploy.py").read_text()
    helpers = sorted(path.parent.name for path in (ROOT / "tools").glob("*/pyproject.toml"))
    assert {"tapo", "ical", "youtube"} <= set(helpers)
    for name in helpers:
        assert f"(cd tools/{name} && uv sync --frozen --quiet)" in run, name
        assert f"(cd tools/{name} && uv run --frozen -q pytest -q && uv run --frozen -q mypy)" in verify, name
        assert f'release / "tools" / "{name}"' in deploy and f'release / "tools/{name}/.venv/bin/python"' in deploy, (
            name
        )


# ------------------------------------------------------------------ run from anywhere (finding 9), doctor.sh (22, 52)


@pytest.mark.parametrize("script", sorted((ROOT / "scripts").glob("*.sh")), ids=lambda p: p.name)
def test_no_script_reads_its_own_folder_from_a_relative_0_after_a_cd(script: Path) -> None:
    """`dirname "$0"` is relative to where the script was started: after a `cd` it points elsewhere (./mail-login.sh
    run from scripts/ would look for ./own-user.sh in the repo root)."""
    moved = False
    for line in script.read_text(encoding="utf-8").splitlines():
        assert not (moved and 'dirname "$0"' in line), line
        moved = moved or bool(re.match(r"\s*cd\s", line))  # the cd's own line reads $0 before it moves


@pytest.mark.parametrize(
    ("script", "args", "passed"),
    [
        ("mail-login.sh", ["info@example.com"], "run --quiet local-assistant --mail-login info@example.com"),
        ("spotify-login.sh", [], "run --quiet local-assistant --spotify-login"),
    ],
)
def test_a_script_started_from_its_own_folder_finds_its_helpers(
    tmp_path: Path, script: str, args: list[str], passed: str
) -> None:
    stub(tmp_path, "uv", 'echo "$*"')
    result = subprocess.run(
        ["/bin/bash", f"./{script}", *args],
        cwd=ROOT / "scripts",
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_TEST_ROOT": NO_TREE},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, result.stdout.strip()) == (0, passed), result.stderr


def doctor_clone(tmp_path: Path, *, venv: bool) -> tuple[Path, Path]:
    """doctor.sh and own-user.sh in a clone, with or without its environment (.venv/bin/python logs its arguments)."""
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    for name in ("doctor.sh", "own-user.sh"):
        shutil.copy2(ROOT / "scripts" / name, clone / "scripts" / name)
    log = tmp_path / "called.log"
    if venv:
        (clone / ".venv/bin").mkdir(parents=True)
        stub(clone / ".venv/bin", "python", f'echo "python $*" >> "{log}"')
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    stub(stubs, "uv", f'echo "uv $*" >> "{log}"')
    return clone, log


def test_doctor_reads_with_the_environment_setup_made_and_installs_nothing(tmp_path: Path) -> None:
    """doctor.sh is read-only: never `uv run`, which may download packages and needs uv on PATH."""
    clone, log = doctor_clone(tmp_path, venv=True)
    env = {"PATH": f"{tmp_path / 'stubs'}:/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_TEST_ROOT": NO_TREE}
    done = subprocess.run(
        ["/bin/bash", str(clone / "scripts/doctor.sh"), "--x"], env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == ["python -I -m local_assistant.doctor --x"]


def test_doctor_before_setup_says_to_run_setup(tmp_path: Path) -> None:
    clone, log = doctor_clone(tmp_path, venv=False)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_TEST_ROOT": NO_TREE}  # no uv on PATH either
    done = subprocess.run(["/bin/bash", str(clone / "scripts/doctor.sh")], env=env, capture_output=True, text=True)
    assert done.returncode == 1
    assert "scripts/setup.sh" in done.stderr and "command not found" not in done.stderr
    assert not log.exists()


def test_doctor_finds_homebrew_off_path(tmp_path: Path) -> None:
    """Homebrew installed but not on PATH (a new Terminal without its line): doctor's tools check sees its tools."""
    clone, log = doctor_clone(tmp_path, venv=True)
    root = tmp_path / "root"
    folder = root / "opt/homebrew/bin"
    folder.mkdir(parents=True)
    stub(folder, "brew", f'[ "$1" = shellenv ] && echo \'export PATH="{folder}:$PATH"\'')
    stub(clone / ".venv/bin", "python", f'echo "$PATH" >> "{log}"')
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_TEST_ROOT": str(root)}
    done = subprocess.run(["/bin/bash", str(clone / "scripts/doctor.sh")], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert log.read_text().startswith(f"{folder}:")


# ------------------------------------------------------------------ the web keys (findings 17, 54)


def keychain_stub(folder: Path, log: Path, *, items: dict[str, str], locked: bool) -> None:
    """security: `items` are the keychain's (service: key); with `locked`, a key's value (-w) is refused until
    unlock-keychain ran, while finding an item without its value works, as on a locked login keychain."""
    unlocked = folder / "unlocked"
    cases = "\n".join(f'    *"-s {service}"*) found="{key}" ;;' for service, key in items.items())
    stub(
        folder,
        "security",
        f'echo "security $*" >> "{log}"\n'
        f'[ "$1" = unlock-keychain ] && {{ touch "{unlocked}"; exit 0; }}\n'
        'found=""\n'
        f'case "$*" in\n{cases}\nesac\n'
        '[ -n "$found" ] || exit 44\n'
        'case "$*" in *" -w"*) ;; *) exit 0 ;; esac\n'
        f"{'[ -e ' + chr(34) + str(unlocked) + chr(34) + ' ] || exit 36' if locked else ''}\n"
        'echo "$found"',
    )


def test_web_keys_without_any_key_say_how_to_add_one_and_never_unlock(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "security.log"
    keychain_stub(stubs, log, items={}, locked=False)
    done = run_script("set-web-keys.sh", env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home")})
    assert done.returncode == 1
    assert 'security add-generic-password -U -a "$USER" -s brave-search -w' in done.stderr
    assert "unlock" not in log.read_text() and "locked" not in done.stdout + done.stderr


def test_web_keys_in_a_locked_keychain_are_read_after_unlocking_it(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "security.log"
    keychain_stub(stubs, log, items={"brave-search": "sk-brave-made-up"}, locked=True)
    done = run_script("set-web-keys.sh", env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path / "home")})
    assert done.returncode == 0, done.stderr
    assert "The login keychain is locked: unlocking it." in done.stdout
    assert "Brave key 16" in done.stdout and "made-up" not in done.stdout + done.stderr
    web = tmp_path / "home/.config/local-assistant/web.env"
    assert web.read_text() == "BRAVE_API_KEY=sk-brave-made-up\n"


def test_web_keys_in_her_own_user_without_a_key_name_her_hidden_prompt(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    keychain_stub(stubs, tmp_path / "security.log", items={}, locked=False)
    own_user(tmp_path / "root")
    done = run_script("set-web-keys.sh", env=owner_env(tmp_path, stubs))
    assert done.returncode == 1
    assert "/usr/bin/sudo /usr/local/estia/bin/estia secret set web.env BRAVE_API_KEY" in done.stderr


# ------------------------------------------------------------------ places and routes' setup (finding 12)


def gcloud_stub(folder: Path, log: Path, *, existing: str = "") -> None:
    """gcloud: signed in, one open billing account; `projects list` finds `existing` by its name."""
    stub(
        folder,
        "gcloud",
        f'echo "gcloud $*" >> "{log}"\n'
        'case "$*" in\n'
        '  "config get-value account") echo alex@example.com ;;\n'
        f'  "projects list"*) echo "{existing}" ;;\n'
        f'  "projects describe"*) exit {0 if existing else 1} ;;\n'
        '  "billing accounts list"*) echo billingAccounts/000000-000000-000000 ;;\n'
        '  "billing accounts describe"*) echo "My billing" ;;\n'
        "esac\nexit 0",
    )


def test_maps_setup_without_gcloud_says_how_to_install_it(tmp_path: Path) -> None:
    done = run_script("setup-google-maps.sh", env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert done.returncode == 1
    assert "brew install --cask gcloud-cli" in done.stderr and "Not signed in" not in done.stderr


def test_maps_setup_run_again_reuses_its_project_and_says_how_to_go_on(tmp_path: Path) -> None:
    """A second run after a no at the billing question: the project named "Estia AI" is used again, never a new one,
    and the line to go on later names it."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "gcloud.log"
    gcloud_stub(stubs, log, existing="estia-ai-0a1b2c")
    done = run_script(
        "setup-google-maps.sh", env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path)}, stdin="n\n"
    )
    assert done.returncode == 1 and "Stopped: nothing was linked." in done.stderr
    assert "projects create" not in log.read_text()
    assert 'scripts/setup-google-maps.sh "Estia AI" estia-ai-0a1b2c' in done.stdout + done.stderr


def test_maps_setup_shows_why_a_key_could_not_be_made(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "gcloud.log"
    gcloud_stub(stubs, log, existing="estia-ai-0a1b2c")
    text = (stubs / "gcloud").read_text()
    text = text.replace(
        "esac\nexit 0",
        '  "services api-keys describe"*) exit 1 ;;\n'
        '  "services api-keys create"*) echo "PERMISSION_DENIED: API keys API not ready" >&2; exit 1 ;;\nesac\nexit 0',
    )
    (stubs / "gcloud").write_text(text)
    done = run_script(
        "setup-google-maps.sh", env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path)}, stdin="y\n"
    )
    assert done.returncode == 1
    assert "PERMISSION_DENIED: API keys API not ready" in done.stderr
    assert 'scripts/setup-google-maps.sh "Estia AI" estia-ai-0a1b2c' in done.stderr
