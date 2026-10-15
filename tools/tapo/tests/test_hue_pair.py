"""Pairing the Hue Bridge: found by mDNS (macOS's dns-sd, its output format as printed on macOS 27) or given, its certificate trusted on first
use and shown, the key asked for once the button is pressed, everything saved in hue.env. Never Philips' cloud."""

import tomllib
from pathlib import Path
from typing import Any

import hue
import pytest

from tests.test_hue import CLIENT_KEY, KEY, LIGHT, PIN, PLUG, FakeBridge, FakeChannel

# `dns-sd` output in the format macOS 27 printed for another service on the home network (no Hue Bridge was at hand),
# with a Hue Bridge's service type, neutral names and addresses
BROWSE = """Browsing for _hue._tcp.local.
DATE: ---Wed 14 Oct 2026---
15:04:16.232  ...STARTING...
Timestamp     A/R    Flags  if Domain               Service Type         Instance Name
15:04:16.436  Add        3   7 local.               _hue._tcp.           Hue Bridge - 1A2B3C
15:04:16.436  Add        2  16 local.               _hue._tcp.           Hue Bridge - 1A2B3C
"""
LOOKUP = """Lookup Hue Bridge - 1A2B3C._hue._tcp.local.
DATE: ---Wed 14 Oct 2026---
15:04:30.710  ...STARTING...
15:04:30.710  Hue\\032Bridge\\032-\\0321A2B3C._hue._tcp.local. can be reached at ecb5fafffe1a2b3c.local.:443 (interface 7) Flags: 1
 bridgeid=ecb5fafffe1a2b3c modelid=BSB002
"""
ADDRESS = """DATE: ---Wed 14 Oct 2026---
15:04:32.721  ...STARTING...
Timestamp     A/R  Flags         IF  Hostname                               Address                                      TTL
15:04:32.721  Add  40000003       7  ecb5fafffe1a2b3c.local.                192.168.1.40                                 120
15:04:32.721  Add  40000002      16  ecb5fafffe1a2b3c.local.                192.168.1.40                                 120
"""


class DnsSd:
    """macOS's dns-sd, canned: what each run printed before its time limit stopped it."""

    def __init__(self, browse: str = BROWSE, lookup: str = LOOKUP, address: str = ADDRESS) -> None:
        self.outputs = {"-B": browse, "-L": lookup, "-G": address}
        self.runs: list[tuple[list[str], float]] = []

    def __call__(self, argv: list[str], seconds: float) -> str:
        self.runs.append((argv, seconds))
        return self.outputs[argv[1]]


def test_the_bridge_is_found_by_mdns_only() -> None:
    dns_sd = DnsSd()
    assert hue.find_bridges(dns_sd) == ["192.168.1.40"]
    assert [argv for argv, _ in dns_sd.runs] == [
        ["/usr/bin/dns-sd", "-B", "_hue._tcp", "local."],
        ["/usr/bin/dns-sd", "-L", "Hue Bridge - 1A2B3C", "_hue._tcp", "local."],
        ["/usr/bin/dns-sd", "-G", "v4", "ecb5fafffe1a2b3c.local."],
    ]
    assert all(0 < seconds <= 5 for _, seconds in dns_sd.runs)  # dns-sd never stops on its own


def test_no_bridge_or_an_address_off_the_home_network_is_none() -> None:
    assert hue.find_bridges(DnsSd(browse="Browsing for _hue._tcp.local.\n")) == []
    public = ADDRESS.replace("192.168.1.40", "203.0.113.9")
    assert hue.find_bridges(DnsSd(address=public)) == []
    assert hue.find_bridges(DnsSd(lookup="no answer\n")) == []


def test_two_bridges_are_both_named() -> None:
    second = "15:04:16.500  Add        2   7 local.               _hue._tcp.           Hue Bridge - 4D5E6F\n"

    def dns_sd(argv: list[str], seconds: float) -> str:
        if argv[1] == "-B":
            return BROWSE + second
        if argv[1] == "-L":
            return LOOKUP.replace("1a2b3c", "4d5e6f") if argv[2].endswith("4D5E6F") else LOOKUP
        return ADDRESS.replace("192.168.1.40", "192.168.1.41") if "4d5e6f" in argv[3] else ADDRESS

    assert hue.find_bridges(dns_sd) == ["192.168.1.40", "192.168.1.41"]


class Said:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def pair(bridge: FakeBridge, path: Path, address: str | None = "192.168.1.40", **kwargs: Any) -> tuple[int, Said]:
    said, clock = Said(), Clock()
    find = kwargs.pop("find", lambda: ["192.168.1.40"])
    opener = kwargs.pop("open", bridge.open)
    code = hue.pair(address, path=path, open=opener, find=find, say=said, sleep=clock.sleep, clock=clock)
    return code, said


def test_pairing_shows_the_fingerprint_waits_for_the_button_and_saves(tmp_path: Path) -> None:
    bridge = FakeBridge()
    bridge.presses_needed = 2
    path = tmp_path / "config" / "hue.env"
    code, said = pair(bridge, path)
    assert code == 0
    assert hue.load(path) == hue.Pairing("192.168.1.40", KEY, PIN)
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert hue.shown(PIN) in said.text and "trust on first use" in said.text
    assert "Press the round button" in said.text
    assert KEY not in said.text and CLIENT_KEY not in said.text  # the key and the client key never shown
    assert CLIENT_KEY not in path.read_text()  # the client key (entertainment streaming) is not kept
    posts = [(headers, body) for method, path_, headers, body in bridge.seen if method == "POST"]
    assert len(posts) == 3
    for headers, body in posts:
        assert body == {"devicetype": "estia#mac", "generateclientkey": True}
        assert "hue-application-key" not in headers
    # the lights, as lines to add to the config
    assert f'light = "{LIGHT}"' in said.text and PLUG in said.text and 'brand = "hue"' in said.text


def test_the_button_not_pressed_in_time_saves_nothing(tmp_path: Path) -> None:
    bridge = FakeBridge()
    bridge.presses_needed = 10_000
    path = tmp_path / "hue.env"
    code, said = pair(bridge, path)
    assert code == 1 and not path.exists() and "nothing saved" in said.text


def test_a_certificate_that_changes_during_pairing_is_refused(tmp_path: Path) -> None:
    bridge = FakeBridge()
    bridge.presses_needed = 1
    original = bridge.open

    def open_then_switch(address: str, timeout: float) -> object:
        channel = original(address, timeout)
        bridge.certificate = b"a machine in between"  # the first contact saw the bridge's; the next sees another
        return channel

    path = tmp_path / "hue.env"
    code, said = pair(bridge, path, open=open_then_switch)
    assert code == 1 and not path.exists()
    assert [method for method, *_ in bridge.seen] == []  # the request was never sent to the other certificate
    assert "certificate" in said.text


def test_an_address_off_the_home_network_is_never_contacted(tmp_path: Path) -> None:
    bridge = FakeBridge()
    code, said = pair(bridge, tmp_path / "hue.env", address="203.0.113.9")
    assert code == 1 and bridge.connections == 0 and "home network" in said.text


def test_without_an_address_mdns_finds_it(tmp_path: Path) -> None:
    bridge = FakeBridge()
    code, said = pair(bridge, tmp_path / "hue.env", address=None)
    assert code == 0 and "192.168.1.40" in said.text


@pytest.mark.parametrize(("found", "words"), [([], "No Hue Bridge"), (["192.168.1.40", "192.168.1.41"], "Several")])
def test_none_or_several_found_asks_for_the_address(tmp_path: Path, found: list[str], words: str) -> None:
    bridge = FakeBridge()
    code, said = pair(bridge, tmp_path / "hue.env", address=None, find=lambda: found)
    assert code == 1 and bridge.connections == 0 and words in said.text
    assert "scripts/hue-pair.sh <address>" in said.text


def test_pairing_again_says_when_the_certificate_differs(tmp_path: Path) -> None:
    path = tmp_path / "hue.env"
    hue.save(path, hue.Pairing("192.168.1.40", KEY, "0" * 64))
    code, said = pair(FakeBridge(), path)
    assert code == 0 and "differs from the one saved at the last pairing" in said.text
    assert hue.load(path).fingerprint == PIN


def test_an_unreachable_bridge_saves_nothing(tmp_path: Path) -> None:
    bridge = FakeBridge()
    bridge.down = True
    path = tmp_path / "hue.env"
    code, said = pair(bridge, path)
    assert code == 1 and not path.exists() and "No answer from 192.168.1.40" in said.text


class Answering(FakeChannel):
    """Something on the home network that answers HTTPS, but not as a Hue Bridge (a router's page, a NAS)."""

    reply: tuple[int, bytes] = (404, b"<html><body>Not Found</body></html>")

    def exchange(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        self.bridge.seen.append((method, path, headers, body))
        return self.reply


@pytest.mark.parametrize(
    "reply",
    [
        (404, b"<html><body>Not Found</body></html>"),
        (200, b'{"error": "unknown path"}'),
        (200, b"[{}]"),
    ],
)
def test_an_address_that_is_not_a_hue_bridge_stops_at_once(tmp_path: Path, reply: tuple[int, bytes]) -> None:
    bridge = FakeBridge()

    class Other(Answering):
        pass

    Other.reply = reply
    path = tmp_path / "hue.env"
    said, clock = Said(), Clock()
    code = hue.pair(
        "192.168.1.1",
        path=path,
        open=lambda address, timeout: Other(bridge),
        find=list,
        say=said,
        sleep=clock.sleep,
        clock=clock,
    )
    assert code == 1 and not path.exists()
    assert "192.168.1.1 answers, but not as a Hue Bridge: nothing saved." in said.text
    assert "button was not pressed" not in said.text
    assert len(bridge.seen) == 1 and clock.now == 0  # asked once, no waiting


def test_a_bridge_that_stops_answering_is_named_not_the_button(tmp_path: Path) -> None:
    bridge = FakeBridge()
    original = bridge.open

    def answers_once(address: str, timeout: float) -> FakeChannel:
        channel = original(address, timeout)
        bridge.down = True  # the certificate was read; then the bridge is gone (unplugged during pairing)
        return channel

    path = tmp_path / "hue.env"
    code, said = pair(bridge, path, open=answers_once)
    assert code == 1 and not path.exists()
    assert "No answer from the bridge at 192.168.1.40: nothing saved." in said.text
    assert "button was not pressed" not in said.text


def test_a_run_is_stopped_at_its_time_limit() -> None:
    assert hue.run_for(["/bin/echo", "found"], 5) == "found\n"
    assert hue.run_for(["/bin/sleep", "30"], 0.2) == ""  # stopped, not waited for
    assert hue.run_for(["/nonexistent/dns-sd"], 1) == ""


def test_the_command_line_checks_the_address_before_any_contact(capsys: pytest.CaptureFixture[str]) -> None:
    assert hue.main(["pair", "203.0.113.9"]) == 1
    assert "home network" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        hue.main(["pair", "192.168.1.40", "extra"])


def test_the_lights_are_listed_for_the_config_without_pairing_again(tmp_path: Path) -> None:
    path = tmp_path / "hue.env"
    hue.save(path, hue.Pairing("192.168.1.40", KEY, PIN))
    bridge, said = FakeBridge(), Said()
    assert hue.show_lights(path=path, open=bridge.open, say=said) == 0
    assert f'light = "{LIGHT}"' in said.text and PLUG in said.text and KEY not in said.text
    assert [method for method, *_ in bridge.seen] == ["GET"]  # read only


@pytest.mark.parametrize(
    ("setup", "words"),
    [
        ("unpaired", "not paired yet: scripts/hue-pair.sh"),
        ("certificate", "certificate is not the one saved at pairing: nothing was sent"),
        ("down", "no answer at 192.168.1.40"),
        ("key", "refused its key: scripts/hue-pair.sh"),
    ],
)
def test_listing_the_lights_names_a_bridge_problem(tmp_path: Path, setup: str, words: str) -> None:
    path = tmp_path / "hue.env"
    if setup != "unpaired":
        hue.save(path, hue.Pairing("192.168.1.40", KEY, PIN))
    bridge, said = FakeBridge(), Said()
    bridge.certificate = b"another" if setup == "certificate" else bridge.certificate
    bridge.down = setup == "down"
    bridge.key = "another key" if setup == "key" else KEY
    assert hue.show_lights(path=path, open=bridge.open, say=said) == 1
    assert words in said.text


def test_the_printed_example_is_valid_toml_whatever_the_lights_name() -> None:
    """A light named in the Hue app with quotes or a backslash: the example pasted into her config still parses (a
    config that does not parse stops her bridge)."""
    name = 'Kid\'s "night" lamp \\ 2'
    lines = hue._config_lines([{"name": name, "kind": "light", "id": LIGHT}])
    example = "\n".join(line.strip() for line in lines[lines.index("For example:") + 1 :])
    assert tomllib.loads(example)["tapo"]["devices"][0] == {
        "name": name,
        "brand": "hue",
        "light": LIGHT,
        "kind": "light",
        "rule": "normal",
    }
