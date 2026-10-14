import json
import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from local_assistant import doctor, prompts
from local_assistant.config import Config, load_config
from local_assistant.doctor import Finding, Probe, exit_code, render, run
from local_assistant.hostsec import Check
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
        self.security: list[Check] = []  # the Mac's checks (hostsec), canned: no system tool runs in tests
        self.install: list[Check] = []  # her own user's checks (ownuser)
        self.homes: list[Path] = []
        self.numbers: list[str] = []
        self.own = False  # she runs as her own user here
        self.her = False  # this doctor runs as her (_estia)
        self.programs: list[Check] = []

    def moved(self) -> bool:
        return self.own

    def her_user(self) -> bool:
        return self.her

    def program_checks(self, cfg: Any) -> list[Check]:
        return list(self.programs)

    def security_checks(self, home: Path, bot_number: str) -> list[Check]:
        self.homes.append(home)
        self.numbers.append(bot_number)  # used to find her account, never shown
        return list(self.security)

    def install_checks(self) -> list[Check]:
        return list(self.install)

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


def test_a_cloud_model_under_the_alias_is_a_fail(home: Path) -> None:
    """Ollama's own entry marks a model that runs on its servers: answers pause, so doctor says ✗ and how to fix it."""
    probe = FakeProbe(home)
    remote = {"remote_model": "gpt-oss:120b", "remote_host": "https://ollama.com:443"}
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", **remote}]}
    model = by_part(run(write_config(home, MINIMAL), probe))["Model"]
    assert model.status == "fail" and "cloud model" in model.text and "Ollama's servers" in model.text
    assert "create local-worker from it" in model.fix


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


def local(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Europe/Amsterdam")).timestamp()


def test_doctor_shows_the_next_reminder_by_time_only(home: Path) -> None:
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # with calls: a "call me" reminder places a call
    path = write_config(home, MINIMAL + f'[voice]\nurl = "http://127.0.0.1:8150"\n[calls]\nsocket_dir = "{short}"\n')
    assert by_part(run(path, probe))["Reminders"] == Finding("ok", "Reminders", "none yet")  # no database: none made
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.add_reminder(ACI, "call mum", "2026-10-09 07:00", "Europe/Amsterdam", local("2026-10-09 07:00"), call=True)
    store.add_reminder(ACI, "take the pills", "2026-10-10 08:00", "Europe/Amsterdam", local("2026-10-10 08:00"))
    found = by_part(run(path, probe))["Reminders"]
    assert found == Finding("ok", "Reminders", "2 open · next Fri 9 Oct 07:00 (call) · 0 failed")
    assert "call mum" not in render(run(path, probe)) and "pills" not in render(run(path, probe))
    store.close()


def test_doctor_warns_on_failed_and_stuck_reminders(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    hour_ago = datetime.fromtimestamp(NOW - 3600, UTC).isoformat(timespec="seconds")
    failed = store.add_reminder(ACI, "x", "2026-10-08 10:00", "Europe/Amsterdam", NOW - 6000, at=hour_ago)
    store.finish_reminder(failed, "failed", "send_failed", at=hour_ago)
    long_ago = datetime.fromtimestamp(NOW - 3 * 3600, UTC).isoformat(timespec="seconds")
    stuck = store.add_reminder(ACI, "y", "2026-10-08 08:00", "Europe/Amsterdam", local("2026-10-08 08:00"), at=long_ago)
    note = store.add_outgoing(store.current_conversation().id, ACI, "note", "Reminder: y", None)
    store.fire_reminder(stuck, note, at=long_ago)
    store.close()
    found = by_part(run(path, probe))["Reminders"]
    assert found.status == "warn" and found.text == "1 open · next Thu 8 Oct 08:00 · 1 failed · 1 stuck"
    assert "/reminders" in found.fix


def test_doctor_has_no_reminders_line_with_them_off(home: Path) -> None:
    path = write_config(home, MINIMAL + "[reminders]\nenabled = false\n")
    assert "Reminders" not in by_part(run(path, FakeProbe(home)))


def test_without_calls_doctor_shows_no_call_marker(home: Path) -> None:
    path = write_config(home, MINIMAL)  # no [calls]: "call me" reminders are wake-up texts
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.add_reminder(ACI, "wake up", "2026-10-09 07:00", "Europe/Amsterdam", local("2026-10-09 07:00"), call=True)
    store.close()
    assert by_part(run(path, FakeProbe(home)))["Reminders"].text == "1 open · next Fri 9 Oct 07:00 · 0 failed"


def test_doctor_shows_her_own_messages_as_counts_and_reason_words(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL + "[initiative]\nenabled = true\n")
    found = by_part(run(path, probe))["Initiative"]  # no database yet
    assert found == Finding(
        "ok",
        "Initiative",
        "on: check-ins, look back, improvised note · quiet hours 22:00-08:00 · calendar notes off (no [calendar])",
    )
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    day_ago = datetime.fromtimestamp(NOW - 86_400, UTC).isoformat(timespec="seconds")
    out = store.add_outgoing(store.current_conversation().id, ACI, "text", "Did you get hold of the dentist?", None)
    entry = store.log_initiative("followup", "2026-10-07", "sent", message_id=out, at=day_ago)
    store.set_initiative_replied(entry, NOW - 80_000, at=day_ago)
    store.log_initiative("lookback", "2026-10-04", "skipped", reason="busy", at=day_ago)
    store.set_state("initiative:quiet", "1")
    store.close()
    found = by_part(run(path, probe))["Initiative"]
    assert found.status == "ok" and found.text.endswith(
        "· last 4 weeks: 1 sent, 1 answered within a day · paused (/quiet) · last skipped: look back busy"
    )
    assert "dentist" not in render(run(path, probe))


def test_doctor_warns_when_one_of_her_messages_could_not_be_sent(home: Path) -> None:
    path = write_config(home, MINIMAL + "[calendar]\n[initiative]\nenabled = true\n")
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    now_iso = datetime.fromtimestamp(NOW - 600, UTC).isoformat(timespec="seconds")
    store.log_initiative("calendar", "2026-10-08", "failed", reason="send_failed", at=now_iso)
    store.close()
    found = by_part(run(path, FakeProbe(home)))["Initiative"]
    assert found.status == "warn" and "calendar notes" in found.text and "calendar change send_failed" in found.text


def test_doctor_says_her_own_messages_are_off_until_turned_on_and_who_turned_them_on_or_off(home: Path) -> None:
    probe = FakeProbe(home)
    path = write_config(home, MINIMAL)  # opt-in
    assert by_part(run(path, probe))["Initiative"] == Finding("ok", "Initiative", prompts.DOCTOR_INITIATIVE_OFF)
    database = load_config(path).db_path
    database.parent.mkdir(parents=True)
    store = Store.open(database)
    store.set_state("initiative:enabled", "on")  # the user's /initiative on wins over the config
    store.close()
    found = by_part(run(path, probe))["Initiative"]
    assert found.text.startswith("on (/initiative on): check-ins, look back, improvised note · quiet hours")
    path = write_config(home, MINIMAL + "[initiative]\nenabled = true\n")
    store = Store.open(database)
    store.set_state("initiative:enabled", "off")
    store.close()
    found = by_part(run(path, probe))["Initiative"]
    assert found == Finding("ok", "Initiative", prompts.DOCTOR_INITIATIVE_OFF_BY_USER)


def test_with_speech_stopping_her_doctor_shows_the_speech_detector(home: Path) -> None:
    probe = FakeProbe(home)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")  # socket paths are at most 104 bytes
    calls = f'[calls]\nsocket_dir = "{short}"\n'
    loudness = write_config(home, MINIMAL + VOICE + calls)
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "ready"}
    assert "Speech detector" not in by_part(run(loudness, probe))  # loudness, the default: nothing to show
    path = write_config(home, MINIMAL + VOICE + calls + 'barge = "speech"\n')
    assert by_part(run(path, probe))["Speech detector"] == Finding("ok", "Speech detector", "ready")
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "loading"}
    waiting = by_part(run(path, probe))["Speech detector"]
    assert waiting == Finding(
        "warn", "Speech detector", prompts.DOCTOR_DETECTOR_LOADING, prompts.DOCTOR_DETECTOR_LOADING_FIX
    )
    assert waiting.text == "loading: loudness stops her on calls until it is ready"
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready", "vad": "unavailable"}
    broken = by_part(run(path, probe))["Speech detector"]
    assert broken == Finding(
        "warn", "Speech detector", prompts.DOCTOR_DETECTOR_UNAVAILABLE, prompts.DOCTOR_DETECTOR_UNAVAILABLE_FIX
    )
    assert broken.fix == (  # its own fix: the model folder downloads again at the next start
        "delete ~/Library/Application Support/local-assistant/voice-models/silero-vad, then run"
        " scripts/install-voice-daemon.sh (asks for sudo: run it yourself); see ~/Library/Logs/local-assistant/voice.log"
    )
    probe.answers[HEALTH] = {"stt": "ready", "tts": "ready"}
    old = by_part(run(path, probe))["Speech detector"]
    assert old == Finding("warn", "Speech detector", prompts.DOCTOR_DETECTOR_OLD, prompts.DOCTOR_VOICE_REINSTALL)
    assert old.text == "old voice service: loudness stops her on calls until it is updated"
    del probe.answers[HEALTH]
    silent = by_part(run(path, probe))["Speech detector"]
    assert silent.status == "warn" and silent.text.startswith("not ready")  # the Voice line fails already


# hardening: her Signal account's devices, the safety number, the model's pin, retention, the Mac and her own user

DIGEST = "ab" * 32


def test_her_signal_accounts_devices_come_from_status_json(home: Path) -> None:
    path = write_config(home, MINIMAL)
    assert by_part(run(path, FakeProbe(home)))["Her Signal account"].status == "untested"  # not counted yet
    status = recorded(path)
    status.linked_devices(1)
    one = by_part(run(path, FakeProbe(home)))["Her Signal account"]
    assert (one.status, one.text) == ("ok", "1 device: this Mac (checked 11:35)")
    status.linked_devices(2)
    findings = run(path, FakeProbe(home))
    two = by_part(findings)["Her Signal account"]
    assert two.status == "fail" and two.text.startswith("2 devices") and "removeDevice" in two.fix
    assert exit_code(findings) == 1


def test_a_changed_safety_number_is_red_with_the_compare_then_trust_steps(home: Path) -> None:
    path = write_config(home, MINIMAL)
    fine = by_part(run(path, FakeProbe(home)))["Safety number"]
    assert fine.status == "ok"
    recorded(path).identity_changed()
    findings = run(path, FakeProbe(home))
    changed = by_part(findings)["Safety number"]
    assert changed.status == "fail" and changed.text.startswith("changed 11:35")
    assert changed.fix == prompts.SAFETY_NUMBER_FIX and ACI not in render(findings)


def test_without_a_pin_the_model_line_says_how_to_pin_it(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": DIGEST}]}
    pin = by_part(run(write_config(home, MINIMAL), probe))["Model pin"]
    assert pin.status == "warn" and f'model_digest = "sha256:{DIGEST}"' in pin.fix


def test_a_pinned_model_is_green_when_it_matches_and_red_when_not(home: Path) -> None:
    probe = FakeProbe(home)
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": DIGEST}]}
    path = write_config(home, MINIMAL + f'model_digest = "sha256:{DIGEST}"\n')
    assert by_part(run(path, probe))["Model pin"].status == "ok"
    other = "cd" * 32
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": other}]}
    findings = run(path, probe)
    pin = by_part(findings)["Model pin"]
    assert pin.status == "fail" and "answers are paused" in pin.text and other in pin.fix
    assert exit_code(findings) == 1


def test_history_retention_is_shown(home: Path) -> None:
    kept = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["History"]
    assert (kept.status, kept.text) == ("ok", "kept until you delete it ([privacy] keep_days is not set)")
    path = write_config(home, MINIMAL + "[privacy]\nkeep_days = 90\n")
    cfg = load_config(path)
    store = Store.open(cfg.db_path)
    try:
        assert by_part(run(path, FakeProbe(home)))["History"].status == "untested"  # not pruned yet
        store.set_state("privacy:pruned_at", f"{NOW - 3600:.0f}")
        recent = by_part(run(path, FakeProbe(home)))["History"]
        assert recent.status == "ok" and recent.text.startswith("older than 90 days deleted each night; last 10:40")
        store.set_state("privacy:pruned_at", f"{NOW - 4 * 86_400:.0f}")
        late = by_part(run(path, FakeProbe(home)))["History"]
        assert late.status == "warn" and "4 Oct" in late.text
    finally:
        store.close()


def test_the_macs_and_her_own_users_checks_follow_the_daemon_line_one_to_one(home: Path) -> None:
    probe = FakeProbe(home)
    probe.security = [
        Check("firewall", "warn", "Firewall", "off", "scripts/harden.sh", (("/usr/bin/true",),)),
        Check("registration_lock", "ok", "Registration Lock", "set"),
    ]
    probe.install = [
        Check("own_user_code", "fail", "Her code", "writable by you", "/usr/bin/sudo /usr/local/estia/bin/estia-deploy")
    ]
    findings = run(write_config(home, MINIMAL), probe)
    parts = [finding.part for finding in findings]
    daemon = parts.index("Always on")
    assert parts[daemon + 1 : daemon + 4] == ["Firewall", "Registration Lock", "Her code"]
    assert findings[daemon + 1] == Finding("warn", "Firewall", "off", "scripts/harden.sh")
    assert findings[daemon + 3] == Finding(
        "fail", "Her code", "writable by you", "/usr/bin/sudo /usr/local/estia/bin/estia-deploy"
    )
    assert probe.homes == [home] and probe.numbers == [NUMBER] and exit_code(findings) == 1
    assert NUMBER not in render(findings)


def test_a_mac_check_that_cannot_run_is_a_warning(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: Probe, home: Path, bot_number: str) -> list[Check]:
        raise OSError("no such tool")

    monkeypatch.setattr(FakeProbe, "security_checks", broken)
    found = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Mac security"]
    assert found.status == "warn"


def test_with_her_own_user_installed_a_missing_config_points_to_her_doctor(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = FakeProbe(home)
    probe.own = True
    config = by_part(run(home / ".config/local-assistant/config.toml", probe))["Config"]
    assert (
        config.status == "fail"
        and config.fix == "she runs as her own user: /usr/bin/sudo /usr/local/estia/bin/estia doctor"
    )
    probe.own = False  # rolled back, or never moved: your own config is missing
    config = by_part(run(home / ".config/local-assistant/config.toml", probe))["Config"]
    assert "cp config.example.toml" in config.fix


# her own user: the fixes say how, as her


def test_in_her_own_user_the_pin_says_how_to_edit_her_config_and_restart_her(home: Path) -> None:
    probe = FakeProbe(home)
    probe.own = True
    probe.answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "local-worker:latest", "digest": DIGEST}]}
    path = write_config(home, MINIMAL)
    pin = by_part(run(path, probe))["Model pin"]
    assert (
        f"/usr/bin/sudo -u _estia -e {path}" in pin.fix
        and "/usr/bin/sudo /usr/local/estia/bin/estia restart" in pin.fix
    )
    assert f'model_digest = "sha256:{DIGEST}"' in pin.fix


def test_in_her_own_user_a_soul_others_can_read_is_fixed_as_her(home: Path) -> None:
    probe = FakeProbe(home)
    probe.own = True
    path = write_config(home, MINIMAL)
    soul = path.parent / "soul.md"
    soul.write_text("Warm.\n")
    soul.chmod(0o644)
    found = by_part(run(path, probe))["Personality"]
    assert found.status == "warn" and found.fix.startswith(f"/usr/bin/sudo -u _estia chmod 600 {soul}")


def test_her_programs_follow_her_own_users_checks(home: Path) -> None:
    probe = FakeProbe(home)
    probe.own = True
    probe.install = [Check("plists", "ok", "Services", "bridge runs as _estia")]
    probe.programs = [
        Check("programs", "fail", "Her programs", "signal-cli (x)", "/usr/bin/sudo /usr/local/estia/bin/estia-deploy")
    ]
    findings = run(write_config(home, MINIMAL), probe)
    parts = [finding.part for finding in findings]
    assert parts.index("Her programs") == parts.index("Services") + 1 and exit_code(findings) == 1


def test_run_as_her_the_tools_line_leaves_your_tools_out(home: Path) -> None:
    """Homebrew's ollama and uv are yours and not on her PATH: as her, doctor checks what she runs instead."""
    probe = FakeProbe(home)
    probe.her = True
    probe.tools = set()
    tools = by_part(run(write_config(home, MINIMAL), probe))["Tools"]
    assert tools.status == "ok" and "Her programs" in tools.text


def test_the_safety_number_and_linked_device_fixes_name_her_signal_command() -> None:
    assert "/usr/bin/sudo /usr/local/estia/bin/estia signal listIdentities" in prompts.SAFETY_NUMBER_FIX
    assert "/usr/bin/sudo /usr/local/estia/bin/estia signal removeDevice" in prompts.LINKED_FIX


# her projects folder: read as whoever runs doctor (as her: /usr/bin/sudo /usr/local/estia/bin/estia doctor)


def projects_config(home: Path) -> Path:
    return write_config(home, MINIMAL + f'[projects]\nroot = "{home / "dev"}"\n')


def a_repository(home: Path) -> Path:
    import subprocess

    repo = home / "dev" / "alpha"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# alpha\n")
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    for argv in (["init", "-q", "-b", "main"], ["add", "README.md"]):
        subprocess.run(["/usr/bin/git", "-C", str(repo), *argv], check=True, capture_output=True, env=env)
    return repo


def test_projects_off_have_no_line(home: Path) -> None:
    """Projects are an extra: without [projects] nothing is read and doctor names no projects folder."""
    a_repository(home)
    probe = FakeProbe(home)
    probe.own = True
    probe.projects = lambda cfg: pytest.fail("the projects folder is read while projects are off")  # type: ignore[method-assign]
    assert "Projects" not in by_part(run(write_config(home, MINIMAL), probe))


def test_a_readable_projects_folder_is_ok(home: Path) -> None:
    a_repository(home)
    found = by_part(run(projects_config(home), FakeProbe(home)))["Projects"]
    assert found.status == "ok" and str(home / "dev") in found.text and "README" in found.text


def test_a_projects_folder_her_user_cannot_read_names_your_share_command(home: Path) -> None:
    a_repository(home)
    probe = FakeProbe(home)
    probe.own = True
    (home / "dev").chmod(0)
    try:
        findings = run(projects_config(home), probe)
    finally:
        (home / "dev").chmod(0o755)
    found = by_part(findings)["Projects"]
    assert found.status == "fail" and exit_code(findings) == 1
    assert found.fix.endswith(f"scripts/share-projects.sh {home / 'dev'}") and "no sudo" in found.fix


@pytest.mark.parametrize(
    ("access", "own", "status", "word"),
    [
        (("missing", 0, None, 0), True, "untested", "no folder"),
        (("open", 2, None, 0), True, "untested", "nothing to check"),
        (("open", 2, None, 2), True, "fail", "cannot be read"),
        (("open", 3, "alpha", 1), True, "warn", "cannot be read"),
        (("open", 3, "alpha", 0), True, "ok", "3 project(s)"),
        (("closed", 0, None, 0), False, "warn", "cannot be read"),
    ],
)
def test_the_projects_line(
    home: Path, access: tuple[str, int, str | None, int], own: bool, status: str, word: str
) -> None:
    from local_assistant.projects import Access

    probe = FakeProbe(home)
    probe.own = own
    probe.projects = lambda cfg: Access(*access)  # type: ignore[method-assign]
    found = by_part(run(projects_config(home), probe))["Projects"]
    assert found.status == status and word in found.text, found
    if status in ("fail", "warn") and own:
        assert "scripts/share-projects.sh" in found.fix
    if not own:
        assert "share-projects" not in found.fix  # she runs as you: nothing to share


def test_the_always_on_line_reads_the_service_file_under_the_root_it_checks(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never the live /Library/LaunchDaemons from a test: the service file is looked for under ESTIA_ROOT."""
    root = tmp_path / "root"
    (root / "Library/LaunchDaemons").mkdir(parents=True)
    monkeypatch.setenv("ESTIA_ROOT", str(root))
    always_on = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Always on"]
    assert always_on.status == "warn"
    (root / "Library/LaunchDaemons/com.local-assistant.bridge.plist").write_bytes(b"x")
    always_on = by_part(run(write_config(home, MINIMAL), FakeProbe(home)))["Always on"]
    assert always_on.status == "ok"


# YouTube: always on with read_link; doctor checks the helper is in place and names its version (from its files: no
# network, the helper is not run), then what its last real read did


def youtube_at(monkeypatch: pytest.MonkeyPatch, venv: Path) -> None:
    def load(path: Path) -> Config:
        return replace(load_config(path), youtube_helper=(str(venv / "bin" / "python"), "-I", "helper.py"))

    monkeypatch.setattr(doctor, "load_config", load)


def built_youtube(home: Path, version: str = "1.2.4") -> Path:
    venv = home / "tools/youtube/.venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    (venv / f"lib/python3.13/site-packages/youtube_transcript_api-{version}.dist-info").mkdir(parents=True)
    return venv


def test_the_youtube_helper_is_checked_with_its_version(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    youtube_at(monkeypatch, built_youtube(home))
    path = write_config(home, MINIMAL)
    findings = run(path, FakeProbe(home))
    youtube = by_part(findings)["YouTube"]
    assert youtube.status == "untested" and youtube.text == "set up, not tested yet (youtube-transcript-api 1.2.4)"
    recorded(path).ok("youtube")
    assert by_part(run(path, FakeProbe(home)))["YouTube"].status == "ok"
    assert exit_code(findings) == 0


def test_a_missing_youtube_helper_is_a_warning_with_its_fix(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    youtube_at(monkeypatch, home / "no-such-venv")
    findings = run(write_config(home, MINIMAL), FakeProbe(home))
    youtube = by_part(findings)["YouTube"]
    assert (youtube.status, youtube.text) == ("warn", "the YouTube helper is not set up: video links cannot be read")
    assert youtube.fix == "cd tools/youtube && uv sync --frozen" and exit_code(findings) == 0


def test_in_her_own_user_a_missing_youtube_helper_names_the_deploy(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # there the release's helper is built by the deploy: the uv line would fail in a root-owned release
    youtube_at(monkeypatch, home / "no-such-venv")
    probe = FakeProbe(home)
    probe.own = True
    youtube = by_part(run(write_config(home, MINIMAL), probe))["YouTube"]
    assert youtube.status == "warn" and "cd tools/youtube" not in youtube.fix
    assert youtube.fix.startswith("/usr/bin/sudo /usr/local/estia/bin/estia-deploy") and "--update-tools" in youtube.fix


def test_a_failing_youtube_read_names_the_next_step(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    youtube_at(monkeypatch, built_youtube(home))
    path = write_config(home, MINIMAL)
    recorded(path).failed("youtube", "bad_answer")
    youtube = by_part(run(path, FakeProbe(home)))["YouTube"]
    assert youtube.status == "fail" and "tools/youtube" in youtube.fix
