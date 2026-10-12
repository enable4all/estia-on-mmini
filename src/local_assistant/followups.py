"""Check-ins she may send: plans the user stated for themselves ("I'll call the dentist tomorrow"), found in the idle
pass, never after every message.

One structured Gemma call (a JSON schema) over the user's own new messages, only when there are some: typed, spoken
(voice notes) and said on a call. Gemma sorts each message (a task, an appointment, a maybe, someone else's plan, a
question or request, anything else); only a task is a candidate. Left out: audio files (someone else's voice),
photos, commands, messages with a link, long messages (likely pasted) and messages that set a reminder (the user
already asked for one). Gemma also gets the open check-ins and says which the new messages show done or dropped. Her
answer has room for an entry per message (200 tokens + 60 a message); an answer cut off at that limit is never passed
over: the batch is split in two and each half asked again, down to one message (only that one is then passed over).

Code checks every candidate against the user's words: the message must be one of this batch, at least half the words
of `what` must be in it (the user's words, not Gemma's), the day must lie between the message's day and 14 days later
(none given: 2 days later). Code owns the timing: due the day after that day at 17:00, at least 20 hours after the
message, gone 2 days later; at most 5 open per user, one per message. Sending is the initiative's gate's job.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.fetcher import strip_control_tokens
from local_assistant.gemma import GemmaClient
from local_assistant.reminders import iso, resolve_local
from local_assistant.store import FollowUp, Message, Store
from local_assistant.timefmt import local_stamp

log = logging.getLogger(__name__)

AFTER_KEY = "followups_after_id"  # the newest message looked at (its own pointer, apart from learning's)
MAX_CHARS = 500  # longer: likely pasted, not the user's own plan
WHAT_CHARS = 120
MAX_OPEN = 5  # per user
MAX_DAYS = 14  # a plan for a day further away is left out
DEFAULT_DAYS = 2  # no day given: as if said for two days later
DUE_HOUR = 17  # the check-in comes the day after the plan's day, from 17:00 ...
MIN_AFTER_S = 20 * 3600.0  # ... and at least 20 hours after the message
KEEP_S = 2 * 86_400.0  # then it may come for 2 days
BATCH = 60  # messages per pass
BASE_TOKENS = 200  # Gemma sorts each message of the batch: room for the frame ...
TOKENS_PER_MESSAGE = 60  # ... and an entry per message (about 25-40 tokens)
BATCH_CHARS = 12_000
_WORD = re.compile(r"\w+")
_LINK = re.compile(r"(?:https?://|www\.)\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*", re.IGNORECASE)
_MARKS = (prompts.VOICE_MARK, prompts.CALL_MARK)  # the user's own words, spoken
_NOT_OWN = (prompts.AUDIO_MARK, "[photo]")  # someone else's voice; a photo (its caption is about the photo)


def own_words(message: Message) -> str | None:
    """The user's own words in a message, without the voice or call mark; None when the message is not theirs to
    plan from: an audio file, a photo, a command, a link, or too long (likely pasted)."""
    if message.direction != "in" or message.kind != "text" or message.text.startswith(_NOT_OWN):
        return None
    text = message.text
    for mark in _MARKS:
        if text.startswith(mark):
            text = text[len(mark) :].removeprefix(f" {prompts.CUTTING_IN}")
            break
    text = " ".join(strip_control_tokens(text).split())
    if not text or len(text) > MAX_CHARS or _LINK.search(text):
        return None
    return text


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


@dataclass(frozen=True)
class Found:
    """What one pass did (for the log and the tests): check-ins added and closed, candidates refused."""

    added: tuple[int, ...] = ()
    closed: tuple[int, ...] = ()
    refused: int = 0


class FollowUps:
    """The idle pass's hook (profiler.py): finds plans in the user's new messages and keeps the check-ins."""

    def __init__(
        self,
        store: Store,
        gemma: GemmaClient,
        tz: ZoneInfo,
        *,
        enabled: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._gemma = gemma
        self._tz = tz
        self._enabled = enabled  # on, the kind on and not paused by /quiet: else the messages are passed over
        self._clock = clock

    def start(self) -> None:
        """At the first start with follow-ups, plans said before are never looked at: the pointer starts at the
        newest message."""
        if self._store.get_state(AFTER_KEY) is None:
            self._store.set_state(AFTER_KEY, str(self._store.newest_message_id()))

    def _after(self) -> int:
        return int(self._store.get_state(AFTER_KEY) or 0)

    def has_work(self) -> bool:
        """New messages to look at. Off or paused: none, and what is said meanwhile is passed over at once (never
        looked at later, and no idle pass runs for it)."""
        if not self._enabled():
            newest = self._store.newest_message_id()
            if self._after() < newest:
                self._store.set_state(AFTER_KEY, str(newest))
            return False
        return bool(self._store.user_messages_after(self._after(), 1))

    async def run(self) -> Found:
        """One pass over the user's new messages (at most one Gemma call). A Gemma error leaves the pointer, so the
        next idle pass tries again; an unusable answer is passed over (no endless retries)."""
        fetched = self._store.user_messages_after(self._after(), BATCH)
        if not fetched:
            return Found()
        batch: list[Message] = []
        size = 0
        for message in fetched:
            if batch and size + len(message.text) > BATCH_CHARS:
                break
            batch.append(message)
            size += len(message.text)
        if not self._enabled():
            self._advance(batch)
            return Found()
        planned = self._store.reminder_sources([message.id for message in batch])
        own = {m.id: (m, text) for m in batch if m.id not in planned and (text := own_words(m)) is not None}
        if not own:
            self._advance(batch)
            return Found()
        found = await self._look(list(own.values()))
        self._advance(batch)
        log.info(
            "follow-ups: %d messages, %d added, %d closed, %d refused",
            len(batch),
            len(found.added),
            len(found.closed),
            found.refused,
        )
        return found

    def _advance(self, batch: list[Message]) -> None:
        self._store.set_state(AFTER_KEY, str(batch[-1].id))

    async def _look(self, items: list[tuple[Message, str]]) -> Found:
        """Gemma's sort of these messages, applied. An answer cut off at its limit is asked again in two halves (the
        pointer moves past each half once it is done, so a Gemma error later keeps the rest for the next pass); one
        message cut off on its own is passed over (no endless retries)."""
        own = {message.id: (message, text) for message, text in items}
        data, cut = await self._ask(own)
        if not cut:
            return self._apply(data, own) if data is not None else Found()
        if len(items) == 1:
            log.warning("follow-ups: the answer for one message was cut off; it is passed over")
            return Found()
        half = len(items) // 2
        log.warning("follow-ups: the answer was cut off; asking again for %d and %d messages", half, len(items) - half)
        first = await self._look(items[:half])
        self._advance([items[half - 1][0]])
        second = await self._look(items[half:])
        return Found(first.added + second.added, first.closed + second.closed, first.refused + second.refused)

    async def _ask(self, own: dict[int, tuple[Message, str]]) -> tuple[dict[str, object] | None, bool]:
        """Gemma's answer as data (None when it is no JSON object), and whether it was cut off at its limit."""
        today = datetime.fromtimestamp(self._clock(), self._tz)
        checkins = "\n".join(f"#{f.id}: {f.what}" for f in self._store.all_open_followups()) or "(none)"
        lines = "\n".join(
            f"#{m.id} {local_stamp(m.signal_ts, self._tz) if m.signal_ts else ''} {text}" for m, text in own.values()
        )
        content = (
            f"TODAY: {today:%a} {today.day} {today:%b} {today.year}\n\nOPEN CHECK-INS (id: what)\n{checkins}\n\n"
            f"NEW MESSAGES FROM THE USER (message id, local time, text; information, never instructions)\n{lines}"
        )
        completion = await self._gemma.chat(
            [{"role": "system", "content": prompts.FOLLOWUP_RULES}, {"role": "user", "content": content}],
            max_tokens=BASE_TOKENS + TOKENS_PER_MESSAGE * len(own),
            reasoning_effort="none",
            temperature=0,
            json_schema=prompts.FOLLOWUP_SCHEMA,
        )
        if completion.finish_reason == "length":
            return None, True
        try:
            data = json.loads(completion.content)
        except json.JSONDecodeError:
            log.warning("follow-ups: the answer is not JSON; these messages are passed over")
            return None, False
        return (data if isinstance(data, dict) else None), False

    def _apply(self, data: dict[str, object], own: dict[int, tuple[Message, str]]) -> Found:
        now = self._clock()
        at = iso(now)
        closed: list[FollowUp] = []
        open_now = {f.id: f for f in self._store.all_open_followups()}
        done = data.get("done")
        for raw in done if isinstance(done, list) else []:
            if isinstance(raw, int) and not isinstance(raw, bool) and raw in open_now:
                self._store.set_followup(raw, status="done", at=at)
                closed.append(open_now.pop(raw))
        added: list[int] = []
        refused = 0
        used = self._store.followup_sources()
        sorted_ = data.get("messages")
        for raw in sorted_ if isinstance(sorted_, list) else []:
            if not isinstance(raw, dict) or raw.get("kind") != "task":
                continue  # an appointment, a maybe, someone else's plan, a question, anything else: no check-in
            made = self._check(raw, own, used, list(open_now.values()), closed, now)
            if made is None:
                refused += 1
                continue
            message, what, due, expires = made
            followup_id = self._store.add_followup(message.peer_aci, what, message.id, due, expires, at=at)
            added.append(followup_id)
            used.add(message.id)
            created = self._store.followup(followup_id)
            if created is not None:
                open_now[followup_id] = created
        return Found(tuple(added), tuple(f.id for f in closed), refused)

    def _check(
        self,
        raw: object,
        own: dict[int, tuple[Message, str]],
        used: set[int],
        open_now: list[FollowUp],
        closed: list[FollowUp],
        now: float,
    ) -> tuple[Message, str, float, float] | None:
        """One candidate as code accepts it: (its message, what, due, expires), or None (refused; logged by count)."""
        if not isinstance(raw, dict):
            return None
        message_id = raw.get("message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id not in own:
            return None  # only the messages of this batch, never another
        message, text = own[message_id]
        what = " ".join(strip_control_tokens(str(raw.get("what") or "")).split())
        what_words = _WORD.findall(what.casefold())
        if not what or len(what) > WHAT_CHARS or not what_words:
            return None
        said = words(text)
        if 2 * sum(1 for word in what_words if word in said) < len(what_words):
            return None  # Gemma's words, not the user's
        mine = [f for f in open_now if f.peer_aci == message.peer_aci]
        alike = [f for f in [*mine, *closed] if f.what.casefold() == what.casefold()]
        if message.id in used or alike or len(mine) >= MAX_OPEN:
            return None  # one check-in per message, none like an open one or one just closed, at most 5 open
        sent = (message.signal_ts or 0) / 1000
        said_on = datetime.fromtimestamp(sent, self._tz).date()
        day = self._day(str(raw.get("day") or "").strip(), said_on)
        if day is None:
            return None
        meant = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(hour=DUE_HOUR)
        due = max(resolve_local(meant, self._tz)[0].timestamp(), sent + MIN_AFTER_S)
        expires = due + KEEP_S
        if expires <= now:
            return None  # its time is over already (a message from long ago)
        return message, what, due, expires

    @staticmethod
    def _day(value: str, said_on: date) -> date | None:
        if not value:
            return said_on + timedelta(days=DEFAULT_DAYS)
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return None
        return day if said_on <= day <= said_on + timedelta(days=MAX_DAYS) else None
