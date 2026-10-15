"""Lights and plugs on the home network: names, rules, the confirmation, the helper, the notes (Tapo spec 3).

The bridge never talks to a device itself: it runs the device helper (`tools/tapo/helper.py`, python-kasa in its
own environment) with fixed arguments and reads one JSON object back. Gemma knows device names only; addresses,
the TP-Link login and the helper's arguments stay in code and config. "Ask first" and "never off" are enforced
here, and what really changed is reported to the user in a code-written note.

A Hue light (`brand = "hue"`) goes through the same list, rules, confirmation and notes: the helper reaches it
through the paired Hue Bridge by the bridge's light id (`hue-set` one light; `hue-status` every Hue light asked about
in one run, since a Hue Bridge is slow at new connections); the bridge's address, its key and its certificate's
fingerprint are in hue.env, which only the helper reads. Hue lights take on, off, brightness and the whites, no
colours.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from local_assistant.claims import about_earlier, names_music
from local_assistant.config import Config, DeviceSpec
from local_assistant.language import YES_WORDS, yes_form
from local_assistant.part_status import PartStatus, Reason

log = logging.getLogger(__name__)

# colour name → ("temp", kelvin) or ("hue", degrees); whites as colour temperature, the rest at full saturation
COLOURS: dict[str, tuple[str, int]] = {
    "warm white": ("temp", 2700),
    "cool white": ("temp", 4000),
    "daylight": ("temp", 6500),
    "red": ("hue", 0),
    "orange": ("hue", 30),
    "yellow": ("hue", 55),
    "green": ("hue", 120),
    "blue": ("hue", 240),
    "purple": ("hue", 275),
    "pink": ("hue", 320),
}
_YES = {"yes", "y", "ok", "okay", "ja", "ναι", "do it", "yes please", "👍"} | YES_WORDS  # the 11 languages' yes words
_LOGIN = "The TP-Link login failed: run scripts/set-tapo-credentials.sh on the Mac."
_NOT_ANSWERING = "isn't answering (switched off at the wall, or a new address?)"
_HELPER_FAILED = "The device helper failed."
_MISSING = "The device helper isn't set up on the Mac: run `cd tools/tapo && uv sync --frozen` there."
# a Hue light: through the paired Hue Bridge; a problem of the bridge's pairing is the same for every Hue light
_HUE_NOT_ANSWERING = (
    "isn't answering (the Hue Bridge unplugged or at a new address, or the light switched off at the wall?)"
)
_HUE_BRIDGE = {
    "not_paired": "The Hue Bridge isn't paired yet: run scripts/hue-pair.sh on the Mac.",
    "key": "The Hue Bridge refused its key: run scripts/hue-pair.sh on the Mac.",
    "certificate": "The Hue Bridge's certificate is not the one saved at pairing, so nothing was sent to it. If the "
    "bridge was replaced or reset, run scripts/hue-pair.sh on the Mac.",
}
_HUE_WHITES = "{name} is a Hue light: on, off, brightness and whites only (warm white, cool white, daylight)."
# "unsupported": the helper's detail names the device's login. It comes from the device's own answer, so only a name
# from these lists is ever said (python-kasa 0.11.0.1's kinds, the helper's "AES v1" and " over HTTPS", and TPAP)
_LIBRARY_LOGINS = frozenset({"AES", "KLAP", "XOR", "TPAP"})
_REFUSED_LOGINS = frozenset({*_LIBRARY_LOGINS, *(f"{kind} over HTTPS" for kind in _LIBRARY_LOGINS)})
_AES_V1 = "AES v1"  # tools/tapo/helper.py's AES_V1: the password itself would go to the device
# the helper's error codes as part_status reasons ("invalid": the device cannot do that, the part is fine)
_REASONS: dict[str, Reason] = {
    "unreachable": "no_answer",
    "login": "login",
    "unsupported": "unsupported",
    "missing": "helper_missing",
    "not_paired": "not_paired",
    "key": "hue_key",
    "certificate": "certificate",
    "no_light": "no_light",
}
# a refused login, key or certificate is shared by every device of that brand: it fails the part whatever else answered
_SHARED: frozenset[Reason] = frozenset({"login", "hue_key", "not_paired", "certificate"})
_WORST_FIRST: tuple[Reason, ...] = (
    "helper_missing",
    "certificate",
    "not_paired",
    "hue_key",
    "login",
    "helper_failed",
    "unsupported",
    "no_light",
    "no_answer",
)


@dataclass(frozen=True)
class Change:
    power: str | None = None  # "on" | "off"
    brightness: int | None = None
    colour: str | None = None

    def arguments(self) -> list[str]:
        args = ["--power", self.power] if self.power else []
        if self.power == "off":
            return args
        if self.brightness is not None:
            args += ["--brightness", str(self.brightness)]
        if self.colour:
            kind, value = COLOURS[self.colour]
            args += ["--temp", str(value)] if kind == "temp" else ["--hue", str(value), "--saturation", "100"]
        return args

    def text(self) -> str:
        """'off', 'on' or 'on, 30 %, warm white', as the user reads it."""
        if self.power == "off":
            return "off"
        parts = ["on"]
        if self.brightness is not None:
            parts.append(f"{self.brightness} %")
        if self.colour:
            parts.append(self.colour)
        return ", ".join(parts)


@dataclass(frozen=True)
class _Pending:
    spec: DeviceSpec
    change: Change
    at: float
    aci: str  # only this person's yes counts


# a message with one of these words (or a word of a device's name) is about lights and plugs; a change asked for in
# any other message waits for the user's yes (a planted line from an earlier page cannot act on a later "thanks!")
_DEVICE_WORDS = frozenset(
    {
        "light",
        "lights",
        "lamp",
        "lamps",
        "plug",
        "plugs",
        "heater",
        "heaters",
        "socket",
        "sockets",
        "device",
        "devices",
        "switch",
        "dim",
        "dimmer",
        "brighter",
        "brightness",
        "darker",
        "colour",
        "color",
        "warmer",
        "cooler",
    }
)

_SHORT_WORDS = frozenset({"on", "off", "turn"})
# a word of hers about an earlier switch ("a few minutes ago", "earlier today") is true when it happened this long ago
RECENT_SWITCH_SECONDS = 24 * 3600


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.casefold()))


def _split_camel(name: str) -> str:
    """ "hallLight" as "hall Light"."""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", name)


def _spoken(text: str) -> str:
    """The text's words, lower case, one space apart ("Living-Room Lamp!" as "living room lamp")."""
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _key(name: str) -> str:
    return re.sub(r"[^\w]", "", name.casefold())


def _is_yes(text: str) -> bool:
    """A clear yes, typed, in a voice note ([voice]) or said on a call ([call]): the same words everywhere, in any of
    the languages ("Ja, graag.", "¡Sí!", "はい。"). The whole message must be the yes."""
    return yes_form(text) in _YES


def _refused(data: dict[str, Any]) -> bool:
    """Whether the helper refused to send the login to the device (never the library's gap)."""
    return data.get("refused") is True or data.get("detail") == _AES_V1


def _unsupported(name: str, login: str, *, refused: bool) -> str:
    """Why a device cannot be controlled, from the helper's detail (finding 35): a login the helper refused to send
    (AES login v1 gives the password itself), or a device the library cannot control yet, its login named only when
    it is one code knows."""
    if login == _AES_V1:  # refused by the helper whatever its flag says
        return (
            f"{name} can't be controlled: it asks for the TP-Link password itself (AES login v1), so the login is "
            "never sent to it"
        )
    if refused:
        kind = f" ({login})" if login in _REFUSED_LOGINS else ""
        return (
            f"{name} can't be controlled: the way it asks for the TP-Link login{kind} could give the login away, so "
            "it is never sent to it"
        )
    kind = f" ({login} login)" if login in _LIBRARY_LOGINS else ""
    return f"{name} can't be controlled yet: the device library doesn't support this device{kind} yet"


def _target(spec: DeviceSpec, action: str) -> list[str]:
    """The helper's action and device: a Kasa device by its address, a Hue light by the bridge's id for it."""
    return [f"hue-{action}", spec.light] if spec.hue else [action, spec.host]


def _one_light(run: dict[str, Any], light: str) -> dict[str, Any]:
    """One light's answer from a `hue-status` run: the run's own failure is every light's (the bridge's pairing, the
    helper missing); an answer without that light is the helper failing."""
    if not run.get("ok"):
        return run
    lights = run.get("lights")
    found = lights.get(light) if isinstance(lights, dict) else None
    return found if isinstance(found, dict) else {"ok": False, "error": "failed"}


def helper_env() -> dict[str, str]:
    """The helper gets a clean environment: no API keys or other secrets of the bridge's shell."""
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG") if key in os.environ}
    return env | {"PATH": "/usr/bin:/bin"}


def _colour_name(temp: Any, hsv: Any) -> str:
    if isinstance(temp, int) and temp > 0:
        return "warm white" if temp < 3300 else "cool white" if temp < 5000 else "daylight"
    if isinstance(hsv, list) and len(hsv) == 3 and hsv[1] > 20:
        hues = {name: value for name, (kind, value) in COLOURS.items() if kind == "hue"}
        return min(hues, key=lambda name: min(abs(hsv[0] - hues[name]), 360 - abs(hsv[0] - hues[name])))
    return ""


def _state(data: dict[str, Any]) -> str:
    """'off', 'on' or 'on, 30 %, warm white' from the helper's answer."""
    if not data.get("on"):
        return "off"
    parts = ["on"]
    if data.get("kind") == "light":
        if isinstance(data.get("brightness"), int):
            parts.append(f"{data['brightness']} %")
        colour = _colour_name(data.get("temp"), data.get("hsv"))
        if colour:
            parts.append(colour)
    return ", ".join(parts)


class Devices:
    def __init__(
        self, config: Config, *, clock: Callable[[], float] = time.monotonic, status: PartStatus | None = None
    ) -> None:
        self._status = status or PartStatus(None)  # what the helper's last real answer was (doctor, /status)
        self._specs = config.tapo_devices
        self._command = config.tapo_helper
        self._timeout = config.tapo_helper_timeout
        self._confirm_seconds = config.tapo_confirm_seconds
        self._clock = clock
        self._pending: _Pending | None = None
        self._done: list[str] = []  # for the next note
        self._noted: set[str] = set()  # a shared problem ("kasa:login", "hue:key", ...) noted until that brand works
        self._switched: dict[str, float] = {}  # each device's last change that its helper confirmed (in memory)

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def names_a_device(self, text: str) -> bool:
        """Whether the text has a device word or a word of a device's name ("hallLight" counts as hall and light)."""
        names = {word for spec in self._specs for word in _words(_split_camel(spec.name)) if len(word) >= 3}
        return bool(_words(text) & (_DEVICE_WORDS | names))

    def named(self, text: str) -> set[str]:
        """The devices the text names by their whole name ("the living room lamp", "livingRoomLamp" as living room
        lamp), as `find` spells them."""
        said = f" {_spoken(text)} "
        spoken = {spec.name: _spoken(_split_camel(spec.name)) for spec in self._specs}
        return {name for name, words in spoken.items() if words and f" {words} " in said}

    def recently_switched(self) -> set[str]:
        """The devices a confirmed change reached within `RECENT_SWITCH_SECONDS` (an answer's, or a yes's)."""
        now = self._clock()
        return {name for name, at in self._switched.items() if now - at <= RECENT_SWITCH_SECONDS}

    def unmade(self, clause: str, message: str, switched: Collection[str]) -> bool:
        """Whether a clause in which she claims a light or plug change (claims.switch_clauses) names a change that no
        device made. `switched`: the devices this answer's device_set changed; a clause about an earlier switch ("a
        few minutes ago") also counts the ones changed within `RECENT_SWITCH_SECONDS`. A clause that names music is
        none of these devices'; one that names devices by their whole name needs each of them switched; one with a
        device word ("the lights") or, saying only "it", after a message about a device, needs any switched."""
        if names_music(clause):
            return False
        done = set(switched) | (self.recently_switched() if about_earlier(clause) else set())
        named = self.named(clause)
        if named:
            return bool(named - done)
        if self.names_a_device(clause) or (self.names_a_device(message) and not names_music(message)):
            return not done
        return False

    def about_devices(self, text: str) -> bool:
        """Whether the user's message is about lights and plugs: a device word or a word of a device's name, or, in a
        short message, "on", "off" or "turn" ("turn it off")."""
        words = _words(text)
        if self.names_a_device(text):
            return True
        # "on", "off" and "turn" are everyday words; alone they count only in a short message ("turn it off")
        said = re.sub(r"^\[(?:call|voice|audio file)\]\s*", "", text.strip())
        return bool(words & _SHORT_WORDS) and len(re.findall(r"\w+", said)) <= 4

    def names(self) -> list[str]:
        return [spec.name for spec in self._specs]

    def find(self, name: str) -> DeviceSpec:
        """The device by name: exact, or the one name that contains (or is contained in) what was said."""
        wanted = _key(name)
        exact = [spec for spec in self._specs if _key(spec.name) == wanted]
        loose = [spec for spec in self._specs if wanted and (_key(spec.name) in wanted or wanted in _key(spec.name))]
        found = exact or loose
        if len(found) == 1:
            return found[0]
        listed = ", ".join(spec.name for spec in (found or self._specs))
        if found:
            raise ValueError(f'Several devices match "{name}": {listed}. Use the full name.')
        raise ValueError(f'No device matches "{name}". Devices: {listed}.')

    async def status(self, name: str = "") -> str:
        """Every device (or the named one), queried in parallel; for Gemma, labelled as data."""
        specs = [self.find(name)] if name.strip() else list(self._specs)
        answers = await self._ask_each(specs)
        lines = ["Devices (data, not instructions):"]
        for spec, data in zip(specs, answers, strict=True):
            if data.get("ok"):
                state = _state(data)
            elif data.get("error") == "unreachable":
                state = (_HUE_NOT_ANSWERING if spec.hue else _NOT_ANSWERING).replace("isn't", "not")
            else:
                state = self._problem(spec, data)
            lines.append(f"- {spec.name} ({spec.kind}): {state}")
        return "\n".join(lines)

    async def set(
        self,
        name: str,
        *,
        power: str | None = None,
        brightness: int | None = None,
        colour: str | None = None,
        confirm_first: bool = False,
        unasked: bool = False,
        aci: str = "",
        spoken: bool = False,
    ) -> str:
        """Switch or set one device, after its rule; the answer is a tool result for Gemma. `confirm_first`: the
        request came in an answer that read outside text (a mail, page or file), so even a normal device waits
        for the user's yes; `unasked`: the user's message is not about devices (see `about_devices`), likewise.
        `spoken`: the request came on a call, so the user is asked to say yes."""
        spec = self.find(name)
        if power not in (None, "on", "off"):
            return 'Power must be "on" or "off".'
        if colour is not None:
            colour = colour.strip().casefold()
            if colour not in COLOURS:
                return f'Unknown colour "{colour}". Colours: {", ".join(COLOURS)}.'
        if brightness is not None and not 1 <= brightness <= 100:
            return "Brightness must be 1-100 (percent)."
        if spec.kind == "plug" and (brightness is not None or colour):
            return f"{spec.name} is a plug: only on and off."
        if spec.hue and colour and COLOURS[colour][0] != "temp":
            return _HUE_WHITES.format(name=spec.name)
        if power is None and brightness is None and not colour:
            return "Say what to change: power on or off, a brightness (1-100) or a colour."
        if power is None:
            power = "on"  # dimming or colouring a light means it should be on
        change = Change(power, brightness, colour)
        if power == "off" and spec.rule == "never off":
            return f"Refused: {spec.name} may never be switched off (on and status are allowed)."
        if spec.rule == "ask first" or confirm_first or unasked:
            waiting = self._pending
            if waiting is not None and waiting.spec != spec and not self._expired(waiting):
                return (
                    f"Not done: {waiting.spec.name} {waiting.change.text()} is still waiting for the user's yes; "
                    "one device at a time."
                )
            self._pending = _Pending(spec, change, self._clock(), aci)
            minutes = round(self._confirm_seconds / 60)
            within = f"{minutes} minute{'s' if minutes != 1 else ''}"
            # the user reads in the bridge's own note exactly what their yes will do
            self._done.append(f"reply yes within {within} to switch {spec.name} {change.text()}")
            why = (
                ""
                if spec.rule == "ask first"
                else ", because the request came while reading a mail, page or file"
                if confirm_first
                else ", because the user's message did not ask for a device change"
            )
            return (
                f"Not done: {spec.name} needs the user's yes first{why}. Ask the user to "
                f"{'say' if spoken else 'reply'} yes within {within}; "
                "the bridge then does it itself."
            )
        return await self._execute(spec, change)

    def cancel(self) -> None:
        """Any message other than the yes, or a failed answer, drops the waiting action."""
        if self._pending is not None:
            log.info("device action for %s cancelled", self._pending.spec.name)
        self._pending = None
        self._done = [line for line in self._done if not line.startswith("reply yes within")]

    def _expired(self, pending: _Pending) -> bool:
        return self._clock() - pending.at > self._confirm_seconds

    async def confirm(self, text: str, aci: str = "") -> str | None:
        """The asked user's next message after an "ask first" request: a clear yes in time carries it out (what
        happened comes back); anything else cancels it and the message is handled as usual (None)."""
        pending, self._pending = self._pending, None
        if pending is None:
            return None
        self._done = [line for line in self._done if not line.startswith("reply yes within")]  # answered now
        if self._expired(pending) or not _is_yes(text) or (pending.aci and aci and aci != pending.aci):
            log.info("device action for %s cancelled", pending.spec.name)
            return None
        result = await self._execute(pending.spec, pending.change)
        return self.take_note() or result  # never an empty answer (the login problem is noted only once)

    def question(self) -> str:
        """What a yes would do, in code's words (spoken on calls, where the note only arrives as a text)."""
        pending = self._pending
        return f"Shall I switch {pending.spec.name} {pending.change.text()}?" if pending else ""

    def take_note(self) -> str:
        """What really changed or failed since the last note, for the user ("" when nothing)."""
        done, self._done = self._done, []
        return f"Devices: {' · '.join(done)}" if done else ""

    async def reachability(self) -> str:
        """One line for mini_status."""
        answers = await self._ask_each(self._specs)
        silent = [spec.name for spec, data in zip(self._specs, answers, strict=True) if not data.get("ok")]
        if not silent:
            return f"Devices: all {len(self._specs)} answer"
        answering = len(self._specs) - len(silent)
        return f"Devices: {answering} of {len(self._specs)} answer ({', '.join(silent)} not)"

    async def _execute(self, spec: DeviceSpec, change: Change) -> str:
        data = await self._helper([*_target(spec, "set"), *change.arguments()])
        if data.get("ok"):
            state = _state(data)
            self._done.append(f"{spec.name} {state}")
            self._switched[spec.name] = self._clock()
            return f"Done: {spec.name} is now {state}."
        problem = self._problem(spec, data)
        error = str(data.get("error"))
        shared = f"{spec.brand}:{error}"
        if error == "login" or (spec.hue and error in _HUE_BRIDGE):
            if shared not in self._noted:  # the same for every device of that brand: noted once until it works
                self._done.append(problem)
                self._noted.add(shared)
        elif error == "unreachable":
            self._done.append(f"{spec.name} didn't answer")
        elif error == "unsupported":
            self._done.append(f"{spec.name} can't be controlled" + ("" if _refused(data) else " yet"))
        elif error == "no_light":
            self._done.append(f"{spec.name} isn't on the Hue Bridge")
        elif (
            error == "invalid"
        ):  # the device cannot do that (a Hue White bulb and a white temperature): the helper's why
            self._done.append(problem)
        else:
            self._done.append(f"{spec.name}: the device helper failed")
        return problem if problem.endswith(".") else f"{problem}."

    def _problem(self, spec: DeviceSpec, data: dict[str, Any]) -> str:
        error = data.get("error")
        if error == "unreachable":
            return f"{spec.name} {_HUE_NOT_ANSWERING if spec.hue else _NOT_ANSWERING}"
        if spec.hue and error in _HUE_BRIDGE:
            return _HUE_BRIDGE[str(error)]
        if error == "no_light":
            return (
                f"{spec.name} isn't on the Hue Bridge (removed, or a new id?): scripts/tapo-discover.sh lists its "
                "lights"
            )
        if error == "login":
            return _LOGIN
        if error == "unsupported":
            return _unsupported(spec.name, str(data.get("detail") or ""), refused=_refused(data))
        if error == "missing":
            return _MISSING
        if error == "invalid":
            return f"{spec.name}: {data.get('detail') or 'not supported by this device'}"
        return _HELPER_FAILED

    async def _helper(self, args: list[str]) -> dict[str, Any]:
        """Run the device helper for one device and record what its answer says about the part."""
        data = await self._run(args)
        self._record([data])
        return data

    async def _ask_each(self, specs: Sequence[DeviceSpec]) -> list[dict[str, Any]]:
        """Every device's status in parallel (the Hue lights in one helper run), recorded once from all the answers
        (never by whichever came last)."""
        kasa = [spec for spec in specs if not spec.hue]
        hue = [spec.light for spec in specs if spec.hue]
        runs = [self._run(_target(spec, "status")) for spec in kasa]
        if hue:
            runs.append(self._run(["hue-status", *dict.fromkeys(hue)]))
        done = await asyncio.gather(*runs)
        kasa_answers = iter(done[: len(kasa)])
        answers = [_one_light(done[-1], spec.light) if spec.hue else next(kasa_answers) for spec in specs]
        self._record(answers)
        return answers

    def _record(self, answers: Sequence[dict[str, Any]]) -> None:
        """The part works when a device answered and no shared login, key or certificate was refused (the TP-Link
        login, the Hue Bridge's pairing): one device unplugged at the wall is not a broken part. Otherwise the worst
        failure counts."""
        reasons = [
            _REASONS.get(str(data.get("error")), "helper_failed")
            for data in answers
            if not data.get("ok") and data.get("error") != "invalid"
        ]
        if not _SHARED.intersection(reasons) and any(data.get("ok") for data in answers):
            self._status.ok("devices")
        elif reasons:
            self._status.failed("devices", min(reasons, key=_WORST_FIRST.index))

    async def _run(self, args: list[str]) -> dict[str, Any]:
        """Run the device helper (fixed command, list arguments, no shell); its JSON, or a failure object."""
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=helper_env(),
            )
        except OSError as exc:  # not set up yet (tools/tapo/.venv missing) or broken
            log.warning("device helper did not start: %s", type(exc).__name__)
            return {"ok": False, "error": "missing" if isinstance(exc, FileNotFoundError) else "failed"}
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            log.warning("device helper %s timed out", args[0])
            return {"ok": False, "error": "unreachable"}
        try:
            data = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            data = None
        if not isinstance(data, dict):
            log.warning("device helper %s gave no JSON (exit %s)", args[0], proc.returncode)
            return {"ok": False, "error": "failed"}
        if data.get("ok"):  # a later login or pairing problem of this brand is worth a note again
            brand = "hue" if args[0].startswith("hue-") else "kasa"
            self._noted = {noted for noted in self._noted if not noted.startswith(f"{brand}:")}
        log.info(
            "device helper %s: %s in %d ms",
            args[0],
            "ok" if data.get("ok") else data.get("error"),
            (time.monotonic() - started) * 1000,
        )
        return data
