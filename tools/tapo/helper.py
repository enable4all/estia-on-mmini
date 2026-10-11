"""Device helper for local-assistant: Kasa and Tapo lights and plugs on the home network, with python-kasa.

The bridge runs it with fixed arguments (no shell) and reads one JSON object from stdout; exit 0 when ok, 1 on an
error ({"ok": false, "error": "unreachable" | "login" | "unsupported" | "invalid" | "failed"}):

    helper.py status <host>
    helper.py set <host> [--power on|off] [--brightness 1-100] [--temp K] [--hue 0-359 --saturation 0-100]
    helper.py discover

Older Kasa devices need no login; newer Kasa firmware and Tapo devices need the TP-Link login (Tapo is untested).
The login is read from ~/.config/local-assistant/tapo.env (0600) and never printed. No cloud calls: discovery is a
local broadcast, control goes straight to the device's address.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import stat
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kasa import AuthenticationError, Credentials, Device, Discover, KasaException, Module, UnsupportedDeviceError

CREDENTIALS = Path.home() / ".config" / "local-assistant" / "tapo.env"
CONNECT_TIMEOUT = 5
OVERALL_TIMEOUT = 12.0  # the bridge waits 15 s
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_UNSUPPORTED = re.compile(r"Unsupported device (\S+) of type (\S+)")
_ENCRYPTION = re.compile(r"encrypt_type='(\w+)'")

Connect = Callable[[str, Credentials | None], Awaitable[Any]]
DiscoverAll = Callable[[Credentials | None], Awaitable[dict[str, Any]]]


class Refused(ValueError):
    """The helper's own refusal (its fixed text may reach Gemma); other errors are reported without their text."""


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
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
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


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="helper.py", description="Kasa and Tapo devices on the home network")
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


async def connect(host: str, credentials: Credentials | None) -> Device:
    """The device at `host`, updated (python-kasa finds out its protocol from a direct probe)."""
    dev = await Discover.discover_single(host, credentials=credentials, timeout=CONNECT_TIMEOUT, discovery_timeout=3)
    if dev is None:
        raise UnsupportedDeviceError(f"Unsupported device {host} of type unknown")
    await dev.update()
    return dev


def unsupported_entry(error: Exception) -> dict[str, str]:
    text = str(error)
    found, encryption = _UNSUPPORTED.search(text), _ENCRYPTION.search(text)
    return {
        "host": found.group(1) if found else "?",
        "type": found.group(2) if found else "?",
        "encryption": encryption.group(1) if encryption else "?",
    }


async def discover_all(credentials: Credentials | None) -> dict[str, Any]:
    """Every Kasa and Tapo device answering the local broadcast, and the ones python-kasa cannot control."""
    unsupported: list[dict[str, str]] = []

    async def on_unsupported(error: UnsupportedDeviceError) -> None:
        unsupported.append(unsupported_entry(error))

    found = await Discover.discover(credentials=credentials, discovery_timeout=5, on_unsupported=on_unsupported)
    devices = []
    for host, dev in sorted(found.items()):
        try:
            await dev.update()
            devices.append(
                {"host": host, **{k: v for k, v in describe(dev).items() if k in ("alias", "model", "kind")}}
            )
        except KasaException as exc:
            devices.append({"host": host, "error": type(exc).__name__})
        finally:
            await dev.disconnect()
    return {"ok": True, "devices": devices, "unsupported": unsupported}


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
    try:
        credentials = load_credentials(credentials_path)
    except (OSError, UnicodeDecodeError) as exc:
        return _failure("login", f"cannot read the login file ({type(exc).__name__})")
    try:
        if args.action == "discover":
            return 0, await discover(credentials)
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


def main() -> None:
    try:
        code, out = asyncio.run(asyncio.wait_for(run(sys.argv[1:]), OVERALL_TIMEOUT))
    except TimeoutError:
        code, out = _failure("unreachable", "no answer in time")
    print(json.dumps(out))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
