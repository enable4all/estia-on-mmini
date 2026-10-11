import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from local_assistant import doctor
from local_assistant.config import Config, load_config
from local_assistant.doctor import Finding, Probe, exit_code, render, run
from local_assistant.language import LANGUAGE_KEY
from local_assistant.mail_auth import TokenStore
from local_assistant.part_status import PartStatus, Reason
from local_assistant.store import Store

NUMBER = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"
MINIMAL = f'bot_number = "{NUMBER}"\nallowed_acis = ["{ACI}"]\n'
NOW = 1_791_452_400.0  # 2026-10-08 09:40 UTC (11:40 Amsterdam)
OLD = NOW - 30 * 86_400  # when the setup files were saved: long before anything was recorded
MAIL = (
    '[mail]\ntenant_id = "11111111-2222-4333-8444-555555555555"\nclient_id = "66666666-7777-4888-9999-000000000000"\n'
    '[[mail.accounts]]\nname = "home"\naddress = "alex@example.com"\n'
    '[[mail.accounts]]\nname = "work"\naddress = "alex@work.example"\n'
)


class FakeProbe(Probe):
    def __init__(self, home: Path) -> None:
        self.home = home
        self.tools = {"signal-cli", "ollama", "uv"}
        self.system = ("darwin", "arm64")
        self.answers: dict[str, Any] = {
            "http://127.0.0.1:11434/api/tags": {"models": [{"name": "local-worker:latest"}]}
        }
        self.asked: list[str] = []
        self.java: Path | None = None
        self.clock = NOW

    def now(self) -> float:
        return self.clock

    def java_home(self) -> Path | None:
        return self.java

    def which(self, name: str) -> str | None:
        return f"/opt/homebrew/bin/{name}" if name in self.tools else None

    def platform(self) -> tuple[str, str]:
        return self.system

    def get_json(self, url: str) -> Any:
        self.asked.append(url)
        if url not in self.answers:
            raise OSError("connection refused")
        return self.answers[url]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))  # the data folder (tokens) follows the home folder
    accounts = tmp_path / ".local/share/signal-cli/data/accounts.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(json.dumps({"accounts": [{"number": NUMBER, "path": "123456"}]}))
    return tmp_path


def write_config(home: Path, text: str, mode: int = 0o600) -> Path:
    path = home / ".config/local-assistant/config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def by_part(findings: list[Finding]) -> dict[str, Finding]:
    return {finding.part: finding for finding in findings}


def test_a_ready_minimal_setup_has_no_failures(home: Path) -> None:
    findings = run(write_config(home, MINIMAL), FakeProbe(home))
    parts = by_part(findings)
    assert [parts[p].status for p in ("Mac", "Tools", "Config", "Model", "Signal number")] == ["ok"] * 5
    assert exit_code(findings) == 0
    assert NUMBER not in render(findings)  # nothing private on the screen


def test_a_missing_config_says_how_to_create_it(home: Path) -> None:
    findings = run(home / ".config/local-assistant/config.toml", FakeProbe(home))
    config = by_part(findings)["Config"]
    assert config.status == "fail" and "config.example.toml" in config.fix
    assert exit_code(findings) == 1


def test_a_config_others_can_read_is_refused(home: Path) -> None:
    config = by_part(run(write_config(home, MINIMAL, 0o644), FakeProbe(home)))["Config"]
    assert config.status == "fail" and "chmod 600" in config.text


def test_a_config_error_is_shown(home: Path) -> None:
    config = by_part(run(write_config(home, 'bot_number = "123"\n'), FakeProbe(home)))["Config"]
    assert config.status == "fail" and "bot_number" in config.text


def test_missing_tools_come_with_the_install_command(home: Path) -> None:
    probe = FakeProbe(home)
    probe.tools = {"uv"}
    tools = by_part(run(write_config(home, MINIMAL), probe))["Tools"]
    assert tools.status == "fail" and "signal-cli" in tools.text and "ollama" in tools.text
    assert tools.fix == "brew install signal-cli ollama"


def test_only_a_mac_with_apple_silicon_will_do(home: Path) -> None:
    probe = FakeProbe(home)
    probe.system = ("darwin", "x86_64")
    assert by_part(run(write_config(home, MINIMAL), probe))["Mac"].status == "fail"


def test_ollama_must_run_and_have_the_model(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL + 'model = "gemma4:26b"\n')
    model = by_part(run(path, probe))["Model"]
    assert model.status == "fail" and "ollama pull gemma4:26b" in model.fix
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "gemma4:26b"}]}
    assert by_part(run(path, probe))["Model"].status == "ok"
    del probe.answers["http://127.0.0.1:11434/api/tags"]
    down = by_part(run(path, probe))["Model"]
    assert down.status == "fail" and "brew services start ollama" in down.fix


def test_the_bot_number_must_be_registered(home: Path) -> None:
    (home / ".local/share/signal-cli/data/accounts.json").write_text(json.dumps({"accounts": []}))
    signal = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Signal number"]
    assert signal.status == "fail" and "register" in signal.fix and NUMBER not in signal.text + signal.fix


def test_optional_parts_are_checked_only_when_turned_on(home: Path) -> None:
    probe = FakeProbe(home)
    parts = by_part(run(write_config(home, MINIMAL), probe))
    assert not {"Web search", "Places", "Mail", "Calendar", "Voice", "Calls", "Spotify"} & set(parts)
    assert probe.asked == ["http://127.0.0.1:11434/api/tags"]


def test_web_search_needs_a_private_key_file_and_never_shows_a_key(home: Path) -> None:
    path = write_config(home, MINIMAL + "[web]\n")
    web = by_part(run(path, FakeProbe(home)))["Web search"]
    assert web.status == "fail" and "set-web-keys.sh" in web.fix
    keys = home / ".config/local-assistant/web.env"
    keys.write_text("OLLAMA_API_KEY=secret-value-123\n")
    keys.chmod(0o600)
    findings = run(path, FakeProbe(home))
    assert by_part(findings)["Web search"].status == "untested"  # set up; the bridge has not searched yet
    assert "secret-value-123" not in render(findings)


def test_services_that_do_not_answer_are_named(home: Path) -> None:
    text = MINIMAL + '[voice]\nurl = "http://127.0.0.1:8150"\n[mail]\njev_url = "http://127.0.0.1:8149"\n'
    text += 'tenant_id = "11111111-2222-4333-8444-555555555555"\nclient_id = "66666666-7777-4888-9999-000000000000"\n'
    text += '[[mail.accounts]]\nname = "home"\naddress = "alex@example.com"\n'
    parts = by_part(run(write_config(home, text), FakeProbe(home)))
    assert parts["Voice"].status == "fail" and "install-voice-daemon.sh" in parts["Voice"].fix
    assert parts["Mail"].status == "fail" and "mail-login.sh" in parts["Mail"].fix  # not signed in yet
    assert parts["Mail pre-filter (AgentJev)"].status == "warn"  # optional: mail still works without it


def test_doctor_says_her_configured_name(home: Path) -> None:
    probe = FakeProbe(home)
    probe.system = ("linux", "x86_64")
    named = by_part(run(write_config(home, MINIMAL + 'name = "Tiffany"\n'), probe))
    assert named["Mac"].fix == "Tiffany needs a Mac with Apple silicon (M1 or later)"
    assert named["Personality"].text == "no soul.md: Tiffany's built-in playful style (soul.example.md: your own)"
    assert named["Personality"].status == "ok"  # the default, nothing to fix
    plain = by_part(run(write_config(home, MINIMAL), probe))
    assert (
        plain["Mac"].fix == "Estia needs a Mac with Apple silicon (M1 or later)"
        and "Estia" in plain["Personality"].text
    )
    broken = by_part(run(write_config(home, MINIMAL + 'name = "R2-D2"\n'), probe))
    assert broken["Mac"].fix.startswith("Estia needs") and "name must be 1-20 letters" in broken["Config"].text


def test_a_soul_with_a_problem_is_a_warning(home: Path) -> None:
    soul = home / ".config/local-assistant/soul.md"
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("Be kind.")
    soul.chmod(0o644)
    findings = run(write_config(home, MINIMAL), FakeProbe(home))
    assert by_part(findings)["Personality"].status == "warn" and exit_code(findings) == 0


def test_the_profile_picture_is_estias_e_your_own_or_none(home: Path) -> None:
    probe = FakeProbe(home)
    estia = by_part(run(write_config(home, MINIMAL), probe))["Profile picture"]
    assert estia == Finding("ok", "Profile picture", "Estia's e. (docs/brand/estia-avatar.png)")
    (home / "her.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    own = by_part(run(write_config(home, MINIMAL + 'avatar = "~/her.png"\n'), probe))["Profile picture"]
    assert own == Finding("ok", "Profile picture", "her.png")
    none = by_part(run(write_config(home, MINIMAL + 'avatar = ""\n'), probe))["Profile picture"]
    assert none.status == "ok" and none.text.startswith("none")


def test_a_picture_that_cannot_be_used_is_a_warning_not_a_failure(home: Path) -> None:
    """The bridge starts without it (Signal keeps the picture it has), so doctor warns and setup goes on."""
    findings = run(write_config(home, MINIMAL + 'name = "Tiffany"\navatar = "~/gone.png"\n'), FakeProbe(home))
    picture = by_part(findings)["Profile picture"]
    assert picture.status == "warn"
    assert picture.text == f"{home / 'gone.png'}: no such file; Tiffany runs without it (Signal keeps the one it has)"
    assert "avatar" in picture.fix
    assert by_part(findings)["Config"].status == "ok" and exit_code(findings) == 0


def test_render_marks_each_line_and_gives_the_fix() -> None:
    text = render([Finding("ok", "Mac", "Apple silicon"), Finding("fail", "Tools", "missing: uv", "brew install uv")])
    assert text.splitlines() == ["✓ Mac: Apple silicon", "✗ Tools: missing: uv", "    → brew install uv"]


def test_mail_names_each_mailbox_that_still_needs_a_sign_in(home: Path) -> None:
    text = MINIMAL + '[mail]\ntenant_id = "11111111-2222-4333-8444-555555555555"\n'
    text += 'client_id = "66666666-7777-4888-9999-000000000000"\n'
    text += '[[mail.accounts]]\nname = "home"\naddress = "alex@example.com"\n'
    text += '[[mail.accounts]]\nname = "work"\naddress = "alex@work.example"\n'
    path = write_config(home, text)
    TokenStore(load_config(path).mail_token_path).save("alex@example.com", "rt-home")
    findings = run(path, FakeProbe(home))
    mail = by_part(findings)["Mail"]
    assert mail.status == "fail" and mail.text == "not signed in: work (1 of 2 signed in)"
    assert mail.fix == "scripts/mail-login.sh <address of work>" and "rt-home" not in render(findings)


def test_the_example_placeholders_must_be_replaced(home: Path) -> None:
    accounts = home / ".local/share/signal-cli/data/accounts.json"
    accounts.write_text(json.dumps({"accounts": [{"number": "+300000000000"}]}))
    text = 'bot_number = "+300000000000"\nallowed_acis = ["00000000-0000-0000-0000-000000000000"]\n'
    findings = run(write_config(home, text), FakeProbe(home))
    config = by_part(findings)["Config"]
    assert config.status == "fail" and "placeholder" in config.text
    assert "bot_number" in config.text and "allowed_acis" in config.text and "2.4" in config.fix
    assert exit_code(findings) == 1


def test_calls_need_java_as_well_as_their_two_programs(home: Path) -> None:
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    path = write_config(home, MINIMAL + f'[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\nsocket_dir = "{short}"\n')
    cfg = load_config(path)
    for program in (cfg.call_signal_cli, cfg.call_tunnel):
        program.parent.mkdir(parents=True, exist_ok=True)
        program.write_text("#!/bin/sh\n")
    calls = by_part(run(path, probe))["Calls"]
    assert calls.status == "fail" and "Java" in calls.text and "java_home" in calls.fix
    java = home / "jdk"
    (java / "bin").mkdir(parents=True)
    (java / "bin/java").write_text("#!/bin/sh\n")
    probe.java = java
    assert by_part(run(path, probe))["Calls"].status == "ok"


def web_keys(home: Path, at: float = OLD) -> None:
    keys = home / ".config/local-assistant/web.env"
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text("OLLAMA_API_KEY=o-key\nGOOGLE_MAPS_API_KEY=g-key\n")
    keys.chmod(0o600)
    os.utime(keys, (at, at))


def mail_tokens(path: Path, saved: dict[str, float], signed_in: dict[str, float] | None = None) -> None:
    """The mailboxes' refresh tokens (address → when saved), as scripts/mail-login.sh and the bridge leave them: signed
    in when saved, unless `signed_in` says when (the bridge renewed the token since)."""
    tokens = load_config(path).mail_token_path
    tokens.parent.mkdir(parents=True, exist_ok=True)
    entries = {
        a: {"refresh_token": f"rt-{a[5:9]}", "updated_at": t, "signed_in_at": (signed_in or {}).get(a, t)}
        for a, t in saved.items()
    }
    tokens.write_text(json.dumps(entries))
    tokens.chmod(0o600)


def recorded(path: Path, at: float = NOW - 300) -> PartStatus:
    """The bridge's recorder on the config's status file, at a fixed time."""
    return PartStatus(load_config(path).data_dir / "status.json", clock=lambda: at)


def test_a_part_that_is_set_up_but_never_used_is_not_tested_yet(home: Path) -> None:
    web_keys(home)
    findings = run(write_config(home, MINIMAL + "[web]\n[maps]\n"), FakeProbe(home))
    web = by_part(findings)["Web search"]
    assert (web.status, web.text) == ("untested", "set up, not tested yet (keys: OLLAMA_API_KEY)")
    assert "○ Web search: set up, not tested yet (keys: OLLAMA_API_KEY)" in render(findings).splitlines()
    assert by_part(findings)["Places"].text == "set up, not tested yet (no home address set)"
    assert exit_code(findings) == 0  # a fresh install can reach "no ✗"


def test_a_part_that_worked_is_green_with_its_time(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path).ok("web")
    web = by_part(run(path, FakeProbe(home)))["Web search"]
    assert (web.status, web.text) == ("ok", "working, last used 11:35 (keys: OLLAMA_API_KEY)")


def test_a_part_that_fails_is_red_with_its_fix(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path, NOW - 2 * 86_400).failed("web", "refused", 401)
    findings = run(path, FakeProbe(home))
    web = by_part(findings)["Web search"]
    assert web.status == "fail" and web.text.startswith("failing since 6 Oct 11:40: refused by the service (401)")
    assert web.fix == "check the keys: scripts/set-web-keys.sh" and exit_code(findings) == 1
    assert "    → check the keys: scripts/set-web-keys.sh" in render(findings).splitlines()


def test_each_signed_in_mailbox_gets_its_own_state(home: Path) -> None:
    path = write_config(home, MINIMAL + MAIL)
    mail_tokens(path, {"alex@example.com": OLD, "alex@work.example": OLD})
    status = recorded(path)
    status.ok("mail:home")
    status.failed("mail:work", "sign_in")  # a revoked token: the file is there, the sign-in is not
    findings = run(path, FakeProbe(home))
    parts = by_part(findings)
    assert "Mail" not in parts
    assert (parts["Mail (home)"].status, parts["Mail (home)"].text) == ("ok", "working, last read 11:35")
    work = parts["Mail (work)"]
    assert work.status == "fail" and work.text == "failing since 11:35: sign-in expired"
    assert work.fix == "scripts/mail-login.sh <address of work>"
    assert "rt-" not in render(findings) and "alex@" not in render(findings)


def test_a_mailbox_without_a_sign_in_keeps_its_setup_line(home: Path) -> None:
    path = write_config(home, MINIMAL + MAIL)
    TokenStore(load_config(path).mail_token_path).save("alex@example.com", "rt-home")
    parts = by_part(run(path, FakeProbe(home)))
    assert parts["Mail"].status == "fail" and parts["Mail"].text == "not signed in: work (1 of 2 signed in)"
    assert parts["Mail (home)"].status == "untested" and "Mail (work)" not in parts


def helpers_at(monkeypatch: pytest.MonkeyPatch, python: Path) -> None:
    """The calendar and device helpers as doctor will look for them (the repo's own may or may not be built)."""

    def load(path: Path) -> Config:
        return replace(load_config(path), calendar_helper=(str(python), "helper.py"), tapo_helper=(str(python),))

    monkeypatch.setattr(doctor, "load_config", load)


def test_a_missing_setup_file_beats_a_good_record(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers_at(monkeypatch, home / "no-such-python")
    path = write_config(home, MINIMAL + "[calendar]\n")
    recorded(path).ok("calendar")
    calendar = by_part(run(path, FakeProbe(home)))["Calendar"]
    assert calendar.status == "fail" and calendar.fix == "cd tools/ical && uv sync"


def test_every_optional_part_shows_its_state(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    secret = home / ".config/local-assistant/calendar.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("ICAL_URL=https://calendar.example.com/private-x/basic.ics\n")
    secret.chmod(0o600)
    os.utime(secret, (OLD, OLD))
    text = MINIMAL + '[voice]\nurl = "http://127.0.0.1:8150"\n[calendar]\n'
    text += '[[tapo.devices]]\nname = "lamp"\nhost = "192.168.1.20"\nkind = "light"\n'
    text += '[spotify]\nclient_id = "0123456789abcdef0123456789abcdef"\n'
    path = write_config(home, text)
    tokens = load_config(path).spotify_token_path
    tokens.parent.mkdir(parents=True, exist_ok=True)
    tokens.write_text("{}")
    tokens.chmod(0o600)
    status = recorded(path)
    status.ok("voice")
    status.failed("calendar", "gone")
    status.failed("devices", "no_answer")
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:8150/health"] = {"stt": "ready", "tts": "ready"}
    parts = by_part(run(path, probe))
    assert (parts["Voice"].status, parts["Voice"].text) == ("ok", "working, last used 11:35")
    assert parts["Calendar"].status == "fail" and parts["Calendar"].fix == "scripts/set-calendar-url.sh"
    lights = parts["Lights and plugs"]
    assert lights.status == "fail" and lights.text == "failing since 11:35: no answer (1 device(s))"
    assert "check the device" in lights.fix
    assert (parts["Spotify"].status, parts["Spotify"].text) == ("untested", "set up, not tested yet")


def test_an_unreadable_status_file_means_not_tested_yet(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    status_file = load_config(path).data_dir / "status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text("{broken")
    assert by_part(run(path, FakeProbe(home)))["Web search"].status == "untested"


def test_a_failure_whose_fix_was_saved_since_is_not_tested_yet(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path).failed("web", "refused", 401)
    web_keys(home, at=NOW - 60)  # scripts/set-web-keys.sh, after the failure
    findings = run(path, FakeProbe(home))
    web = by_part(findings)["Web search"]
    assert (web.status, web.text, web.fix) == ("untested", "set up again, not tested yet (keys: OLLAMA_API_KEY)", "")
    assert "○ Web search: set up again, not tested yet (keys: OLLAMA_API_KEY)" in render(findings).splitlines()
    assert exit_code(findings) == 0


def test_saving_the_setup_again_does_not_hide_a_failure_it_cannot_fix(home: Path) -> None:
    web_keys(home)
    path = write_config(home, MINIMAL + "[web]\n")
    recorded(path).failed("web", "busy", 429)
    web_keys(home, at=NOW - 60)
    assert by_part(run(path, FakeProbe(home)))["Web search"].status == "fail"


def test_a_mailbox_signed_in_again_since_its_failure_is_not_tested_yet(home: Path) -> None:
    path = write_config(home, MINIMAL + MAIL)
    status = recorded(path)
    status.failed("mail:home", "sign_in")
    status.failed("mail:work", "refused", 403)
    # home: scripts/mail-login.sh since; work: the bridge rotated its token, which does not fix a refusal
    mail_tokens(path, {"alex@example.com": NOW - 60, "alex@work.example": NOW - 60})
    parts = by_part(run(path, FakeProbe(home)))
    assert (parts["Mail (home)"].status, parts["Mail (home)"].text) == ("untested", "set up again, not tested yet")
    assert parts["Mail (work)"].status == "fail"
    mail_tokens(path, {"alex@example.com": NOW - 600, "alex@work.example": NOW - 600})  # saved before it failed
    assert by_part(run(path, FakeProbe(home)))["Mail (home)"].status == "fail"
    mail_tokens(path, {"alex@example.com": NOW - 60}, signed_in={"alex@example.com": OLD})  # only renewed since
    assert by_part(run(path, FakeProbe(home)))["Mail (home)"].status == "fail"


@pytest.mark.parametrize(
    ("saved", "state"),
    [
        ({"authorized_at": NOW - 60}, "untested"),  # scripts/spotify-login.sh, after the failure
        ({"authorized_at": OLD, "ended_at": NOW - 300}, "fail"),  # the bridge noting the end is no new sign-in
    ],
)
def test_spotify_signed_in_again_since_its_failure_is_not_tested_yet(
    home: Path, saved: dict[str, float], state: str
) -> None:
    path = write_config(home, MINIMAL + '[spotify]\nclient_id = "0123456789abcdef0123456789abcdef"\n')
    recorded(path).failed("spotify", "sign_in")
    tokens = load_config(path).spotify_token_path
    tokens.parent.mkdir(parents=True, exist_ok=True)
    tokens.write_text(json.dumps({"refresh_token": "rt-spotify", **saved}))
    tokens.chmod(0o600)
    assert by_part(run(path, FakeProbe(home)))["Spotify"].status == state


@pytest.mark.parametrize(
    ("part", "label", "reason", "saved", "state"),
    [
        ("devices", "Lights and plugs", "login", "tapo.env", "untested"),  # scripts/set-tapo-credentials.sh
        ("devices", "Lights and plugs", "no_answer", "tapo.env", "fail"),  # a new login does not wake a device
        ("devices", "Lights and plugs", "helper_missing", "helper", "untested"),  # cd tools/tapo && uv sync
        ("calendar", "Calendar", "gone", "calendar.env", "untested"),  # scripts/set-calendar-url.sh
        ("calendar", "Calendar", "no_answer", "calendar.env", "fail"),
        ("calendar", "Calendar", "helper_missing", "helper", "untested"),  # cd tools/ical && uv sync
    ],
)
def test_a_saved_login_address_or_helper_counts_only_for_the_failure_it_fixes(
    home: Path, monkeypatch: pytest.MonkeyPatch, part: str, label: str, reason: Reason, saved: str, state: str
) -> None:
    helper = home / "python"  # uv sync makes it as a symlink: its own time counts, not its target's
    helper.symlink_to(Path(os.__file__))
    helpers_at(monkeypatch, helper)
    folder = home / ".config/local-assistant"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("tapo.env", "calendar.env"):
        (folder / name).write_text(
            "SAVED=yes\nCALENDAR_ICS_URL=https://calendar.google.com/calendar/ical/x/basic.ics\n"
        )
        (folder / name).chmod(0o600)
    for file in (folder / "tapo.env", folder / "calendar.env", helper):
        os.utime(file, (OLD, OLD), follow_symlinks=False)
    text = MINIMAL + '[calendar]\n[[tapo.devices]]\nname = "lamp"\nhost = "192.168.1.20"\nkind = "light"\n'
    path = write_config(home, text)
    recorded(path).failed(part, reason)
    os.utime(helper if saved == "helper" else folder / saved, (NOW - 60, NOW - 60), follow_symlinks=False)
    assert by_part(run(path, FakeProbe(home)))[label].status == state


VOICE = '[voice]\nurl = "http://127.0.0.1:8150"\n'
HEALTH = "http://127.0.0.1:8150/health"


def test_english_needs_nothing_more_from_the_voice_service(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}  # older than the languages: fine for English
    language = by_part(run(write_config(home, MINIMAL + VOICE), probe))["Language"]
    assert (language.status, language.text) == ("ok", "English (en)")


def test_another_language_needs_a_voice_service_that_has_it(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en", "nl", "de"]}
    path = write_config(home, 'language = "nl"\n' + MINIMAL + VOICE)
    findings = run(path, probe)
    assert by_part(findings)["Language"] == Finding("ok", "Language", "Nederlands (nl)")
    assert "✓ Language: Nederlands (nl)" in render(findings)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}
    old = by_part(run(path, probe))["Language"]
    assert (
        old.status == "fail" and old.text == "Nederlands (nl), but the voice service is older than the language feature"
    )
    assert old.fix == "scripts/install-voice-daemon.sh (asks for sudo: run it yourself)"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en"]}
    assert by_part(run(path, probe))["Language"].status == "fail"
    del probe.answers[HEALTH]
    assert by_part(run(path, probe))["Language"].status == "warn"  # no answer: the Voice line fails already


def test_the_language_chosen_with_the_command_is_the_one_checked(home: Path) -> None:
    # an install that started in English and switched with /language nl: the doctor checks Dutch, the one in use
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}  # older than the language feature
    path = write_config(home, MINIMAL + VOICE)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.set_state(LANGUAGE_KEY, "nl")
    running = by_part(run(path, probe))["Language"]  # the bridge has the database open
    assert running.status == "fail" and running.text.startswith("Nederlands (nl), but the voice service is older")
    store.close()  # the bridge stopped: its database is read as it is, nothing is created next to it
    stopped = by_part(run(path, probe))["Language"]
    assert (
        stopped.status == "fail" and stopped.fix == "scripts/install-voice-daemon.sh (asks for sudo: run it yourself)"
    )
    assert sorted(item.name for item in database.parent.iterdir()) == ["assistant.db"]
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en", "nl"]}
    assert by_part(run(path, probe))["Language"] == Finding("ok", "Language", "Nederlands (nl)")


@pytest.mark.parametrize("stored", ["en", "xx", None])
def test_without_a_usable_choice_the_config_language_is_checked(home: Path, stored: str | None) -> None:
    probe = FakeProbe(home)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "languages": ["en", "de"]}
    path = write_config(home, 'language = "de"\n' + MINIMAL + VOICE)
    database = load_config(path).db_path
    if stored is not None:
        database.parent.mkdir(parents=True)
        store = Store.open(database)
        store.set_state(LANGUAGE_KEY, stored)
        store.close()
    expected = "English (en)" if stored == "en" else "Deutsch (de)"
    assert by_part(run(path, probe))["Language"] == Finding("ok", "Language", expected)
    assert stored is not None or not database.exists()  # no database yet: none is created


def test_another_language_without_voice_is_text_only(home: Path) -> None:
    probe = FakeProbe(home)
    language = by_part(run(write_config(home, 'language = "ja"\n' + MINIMAL), probe))["Language"]
    assert (language.status, language.text) == ("ok", "日本語 (ja), text only (no [voice])")
    assert probe.asked == ["http://127.0.0.1:11434/api/tags"]


SOURCES = (
    '[calendar]\n[[calendar.sources]]\nname = "family"\n[[calendar.sources]]\nname = "school-days"\n'
    '[[calendar.sources]]\nname = "office"\nmailbox = "work"\n'
)


def signed_in_long_ago(path: Path, address: str) -> TokenStore:
    tokens = TokenStore(load_config(path).mail_token_path)
    tokens.save(address, "rt-work")
    saved = json.loads(tokens.path.read_text())
    saved[address]["updated_at"] = OLD
    tokens.path.write_text(json.dumps(saved))
    return tokens


def save_addresses(home: Path, text: str, when: float = OLD) -> Path:
    env = home / ".config/local-assistant/calendar.env"
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(text)
    env.chmod(0o600)
    os.utime(env, (when, when))
    return env


def test_each_calendar_shows_its_own_setup(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    save_addresses(home, "CALENDAR_ICS_URL_FAMILY=https://calendar.google.com/calendar/ical/x/basic.ics\n")
    path = write_config(home, MINIMAL + MAIL + SOURCES)
    parts = by_part(run(path, FakeProbe(home)))
    assert (parts["Calendar (family)"].status, parts["Calendar (family)"].text) == (
        "untested",
        "set up, not tested yet",
    )
    school = parts["Calendar (school-days)"]
    assert school.status == "fail" and school.fix == "scripts/set-calendar-url.sh school-days"
    office = parts["Calendar (office)"]
    assert office.status == "fail" and office.fix == "scripts/mail-login.sh <address of work>"
    assert "Calendar" not in parts  # no line for a calendar that is not configured
    signed_in_long_ago(path, "alex@work.example")
    recorded(path).failed("calendar:office", "no_consent")
    office = by_part(run(path, FakeProbe(home)))["Calendar (office)"]
    assert office.status == "fail" and office.text == "failing since 11:35: Calendars.Read not granted yet"
    assert "Grant admin consent" in office.fix and "<address of work>" in office.fix


def test_an_outlook_calendar_alone_needs_no_calendar_helper(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helpers_at(monkeypatch, home / "no-such-python")  # tools/ical not set up, no calendar.env: not needed
    path = write_config(home, MINIMAL + MAIL + '[calendar]\n[[calendar.sources]]\nname = "office"\nmailbox = "work"\n')
    TokenStore(load_config(path).mail_token_path).save("alex@work.example", "rt-work")
    findings = run(path, FakeProbe(home))
    office = by_part(findings)["Calendar (office)"]
    assert (office.status, office.text) == ("untested", "set up, not tested yet")
    assert not [f for f in findings if f.part.startswith("Calendar") and f.part != "Calendar (office)"]


OFFICE = '[calendar]\n[[calendar.sources]]\nname = "office"\nmailbox = "work"\n'


def sign_in(path: Path, at: float, *, calendar: bool = True, renewed: bool = False) -> None:
    """scripts/mail-login.sh for the work mailbox at a given time (with or without Calendars.Read), or mail's refresh
    saving a renewed token."""
    tokens = TokenStore(load_config(path).mail_token_path, clock=lambda: at)
    tokens.save("alex@work.example", f"rt-{at:.0f}", signed_in=not renewed, calendar=calendar and not renewed)


@pytest.mark.parametrize(
    ("reason", "calendar", "state"),
    [
        ("sign_in", False, "untested"),  # any new sign-in of the mailbox
        ("no_consent", True, "untested"),  # a sign-in that asked for Calendars.Read
        ("no_consent", False, "fail"),  # --mail-only asks for no consent
    ],
)
def test_a_new_sign_in_counts_for_an_outlook_calendar(
    home: Path, monkeypatch: pytest.MonkeyPatch, reason: Reason, calendar: bool, state: str
) -> None:
    helpers_at(monkeypatch, home / "no-such-python")
    path = write_config(home, MINIMAL + MAIL + OFFICE)
    sign_in(path, OLD)
    recorded(path).failed("calendar:office", reason)
    assert by_part(run(path, FakeProbe(home)))["Calendar (office)"].status == "fail"
    sign_in(path, NOW - 60, calendar=calendar)
    assert by_part(run(path, FakeProbe(home)))["Calendar (office)"].status == state


@pytest.mark.parametrize("reason", ["no_consent", "sign_in"])
def test_mail_renewing_the_token_does_not_hide_an_outlook_calendar_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch, reason: Reason
) -> None:
    helpers_at(monkeypatch, home / "no-such-python")
    path = write_config(home, MINIMAL + MAIL + OFFICE)
    sign_in(path, OLD)
    recorded(path).failed("calendar:office", reason)
    sign_in(path, NOW - 60, renewed=True)  # mail's hourly refresh, after the failure: no sign-in
    office = by_part(run(path, FakeProbe(home)))["Calendar (office)"]
    assert office.status == "fail" and "mail-login.sh" in office.fix


GOOGLE = "https://calendar.google.com/calendar/ical/x/basic.ics"


def google_addresses(home: Path, family: float, school: float) -> None:
    """Both Google calendars' addresses, each with the time scripts/set-calendar-url.sh saved it."""
    text = (
        f"CALENDAR_ICS_URL_FAMILY={GOOGLE}\nCALENDAR_SAVED_AT_FAMILY={family:.0f}\n"
        f"CALENDAR_ICS_URL_SCHOOL_DAYS={GOOGLE}\nCALENDAR_SAVED_AT_SCHOOL_DAYS={school:.0f}\n"
    )
    save_addresses(home, text, max(family, school))


def test_a_google_calendar_counts_only_its_own_saved_address(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    path = write_config(home, MINIMAL + MAIL + SOURCES)
    google_addresses(home, OLD, OLD)
    recorded(path).failed("calendar:family", "gone")
    google_addresses(home, OLD, NOW - 60)  # scripts/set-calendar-url.sh school-days, after family failed
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "fail"
    google_addresses(home, NOW - 60, NOW - 60)  # family's own address saved again
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "untested"
    text = f"CALENDAR_ICS_URL_FAMILY={GOOGLE}\nCALENDAR_ICS_URL_SCHOOL_DAYS={GOOGLE}\n"
    save_addresses(home, text, NOW - 60)  # saved by hand, no times: the file's time could be either calendar's
    assert by_part(run(path, FakeProbe(home)))["Calendar (family)"].status == "fail"


def test_the_one_calendar_needs_its_address_in_calendar_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = home / "python"
    helper.write_text("#!/bin/sh\n")
    helpers_at(monkeypatch, helper)
    save_addresses(home, "CALENDAR_ICS_URL_FAMILY=https://calendar.google.com/calendar/ical/x/basic.ics\n")
    calendar = by_part(run(write_config(home, MINIMAL + "[calendar]\n"), FakeProbe(home)))["Calendar"]
    assert calendar.status == "fail" and calendar.fix == "scripts/set-calendar-url.sh"
    assert "https://" not in calendar.text
