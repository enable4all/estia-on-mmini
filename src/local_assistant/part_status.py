"""What each part last did for real (AURA's "green means it works", not just that it is set up).

The bridge records, per part, when its last real read or call worked and since when it fails, with a reason from a
fixed list (`Reason`). Parts: each mailbox ("mail:<name>"), each calendar ("calendar" for the one named calendar,
"calendar:<name>" for the others), the voice service, the device helper, web search, Google Places and Routes ("places"),
Spotify, the YouTube helper ("youtube", always on) and the documents folder with its helper ("documents"). Calls are not
a part of their own: they use the voice service.

The records live in <data_dir>/status.json (0600, replaced atomically) so doctor can read them without the bridge. The
file holds part names, times, reasons and HTTP status numbers only: never message, mail, event, search or device text,
addresses or keys. A missing or unreadable file means nothing is known, and recording never raises into the caller.
An unchanged state is written at most once a minute.

Beside the parts, a "security" section keeps three facts as numbers and times only: since when a message from an allowed
user could not be opened (a changed safety number), how many devices her own Signal account has (this Mac is one) and
when that was checked, and since when answers are paused because the model no longer matches its pinned digest.

`describe` turns a record into one of three states, the same words in doctor and /status: set up, not tested yet ·
working, last read 14:05 · failing since 14:05: reason → the next step. A failure whose fix (a sign-in, key, address
or helper) was saved again after its latest failure is not tested yet: set up again, not tested yet; the next failure
makes it failing again.

A part that works may also carry a warning: one of its abilities does not (documents: text recognition failed, so
scanned pages are not read, while text pages are). It is kept from its first time through reads that work and under a
failure, until the bridge sees that ability work again (`unwarn`), and shown as "!" with its fix, unless the part is
failing (✗ is what shows then).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.config import DEFAULT_CALENDAR, Config, is_estia_kb
from local_assistant.timefmt import local_when

log = logging.getLogger(__name__)

Reason = Literal[
    "sign_in",
    "no_answer",
    "busy",
    "refused",
    "no_key",
    "helper_missing",
    "helper_failed",
    "not_set",
    "gone",
    "login",
    "unsupported",
    "loading",
    "bad_answer",
    "no_consent",
    "not_shared",
    "os_refused",
    "no_password",
    "password_refused",
    "no_calendar",
    "not_paired",
    "hue_key",
    "certificate",
    "no_light",
    "not_hers",
    "ocr_unavailable",
    "error",
]
State = Literal["untested", "working", "failing", "warning"]  # "warning": `describe` only, never `state`
REASONS: frozenset[str] = frozenset(get_args(Reason))
REASON_MAX_CHARS = 60
WRITE_EVERY_SECONDS = 60.0  # an unchanged state is written at most this often
PARTS = (
    "voice",
    "calendar",
    "devices",
    "web",
    "places",
    "spotify",
    "youtube",
    "documents",
)  # and "mail:<name>", "calendar:<name>"
_NAMED = re.compile(r"^(?:mail|calendar):[a-z0-9][a-z0-9_-]{0,19}$")  # config.py's rule for mailbox and calendar names


@dataclass(frozen=True)
class Record:
    ok: float | None = None  # the last real read or call that worked (epoch seconds)
    failed: float | None = None  # since when it fails: the first failure after `ok`
    reason: str = ""  # a Reason, when failed
    code: int = 0  # the HTTP status of the failure, when there was one
    last: float | None = None  # the latest failure, when it went on after `failed` (written at most once a minute)
    warning: str = ""  # a Reason: the part works, but one of its abilities does not (documents: "ocr_unavailable")
    warned: float | None = None  # since when, kept from its first time until the ability works again


@dataclass(frozen=True)
class Security:
    """The security facts of status.json: times (epoch seconds) and a count, never an account, a number or a name."""

    identity_changed: float | None = None  # since when messages from an allowed user cannot be opened
    linked_devices: int | None = None  # devices on her own Signal account (this Mac is one)
    devices_checked: float | None = None  # when they were counted
    model_paused: float | None = None  # since when answers are paused: the model does not match its pinned digest
    model_cloud: float | None = None  # since when answers are paused: Ollama lists the model as a cloud model


DEVICES_WRITE_EVERY_SECONDS = 3_600.0  # an unchanged device count is written at most once an hour


@dataclass(frozen=True)
class PartLine:
    label: str  # "Mail (family)", "Calendar", "Calendar (work)"
    state: State
    text: str  # "working, last read 14:05"
    fix: str = ""  # the next step, when failing

    def line(self) -> str:
        """/status's line; a warning is marked "!" as in doctor (a failure's text says failing)."""
        mark = "! " if self.state == "warning" else ""
        return f"{self.label}: {mark}{self.text}" + (f" → {self.fix}" if self.fix else "")


def mail_part(name: str) -> str:
    return f"mail:{name}"


def calendar_part(name: str) -> str:
    """ "calendar" for the calendar named calendar (the one of before names: its records stay), else "calendar:<name>"."""
    return "calendar" if name == DEFAULT_CALENDAR else f"calendar:{name}"


def is_part(part: str) -> bool:
    return part in PARTS or bool(_NAMED.match(part))


def state(record: Record | None) -> State:
    if record is None or (record.ok is None and record.failed is None):
        return "untested"
    if record.failed is not None and (record.ok is None or record.failed >= record.ok):
        return "failing"  # a success clears the failure, so both times mean: it failed after that success
    return "working"


def read(path: Path) -> dict[str, Record]:
    """The records in the file; whatever is missing, unreadable or odd is left out (nothing known)."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    parts = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(parts, dict):
        return {}
    records: dict[str, Record] = {}
    for part, entry in parts.items():
        record = _record(entry) if isinstance(part, str) and is_part(part) else None
        if record is not None:
            records[part] = record
    return records


def read_security(path: Path) -> Security:
    """The security section of the file; whatever is missing, unreadable or odd is left out (nothing known)."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Security()
    section = data.get("security") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return Security()

    def moment(key: str) -> float | None:
        try:
            return _time(section.get(key))
        except ValueError:
            return None

    count = section.get("linked_devices")
    devices = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    return Security(
        identity_changed=moment("identity_changed"),
        linked_devices=devices,
        devices_checked=moment("devices_checked") if devices is not None else None,
        model_paused=moment("model_paused"),
        model_cloud=moment("model_cloud"),
    )


def security_lines(security: Security, now: float, tz: ZoneInfo, *, model: str) -> list[PartLine]:
    """/status: the safety number when it changed, her Signal account's devices once counted, the model's pause."""
    found: list[PartLine] = []
    if security.identity_changed is not None:
        when = local_when(security.identity_changed, now, tz)
        text = "✗ " + prompts.SAFETY_NUMBER_CHANGED.format(when=when)
        found.append(PartLine(prompts.SAFETY_NUMBER_LABEL, "failing", text, prompts.SAFETY_NUMBER_FIX))
    if security.linked_devices is not None:
        when = local_when(security.devices_checked or now, now, tz)
        if security.linked_devices <= 1:
            found.append(PartLine(prompts.LINKED_LABEL, "working", prompts.LINKED_ONE.format(when=when)))
        else:
            text = "✗ " + prompts.LINKED_MORE.format(count=security.linked_devices, when=when)
            found.append(PartLine(prompts.LINKED_LABEL, "failing", text, prompts.LINKED_FIX))
    if security.model_paused is not None:
        when = local_when(security.model_paused, now, tz)
        text = "✗ " + prompts.MODEL_PAUSED_LINE.format(when=when, model=model)
        found.append(PartLine(prompts.MODEL_LABEL, "failing", text, prompts.MODEL_PAUSED_FIX))
    if security.model_cloud is not None:
        when = local_when(security.model_cloud, now, tz)
        text = "✗ " + prompts.MODEL_CLOUD_LINE.format(when=when, model=model)
        found.append(PartLine(prompts.MODEL_LABEL, "failing", text, prompts.model_cloud_fix(model)))
    return found


def _time(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("not a time")
    return float(value)


def _record(entry: Any) -> Record | None:
    if not isinstance(entry, dict):
        return None
    try:
        ok, failed = _time(entry.get("ok")), _time(entry.get("failed"))
    except ValueError:
        return None
    warning, warned = _warning(entry)
    if ok is None and failed is None and not warning:
        return None
    if failed is None:
        return Record(ok=ok, warning=warning, warned=warned)
    reason = entry.get("reason")
    try:
        last = _time(entry.get("last"))
    except ValueError:
        last = None  # odd: as if it failed only once
    reason = reason if reason in REASONS else "error"
    return Record(ok, failed, reason, _code(entry.get("code")), last, warning, warned)


def _warning(entry: dict[str, Any]) -> tuple[str, float | None]:
    """The record's warning and since when; none when either is missing or odd."""
    warning = entry.get("warning")
    try:
        warned = _time(entry.get("warned"))
    except ValueError:
        return "", None
    return (warning, warned) if warning in REASONS and warned is not None else ("", None)


def _code(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599 else 0


def reason_text(reason: str, code: int = 0) -> str:
    """'too many requests (429)': a fixed reason, with the HTTP status when there is one."""
    text = prompts.PART_REASONS.get(reason, prompts.PART_REASONS["error"])
    return (f"{text} ({code})" if code else text)[:REASON_MAX_CHARS]


def fix_for(part: str, reason: str, *, mailbox: str = "", estia_kb: bool = False) -> str:
    """The code-written next step for a failing part; `mailbox`: the mailbox name of an Outlook calendar; `estia_kb`:
    the documents folder already is the owner's ~/Estia-KB (a fix that moves it there would go in a circle)."""
    kind, _, name = part.partition(":")
    fix = (
        (prompts.PART_FIXES.get(f"{kind}/{reason}/estia_kb") if estia_kb else None)
        or prompts.PART_FIXES.get(f"{kind}/{reason}")
        or prompts.PART_FIXES.get(reason)
        or prompts.PART_FIXES["error"]
    )
    return " ".join(fix.format(name=name, mailbox=mailbox).split())  # no double space where an empty name was


def label_of(part: str) -> str:
    """ "Mail (family)", "Calendar", "Calendar (work)", "Voice"."""
    kind, _, name = part.partition(":")
    return (
        prompts.PART_LABELS[kind].format(name=name)
        if name
        else prompts.PART_LABELS_PLAIN.get(kind, prompts.PART_LABELS[kind])
    )


def describe(
    part: str,
    record: Record | None,
    now: float,
    tz: ZoneInfo,
    *,
    saved: float | None = None,
    mailbox: str = "",
    estia_kb: bool = False,
) -> PartLine:
    """The part's state in words, for doctor and /status alike. `saved`: when the setup that fixes the recorded
    failure (a sign-in, key, address or helper) was saved, if known; saved after the failure, it is not tested yet.
    `mailbox`: for an Outlook calendar, its mailbox's name (the fix names it). `estia_kb`: see `fix_for`. A warning
    follows the state, with its own fix, unless the part is failing."""
    line = _described(part, record, now, tz, saved=saved, mailbox=mailbox, estia_kb=estia_kb)
    if record is None or not record.warning or record.warned is None or line.state == "failing":
        return line
    text = prompts.PART_WARNING.format(
        text=line.text, when=local_when(record.warned, now, tz), reason=reason_text(record.warning)
    )
    return PartLine(line.label, "warning", text, fix_for(part, record.warning, mailbox=mailbox, estia_kb=estia_kb))


def _described(
    part: str,
    record: Record | None,
    now: float,
    tz: ZoneInfo,
    *,
    saved: float | None,
    mailbox: str,
    estia_kb: bool,
) -> PartLine:
    """`describe` without the warning."""
    kind, _, name = part.partition(":")
    label = (
        prompts.PART_LABELS[kind].format(name=name)
        if name
        else prompts.PART_LABELS_PLAIN.get(kind, prompts.PART_LABELS[kind])
    )
    current = state(record)
    if record is None or current == "untested":
        return PartLine(label, "untested", prompts.PART_UNTESTED)
    latest = record.last if record.last is not None else record.failed
    if current == "failing" and latest is not None and saved is not None and saved > latest:
        return PartLine(label, "untested", prompts.PART_SET_UP_AGAIN)
    if current == "failing" and record.failed is not None:
        when = local_when(record.failed, now, tz)
        text = prompts.PART_FAILING.format(when=when, reason=reason_text(record.reason, record.code))
        return PartLine(label, "failing", text, fix_for(part, record.reason, mailbox=mailbox, estia_kb=estia_kb))
    verb = prompts.PART_VERBS.get(part.partition(":")[0], "used")
    return PartLine(label, "working", prompts.PART_WORKING.format(verb=verb, when=local_when(record.ok or 0, now, tz)))


def enabled_parts(cfg: Config) -> list[str]:
    """The parts that are turned on, in doctor's order."""
    parts = ["voice"] if cfg.voice_enabled else []
    parts += [mail_part(account.name) for account in cfg.mail_accounts]
    parts += [calendar_part(source.name) for source in cfg.calendars]
    for part, enabled in (
        ("devices", cfg.tapo_enabled),
        ("web", cfg.web_enabled),
        ("places", cfg.maps_enabled),
        ("spotify", cfg.spotify_enabled),
        ("youtube", True),  # always on, with read_link: no switch
        ("documents", cfg.documents_enabled),
    ):
        if enabled:
            parts.append(part)
    return parts


def calendar_mailbox(cfg: Config, part: str) -> str:
    """The mailbox name of a part that is an Outlook calendar; "" for anything else."""
    return next((s.mailbox for s in cfg.calendars if calendar_part(s.name) == part), "")


def in_estia_kb(cfg: Config, part: str) -> bool:
    """Whether `part` is documents and its folder already is the owner's ~/Estia-KB (`fix_for`)."""
    return part == "documents" and is_estia_kb(cfg.documents_folder)


def lines(cfg: Config, records: Mapping[str, Record], now: float) -> list[PartLine]:
    """One line per part that is turned on (/status)."""
    return [
        describe(
            part,
            records.get(part),
            now,
            cfg.timezone,
            mailbox=calendar_mailbox(cfg, part),
            estia_kb=in_estia_kb(cfg, part),
        )
        for part in enabled_parts(cfg)
    ]


class PartStatus:
    """The bridge's recorder (one per bridge). Without a path it keeps the records in memory only."""

    def __init__(self, path: Path | None, *, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock
        self._records = read(path) if path is not None else {}
        self._security = read_security(path) if path is not None else Security()

    def snapshot(self) -> dict[str, Record]:
        return dict(self._records)

    def security(self) -> Security:
        return self._security

    def identity_changed(self) -> None:
        """A message from an allowed user could not be opened: their safety number changed (kept from its first time)."""
        if self._security.identity_changed is None:
            self._keep_security(replace(self._security, identity_changed=self._clock()))

    def identity_ok(self) -> None:
        """A message from an allowed user opened fine: the safety number is trusted again."""
        if self._security.identity_changed is not None:
            self._keep_security(replace(self._security, identity_changed=None))

    def linked_devices(self, count: int) -> None:
        """The devices on her own Signal account, just counted (written when it changed, else once an hour)."""
        now = self._clock()
        old = self._security
        recent = old.devices_checked is not None and 0 <= now - old.devices_checked < DEVICES_WRITE_EVERY_SECONDS
        if old.linked_devices == count and recent:
            return
        self._keep_security(replace(old, linked_devices=count, devices_checked=now))

    def model_paused(self, paused: bool) -> None:
        """Answers paused (the model does not match its pinned digest), kept from its first time; or not any more."""
        if paused and self._security.model_paused is None:
            self._keep_security(replace(self._security, model_paused=self._clock()))
        elif not paused and self._security.model_paused is not None:
            self._keep_security(replace(self._security, model_paused=None))

    def model_cloud(self, paused: bool) -> None:
        """Answers paused (Ollama lists the model as a cloud model), kept from its first time; or not any more."""
        if paused and self._security.model_cloud is None:
            self._keep_security(replace(self._security, model_cloud=self._clock()))
        elif not paused and self._security.model_cloud is not None:
            self._keep_security(replace(self._security, model_cloud=None))

    def warn(self, part: str, reason: Reason) -> None:
        """The part works, but one of its abilities does not (documents: "ocr_unavailable"); kept from its first time."""
        try:
            self._warn(part, reason)
        except Exception:
            log.exception("part status: recording %s failed", part)

    def unwarn(self, part: str) -> None:
        """The ability the part's warning named works again."""
        try:
            old = self._records.get(part)
            if old is not None and old.warning:
                self._keep(part, replace(old, warning="", warned=None))
        except Exception:
            log.exception("part status: recording %s failed", part)

    def _warn(self, part: str, reason: str) -> None:
        if not is_part(part):
            log.warning("part status: not a part, ignored")
            return
        old = self._records.get(part) or Record()
        reason = reason if reason in REASONS else "error"
        if old.warning != reason:
            self._keep(part, replace(old, warning=reason, warned=self._clock()))

    def _keep_security(self, security: Security) -> None:
        self._security = security
        if self.path is None:
            return
        try:
            self._write(self.path)
        except OSError as exc:
            log.warning("part status: %s not written (%s)", self.path.name, type(exc).__name__)

    def ok(self, part: str) -> None:
        """A real read or call of the part worked."""
        try:
            self._ok(part)
        except Exception:  # recording must never break the part it watches
            log.exception("part status: recording %s failed", part)

    def failed(self, part: str, reason: Reason, code: int = 0) -> None:
        """A real read or call of the part failed, for a reason from the fixed list (and its HTTP status, if any)."""
        try:
            self._failed(part, reason, code)
        except Exception:
            log.exception("part status: recording %s failed", part)

    def _ok(self, part: str) -> None:
        if not is_part(part):
            log.warning("part status: not a part, ignored")
            return
        now = self._clock()
        old = self._records.get(part)
        recent = old is not None and old.ok is not None and 0 <= now - old.ok < WRITE_EVERY_SECONDS
        if recent and state(old) == "working":
            return  # still working, and written less than a minute ago
        self._keep(part, Record(ok=now, warning=old.warning, warned=old.warned) if old else Record(ok=now))

    def _failed(self, part: str, reason: str, code: int) -> None:
        if not is_part(part):
            log.warning("part status: not a part, ignored")
            return
        reason = reason if reason in REASONS else "error"
        code = _code(code)
        old = self._records.get(part) or Record()
        now = self._clock()
        if state(old) == "failing":
            latest = old.last if old.last is not None else old.failed
            recent = latest is not None and 0 <= now - latest < WRITE_EVERY_SECONDS
            if (old.reason, old.code) == (reason, code) and recent:
                return  # the same failure goes on, written less than a minute ago
            later = now if now != old.failed else old.last  # the latest failure beats an older fix
            new = replace(old, reason=reason, code=code, last=later)  # "since" stays
        else:
            new = Record(ok=old.ok, failed=now, reason=reason, code=code, warning=old.warning, warned=old.warned)
        self._keep(part, new)

    def _keep(self, part: str, record: Record) -> None:
        self._records[part] = record
        if self.path is None:
            return
        try:
            self._write(self.path)
        except OSError as exc:
            log.warning("part status: %s not written (%s)", self.path.name, type(exc).__name__)

    def _write(self, path: Path) -> None:
        parts: dict[str, dict[str, Any]] = {}
        for part, record in sorted(self._records.items()):
            entry: dict[str, Any] = {} if record.ok is None else {"ok": record.ok}
            if record.failed is not None:
                entry |= {"failed": record.failed, "reason": record.reason}
                if record.last is not None:
                    entry["last"] = record.last
                if record.code:
                    entry["code"] = record.code
            if record.warning:
                entry |= {"warning": record.warning, "warned": record.warned}
            parts[part] = entry
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".status-", suffix=".tmp")  # 0600
        try:
            facts = self._security
            security = {
                key: value
                for key, value in (
                    ("identity_changed", facts.identity_changed),
                    ("linked_devices", facts.linked_devices),
                    ("devices_checked", facts.devices_checked),
                    ("model_paused", facts.model_paused),
                    ("model_cloud", facts.model_cloud),
                )
                if value is not None
            }
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"parts": parts, **({"security": security} if security else {})}, handle)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
