"""`sudo estia secret set <file> <KEY>` (secretfile): the value only through stdin, an allowlist of files and keys, one
key replaced, 0600, no links followed, lengths only in the output. Run as a process, as the wrapper runs it."""

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

SECRET = "sk-test-0123456789abcdefghij"  # privacy-ok: a made-up key for the test (gitleaks:allow)
LINK = "https://calendar.google.com/calendar/ical/alex%40example.com/private-0123456789abcdef/basic.ics"


def folder(root: Path) -> Path:
    path = root / "var/estia/.config/local-assistant"
    path.mkdir(parents=True)
    path.chmod(0o700)
    return path


def secret(root: Path, *args: str, value: str = SECRET) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "local_assistant.secretfile", *args],
        input=f"  {value}  \n",
        env={"PATH": "/usr/bin:/bin", "HOME": str(root / "home"), "ESTIA_ROOT": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_key_is_saved_from_stdin_and_the_other_lines_stay(tmp_path: Path) -> None:
    env = folder(tmp_path) / "web.env"
    env.write_text("# mine\nOLLAMA_API_KEY=kept\nBRAVE_API_KEY=old\n")
    env.chmod(0o600)
    done = secret(tmp_path, "set", "web.env", "BRAVE_API_KEY")
    assert done.returncode == 0, done.stderr
    assert env.read_text().splitlines() == ["# mine", "OLLAMA_API_KEY=kept", f"BRAVE_API_KEY={SECRET}"]
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert SECRET not in done.stdout + done.stderr
    assert f"{len(SECRET)} characters" in done.stdout
    assert sorted(p.name for p in env.parent.iterdir()) == ["web.env"]  # no temporary file left


def test_a_new_file_is_private(tmp_path: Path) -> None:
    done = secret(tmp_path, "set", "tapo.env", "TAPO_PASSWORD")
    tapo = tmp_path / "var/estia/.config/local-assistant/tapo.env"
    assert done.returncode == 1 and not tapo.exists()  # her folder is made by the move, never here
    folder(tmp_path)
    assert secret(tmp_path, "set", "tapo.env", "TAPO_PASSWORD").returncode == 0
    assert stat.S_IMODE(tapo.stat().st_mode) == 0o600 and tapo.read_text() == f"TAPO_PASSWORD={SECRET}\n"


def test_a_value_on_the_command_line_is_refused(tmp_path: Path) -> None:
    folder(tmp_path)
    done = secret(tmp_path, "set", "web.env", "BRAVE_API_KEY", SECRET)
    assert done.returncode == 2 and "stdin" in done.stderr
    assert SECRET not in done.stdout + done.stderr
    assert not (tmp_path / "var/estia/.config/local-assistant/web.env").exists()


@pytest.mark.parametrize(
    ("file", "key"),
    [
        ("web.env", "TAPO_PASSWORD"),
        ("config.toml", "bot_number"),
        ("../web.env", "BRAVE_API_KEY"),
        ("calendar.env", "CALENDAR_ICS_URL_school"),
        ("tapo.env", "TAPO_USERNAME=x"),
    ],
)
def test_only_the_allowlist(tmp_path: Path, file: str, key: str) -> None:
    folder(tmp_path)
    done = secret(tmp_path, "set", file, key)
    assert done.returncode == 2 and list((tmp_path / "var/estia/.config/local-assistant").iterdir()) == []


def test_links_are_refused(tmp_path: Path) -> None:
    here = folder(tmp_path)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("keep me\n")
    (here / "web.env").symlink_to(elsewhere)
    assert secret(tmp_path, "set", "web.env", "BRAVE_API_KEY").returncode == 1
    assert elsewhere.read_text() == "keep me\n"
    (here / "web.env").unlink()
    here.rename(tmp_path / "real")
    here.symlink_to(tmp_path / "real")  # the folder itself a link
    assert secret(tmp_path, "set", "web.env", "BRAVE_API_KEY").returncode == 1
    assert list((tmp_path / "real").iterdir()) == []


def test_a_calendar_address_gets_its_saved_time(tmp_path: Path) -> None:
    env = folder(tmp_path) / "calendar.env"
    env.write_text("CALENDAR_ICS_URL=first\nCALENDAR_SAVED_AT=1\n")
    began = int(time.time())
    done = secret(tmp_path, "set", "calendar.env", "CALENDAR_ICS_URL_SCHOOL_DAYS", value=LINK)
    assert done.returncode == 0, done.stderr
    lines = env.read_text().splitlines()
    assert lines[:3] == ["CALENDAR_ICS_URL=first", "CALENDAR_SAVED_AT=1", f"CALENDAR_ICS_URL_SCHOOL_DAYS={LINK}"]
    assert lines[3].startswith("CALENDAR_SAVED_AT_SCHOOL_DAYS=") and int(lines[3].split("=")[1]) >= began
    assert "calendar.google.com" not in done.stdout + done.stderr
    bad = secret(tmp_path, "set", "calendar.env", "CALENDAR_ICS_URL", value="https://example.com/x.ics")
    assert bad.returncode == 1 and env.read_text().splitlines() == lines


def test_a_caldav_password_gets_its_saved_time(tmp_path: Path) -> None:
    env = folder(tmp_path) / "calendar.env"
    env.write_text("CALENDAR_ICS_URL=first\nCALENDAR_SAVED_AT=1\n")
    began = int(time.time())
    password = "abcd-efgh-ijkl-mnop"  # privacy-ok: made up, an app-specific password's shape
    done = secret(tmp_path, "set", "calendar.env", "CALDAV_PASSWORD_FAMILY", value=password)
    assert done.returncode == 0, done.stderr
    lines = env.read_text().splitlines()
    assert lines[:3] == ["CALENDAR_ICS_URL=first", "CALENDAR_SAVED_AT=1", f"CALDAV_PASSWORD_FAMILY={password}"]
    assert lines[3].startswith("CALENDAR_SAVED_AT_FAMILY=") and int(lines[3].split("=")[1]) >= began
    assert password not in done.stdout + done.stderr
    assert secret(tmp_path, "set", "calendar.env", "CALDAV_PASSWORD", value=password).returncode == 0
    assert secret(tmp_path, "set", "calendar.env", "CALDAV_PASSWORD_family", value=password).returncode == 2


@pytest.mark.parametrize("value", ["", "two\nlines"])
def test_an_empty_or_broken_value_is_refused(tmp_path: Path, value: str) -> None:
    folder(tmp_path)
    done = subprocess.run(
        [sys.executable, "-I", "-m", "local_assistant.secretfile", "set", "web.env", "BRAVE_API_KEY"],
        input=value,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ESTIA_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    saved = tmp_path / "var/estia/.config/local-assistant/web.env"
    if value:  # only the first line is read: one line, the rest is never kept
        assert done.returncode == 0 and saved.read_text() == "BRAVE_API_KEY=two\n"
    else:
        assert done.returncode == 1 and not saved.exists()


def test_root_ignores_estia_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from local_assistant import secretfile

    monkeypatch.setenv("ESTIA_ROOT", str(tmp_path))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert secretfile.target_folder() == Path("/var/estia/.config/local-assistant")
