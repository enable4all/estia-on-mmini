"""Device helper for local-assistant: Kasa and Tapo lights and plugs on the home network, with python-kasa, and
Philips Hue lights through the Hue Bridge on the home network (hue.py, standard library only).

The bridge runs it with fixed arguments (no shell) and reads one JSON object from stdout; exit 0 when ok, 1 on an
error ({"ok": false, "error": "unreachable" | "login" | "unsupported" | "invalid" | "failed"}; for Hue also
"not_paired" | "certificate" | "key" | "no_light"). "unsupported" carries the device's login in "detail" ("AES v1",
"KLAP", "TPAP", "?" …), and "refused": true when the helper itself refused to send the login that way:

    helper.py status <host>
    helper.py set <host> [--power on|off] [--brightness 1-100] [--temp K] [--hue 0-359 --saturation 0-100]
    helper.py discover
    helper.py hue-status <light id> [<light id> ...]
    helper.py hue-set <light id> [--power on|off] [--brightness 1-100] [--temp K]
    helper.py hue-lights
    helper.py hue-check [<light id> ...]

`hue-status` reads every light it names with two requests to the bridge (a Hue Bridge is slow at new connections):
{"ok": true, "lights": {"<light id>": <the light, or its own error>}}; a problem of the bridge fails the whole run.

Older Kasa devices need no login; newer Kasa firmware and Tapo devices need the TP-Link login (Tapo is untested).
The login is read from ~/.config/local-assistant/tapo.env (0600) and never printed. It goes only to a device named in
her config (`status`, `set`), and only over KLAP (the device first proves it knows the login) or AES login v2 (a hash
of the password): never with AES login v1, which sends the password itself, and never in `discover`, which any device
on the network may answer. A Hue light is reached only
through the bridge paired by scripts/hue-pair.sh: its address, application key and certificate fingerprint are read
from ~/.config/local-assistant/hue.env (0600), and every connection checks that fingerprint before the key is sent.
No cloud calls: discovery is a local broadcast, control goes straight to the device's or the bridge's address.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import json
import re
import stat
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kasa import (
    AuthenticationError,
    Credentials,
    Device,
    DeviceConnectionParameters,
    DeviceEncryptionType,
    DeviceFamily,
    Discover,
    KasaException,
    Module,
    UnsupportedDeviceError,
)

sys.path.append(str(Path(__file__).resolve().parent))  # -I leaves this folder off sys.path; hue.py lives here
import hue

CREDENTIALS = Path.home() / ".config" / "local-assistant" / "tapo.env"
CONNECT_TIMEOUT = 5
OVERALL_TIMEOUT = 12.0  # the bridge waits 15 s
DISCOVER_TIMEOUT = 30.0  # scripts/tapo-discover.sh: 5 s of listening, then the devices read at the same time
AES_V1 = "AES v1"  # AES with login v1: the password itself goes to the device (base64)
# families python-kasa reaches over its other transports (an MD5 or a digest of the password; cameras, hubs over HTTPS,
# robot vacuums): no light or plug of hers, so the login never goes to them
_OTHER_TRANSPORTS = frozenset(
    {DeviceFamily.SmartIpCamera, DeviceFamily.SmartTapoDoorbell, DeviceFamily.SmartTapoRobovac}
)
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_UNSUPPORTED = re.compile(r"Unsupported device (\S+) of type (\S+)")
_ENCRYPTION = re.compile(r"encrypt_type='(\w+)'")

Connect = Callable[[str, Credentials | None], Awaitable[Any]]
DiscoverAll = Callable[[], Awaitable[dict[str, Any]]]


class Refused(ValueError):
    """The helper's own refusal (its fixed text may reach Gemma); other errors are reported without their text."""


class UnsafeLogin(UnsupportedDeviceError):
    """The device asks for the login in a way that would give it away (AES login v1: the password itself). `login`
    names that way (`_login_name`), for the bridge's note."""

    def __init__(self, message: str, login: str) -> None:
        super().__init__(message)
        self.login = login


@dataclass(frozen=True)
class Change:
    power: str | None = None  # "on" | "off"
    brightness: int | None = None
    temp: int | None = None
    hue: int | None = None
    saturation: int | None = None

    @property
    def lights(self) -> bool:
        return self.brightness is not None or self.temp is not None or self.hue is not None


def load_credentials(path: Path) -> Credentials | None:
    """The TP-Link login from the private file, or None when there is none (enough for older Kasa devices only)."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path} must be private (chmod 600)")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":  # one matching pair (a hand edit) only
            value = value[1:-1].strip()
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value
    username, password = values.get("TAPO_USERNAME", ""), values.get("TAPO_PASSWORD", "")
    return Credentials(username, password) if username and password else None


def _host(value: str) -> str:
    if not _HOST.match(value):
        raise argparse.ArgumentTypeError("not an address")
    return value


def _range(low: int, high: int) -> Callable[[str], int]:
    def check(value: str) -> int:
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be {low}-{high}")
        return number

    return check


def _light_id(value: str) -> str:
    if not hue.LIGHT_ID.match(value):
        raise argparse.ArgumentTypeError("not a Hue light id")
    return value


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="helper.py", description="Kasa, Tapo and Hue devices on the home network")
    actions = parser.add_subparsers(dest="action", required=True)
    status = actions.add_parser("status")
    status.add_argument("host", type=_host)
    change = actions.add_parser("set")
    change.add_argument("host", type=_host)
    change.add_argument("--power", choices=("on", "off"))
    change.add_argument("--brightness", type=_range(1, 100))
    change.add_argument("--temp", type=_range(1000, 10000))
    change.add_argument("--hue", type=_range(0, 359))
    change.add_argument("--saturation", type=_range(0, 100), default=100)
    actions.add_parser("discover")
    hue_status = actions.add_parser("hue-status")
    hue_status.add_argument("lights", nargs="+", type=_light_id)
    hue_set = actions.add_parser("hue-set")
    hue_set.add_argument("light", type=_light_id)
    hue_set.add_argument("--power", choices=("on", "off"))
    hue_set.add_argument("--brightness", type=_range(1, 100))
    hue_set.add_argument("--temp", type=_range(1000, 10000))
    actions.add_parser("hue-lights")
    hue_check = actions.add_parser("hue-check")
    hue_check.add_argument("lights", nargs="*", type=_light_id)
    return parser.parse_args(argv)


def describe(dev: Any) -> dict[str, Any]:
    """The device's state as the bridge reads it."""
    light = dev.modules.get(Module.Light)
    info: dict[str, Any] = {"ok": True, "alias": dev.alias, "model": dev.model, "kind": "light" if light else "plug"}
    info["on"] = bool(dev.is_on)
    if light is None:
        return info
    info["brightness"] = light.brightness if light.has_feature("brightness") else None
    feature = light.get_feature("color_temp") if light.has_feature("color_temp") else None
    info["temp"] = light.color_temp if feature else None
    info["temp_range"] = [feature.minimum_value, feature.maximum_value] if feature else None
    info["hsv"] = list(light.hsv) if light.has_feature("hsv") else None
    return info


async def apply(dev: Any, change: Change) -> None:
    """Switch and set the light; "off" ignores the light settings."""
    light = dev.modules.get(Module.Light)
    if change.lights and light is None:
        raise Refused("this device is a plug: only on and off")
    if change.power == "off":
        await dev.turn_off()
        return
    if change.power == "on":
        await dev.turn_on()
    if light is None:
        return
    if change.hue is not None:
        if not light.has_feature("hsv"):
            raise Refused("this light has no colours")
        value = change.brightness or light.brightness or 100
        await light.set_hsv(change.hue, change.saturation if change.saturation is not None else 100, value)
    elif change.temp is not None:
        feature = light.get_feature("color_temp") if light.has_feature("color_temp") else None
        if feature is None:
            raise Refused("this light has no white temperatures")
        temp = min(max(change.temp, feature.minimum_value), feature.maximum_value)
        await light.set_color_temp(temp, brightness=change.brightness)
    elif change.brightness is not None:
        if not light.has_feature("brightness"):
            raise Refused("this light cannot be dimmed")
        await light.set_brightness(change.brightness)


def login_allowed(connection: DeviceConnectionParameters) -> bool:
    """Whether the login may go over this connection: none is sent with XOR (older Kasa); KLAP sends only a check
    built from it, after the device proved it knows the login; AES login v2 sends a hash of the password. AES login v1
    sends the password itself (base64), and python-kasa's other transports a hash without that proof: never."""
    if connection.device_family in _OTHER_TRANSPORTS:
        return False
    if connection.encryption_type in (DeviceEncryptionType.Xor, DeviceEncryptionType.Klap):
        return True
    return connection.login_version == 2 and not connection.https


def _login_name(connection: DeviceConnectionParameters) -> str:
    if connection.encryption_type is DeviceEncryptionType.Aes and connection.login_version != 2:
        return AES_V1
    return connection.encryption_type.value + (" over HTTPS" if connection.https else "")


async def connect(host: str, credentials: Credentials | None) -> Device:
    """The device at `host` (named in her config), updated. python-kasa learns its protocol from the device's own
    answer to a direct probe; nothing goes to it before `update()`, so the login goes only over a connection
    `login_allowed` accepts (UnsafeLogin otherwise)."""
    dev = await Discover.discover_single(host, credentials=credentials, timeout=CONNECT_TIMEOUT, discovery_timeout=3)
    if dev is None:
        raise UnsupportedDeviceError(f"Unsupported device {host} of type unknown")
    connection = dev.config.connection_type
    if not login_allowed(connection):
        await dev.disconnect()
        login = _login_name(connection)
        raise UnsafeLogin(f"Unsupported device {host} of type {connection.device_family.value}: {login}", login)
    await dev.update()
    return dev


def unsupported_entry(error: Exception) -> dict[str, str]:
    text = str(error)
    found, encryption = _UNSUPPORTED.search(text), _ENCRYPTION.search(text)
    return {
        "host": hue._printable(found.group(1)) if found else "?",
        "type": hue._printable(found.group(2)) if found else "?",
        "encryption": encryption.group(1) if encryption else "?",
    }


async def discover_all() -> dict[str, Any]:
    """Every Kasa and Tapo device answering the local broadcast, and the ones python-kasa cannot control, without the
    login: anything on the network may answer a broadcast. A device that needs no login (older Kasa) is read, all at
    once; one that needs it is listed by its address, model and login (its name is the one in the app); one that would
    get the login unsafely (AES login v1) is listed as not controllable."""
    unsupported: list[dict[str, str]] = []

    async def on_unsupported(error: UnsupportedDeviceError) -> None:
        unsupported.append(unsupported_entry(error))

    found = await Discover.discover(discovery_timeout=5, on_unsupported=on_unsupported)
    read: dict[str, Any] = {}
    listed: dict[str, dict[str, Any]] = {}
    for host, dev in found.items():
        connection = dev.config.connection_type
        if connection.encryption_type is DeviceEncryptionType.Xor:
            read[host] = dev
        elif login_allowed(connection):  # what a device says is printed: never a control character (an escape)
            listed[host] = {"host": host, "model": hue._printable(dev.model), "login": _login_name(connection)}
        else:
            unsupported.append(
                {"host": host, "type": connection.device_family.value, "encryption": _login_name(connection)}
            )

    async def described(host: str, dev: Any) -> dict[str, Any]:
        try:
            await asyncio.wait_for(dev.update(), CONNECT_TIMEOUT)
            shown = {key: value for key, value in describe(dev).items() if key in ("alias", "model", "kind")}
            return {"host": host, **{key: hue._printable(value) for key, value in shown.items()}}
        except (KasaException, TimeoutError, OSError) as exc:
            return {"host": host, "error": type(exc).__name__}

    answers = await asyncio.gather(*(described(host, dev) for host, dev in read.items()))
    for dev in found.values():
        with contextlib.suppress(KasaException, OSError):
            await dev.disconnect()
    devices = sorted([*answers, *listed.values()], key=lambda item: ("error" in item, _host_key(item["host"])))
    return {"ok": True, "devices": devices, "unsupported": unsupported}


def _host_key(host: str) -> tuple[int, bytes]:
    """Addresses in their numeric order (192.168.1.9 before 192.168.1.10), IPv4 first."""
    try:
        packed = ipaddress.ip_address(host).packed
    except ValueError:
        return 99, host.encode()
    return len(packed), packed


def _failure(kind: str, detail: str = "") -> tuple[int, dict[str, Any]]:
    return 1, {"ok": False, "error": kind, "detail": detail}


async def run(
    argv: list[str],
    *,
    connect: Connect = connect,
    discover: DiscoverAll = discover_all,
    credentials_path: Path = CREDENTIALS,
) -> tuple[int, dict[str, Any]]:
    """One action; every outcome is one JSON-ready object (never the login)."""
    args = parse(argv)
    if args.action == "discover":  # no login: anything on the network may answer the broadcast
        try:
            return 0, await discover()
        except (TimeoutError, OSError):
            return _failure("unreachable")
        except KasaException as exc:
            return _failure("failed", type(exc).__name__)
    try:
        credentials = load_credentials(credentials_path)
    except (OSError, UnicodeDecodeError) as exc:
        return _failure("login", f"cannot read the login file ({type(exc).__name__})")
    try:
        dev = await connect(args.host, credentials)
        try:
            if args.action == "set":
                change = Change(args.power, args.brightness, args.temp, args.hue, args.saturation)
                await apply(dev, change)
                await dev.update()
            return 0, describe(dev)
        finally:
            await dev.disconnect()
    except AuthenticationError:
        return _failure("login", "the TP-Link login was refused")
    except UnsafeLogin as exc:  # refused by us, never sent: marked so the bridge never blames the library
        return 1, {"ok": False, "error": "unsupported", "detail": exc.login, "refused": True}
    except UnsupportedDeviceError as exc:
        return _failure("unsupported", unsupported_entry(exc)["encryption"])
    except Refused as exc:
        return _failure("invalid", str(exc))
    except ValueError as exc:  # from python-kasa or the device: its text is not passed on
        return _failure("failed", type(exc).__name__)
    except (TimeoutError, OSError):
        return _failure("unreachable")
    except KasaException as exc:
        return _failure("failed", type(exc).__name__)


def run_hue(argv: list[str], *, hue_file: Path = hue.HUE_FILE, open: hue.Open = hue.tls) -> tuple[int, dict[str, Any]]:
    """One Hue action through the paired bridge (blocking, each request within the helper's time); every outcome is
    one JSON-ready object (never the key, never the bridge's text)."""
    args = parse(argv)
    try:
        bridge = hue.Bridge(hue.load(hue_file), open=open, seconds=OVERALL_TIMEOUT)
        if args.action == "hue-lights":
            return 0, {"ok": True, "lights": hue.lights(bridge)}
        if args.action == "hue-check":
            return 0, hue.check(bridge, args.lights)
        if args.action == "hue-set":
            return 0, hue.apply(bridge, args.light, power=args.power, brightness=args.brightness, temp=args.temp)
        return 0, {"ok": True, "lights": hue.statuses(bridge, args.lights)}
    except hue.HueError as exc:
        return _failure(exc.code, exc.detail)
    except hue.Refused as exc:
        return _failure("invalid", str(exc))
    except (TypeError, KeyError, AttributeError, ValueError) as exc:  # an answer of a shape not foreseen: its name only
        return _failure("failed", type(exc).__name__)


def main() -> None:
    if sys.argv[1:2] and sys.argv[1].startswith("hue-"):
        code, out = run_hue(sys.argv[1:])
        print(json.dumps(out))
        raise SystemExit(code)
    limit = DISCOVER_TIMEOUT if sys.argv[1:2] == ["discover"] else OVERALL_TIMEOUT
    try:
        code, out = asyncio.run(asyncio.wait_for(run(sys.argv[1:]), limit))
    except TimeoutError:
        code, out = _failure("unreachable", "no answer in time")
    print(json.dumps(out))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
