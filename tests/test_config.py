import tempfile
import tomllib
from pathlib import Path

import pytest

from local_assistant import config as config_module
from local_assistant.app import signal_client
from local_assistant.config import (
    AvatarError,
    Config,
    ConfigError,
    DeviceSpec,
    ExtraCheck,
    find_java_home,
    load_config,
    parse_config,
    read_avatar,
)
from local_assistant.signal_io import call_env

ACI = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
GOOD = f'bot_number = "+306900000000"\nallowed_acis = ["{ACI.upper()}"]\n'


def write(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def system_zone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str | None) -> None:
    """Make /etc/localtime a link to `target` (the Mac's zone database path), or missing."""
    link = tmp_path / "localtime"
    if target is not None:
        link.symlink_to(target)
    monkeypatch.setattr(config_module, "LOCALTIME", link)


def test_loads_valid_config_and_normalises_aci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system_zone(tmp_path, monkeypatch, "/var/db/timezone/zoneinfo/Europe/Lisbon")
    cfg = load_config(write(tmp_path, GOOD))
    assert cfg.bot_number == "+306900000000"
    assert cfg.allowed_acis == frozenset({ACI})
    assert str(cfg.timezone) == "Europe/Lisbon"  # not set: the Mac's own time zone
    assert cfg.model == "local-worker"
    assert cfg.db_path.name == "assistant.db"


@pytest.mark.parametrize(
    ("target", "zone"),
    [
        ("/var/db/timezone/zoneinfo/Asia/Tokyo", "Asia/Tokyo"),
        ("/var/db/timezone/zoneinfo/America/Argentina/Buenos_Aires", "America/Argentina/Buenos_Aires"),
        ("/usr/share/zoneinfo.default/Europe/Amsterdam", "Europe/Amsterdam"),  # older macOS
        ("/var/db/timezone/zoneinfo/UTC", "UTC"),
    ],
)
def test_the_default_time_zone_is_read_from_etc_localtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, zone: str
) -> None:
    system_zone(tmp_path, monkeypatch, target)
    assert parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]}).timezone.key == zone
    assert Config(bot_number="+306900000000", allowed_acis=frozenset({ACI})).timezone.key == zone


@pytest.mark.parametrize(
    "target", [None, "/etc/somewhere/else", "/var/db/timezone/zoneinfo/Mars/Base", "/var/db/timezone/zoneinfo/"]
)
def test_the_default_time_zone_falls_back_to_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str | None
) -> None:
    system_zone(tmp_path, monkeypatch, target)
    assert parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]}).timezone.key == "UTC"


def test_a_regular_file_instead_of_a_link_falls_back_to_utc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "localtime").write_bytes(b"TZif2")
    monkeypatch.setattr(config_module, "LOCALTIME", tmp_path / "localtime")
    assert parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]}).timezone.key == "UTC"


def test_a_configured_time_zone_wins_over_the_systems(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system_zone(tmp_path, monkeypatch, "/var/db/timezone/zoneinfo/Asia/Tokyo")
    raw: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI], "timezone": "Europe/Amsterdam"}
    assert str(parse_config(raw).timezone) == "Europe/Amsterdam"


def test_refuses_file_readable_by_others(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(write(tmp_path, GOOD, mode=0o644))


def test_missing_file_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.example\.toml"):
        load_config(tmp_path / "nope.toml")


def test_broken_toml_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load_config(write(tmp_path, "bot_number = "))


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"bot_number": "306900000000", "allowed_acis": [ACI]}, "bot_number"),
        ({"bot_number": "+306900000000", "allowed_acis": []}, "at least one"),
        ({"bot_number": "+306900000000", "allowed_acis": ["not-a-uuid"]}, "not a UUID"),
        ({"bot_number": "+306900000000", "allowed_acis": [ACI], "timezone": "Mars/Base"}, "timezone"),
        ({"bot_number": "+306900000000", "allowed_acis": [ACI], "model": ""}, "model"),
    ],
)
def test_rejects_invalid_values(raw: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_health_and_projects_sections(tmp_path: Path) -> None:
    cfg = parse_config(
        {
            "bot_number": "+306900000000",
            "allowed_acis": [ACI],
            "health": {"interval_hours": 2, "confirm_minutes": 3, "disk_free_min_percent": 15},
            "projects": {
                "root": str(tmp_path),
                "default": "summary",
                "docs": ["my-project"],
                "code": ["other-project"],
            },
        }
    )
    assert (cfg.health_interval_hours, cfg.health_confirm_minutes, cfg.disk_free_min_percent) == (2, 3, 15)
    assert (cfg.projects_root, cfg.projects_default) == (tmp_path, "summary")
    assert (cfg.projects_docs, cfg.projects_code) == (frozenset({"my-project"}), frozenset({"other-project"}))


def test_health_and_projects_defaults() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]})
    assert (cfg.health_interval_hours, cfg.health_confirm_minutes, cfg.disk_free_min_percent) == (4, 5, 10)
    assert cfg.projects_root == Path.home() / "dev" and cfg.projects_default == "summary"
    assert cfg.projects_docs == frozenset() and cfg.photo_max_count == 4


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"projects": {"default": "everything"}}, "projects.default"),
        ({"projects": {"docs": "my-project"}}, "projects.docs"),
        ({"health": {"interval_hours": 0}}, "health.interval_hours"),
    ],
)
def test_invalid_health_and_projects_values(extra: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], **extra})


TENANT = "11111111-2222-4333-8444-555555555555"
CLIENT = "66666666-7777-4888-9999-000000000000"
MAIL = {
    "tenant_id": TENANT,
    "client_id": CLIENT,
    "accounts": [
        {"name": "family", "address": "info@example.com"},
        {"name": "work", "address": "contact@example.app"},
    ],
}


def test_mail_section() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "mail": MAIL})
    assert cfg.mail_enabled
    assert (cfg.mail_tenant_id, cfg.mail_client_id) == (TENANT, CLIENT)
    assert [(a.name, a.address) for a in cfg.mail_accounts] == [
        ("family", "info@example.com"),
        ("work", "contact@example.app"),
    ]
    assert (cfg.digest_time, cfg.mail_sync_minutes, cfg.mail_keep_days) == ("09:15", 5, 30)
    assert (cfg.mail_first_sync_days, cfg.mail_reply_days) == (30, 7)


def test_mail_is_off_without_the_section() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]})
    assert not cfg.mail_enabled and cfg.mail_accounts == ()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"tenant_id": "not-a-uuid"}, "mail.tenant_id"),
        ({"client_id": ""}, "mail.client_id"),
        ({"accounts": []}, "mail.accounts"),
        ({"accounts": [{"name": "Family Box", "address": "info@example.com"}]}, "mail.accounts"),
        ({"accounts": [{"name": "family", "address": "not an address"}]}, "mail.accounts"),
        (
            {"accounts": [{"name": "a", "address": "x@example.com"}, {"name": "a", "address": "y@example.com"}]},
            "unique",
        ),
        ({"digest_time": "9.15"}, "mail.digest_time"),
        ({"keep_days": 0}, "mail.keep_days"),
    ],
)
def test_invalid_mail_values(change: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "mail": {**MAIL, **change}})


def test_voice_section_with_defaults() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}})
    assert cfg.voice_enabled
    assert (cfg.voice_url, cfg.voice_name, cfg.voice_speed) == ("http://127.0.0.1:8150", "af_heart", 1.1)
    assert (cfg.voice_max_spoken_chars, cfg.voice_max_audio_bytes) == (1500, 10_000_000)
    assert cfg.voice_dir == cfg.data_dir / "voice"


def test_voice_is_off_without_the_section() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]})
    assert not cfg.voice_enabled and cfg.voice_url == ""


@pytest.mark.parametrize(
    ("voice", "message"),
    [
        ({"url": "http://example.com:8150"}, "voice.url"),
        ({"url": "http://127.0.0.1"}, "voice.url"),
        ({"voice": "../af_heart"}, "voice.voice"),
        ({"speed": 3}, "voice.speed"),
        ({"max_spoken_chars": 50}, "voice.max_spoken_chars"),
        ({"max_audio_bytes": 20_000_000}, "voice.max_audio_bytes"),
    ],
)
def test_invalid_voice_values(voice: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": voice})


TAPO: dict[str, object] = {
    "devices": [
        {"name": "Living room lamp", "host": "192.168.1.20", "kind": "light", "rule": "normal"},
        {"name": "heater", "host": "192.168.1.30", "kind": "plug", "rule": "ask first"},
    ]
}


def test_tapo_section() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "tapo": TAPO})
    assert cfg.tapo_enabled
    assert cfg.tapo_devices == (
        DeviceSpec("Living room lamp", "192.168.1.20", "light", "normal"),
        DeviceSpec("heater", "192.168.1.30", "plug", "ask first"),
    )
    assert (cfg.tapo_helper_timeout, cfg.tapo_confirm_seconds) == (15, 120)
    assert cfg.tapo_helper[1:] == ("-I", cfg.tapo_helper[2]) and cfg.tapo_helper[2].endswith("tools/tapo/helper.py")


def test_tapo_is_off_without_the_section() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]})
    assert not cfg.tapo_enabled and cfg.tapo_devices == ()


@pytest.mark.parametrize(
    ("device", "message"),
    [
        ({"host": "8.8.8.8"}, "tapo.devices"),  # only addresses on the home network
        ({"host": "0.0.0.0"}, "tapo.devices"),
        ({"host": "169.254.10.1"}, "tapo.devices"),
        ({"host": "255.255.255.255"}, "tapo.devices"),
        ({"host": "198.18.0.1"}, "tapo.devices"),
        ({"host": "lamp.example.com"}, "tapo.devices"),
        ({"kind": "camera"}, "tapo.devices"),
        ({"rule": "sometimes"}, "tapo.devices"),
        ({"name": ""}, "tapo.devices"),
        ({"name": "heater"}, "unique"),
    ],
)
def test_invalid_tapo_values(device: dict[str, object], message: str) -> None:
    devices = TAPO["devices"]
    assert isinstance(devices, list)
    tapo = {"devices": [{**devices[0], **device}, devices[1]]}
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "tapo": tapo})


def test_calendar_section() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "calendar": {}})
    assert cfg.calendar_enabled  # an empty [calendar] turns it on; the secret address lives in calendar.env
    assert (cfg.calendar_digest, cfg.calendar_helper_timeout) == (True, 30)
    assert cfg.calendar_helper[1:] == ("-I", cfg.calendar_helper[2]) and cfg.calendar_helper[2].endswith(
        "tools/ical/helper.py"
    )
    cfg = parse_config(
        {"bot_number": "+306900000000", "allowed_acis": [ACI], "calendar": {"digest": False, "helper_timeout": 20}}
    )
    assert (cfg.calendar_digest, cfg.calendar_helper_timeout) == (False, 20)


def test_calendar_is_off_without_the_section() -> None:
    assert not parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]}).calendar_enabled


@pytest.mark.parametrize(
    ("calendar", "message"),
    [
        ("on", r"\[calendar\] must be a table"),
        ({"digest": "yes"}, "calendar.digest"),
        ({"helper_timeout": 0}, "calendar.helper_timeout"),
        ({"helper_timeout": 31}, "calendar.helper_timeout"),  # the helper stops itself within 35 s anyway
    ],
)
def test_invalid_calendar_values(calendar: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "calendar": calendar})


def test_calendar_sources_name_google_and_microsoft_calendars() -> None:
    sources = [
        {"name": "calendar"},  # the address saved before names: CALENDAR_ICS_URL
        {"name": "school-days", "label": " Σχολείο "},
        {"name": "work", "mailbox": "work", "label": "Work"},
    ]
    cfg = parse_config(
        {"bot_number": "+306900000000", "allowed_acis": [ACI], "mail": MAIL, "calendar": {"sources": sources}}
    )
    assert cfg.calendar_enabled
    assert [(s.name, s.shown, s.mailbox, s.ical_key) for s in cfg.calendars] == [
        ("calendar", "Calendar", "", "CALENDAR_ICS_URL"),
        ("school-days", "Σχολείο", "", "CALENDAR_ICS_URL_SCHOOL_DAYS"),
        ("work", "Work", "work", ""),
    ]
    assert cfg.calendar_mailbox(cfg.calendars[2]) == cfg.mail_accounts[1]
    assert cfg.calendar_mailbox(cfg.calendars[0]) is None
    assert [
        s.shown
        for s in parse_config(
            {"bot_number": "+306900000000", "allowed_acis": [ACI], "calendar": {"sources": [{"name": "school_days"}]}}
        ).calendars
    ] == ["School days"]


def test_without_sources_the_calendar_is_the_one_google_calendar_as_before() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "calendar": {}})
    assert cfg.calendar_sources == ()
    assert [(s.name, s.mailbox, s.ical_key) for s in cfg.calendars] == [("calendar", "", "CALENDAR_ICS_URL")]
    assert parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]}).calendars == ()


@pytest.mark.parametrize(
    ("calendar", "message"),
    [
        ({"sources": []}, "calendar.sources must list"),
        ({"sources": "family"}, "calendar.sources must list"),
        ({"sources": ["family"]}, "calendar.sources: each name"),
        ({"sources": [{"name": "Family"}]}, "calendar.sources: each name"),
        ({"sources": [{"name": "family"}, {"name": "family"}]}, "unique"),
        ({"sources": [{"name": "school-days"}, {"name": "school_days"}]}, "unique"),
        ({"sources": [{"name": "x", "label": ""}]}, "calendar.sources: x: label"),
        ({"sources": [{"name": "x", "label": "a\nb"}]}, "calendar.sources: x: label"),
        ({"sources": [{"name": "x", "label": "L" * 31}]}, "calendar.sources: x: label"),
        ({"sources": [{"name": "x", "mailbox": "nope"}]}, r"calendar.sources: x: mailbox must be the name"),
        ({"sources": [{"name": "x", "mailbox": 1}]}, r"calendar.sources: x: mailbox must be the name"),
        ({"sources": [{"name": "x", "mailbox": "work"}, {"name": "y", "mailbox": "work"}]}, "at most once"),
        ({"sources": [{"name": f"c{number}"} for number in range(9)]}, "at most 8"),
    ],
)
def test_invalid_calendar_sources(calendar: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "mail": MAIL, "calendar": calendar})


def test_a_microsoft_calendar_needs_the_mail_section() -> None:
    calendar = {"sources": [{"name": "work", "mailbox": "work"}]}
    with pytest.raises(ConfigError, match=r"needs \[mail\]"):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "calendar": calendar})


def test_digest_time_is_top_level_and_the_mail_one_still_works() -> None:
    base: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI]}
    assert parse_config(base).digest_time == "09:15"
    assert parse_config({**base, "digest_time": "08:30"}).digest_time == "08:30"
    assert parse_config({**base, "mail": {**MAIL, "digest_time": "07:45"}}).digest_time == "07:45"
    both = {**base, "digest_time": "08:30", "mail": {**MAIL, "digest_time": "07:45"}}
    assert parse_config(both).digest_time == "08:30"  # the top-level one wins
    with pytest.raises(ConfigError, match="digest_time"):
        parse_config({**base, "digest_time": "8:30"})
    with pytest.raises(ConfigError, match="before 12:00"):  # a missed digest is only sent until noon
        parse_config({**base, "digest_time": "12:30"})


def test_calls_section_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/Users/alex")  # a short name: the socket folder fits in the data folder
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}, "calls": {}})
    assert cfg.calls_enabled  # an empty [calls] turns calls on (it needs [voice])
    vendor = cfg.data_dir / "vendor"  # nothing under ~/dev or /opt/homebrew: the helpers live in the data folder
    assert cfg.call_signal_cli == vendor / "signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli"
    assert cfg.call_tunnel == vendor / "signal-call-tunnel/signal-call-tunnel/target/release/signal-call-tunnel"
    assert cfg.calls_java_home is None  # not set: found with /usr/libexec/java_home when the bridge starts
    assert cfg.call_socket_dir == cfg.data_dir / "calls"
    assert (cfg.calls_speed, cfg.calls_end_of_turn_seconds) == (1.05, 1.8)
    assert (cfg.calls_idle_prompt_seconds, cfg.calls_idle_hangup_seconds, cfg.calls_max_minutes) == (90, 600, 60)
    assert cfg.calls_summary


def test_calls_settings() -> None:
    cfg = parse_config(
        {
            "bot_number": "+306900000000",
            "allowed_acis": [ACI],
            "voice": {},
            "calls": {
                "signal_cli": "~/sc/bin/signal-cli",
                "java_home": "/opt/jdk",
                "tunnel": "~/t/sct",
                "socket_dir": "/tmp/c",
                "speed": 1.0,
                "end_of_turn_seconds": 2.5,
                "idle_prompt_seconds": 60,
                "idle_hangup_seconds": 300,
                "max_call_minutes": 30,
                "summary": False,
            },
        }
    )
    assert cfg.call_signal_cli == Path.home() / "sc/bin/signal-cli"
    assert (cfg.calls_java_home, cfg.call_tunnel) == (Path("/opt/jdk"), Path.home() / "t/sct")
    assert cfg.call_socket_dir == Path("/tmp/c")
    assert (cfg.calls_speed, cfg.calls_end_of_turn_seconds) == (1.0, 2.5)
    assert (cfg.calls_idle_prompt_seconds, cfg.calls_idle_hangup_seconds, cfg.calls_max_minutes) == (60, 300, 30)
    assert not cfg.calls_summary


LONGEST_SOCKET = len("/signal-call-18446744073709551615.sock")  # the call helper's socket name, a u64 call id


@pytest.mark.parametrize("user", ["alexexamp", "alexexample", "alexandra-example-smith"])
def test_a_long_home_folder_gets_a_short_socket_folder_for_calls(user: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a short name of 9+ characters the data folder is too long for a Unix socket path (104 bytes): an empty
    [calls] still works, with the sockets in ~/.la-calls."""
    home = f"/Users/{user}"
    monkeypatch.setenv("HOME", home)
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}, "calls": {}})
    assert cfg.call_socket_dir == Path(home) / ".la-calls"
    assert len(str(cfg.call_socket_dir)) + LONGEST_SOCKET <= 103


def test_a_home_folder_too_long_even_for_the_short_socket_folder_asks_for_socket_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/Users/" + "a" * 60)
    with pytest.raises(ConfigError, match=r"set calls\.socket_dir"):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}, "calls": {}})
    short = parse_config(
        {"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}, "calls": {"socket_dir": "/tmp/c"}}
    )
    assert short.call_socket_dir == Path("/tmp/c")  # set by hand: used as it is


def test_calls_are_off_without_the_section() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}})
    assert not cfg.calls_enabled


@pytest.mark.parametrize(
    ("calls", "message"),
    [
        ("on", r"\[calls\] must be a table"),
        ({"speed": 2}, "calls.speed"),
        ({"end_of_turn_seconds": 0.2}, "calls.end_of_turn_seconds"),
        ({"end_of_turn_seconds": 9}, "calls.end_of_turn_seconds"),
        ({"idle_prompt_seconds": 0}, "calls.idle_prompt_seconds"),
        ({"max_call_minutes": 500}, "calls.max_call_minutes"),
        ({"summary": "yes"}, "calls.summary"),
        ({"tunnel": ""}, "calls.tunnel"),
        ({"socket_dir": "/" + "x" * 80}, "calls.socket_dir is too long"),
    ],
)
def test_bad_calls_settings(calls: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}, "calls": calls})


def test_calls_need_voice() -> None:
    with pytest.raises(ConfigError, match=r"\[calls\] needs \[voice\]"):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "calls": {}})


SPOTIFY_ID = "0123456789abcdef0123456789abcdef"


def test_spotify_section() -> None:
    cfg = parse_config(
        {
            "bot_number": "+306900000000",
            "allowed_acis": [ACI],
            "spotify": {
                "client_id": SPOTIFY_ID,
                "bose": {"Kitchen speaker": "192.168.1.40", "Bedroom speaker": "192.168.1.41"},
            },
        }
    )
    assert cfg.spotify_enabled and cfg.spotify_client_id == SPOTIFY_ID
    # no default speaker: Spotify's active device (else the first one it lists)
    assert (cfg.spotify_default_device, cfg.spotify_volume_start, cfg.spotify_volume_cap) == ("", 30, 60)
    assert cfg.spotify_bose == (("Kitchen speaker", "192.168.1.40"), ("Bedroom speaker", "192.168.1.41"))
    assert cfg.spotify_token_path == cfg.data_dir / "spotify-tokens.json"


def test_spotify_default_device_is_a_speaker_name_or_empty() -> None:
    base: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI]}
    named = parse_config(base | {"spotify": {"client_id": SPOTIFY_ID, "default_device": "  Kitchen speaker "}})
    assert named.spotify_default_device == "Kitchen speaker"
    empty = parse_config(base | {"spotify": {"client_id": SPOTIFY_ID, "default_device": ""}})
    assert empty.spotify_default_device == ""


def test_spotify_is_off_without_the_section() -> None:
    assert not parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]}).spotify_enabled


@pytest.mark.parametrize(
    ("spotify", "message"),
    [
        ("on", r"\[spotify\] must be a table"),
        ({}, "spotify.client_id"),
        ({"client_id": "abc"}, "spotify.client_id"),
        ({"client_id": SPOTIFY_ID, "volume_cap": 101}, "spotify.volume_cap"),
        ({"client_id": SPOTIFY_ID, "volume_start": 70}, "spotify.volume_start"),  # above the cap
        ({"client_id": SPOTIFY_ID, "default_device": 5}, "like Kitchen speaker"),
        ({"client_id": SPOTIFY_ID, "bose": {"Kitchen speaker": "8.8.8.8"}}, "home network"),
        ({"client_id": SPOTIFY_ID, "bose": {"Kitchen speaker": "not an ip"}}, "IP address"),
    ],
)
def test_bad_spotify_settings(spotify: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "spotify": spotify})


def test_web_section() -> None:
    base: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI]}
    off = parse_config(base)
    assert not off.web_enabled and off.web_keys_path.name == "web.env"
    on = parse_config(base | {"web": {}})
    assert on.web_enabled and (on.web_country, on.web_brave_monthly_cap) == ("ALL", 2000)
    nl = parse_config(base | {"web": {"country": "nl", "brave_monthly_cap": 0}})
    assert (nl.web_country, nl.web_brave_monthly_cap) == ("NL", 0)


@pytest.mark.parametrize(
    ("web", "error"),
    [
        ("yes", r"\[web\] must be a table"),
        ({"country": "Netherlands"}, "web.country"),
        ({"brave_monthly_cap": -1}, "web.brave_monthly_cap"),
        ({"brave_monthly_cap": "900"}, "web.brave_monthly_cap"),
    ],
)
def test_bad_web_settings(web: object, error: str) -> None:
    with pytest.raises(ConfigError, match=error):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "web": web})


def test_the_soul_lives_next_to_the_config() -> None:
    cfg = parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI]})
    assert cfg.soul_path == Path.home() / ".config" / "local-assistant" / "soul.md"
    assert Config(bot_number="+306900000000", allowed_acis=frozenset({ACI})).soul_path is None  # tests: no soul


def test_maps_section() -> None:
    base: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI]}
    assert not parse_config(base).maps_enabled
    cfg = parse_config(base | {"maps": {"home": "  Home Street 1, 1000 Town  "}})
    assert cfg.maps_enabled and (cfg.maps_home, cfg.maps_daily_cap) == ("Home Street 1, 1000 Town", 30)
    assert parse_config(base | {"maps": {"daily_cap": 5}}).maps_daily_cap == 5


def test_maps_region_is_off_until_set() -> None:
    base: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI]}
    assert parse_config(base | {"maps": {}}).maps_region == ""
    assert parse_config(base | {"maps": {"region": "nl"}}).maps_region == "NL"
    assert parse_config(base | {"maps": {"region": ""}}).maps_region == ""


@pytest.mark.parametrize(
    ("maps", "error"),
    [
        ("yes", r"\[maps\] must be a table"),
        ({"home": 5}, "maps.home"),
        ({"daily_cap": 0}, "maps.daily_cap"),
        ({"region": "Netherlands"}, "maps.region"),
        ({"region": 31}, "maps.region"),
    ],
)
def test_bad_maps_settings(maps: object, error: str) -> None:
    with pytest.raises(ConfigError, match=error):
        parse_config({"bot_number": "+306900000000", "allowed_acis": [ACI], "maps": maps})


def test_the_short_okay_on_calls_is_on_unless_turned_off() -> None:
    base: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}}
    assert parse_config(base | {"calls": {}}).calls_acknowledge is True
    assert parse_config(base | {"calls": {"acknowledge": False}}).calls_acknowledge is False
    with pytest.raises(ConfigError, match=r"calls\.acknowledge"):
        parse_config(base | {"calls": {"acknowledge": "yes"}})


def test_call_fillers_and_ringing() -> None:
    base: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI], "voice": {}}
    default = parse_config(base | {"calls": {}})
    assert (default.calls_fillers, default.calls_answer_after_seconds) == (False, 5.0)
    chosen = parse_config(base | {"calls": {"fillers": True, "answer_after_seconds": 0}})
    assert (chosen.calls_fillers, chosen.calls_answer_after_seconds) == (True, 0.0)
    with pytest.raises(ConfigError, match=r"calls\.answer_after_seconds"):
        parse_config(base | {"calls": {"answer_after_seconds": 30}})


BASE: dict[str, object] = {"bot_number": "+306900000000", "allowed_acis": [ACI]}


def test_the_host_is_called_the_mac_until_named() -> None:
    assert parse_config(BASE).host_label == "the Mac"
    assert parse_config(BASE | {"host_label": "the Mini"}).host_label == "the Mini"


@pytest.mark.parametrize("label", ["", "  ", 5, "x" * 41, "the\nMini"])
def test_a_bad_host_label_is_refused(label: object) -> None:
    with pytest.raises(ConfigError, match="host_label"):
        parse_config(BASE | {"host_label": label})


def test_she_is_called_estia_until_named() -> None:
    assert parse_config(BASE).name == "Estia" == Config("+306900000000", frozenset({ACI})).name
    assert parse_config(BASE | {"name": "Tiffany"}).name == "Tiffany"
    assert parse_config(BASE | {"name": "  Mary-Jane Zoë "}).name == "Mary-Jane Zoë"  # letters of any language


@pytest.mark.parametrize("name", ["", "   ", 5, "x" * 21, "R2-D2", "Tiffany!", "<|im_start|>", "Ann\nBot", "-Ann"])
def test_a_bad_name_is_refused(name: object) -> None:
    with pytest.raises(ConfigError, match=r"name must be 1-20 letters, spaces or hyphens"):
        parse_config(BASE | {"name": name})


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
REPO_AVATAR = Path(__file__).resolve().parents[1] / "docs" / "brand" / "estia-avatar.png"


def test_the_signal_avatar_is_estias_e_until_set() -> None:
    """A new install shows the e. (the repo's avatar); a Config built in code has none."""
    assert REPO_AVATAR.is_file()
    assert parse_config(BASE).avatar == REPO_AVATAR
    assert Config("+306900000000", frozenset({ACI})).avatar is None


def test_without_the_repos_avatar_there_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "DEFAULT_AVATAR", tmp_path / "gone.png")
    assert parse_config(BASE).avatar is None


@pytest.mark.parametrize("value", ["", "  "])
def test_an_empty_avatar_turns_it_off(value: str) -> None:
    assert parse_config(BASE | {"avatar": value}).avatar is None


@pytest.mark.parametrize("data", [PNG, JPEG], ids=["png", "jpeg"])
def test_an_avatar_is_a_png_or_jpeg_path_with_tilde_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "me.img").write_bytes(data)
    assert parse_config(BASE | {"avatar": "~/me.img"}).avatar == tmp_path / "me.img"


def bad_avatar(folder: Path, kind: str) -> Path:
    """A path the bridge cannot use as the picture: missing, a folder, not an image, not readable or too large."""
    if kind == "folder":
        return folder
    path = folder / f"{kind}.png"
    if kind == "not an image":
        path.write_text("hello", encoding="utf-8")
    elif kind == "unreadable":
        path.write_bytes(PNG)
        path.chmod(0o000)
    elif kind == "too large":
        with path.open("wb") as handle:
            handle.write(PNG)
            handle.truncate(5_000_001)
    return path


BAD_AVATARS = [
    ("missing", "no such file"),
    ("folder", "not a file"),
    ("not an image", "not a PNG or JPEG file"),
    ("unreadable", "cannot be read (PermissionError)"),
    ("too large", "larger than 5 MB"),
]


@pytest.mark.parametrize("kind", [kind for kind, _ in BAD_AVATARS])
def test_the_avatar_file_is_not_read_at_load(tmp_path: Path, kind: str) -> None:
    """Only the setting's form is checked at load: a picture moved, replaced or deleted since never stops the bridge
    (the file is checked where it is used, by read_avatar: at the bridge's start and in doctor)."""
    path = bad_avatar(tmp_path, kind)
    assert parse_config(BASE | {"avatar": str(path)}).avatar == path


@pytest.mark.parametrize(("kind", "problem"), BAD_AVATARS)
def test_read_avatar_says_why_a_file_cannot_be_used_without_naming_it(tmp_path: Path, kind: str, problem: str) -> None:
    folder = tmp_path / "private-folder"
    folder.mkdir()
    with pytest.raises(AvatarError) as caught:
        read_avatar(bad_avatar(folder, kind))
    assert str(caught.value) == problem  # it goes to the log: metadata only


@pytest.mark.parametrize("data", [PNG, JPEG], ids=["png", "jpeg"])
def test_read_avatar_gives_a_png_or_jpeg_of_up_to_5_mb(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "me.img"
    path.write_bytes(data)
    assert read_avatar(path) == data
    with path.open("r+b") as handle:
        handle.truncate(5_000_000)
    assert len(read_avatar(path)) == 5_000_000


@pytest.mark.parametrize("value", [5, ["a.png"], "relative/me.png"])
def test_an_avatar_must_be_a_full_path(value: object) -> None:
    with pytest.raises(ConfigError, match="avatar"):
        parse_config(BASE | {"avatar": value})


def test_the_language_is_english_until_one_of_the_eleven_is_set() -> None:
    assert parse_config(BASE).language == "en"
    assert parse_config(BASE | {"language": "nl"}).language == "nl"


@pytest.mark.parametrize("language", ["xx", "NL", "dutch", "", 5, "el"])
def test_another_language_is_refused_with_the_list(language: object) -> None:
    with pytest.raises(ConfigError, match=r"language must be one of en, es, it, ko, pt, nl, pl, de, ja, ru, fr"):
        parse_config(BASE | {"language": language})


def test_no_extra_checks_and_only_the_own_logs_by_default() -> None:
    cfg = parse_config(BASE)
    assert cfg.health_extra_checks == ()
    assert cfg.watched_log_dirs == (Path.home() / "Library" / "Logs" / "local-assistant",)
    assert Config(bot_number="+306900000000", allowed_acis=frozenset({ACI})).health_extra_checks == ()


def test_extra_checks_and_watched_logs(tmp_path: Path) -> None:
    cfg = parse_config(
        BASE
        | {
            "health": {
                "extra_checks": [
                    {"label": "Search index", "http": "http://127.0.0.1:9100/health"},
                    {"label": "Backup job", "launchd": "system/com.example.backup", "stuck_after_minutes": 3},
                    {"label": "Printer", "http": "http://192.168.1.50/status"},
                    {"label": "Agent", "launchd": "gui/501/com.example.agent"},
                ],
                "watch_logs": ["~/Library/Logs/other", str(tmp_path)],
            }
        }
    )
    assert cfg.health_extra_checks == (
        ExtraCheck("Search index", http="http://127.0.0.1:9100/health"),
        ExtraCheck("Backup job", launchd="system/com.example.backup", stuck_after_seconds=180),
        ExtraCheck("Printer", http="http://192.168.1.50/status"),
        ExtraCheck("Agent", launchd="gui/501/com.example.agent"),
    )
    assert [check.key for check in cfg.health_extra_checks] == [
        "extra-search-index",
        "extra-backup-job",
        "extra-printer",
        "extra-agent",
    ]
    assert cfg.watched_log_dirs == (Path.home() / "Library/Logs/other", tmp_path)


def test_an_empty_watch_logs_list_watches_nothing() -> None:
    assert parse_config(BASE | {"health": {"watch_logs": []}}).watched_log_dirs == ()


GOOD_CHECK: dict[str, object] = {"label": "Search index", "http": "http://127.0.0.1:9100/health"}


@pytest.mark.parametrize(
    ("health", "message"),
    [
        ({"extra_checks": "tiny"}, "health.extra_checks"),
        ({"extra_checks": ["tiny"]}, "health.extra_checks"),
        ({"extra_checks": [{"http": "http://127.0.0.1:9100/health"}]}, "label"),
        ({"extra_checks": [{**GOOD_CHECK, "label": ""}]}, "label"),
        ({"extra_checks": [{**GOOD_CHECK, "label": "x" * 41}]}, "label"),
        ({"extra_checks": [{"label": "Nothing"}]}, "exactly one of http or launchd"),
        ({"extra_checks": [{**GOOD_CHECK, "launchd": "system/com.example.backup"}]}, "exactly one of http or launchd"),
        ({"extra_checks": [{**GOOD_CHECK, "http": "http://example.com/health"}]}, "local address"),
        ({"extra_checks": [{**GOOD_CHECK, "http": "https://127.0.0.1/health"}]}, "http://"),
        ({"extra_checks": [{**GOOD_CHECK, "http": "http://8.8.8.8/health"}]}, "local address"),
        ({"extra_checks": [{"label": "Job", "launchd": "com.example.backup"}]}, "launchd"),
        ({"extra_checks": [{"label": "Job", "launchd": "system/-x"}]}, "launchd"),
        ({"extra_checks": [{"label": "Job", "launchd": "system/a b"}]}, "launchd"),
        ({"extra_checks": [{**GOOD_CHECK, "stuck_after_minutes": 3}]}, "stuck_after_minutes"),
        ({"extra_checks": [{"label": "Job", "launchd": "system/a", "stuck_after_minutes": 0}]}, "stuck_after_minutes"),
        ({"extra_checks": [GOOD_CHECK, {**GOOD_CHECK, "label": "search INDEX"}]}, "unique"),
        ({"watch_logs": "~/Library/Logs"}, "health.watch_logs"),
        ({"watch_logs": [5]}, "health.watch_logs"),
        ({"watch_logs": [""]}, "health.watch_logs"),
        ({"watch_logs": ["logs/here"]}, "absolute"),
    ],
)
def test_bad_extra_checks_and_watched_logs(health: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(BASE | {"health": health})


def fake_java_home(tmp_path: Path, script: str) -> Path:
    tool = tmp_path / "java_home"
    tool.write_text(f"#!/bin/sh\n{script}\n")
    tool.chmod(0o755)
    return tool


def test_java_is_found_with_the_macos_tool(tmp_path: Path) -> None:
    tool = fake_java_home(tmp_path, "echo /Library/Java/JavaVirtualMachines/jdk-25.jdk/Contents/Home")
    assert find_java_home(tool) == Path("/Library/Java/JavaVirtualMachines/jdk-25.jdk/Contents/Home")


@pytest.mark.parametrize(
    "script",
    [
        "echo 'Unable to locate a Java Runtime.' >&2; exit 1",  # no Java installed
        "exit 0",  # no answer
        "echo not/absolute",
        "exec sleep 30",  # hangs
    ],
)
def test_no_java_found_is_none(tmp_path: Path, script: str) -> None:
    assert find_java_home(fake_java_home(tmp_path, script), timeout=0.5) is None


def test_a_missing_java_home_tool_is_none(tmp_path: Path) -> None:
    assert find_java_home(tmp_path / "nope") is None


def test_the_call_env_names_java_only_when_it_is_known(tmp_path: Path) -> None:
    known = call_env(java_home=Path("/jdk"), tunnel=Path("/t/sct"), socket_dir=tmp_path)
    assert known["JAVA_HOME"] == "/jdk"
    unknown = call_env(java_home=None, tunnel=Path("/t/sct"), socket_dir=tmp_path)  # the launcher finds `java` itself
    assert "JAVA_HOME" not in unknown and unknown["JAVA_OPTS"] == "-Djava.net.preferIPv4Stack=true"
    assert unknown["SIGNAL_CALL_TUNNEL_BIN"] == "/t/sct"


def test_the_calls_signal_cli_comes_from_the_data_folder_with_the_configured_java(tmp_path: Path) -> None:
    sockets = Path(tempfile.mkdtemp(prefix="c", dir="/tmp"))
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ACI}),
        data_dir=tmp_path,
        voice_url="http://127.0.0.1:9",
        calls_enabled=True,
        calls_socket_dir=sockets,
        calls_java_home=Path("/opt/jdk"),
    )
    client = signal_client(cfg)
    assert client._command[0] == str(tmp_path / "vendor/signal-cli-0.14.9-jvm/signal-cli-0.14.9/bin/signal-cli")
    assert client._env is not None and client._env["JAVA_HOME"] == "/opt/jdk"
    assert client._env["SIGNAL_CALL_TUNNEL_BIN"] == str(cfg.call_tunnel)
    assert str(cfg.call_tunnel).startswith(str(tmp_path / "vendor"))


def test_mail_prefilter_and_jev() -> None:
    base: dict[str, object] = {
        "bot_number": "+306900000000",
        "allowed_acis": [ACI],
        "mail": {
            "tenant_id": "11111111-2222-4333-8444-555555555555",
            "client_id": "66666666-7777-4888-9999-000000000000",
            "accounts": [{"name": "family", "address": "info@example.com"}],
        },
    }
    plain = parse_config(base)
    assert (plain.mail_prefilter, plain.mail_jev_url, plain.mail_jev_threshold) == (True, "", 0.9)
    mail = dict(base["mail"])  # type: ignore[call-overload]
    tuned = parse_config(
        base | {"mail": mail | {"prefilter": False, "jev_url": "http://127.0.0.1:8149", "jev_threshold": 0.85}}
    )
    assert (tuned.mail_prefilter, tuned.mail_jev_url, tuned.mail_jev_threshold) == (
        False,
        "http://127.0.0.1:8149",
        0.85,
    )
    for bad, field in (
        ({"jev_url": "https://jev.example.com"}, "mail.jev_url"),
        ({"jev_url": "http://[::1"}, "mail.jev_url"),  # unreadable: a config error, not a crash
        ({"jev_url": "http://127.0.0.1:99999"}, "mail.jev_url"),
        ({"jev_url": "http://127.0.0.1"}, "mail.jev_url"),  # the port must be named
        ({"jev_url": "http://user@127.0.0.1:8149"}, "mail.jev_url"),
        ({"jev_url": "http://127.0.0.1:8149/api?x=1"}, "mail.jev_url"),
        ({"jev_url": 8149}, "mail.jev_url"),
        ({"jev_threshold": 0.4}, "mail.jev_threshold"),
        ({"jev_threshold": True}, "mail.jev_threshold"),
    ):
        with pytest.raises(ConfigError, match=field):
            parse_config(base | {"mail": mail | bad})


def test_the_example_config_reads_as_it_is() -> None:
    """config.example.toml must stay valid: copied unchanged, it parses (its placeholders have the right form)."""
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    cfg = parse_config(tomllib.loads(example.read_text(encoding="utf-8")))
    assert cfg.bot_number == "+300000000000" and cfg.model == "local-worker"  # model unset: the alias from 2.2
    assert cfg.host_label == "the Mac" and not (cfg.mail_enabled or cfg.voice_enabled or cfg.calls_enabled)
    assert cfg.name == "Estia"


@pytest.mark.parametrize("url", ["http://192.168.1.20:11434/v1", "https://ollama.example.com/v1", "http://[::1"])
def test_the_model_server_must_be_on_this_mac(url: str) -> None:
    """Mail, calendar and profile text go to the model: never over the network."""
    with pytest.raises(ConfigError, match="ollama_base_url"):
        parse_config(BASE | {"ollama_base_url": url})
    assert parse_config(BASE | {"ollama_base_url": "http://localhost:11500/v1"}).ollama_base_url.startswith(
        "http://localhost"
    )
