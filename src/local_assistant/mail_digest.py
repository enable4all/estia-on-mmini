"""The mail part of the morning digest (morning_digest.py sends it) and the day's mail numbers.

Two blocks, both mailboxes together: mail that needs a reply (urgent first, then oldest; unanswered,
still in the Inbox, at most `reply_days` old; unlabelled mail from people the user writes to is
listed with its subject) and actions that arrived since the previous digest. Numbers are kept for the
whole day, so "read 2" means the same mail in the digest, in search results and to Gemma's tools.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from local_assistant.config import Config
from local_assistant.store import MailHeader, Store

NUMBERS_KEY = "mail:numbers"
LAST_DIGEST_KEY = "mail:last_digest_at"  # set by the morning digest: actions since then are new
BLOCK_LIMIT = 10
EMPTY = "Mail: nothing needs you today."


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


class MailNumbers:
    """Numbers for mails, stable for one local day (stored in the state table)."""

    def __init__(self, store: Store, tz: ZoneInfo, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._tz = tz
        self._clock = clock

    def number(self, mail_id: int) -> int:
        day, ids = self._load()
        if mail_id not in ids:
            ids.append(mail_id)
            self._store.set_state(NUMBERS_KEY, json.dumps({"day": day, "ids": ids}))
        return ids.index(mail_id) + 1

    def mail_id(self, number: int) -> int | None:
        _, ids = self._load()
        return ids[number - 1] if 1 <= number <= len(ids) else None

    def _load(self) -> tuple[str, list[int]]:
        today = datetime.fromtimestamp(self._clock(), self._tz).date().isoformat()
        try:
            data = json.loads(self._store.get_state(NUMBERS_KEY) or "{}")
        except json.JSONDecodeError:
            data = {}
        ids = data.get("ids") if isinstance(data, dict) and data.get("day") == today else None
        return today, [int(i) for i in ids] if isinstance(ids, list) else []


class MailDigest:
    def __init__(
        self,
        store: Store,
        config: Config,
        numbers: MailNumbers,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._tz = config.timezone
        self._reply_days = config.mail_reply_days
        self._numbers = numbers
        self._clock = clock

    def compose(self, header: str | None = None) -> str:
        """The mail digest now (also what the mail_digest tool returns); `header` replaces the dated first line."""
        now = self._clock()
        last = float(self._store.get_state(LAST_DIGEST_KEY) or now - 86_400)
        since = _iso(now - self._reply_days * 86_400)
        needs = self._store.mail_needing_reply(since)
        known = [m for m in self._store.unlabelled_from_known(since) if m.id not in {n.id for n in needs}]
        actions = self._store.mail_actions(_iso(last), since)
        if not (needs or known or actions):
            return EMPTY
        local = datetime.fromtimestamp(now, self._tz)
        lines = [header or f"Mail, {local:%a} {local.day} {local:%b}:"]
        if needs or known:
            lines.append("Needs a reply:")
            lines += self._block([*needs, *known], local)
        if actions:
            lines.append("Actions:")
            lines += self._block(actions, local)
        return "\n".join(lines)

    def _block(self, mails: list[MailHeader], local: datetime) -> list[str]:
        lines = [self._line(mail, local) for mail in mails[:BLOCK_LIMIT]]
        if len(mails) > BLOCK_LIMIT:
            lines.append(f"…and {len(mails) - BLOCK_LIMIT} more")
        return lines

    def _line(self, mail: MailHeader, local: datetime) -> str:
        received = datetime.fromisoformat(mail.received_at).astimezone(self._tz)
        days = (local.date() - received.date()).days
        age = "today" if days <= 0 else f"{days} d"
        who = mail.sender_name or mail.sender_address
        what = mail.summary or mail.subject or "(no subject)"
        return f"{self._numbers.number(mail.id)}. [{mail.account}] {who}: {what} ({age})"
