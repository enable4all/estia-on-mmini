"""The Philips Hue Bridge on the home network, for the device helper: standard library only, CLIP API v2.

Only the bridge's own address on the home network is ever reached, never Philips' cloud (no discovery.meethue.com,
no remote API): the address is found by mDNS or given at pairing, and must be a private address. Every connection
checks the bridge's certificate against the SHA-256 fingerprint saved at pairing (trust on first use) before anything
is sent, and refuses on a mismatch, so the application key never reaches another machine. The bridge's address, the
key and the fingerprint live in hue.env (0600), read only by the device helper; the key goes only into the
`hue-application-key` header, never into arguments, output or logs.

Errors are fixed codes (`HueError.code`): not_paired, unreachable, certificate, key, no_light, failed; a change a
light cannot make is `Refused` with a fixed text. The bridge's own text never comes out.

Pairing (`python -I hue.py pair [address]`, run by scripts/hue-pair.sh): without an address the bridge is found by
mDNS with macOS's own dns-sd, each run stopped after a few seconds; the first connection's certificate is trusted and
shown; once the bridge's button is pressed, it gives an application key (POST /api with generateclientkey; the client
key for entertainment streaming is not kept), and hue.env is written. The key is never shown.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
import re
import ssl
import stat
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

HUE_FILE = Path.home() / ".config" / "local-assistant" / "hue.env"
PORT = 443
REQUEST_SECONDS = 4.0  # each connection and read; the helper's whole run has its own limit
MAX_ANSWER = 1_000_000  # bytes read from one answer
LIGHT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")  # a CLIP v2 resource id
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# private unicast ranges only (RFC 1918, IPv6 unique local), as config.py's rule for [[tapo.devices]] hosts
_HOME_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_LIGHTS = "/clip/v2/resource/light"
_LINKS = "/clip/v2/resource/zigbee_connectivity"  # each device's Zigbee link to the bridge: "connected" or not
SETTLE_SECONDS = 0.4  # a change read back before the light confirmed it is read once more after this
DNS_SD = "/usr/bin/dns-sd"  # macOS's own mDNS tool; it never stops on its own, so every run has a time limit
BROWSE_SECONDS = 4.0
LOOKUP_SECONDS = 3.0
MAX_BRIDGES = 4
BUTTON_SECONDS = 60.0  # how long pairing waits for the button
POLL_SECONDS = 2.0
DEVICE_TYPE = "estia#mac"  # how the bridge lists this key (the Hue app shows it under the bridge's apps)
_BROWSED = re.compile(r"^\S+\s+Add\s+\d+\s+\d+\s+local\.\s+_hue\._tcp\.\s+(.+?)\s*$")
_REACHED = re.compile(r"can be reached at ([A-Za-z0-9-]{1,63}\.local)\.?:\d+")
_ADDED = re.compile(r"^\S+\s+Add\s+\S+\s+\d+\s+\S+\s+(\S+)\s+\d+\s*$")


class HueError(Exception):
    """A fixed code (and a fixed detail); never the bridge's text, never the key."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class Refused(ValueError):
    """The helper's own refusal: a change this light cannot make (its fixed text may reach Gemma)."""


@dataclass(frozen=True)
class Pairing:
    bridge: str  # its address on the home network
    key: str = field(repr=False)  # the application key: never printed, not even by mistake
    fingerprint: str  # SHA-256 of its certificate, lower-case hex


class Channel(Protocol):
    """One connection to the bridge: its certificate first, then one exchange."""

    def certificate(self) -> bytes: ...

    def exchange(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]: ...

    def close(self) -> None: ...


Open = Callable[[str, float], Channel]


def home_address(address: str) -> bool:
    """Whether `address` is an IP address on the home network (never a name, never a public address)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in _HOME_NETWORKS if ip.version == network.version)


def fingerprint(certificate: bytes) -> str:
    return hashlib.sha256(certificate).hexdigest()


def shown(pin: str) -> str:
    """The fingerprint as people compare it: "AB:CD:…"."""
    return ":".join(pin[i : i + 2] for i in range(0, len(pin), 2)).upper()


class _Tls:
    """A TLS connection to the bridge. The certificate is not checked by a CA here: the pinned fingerprint is the
    check, made by `Bridge` before anything is sent (the bridge's certificate is its own, for its own id)."""

    def __init__(self, address: str, timeout: float, port: int = PORT) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # http.client reads no proxy settings; the port is given, so an IPv6 address is taken whole
        self._conn = http.client.HTTPSConnection(address, port, timeout=timeout, context=context)
        self._conn.connect()
        # never a connection of its own: http.client would reconnect without the fingerprint check and send the key
        self._conn.auto_open = 0

    def certificate(self) -> bytes:
        sock = self._conn.sock
        der = sock.getpeercert(binary_form=True) if isinstance(sock, ssl.SSLSocket) else None
        if not der:
            raise HueError("certificate", "the bridge showed no certificate")
        return der

    def exchange(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        self._conn.request(method, path, body=body, headers=headers)
        response = self._conn.getresponse()
        return response.status, response.read(MAX_ANSWER)

    def close(self) -> None:
        self._conn.close()


def tls(address: str, timeout: float) -> Channel:
    return _Tls(address, timeout)


class Bridge:
    """The paired bridge: every call opens a connection, checks the pinned certificate, then sends."""

    def __init__(self, pairing: Pairing, *, open: Open = tls, seconds: float = 12.0) -> None:
        self.pairing = pairing
        self._open = open
        self._deadline = time.monotonic() + seconds

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        """One request with the key; (HTTP status, JSON). HueError when it never got a usable answer."""
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise HueError("unreachable", "no answer in time")
        headers = {"Accept": "application/json", "hue-application-key": self.pairing.key}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode()
        request = (method, path, headers, payload)
        return exchange(self._open, self.pairing.bridge, self.pairing.fingerprint, min(REQUEST_SECONDS, left), request)

    def left(self) -> float:
        """Seconds until the run's deadline."""
        return self._deadline - time.monotonic()


def exchange(
    open: Open, address: str, pin: str, timeout: float, request: tuple[str, str, dict[str, str], bytes | None]
) -> tuple[int, Any]:
    """Connect, compare the certificate with `pin` (nothing is sent on a mismatch), send one request, read JSON."""
    try:
        channel = open(address, timeout)
    except (OSError, ssl.SSLError) as exc:
        raise HueError("unreachable") from exc
    try:
        if not hmac.compare_digest(fingerprint(channel.certificate()), pin):
            raise HueError("certificate", "the bridge's certificate is not the one saved at pairing")
        status, raw = channel.exchange(*request)
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        raise HueError("unreachable") from exc
    finally:
        with contextlib.suppress(OSError):
            channel.close()
    try:
        return status, json.loads(raw)
    except (ValueError, RecursionError) as exc:  # not JSON, or nested past Python's limit
        raise HueError("failed", "not a CLIP answer") from exc


def _data(status: int, answer: Any, *, light: bool = True) -> list[dict[str, Any]]:
    """The answer's data list; the status as a fixed code otherwise."""
    if status in (401, 403):
        raise HueError("key", "the bridge refused the key")
    if status == 404 and light:
        raise HueError("no_light", "the bridge has no light with that id")
    if status == 429 or status >= 500:
        raise HueError("failed", "the bridge is busy")
    data = answer.get("data") if isinstance(answer, dict) else None
    if not 200 <= status < 300 or not isinstance(data, list):
        raise HueError("failed", f"HTTP {status}")
    return [item for item in data if isinstance(item, dict)]


def _printable(text: Any) -> str:
    """A name from the bridge, without control characters (it may be printed in a terminal)."""
    return "".join(ch for ch in str(text) if ch.isprintable())[:60]


def _number(value: Any) -> float | None:
    """A finite number from the bridge's JSON (which may also hold Infinity, NaN or 1e999), never a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _kelvin(mirek: Any) -> int | None:
    number = _number(mirek)
    return round(1_000_000 / number) if number is not None and number > 0 else None


def _dims(light: dict[str, Any]) -> bool:
    """Whether the light can be dimmed (a Hue smart plug has no dimming: on and off only)."""
    return isinstance(light.get("dimming"), dict)


def describe(light: dict[str, Any]) -> dict[str, Any]:
    """A light as the device helper describes a Kasa device (the bridge reads the same keys); a light without
    dimming (a Hue smart plug) is a plug."""
    on = light.get("on")
    if not isinstance(on, dict) or not isinstance(on.get("on"), bool):
        raise HueError("failed", "not a CLIP light")
    metadata = light.get("metadata")
    name = _printable(metadata.get("name", "")) if isinstance(metadata, dict) else ""
    dimming = light.get("dimming")
    info: dict[str, Any] = {"ok": True, "alias": name, "model": "Hue", "kind": "light" if _dims(light) else "plug"}
    info["on"] = on["on"]
    if not isinstance(dimming, dict):
        return info
    level = _number(dimming.get("brightness"))
    info["brightness"] = min(100, max(1, round(level))) if level is not None else None
    white = light.get("color_temperature")
    white = white if isinstance(white, dict) else {}
    info["temp"] = _kelvin(white.get("mirek")) if white.get("mirek_valid") is True else None
    schema = white.get("mirek_schema")
    low, high = (schema.get("mirek_maximum"), schema.get("mirek_minimum")) if isinstance(schema, dict) else (0, 0)
    kelvins = (_kelvin(low), _kelvin(high))
    info["temp_range"] = list(kelvins) if None not in kelvins else None
    info["hsv"] = None  # colours are not offered for Hue lights
    return info


def _light(bridge: Bridge, light_id: str) -> dict[str, Any]:
    if not LIGHT_ID.match(light_id):
        raise Refused("not a Hue light id")
    found = _data(*bridge.call("GET", f"{_LIGHTS}/{light_id}"))
    if not found:
        raise HueError("no_light", "the bridge has no light with that id")
    return found[0]


def _links(bridge: Bridge) -> dict[str, str]:
    """Each device's Zigbee link as the bridge sees it ("connected", "disconnected", "connectivity_issue", ...), by
    the device's id; empty when the bridge's answer cannot be read (the lights are then read as before)."""
    try:
        found = _data(*bridge.call("GET", _LINKS), light=False)
    except HueError as exc:
        if exc.code == "failed":
            return {}
        raise
    links: dict[str, str] = {}
    for link in found:
        owner, state = link.get("owner"), link.get("status")
        device = owner.get("rid") if isinstance(owner, dict) else None
        if isinstance(device, str) and isinstance(state, str):
            links[device] = state
    return links


def _reached(light: dict[str, Any], links: dict[str, str]) -> None:
    """A light the bridge cannot reach (switched off at the wall) is unreachable: the bridge answers a read of it with
    its last known state, and only its device's Zigbee link says that state is not current."""
    owner = light.get("owner")
    device = owner.get("rid") if isinstance(owner, dict) else None
    state = links.get(device) if isinstance(device, str) else None
    if state is not None and state != "connected":
        raise HueError("unreachable", "the light did not answer the bridge")


def statuses(bridge: Bridge, light_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Each light's status from two requests whatever their number (every light, then every Zigbee link): a Hue
    Bridge is slow at new connections. A light's own problem is its entry's (`no_light`, `unreachable`, `failed`); a
    problem of the bridge (unreachable, key, certificate) is raised for all."""
    have = _every_light(bridge)
    links = _links(bridge)
    out: dict[str, dict[str, Any]] = {}
    for light_id in light_ids:
        try:
            if light_id not in have:
                raise HueError("no_light", "the bridge has no light with that id")
            _reached(have[light_id], links)
            out[light_id] = describe(have[light_id])
        except HueError as exc:
            out[light_id] = {"ok": False, "error": exc.code, "detail": exc.detail}
    return out


def status(bridge: Bridge, light_id: str) -> dict[str, Any]:
    """One light's status (`statuses`); its own problem raised as a HueError."""
    answer = statuses(bridge, [light_id])[light_id]
    if not answer.get("ok"):
        raise HueError(str(answer.get("error")), str(answer.get("detail", "")))
    return answer


def _settled(light: dict[str, Any], body: dict[str, Any]) -> bool:
    """Whether the light as read back shows the change just sent (the bridge may show it only once the light has
    confirmed it over Zigbee)."""
    if "on" in body:
        on = light.get("on")
        if not isinstance(on, dict) or on.get("on") is not body["on"]["on"]:
            return False
    for part, key, sent in (
        ("dimming", "brightness", body.get("dimming", {}).get("brightness")),
        ("color_temperature", "mirek", body.get("color_temperature", {}).get("mirek")),
    ):
        if sent is None:
            continue
        values = light.get(part)
        value = _number(values.get(key)) if isinstance(values, dict) else None
        if value is None or abs(value - sent) > 1:
            return False
    return True


def _as_sent(light: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """The light with the values the bridge took without an error, for a read-back that still shows the old ones."""
    shown = dict(light)
    if "on" in body:
        shown["on"] = dict(body["on"])
    if "dimming" in body and isinstance(light.get("dimming"), dict):
        shown["dimming"] = light["dimming"] | body["dimming"]
    if "color_temperature" in body and isinstance(light.get("color_temperature"), dict):
        shown["color_temperature"] = light["color_temperature"] | body["color_temperature"] | {"mirek_valid": True}
    return shown


def apply(
    bridge: Bridge,
    light_id: str,
    *,
    power: str | None = None,
    brightness: int | None = None,
    temp: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Switch and set the light (one request); "off" ignores the light settings. Then its state as read back: read
    once more after `SETTLE_SECONDS` when it does not show the change yet, and, still not, the values the bridge took
    without an error (untested on a real bridge whether it shows a change at once)."""
    light = _light(bridge, light_id)
    dimming = light.get("dimming")
    white = light.get("color_temperature")
    body: dict[str, Any] = {}
    if power is not None:
        body["on"] = {"on": power == "on"}
    if power != "off":
        if (brightness is not None or temp is not None) and not isinstance(dimming, dict):
            raise Refused("this device is a plug: only on and off")
        if brightness is not None:
            body["dimming"] = {"brightness": float(brightness)}
        if temp is not None:
            schema = white.get("mirek_schema") if isinstance(white, dict) else None
            if not isinstance(schema, dict):
                raise Refused("this light has no white temperatures")
            low, high = schema.get("mirek_minimum"), schema.get("mirek_maximum")
            if not isinstance(low, int) or not isinstance(high, int):
                raise Refused("this light has no white temperatures")
            body["color_temperature"] = {"mirek": min(max(round(1_000_000 / temp), low), high)}
    if not body:
        raise Refused("nothing to change")
    status_code, answer = bridge.call("PUT", f"{_LIGHTS}/{light_id}", body)
    _data(status_code, answer)
    errors = answer.get("errors") if isinstance(answer, dict) else None
    if isinstance(errors, list) and errors:
        said = " ".join(str(error.get("description", "")) for error in errors if isinstance(error, dict))
        if "communication issues" in said:  # the bridge cannot reach the light (switched off at the wall)
            raise HueError("unreachable", "the light did not answer the bridge")
        raise HueError("failed", "the bridge did not make the change")
    if errors not in (None, []):  # not CLIP's list: no sign the change was made
        raise HueError("failed", "the bridge did not make the change")
    after = _light(bridge, light_id)
    if not _settled(after, body) and bridge.left() > SETTLE_SECONDS + 1:
        sleep(SETTLE_SECONDS)
        after = _light(bridge, light_id)
    return describe(after if _settled(after, body) else _as_sent(after, body))


def _every_light(bridge: Bridge) -> dict[str, dict[str, Any]]:
    """Every light on the bridge with a CLIP v2 id, by id."""
    found: dict[str, dict[str, Any]] = {}
    for light in _data(*bridge.call("GET", _LIGHTS), light=False):
        light_id = light.get("id")
        if isinstance(light_id, str) and LIGHT_ID.match(light_id):
            found.setdefault(light_id, light)
    return found


def lights(bridge: Bridge) -> list[dict[str, str]]:
    """Every light on the bridge: its id, name and kind, by name."""
    found = []
    for light_id, light in _every_light(bridge).items():
        metadata = light.get("metadata")
        name = _printable(metadata.get("name", "")) if isinstance(metadata, dict) else ""
        found.append({"id": light_id, "name": name, "kind": "light" if _dims(light) else "plug"})
    return sorted(found, key=lambda light: (light["name"].casefold(), light["id"]))


def check(bridge: Bridge, wanted: Sequence[str]) -> dict[str, Any]:
    """The bridge answers with the pinned certificate and the key; which of the wanted lights it has, and which of
    those it cannot reach (switched off at the wall)."""
    have = _every_light(bridge)
    links = _links(bridge)
    found = [light for light in wanted if light in have]
    disconnected = []
    for light_id in found:
        try:
            _reached(have[light_id], links)
        except HueError:
            disconnected.append(light_id)
    return {
        "ok": True,
        "found": found,
        "missing": [light for light in wanted if light not in have],
        "disconnected": disconnected,
    }


def load(path: Path = HUE_FILE) -> Pairing:
    """The pairing from hue.env: private, with a home address, a key and a fingerprint, else not paired."""
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HueError("not_paired", "no hue.env: pair the bridge first") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise HueError("not_paired", "hue.env must be private (chmod 600)")
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    bridge, key, pin = values.get("HUE_BRIDGE", ""), values.get("HUE_KEY", ""), values.get("HUE_FINGERPRINT", "")
    if not home_address(bridge) or not _KEY.match(key) or not _FINGERPRINT.match(pin):
        raise HueError("not_paired", "hue.env is incomplete: pair the bridge again")
    return Pairing(bridge, key, pin)


def save(path: Path, pairing: Pairing) -> None:
    """hue.env with the three values, 0600, in one rename; its folder private."""
    folder = path.parent
    for level in reversed([level for level in (folder, *folder.parents) if not level.exists()]):
        level.mkdir(mode=0o700)  # each level its own mode (mkdir(parents=True) gives only the last one)
    text = f"HUE_BRIDGE={pairing.bridge}\nHUE_KEY={pairing.key}\nHUE_FINGERPRINT={pairing.fingerprint}\n"
    tmp = folder / f".{path.name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


# pairing (scripts/hue-pair.sh)

Run = Callable[[list[str], float], str]


def run_for(argv: list[str], seconds: float) -> str:
    """A fixed command (no shell) for at most `seconds`, then stopped; what it printed meanwhile."""
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env={"PATH": "/usr/bin:/bin"}
        )
    except OSError:
        return ""
    try:
        out, _ = proc.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    return out


def find_bridges(run: Run = run_for) -> list[str]:
    """The addresses of the Hue Bridges that answer mDNS on the home network (never Philips' discovery service)."""
    names: list[str] = []
    for line in run([DNS_SD, "-B", "_hue._tcp", "local."], BROWSE_SECONDS).splitlines():
        found = _BROWSED.match(line)
        name = _printable(found.group(1)) if found else ""
        if name and name not in names:
            names.append(name)
    addresses: list[str] = []
    for name in names[:MAX_BRIDGES]:
        reached = _REACHED.search(run([DNS_SD, "-L", name, "_hue._tcp", "local."], LOOKUP_SECONDS))
        if reached is None:
            continue
        for line in run([DNS_SD, "-G", "v4", f"{reached.group(1)}."], LOOKUP_SECONDS).splitlines():
            added = _ADDED.match(line)
            if added and home_address(added.group(1)):
                if added.group(1) not in addresses:
                    addresses.append(added.group(1))
                break
    return addresses


NO_BRIDGE = (
    "No Hue Bridge answered on the home network. Give its address: scripts/hue-pair.sh <address> "
    "(the Hue app shows it under Settings, Bridges)."
)


def _first_certificate(open: Open, address: str) -> str:
    """The fingerprint of the certificate the bridge shows now: trusted from here on (trust on first use)."""
    try:
        channel = open(address, REQUEST_SECONDS)
    except (OSError, ssl.SSLError) as exc:
        raise HueError("unreachable") from exc
    try:
        return fingerprint(channel.certificate())
    except (OSError, ssl.SSLError) as exc:
        raise HueError("unreachable") from exc
    finally:
        with contextlib.suppress(OSError):
            channel.close()


def _new_key(answer: Any) -> tuple[str, int | None]:
    """The application key from POST /api's answer, or the error type (101: the button is not pressed yet); None when
    the answer is not a Hue Bridge's (a router's page, a NAS: something else answers at that address)."""
    first = answer[0] if isinstance(answer, list) and answer and isinstance(answer[0], dict) else {}
    success, error = first.get("success"), first.get("error")
    if isinstance(success, dict) and isinstance(success.get("username"), str) and _KEY.match(success["username"]):
        return success["username"], 0
    kind = error.get("type") if isinstance(error, dict) else None
    return "", kind if isinstance(kind, int) and not isinstance(kind, bool) else None


def _toml(text: str) -> str:
    """A TOML basic string: JSON's escapes (quotes, backslashes, control characters as \\uXXXX) are TOML's too."""
    return json.dumps(text, ensure_ascii=False)


def _config_lines(found: list[dict[str, str]]) -> list[str]:
    lines = ["", "Lights on the Hue Bridge (add the ones she may switch to [[tapo.devices]] in her config):"]
    lines += [f"  {light['name'] or '?':28} {light['kind']:6} {light['id']}" for light in found]
    example = next((light for light in found if light["kind"] == "light"), found[0])
    lines += [
        "",
        "For example:",
        "  [[tapo.devices]]",
        f"  name = {_toml(example['name'] or 'hall light')}",  # a TOML string, whatever the app's name holds
        '  brand = "hue"',
        f"  light = {_toml(example['id'])}",
        f"  kind = {_toml(example['kind'])}",
        '  rule = "normal"',
    ]
    return lines


def pair(
    address: str | None,
    *,
    path: Path = HUE_FILE,
    open: Open = tls,
    find: Callable[[], list[str]] = find_bridges,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Pair the bridge at `address` (found by mDNS when None); the exit code."""
    if address is None:
        say("Looking for a Hue Bridge on the home network (a few seconds)...")
        found = find()
        if not found:
            say(NO_BRIDGE)
            return 1
        if len(found) > 1:
            say(f"Several Hue Bridges answered: {', '.join(found)}. Run scripts/hue-pair.sh <address> with one.")
            return 1
        address = found[0]
        say(f"Found a Hue Bridge at {address}.")
    if not home_address(address):
        say("That is not an address on the home network (for example 192.168.1.40): nothing was contacted.")
        return 1
    try:
        pin = _first_certificate(open, address)
    except HueError:
        say(f"No answer from {address}: is the bridge plugged in, and is the address right? Nothing saved.")
        return 1
    say(f"Hue Bridge at {address}. Its certificate's SHA-256 fingerprint:")
    say(f"  {shown(pin)}")
    say("It is saved and checked on every connection from now on (trust on first use).")
    with contextlib.suppress(HueError):
        before = load(path).fingerprint
        if before != pin:
            say(
                f"This differs from the one saved at the last pairing ({shown(before)}): pairing again trusts this one."
            )
    say(f"Press the round button on top of the Hue Bridge now. Waiting up to {round(BUTTON_SECONDS)} seconds...")
    body = json.dumps({"devicetype": DEVICE_TYPE, "generateclientkey": True}).encode()
    request = ("POST", "/api", {"Accept": "application/json", "Content-Type": "application/json"}, body)
    deadline = clock() + BUTTON_SECONDS
    not_a_bridge = (
        f"{address} answers, but not as a Hue Bridge: nothing saved. Check the address: the Hue app shows it under "
        "Settings, Bridges."
    )
    key = ""
    while not key:
        answered = True
        try:
            _status, answer = exchange(open, address, pin, REQUEST_SECONDS, request)
            key, error = _new_key(answer)
        except HueError as exc:
            if exc.code == "certificate":
                say("The bridge's certificate changed during pairing: nothing was sent to it, nothing saved.")
                return 1
            if exc.code != "unreachable":  # an answer, but not JSON: not a Hue Bridge
                say(not_a_bridge)
                return 1
            answered, error = False, 101  # no answer this time (a busy or restarting bridge): ask again
        if key:
            break
        if error is None:
            say(not_a_bridge)
            return 1
        if error != 101:
            say(f"The bridge refused (error {error}): nothing saved.")
            return 1
        if clock() >= deadline:
            say(
                "The button was not pressed in time: nothing saved. Run it again and press the button."
                if answered
                else f"No answer from the bridge at {address}: nothing saved. Check that it is plugged in, then "
                "run it again."
            )
            return 1
        sleep(POLL_SECONDS)
    pairing = Pairing(address, key, pin)
    save(path, pairing)
    say(f"Saved in {path} (mode 600): the bridge's address, its key and the fingerprint. The key is never shown.")
    try:
        found_lights = lights(Bridge(pairing, open=open))
    except HueError:
        say("Paired, but the lights could not be listed just now: scripts/tapo-discover.sh lists them later.")
        return 0
    if found_lights:
        for line in _config_lines(found_lights):
            say(line)
    else:
        say("The bridge has no lights yet: add them in the Hue app, then scripts/tapo-discover.sh lists them.")
    return 0


_PROBLEMS = {
    "not_paired": "The Hue Bridge is not paired yet: scripts/hue-pair.sh pairs it.",
    "certificate": "The Hue Bridge's certificate is not the one saved at pairing: nothing was sent to it. If the "
    "bridge was replaced or reset, pair it again: scripts/hue-pair.sh.",
    "key": "The Hue Bridge refused its key: scripts/hue-pair.sh pairs it again.",
}


def show_lights(*, path: Path = HUE_FILE, open: Open = tls, say: Callable[[str], None] = print) -> int:
    """The paired bridge's lights as lines for [[tapo.devices]] (read only, through the pinned certificate)."""
    try:
        pairing = load(path)
    except HueError:
        say(_PROBLEMS["not_paired"])
        return 1
    try:
        found = lights(Bridge(pairing, open=open))
    except HueError as exc:
        if exc.code == "unreachable":
            say(f"The Hue Bridge did not answer: no answer at {pairing.bridge}, the address saved at pairing.")
        else:
            say(_PROBLEMS.get(exc.code, "The Hue Bridge's answer could not be read."))
        return 1
    if not found:
        say("The Hue Bridge has no lights yet: add them in the Hue app first.")
        return 0
    for line in _config_lines(found):
        say(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hue.py", description="Pair the Hue Bridge on the home network.")
    actions = parser.add_subparsers(dest="action", required=True)
    pairing = actions.add_parser("pair", help="pair the bridge: press its button when asked")
    pairing.add_argument("address", nargs="?", help="the bridge's address (found by mDNS when left out)")
    actions.add_parser("lights", help="the paired bridge's lights, as lines for [[tapo.devices]]")
    args = parser.parse_args(argv)
    if args.action == "lights":
        return show_lights()
    return pair(args.address)


if __name__ == "__main__":
    raise SystemExit(main())
