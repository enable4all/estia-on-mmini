"""The launchd templates and the shell scripts: no host-specific values, rendering works, every script parses."""

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RENDER = ROOT / "scripts" / "render-launchd.sh"
BRIDGE = ROOT / "launchd" / "com.local-assistant.bridge.plist.in"
VOICE = ROOT / "launchd" / "com.local-assistant.voice.plist.in"
NEWSYSLOG = ROOT / "launchd" / "local-assistant.newsyslog.conf.in"
SHELL_SCRIPTS = sorted([*(ROOT / "scripts").glob("*.sh"), *(ROOT / "tools" / "call-tunnel").glob("*.sh")])

needs_plutil = pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")


def render(template: Path, out: Path, **values: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "LA_USER": values.get("user", "alex"),
        "LA_HOME": values.get("home", "/Users/alex"),
        "LA_REPO": values.get("repo", "/Users/alex/code/local-assistant"),
        "LA_BREW_PREFIX": values.get("brew", "/opt/homebrew"),
    }
    return subprocess.run([str(RENDER), str(template), str(out)], env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("template", [BRIDGE, VOICE, NEWSYSLOG], ids=lambda p: p.name)
def test_templates_hold_placeholders_not_host_values(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "@HOME@" in text
    assert "/opt/homebrew" not in text


def test_bridge_template_placeholders() -> None:
    text = BRIDGE.read_text(encoding="utf-8")
    assert "<string>@USER@</string>" in text
    assert "<string>@REPO@/.venv/bin/local-assistant</string>" in text
    assert "@BREW_PREFIX@/bin:/usr/bin:/bin" in text


@needs_plutil
@pytest.mark.parametrize("template", [BRIDGE, VOICE], ids=lambda p: p.name)
def test_plist_templates_render_to_valid_plists(template: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.plist"
    result = render(template, out)
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert not re.search(r"@[A-Z_]+@", text)
    assert "<string>alex</string>" in text
    assert "<string>/Users/alex</string>" in text
    assert "/Users/alex/code/local-assistant" in text
    assert "/Users/alex/Library/Logs/local-assistant/" in text
    assert subprocess.run(["plutil", "-lint", str(out)], capture_output=True, check=False).returncode == 0


@needs_plutil
def test_plist_values_are_xml_escaped_and_sed_safe(tmp_path: Path) -> None:
    out = tmp_path / "out.plist"
    result = render(BRIDGE, out, repo="/Users/alex/a&b|c <d>\\e")
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "/Users/alex/a&amp;b|c &lt;d&gt;\\e/.venv/bin/local-assistant" in text
    assert subprocess.run(["plutil", "-lint", str(out)], capture_output=True, check=False).returncode == 0


def test_newsyslog_template_renders_owner_and_log_path(tmp_path: Path) -> None:
    out = tmp_path / "out.conf"
    result = render(NEWSYSLOG, out)
    assert result.returncode == 0, result.stderr
    line = next(ln for ln in out.read_text(encoding="utf-8").splitlines() if not ln.startswith("#"))
    assert line.split()[:2] == ["/Users/alex/Library/Logs/local-assistant/*.log", "alex:staff"]


def test_newsyslog_refuses_whitespace_in_paths(tmp_path: Path) -> None:
    result = render(NEWSYSLOG, tmp_path / "out.conf", home="/Users/alex smith")
    assert result.returncode != 0
    assert not (tmp_path / "out.conf").exists()


def test_unknown_placeholder_is_an_error(tmp_path: Path) -> None:
    template = tmp_path / "x.conf.in"
    template.write_text("@HOME@ @NOPE@\n", encoding="utf-8")
    result = render(template, tmp_path / "x.conf")
    assert result.returncode != 0
    assert "@NOPE@" in result.stderr
    assert not (tmp_path / "x.conf").exists()


def test_no_output_left_when_the_plist_is_invalid(tmp_path: Path) -> None:
    template = tmp_path / "bad.plist.in"
    template.write_text("<plist><dict>@HOME@</plist>\n", encoding="utf-8")
    result = render(template, tmp_path / "bad.plist")
    assert result.returncode != 0
    assert not (tmp_path / "bad.plist").exists()


def test_install_scripts_render_the_templates_before_installing() -> None:
    for name, label in [("install-daemon.sh", "bridge"), ("install-voice-daemon.sh", "voice")]:
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert f"label=com.local-assistant.{label}\n" in text
        assert "render-launchd.sh" in text
        assert '$label.plist.in" "$rendered/$label.plist"' in text
        assert 'install -m 644 -o root -g wheel "$rendered/$label.plist"' in text
        assert 'install -m 644 -o root -g wheel "launchd/' not in text


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


SET_CALENDAR = ROOT / "scripts" / "set-calendar-url.sh"
FAMILY_LINK = "https://calendar.google.com/calendar/ical/alex%40example.com/private-0123456789abcdef/basic.ics"
SCHOOL_LINK = "https://calendar.google.com/calendar/ical/school%40example.com/public/basic.ics"


def set_calendar(home: Path, link: str, *args: str) -> subprocess.CompletedProcess[str]:
    """scripts/set-calendar-url.sh with the link typed at its hidden prompt, under the macOS bash (3.2)."""
    return subprocess.run(
        ["/bin/bash", str(SET_CALENDAR), *args],
        input=f"  {link}  \n",
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
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
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, result.stdout.strip()) == (code, passed)
    if code:
        assert "--mail-only" in result.stderr  # the usage names it
