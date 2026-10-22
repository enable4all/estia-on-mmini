"""The mail part of the morning digest (morning_digest.py sends it) and the day's mail numbers.

Mail that needs a reply (urgent first, then oldest; unanswered, still in the Inbox, at most `reply_days`
old; unlabelled mail from people the user writes to is listed with its subject) and actions that arrived
since the previous digest. The morning digest gives each mailbox its own block under a bold heading
(`digest_part`), its senders, summaries and subjects without links (a note of code's: never shown); the
mail_digest tool (`compose`) keeps both mailboxes together, each line tagged with its mailbox, its text as it was. Both show the same mail: at most `BLOCK_LIMIT` replies and `BLOCK_LIMIT` actions over all mailboxes.
Numbers are kept for the whole day, so "read 2" means the same mail in the digest, in search results and to
Gemma's tools; the digest and the tool give them in the digest's order, so the digest reads 1..n whichever runs
first.

A mailbox that has not been read (its part in status.json failing for a sync round at least, its last good read older
than `STALE_ROUNDS` sync rounds: one bad round is not flagged, also not after a long downtime) gets one line from code, whatever else the digest shows: "[work] not read since
Mon 09:10: needs sign-in." in the digest and the mail tools' headers, and counts and times only in the call's glance.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.calendar_tools import clean_note
from local_assistant.config import Config
from local_assistant.part_status import PartStatus, mail_part, reason_text, state
from local_assistant.store import MailHeader, Store
from local_assistant.timefmt import local_recent

NUMBERS_KEY = "mail:numbers"
LAST_DIGEST_KEY = "mail:last_digest_at"  # set by the morning digest: actions since then are new
BLOCK_LIMIT = 10
EMPTY = "Mail: nothing needs you today."
GLANCE_EMPTY = "Mail digest: nothing needs a reply or an action."
STALE_ROUNDS = 2  # a failing mailbox whose last good read is older than this many sync rounds has not been read
NOTE_CHARS = 255  # a sender or a subject in the digest: Outlook's own limit, so a real one is never cut
NO_NAME = "(no name)"  # the digest's sender when nothing of it is left without links


@dataclass(frozen=True)
class Stale:
    """A mailbox that has not been read: its name, its last good read (None: never) and why, in words."""

    name: str
    since: float | None
    reason: str


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
        status: PartStatus | None = None,
    ) -> None:
        self._store = store
        self._tz = config.timezone
        self._reply_days = config.mail_reply_days
        self._mailboxes = tuple(account.name for account in config.mail_accounts)
        self._numbers = numbers
        self._clock = clock
        self._status = status  # each mailbox's last real sync (mail_sync records it; it survives a restart)
        self._stale_after = STALE_ROUNDS * config.mail_sync_minutes * 60

    def compose(self, header: str | None = None) -> str:
        """The mail digest now (also what the mail_digest tool returns); `header` replaces the dated first line."""
        now = self._clock()
        needs, known, actions = self._pending(now)
        if not (needs or known or actions):
            return EMPTY
        local = datetime.fromtimestamp(now, self._tz)
        self._number([*needs, *known], actions)
        lines = [header or f"Mail, {local:%a} {local.day} {local:%b}:"]
        if needs or known:
            lines.append("Needs a reply:")
            lines += self._block([*needs, *known], local)
        if actions:
            lines.append("Actions:")
            lines += self._block(actions, local)
        return "\n".join(lines)

    def digest_part(self) -> str:
        """The morning digest's mail: each mailbox its own block under a bold heading ("**Mail (work)**", as /status
        names it; with one
        mailbox "**Mail**"), its mail that needs a reply, then its actions, one per line with the day's number, in
        the order shown; the tool's mail (at most `BLOCK_LIMIT` replies and actions over all mailboxes), the rest
        counted under its mailbox; a blank line between mailboxes (markup: sent as Signal's text styles)."""
        needs, known, actions = self._pending(self._clock())
        replies = [*needs, *known]
        stale = self.stale_lines()
        tail = "\n\n" + "\n".join(stale) if stale else ""  # after the mail, a block of its own
        if not (replies or actions):
            return EMPTY + tail
        local = datetime.fromtimestamp(self._clock(), self._tz)
        self._number(replies, actions)
        several = len({*self._mailboxes, *(mail.account for mail in [*replies, *actions])}) > 1
        blocks = []
        for name in self._order(replies, actions):
            lines = [f"**Mail ({name})**" if several else "**Mail**"]
            for title, mails in (("Needs a reply:", replies), ("Actions:", actions)):
                mine = [self._line(mail, local, note=True) for mail in mails[:BLOCK_LIMIT] if mail.account == name]
                more = sum(1 for mail in mails[BLOCK_LIMIT:] if mail.account == name)
                if mine or more:
                    lines += [title, *mine, *([f"…and {more} more"] if more else [])]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) + tail

    def stale(self) -> list[Stale]:
        """The mailboxes not read: failing for a sync round at least, with the last good read (or, never read, the first
        failure) older than `STALE_ROUNDS` sync rounds; in the configured order. Nothing on record or no status: none."""
        if self._status is None:
            return []
        now, records, found = self._clock(), self._status.snapshot(), []
        for name in self._mailboxes:
            record = records.get(mail_part(name))
            if record is None or record.failed is None or state(record) != "failing":
                continue
            if now - (record.ok if record.ok is not None else record.failed) <= self._stale_after:
                continue  # one bad round is not flagged
            if now - record.failed < self._stale_after / STALE_ROUNDS:
                continue  # nor the first bad round after a long downtime: failing for a round at least
            reason = prompts.MAIL_STALE_REASONS.get(record.reason) or reason_text(record.reason, record.code)
            found.append(Stale(name, record.ok, reason))
        return found

    def stale_lines(self) -> list[str]:
        """ "[work] not read since Mon 09:10: needs sign-in.", one line per mailbox not read."""
        now = self._clock()
        return [
            prompts.MAIL_STALE.format(name=item.name, when=local_recent(item.since, now, self._tz), reason=item.reason)
            if item.since is not None
            else prompts.MAIL_STALE_NEVER.format(name=item.name, reason=item.reason)
            for item in self.stale()
        ]

    def _stale_count(self) -> str:
        """The glance's line: how many mailboxes were not read and since when (the latest of their last good reads:
        none was read since), never a name or a reason."""
        stale = self.stale()
        if not stale:
            return ""
        es = "" if len(stale) == 1 else "es"
        reads = [item.since for item in stale if item.since is not None]
        if not reads:
            return prompts.GLANCE_STALE_NEVER.format(count=len(stale), es=es)
        when = local_recent(max(reads), self._clock(), self._tz)
        return prompts.GLANCE_STALE.format(count=len(stale), es=es, when=when)

    def _order(self, replies: list[MailHeader], actions: list[MailHeader]) -> list[str]:
        """The mailboxes with mail, in the digest's order: as configured, then any other by name."""
        shown = {mail.account for mail in [*replies, *actions]}
        return [name for name in (*self._mailboxes, *sorted(shown - set(self._mailboxes))) if name in shown]

    def _number(self, replies: list[MailHeader], actions: list[MailHeader]) -> None:
        """The day's numbers for the mail both show, given in the digest's order (each mailbox's replies, then its
        actions): the digest reads 1..n also when the tool ran first ("any mail?" at 08:00)."""
        for name in self._order(replies, actions):
            for mails in (replies[:BLOCK_LIMIT], actions[:BLOCK_LIMIT]):
                for mail in mails:
                    if mail.account == name:
                        self._numbers.number(mail.id)

    def glance(self) -> str:
        """The digest as numbers only, for the start of a call: no sender, subject or summary (outside text), and no
        mail gets its number of the day; a line with the count of mailboxes not read and a time, never their names."""
        stale = self._stale_count()
        return self._glance_counts() + (f"\n{stale}" if stale else "")

    def _glance_counts(self) -> str:
        needs, known, found = self._pending(self._clock())
        answer, actions = len(needs) + len(known), len(found)
        if not (answer or actions):
            return GLANCE_EMPTY
        urgent = sum(1 for mail in needs if mail.label == "urgent_reply")
        parts = [f"{answer} to answer" + (f" ({urgent} urgent)" if urgent else "")] if answer else []
        parts += [f"{actions} action{'' if actions == 1 else 's'}"] if actions else []
        return f"Mail digest: {', '.join(parts)}."

    def _pending(self, now: float) -> tuple[list[MailHeader], list[MailHeader], list[MailHeader]]:
        """What the digest shows now: mail that needs a reply (labelled, then unlabelled mail from people the user
        writes to) and actions since the last digest."""
        last = float(self._store.get_state(LAST_DIGEST_KEY) or now - 86_400)
        since = _iso(now - self._reply_days * 86_400)
        needs = self._store.mail_needing_reply(since)
        known = [m for m in self._store.unlabelled_from_known(since) if m.id not in {n.id for n in needs}]
        return needs, known, self._store.mail_actions(_iso(last), since)

    def _block(self, mails: list[MailHeader], local: datetime) -> list[str]:
        lines = [self._line(mail, local) for mail in mails[:BLOCK_LIMIT]]
        if len(mails) > BLOCK_LIMIT:
            lines.append(f"…and {len(mails) - BLOCK_LIMIT} more")
        return lines

    def _line(self, mail: MailHeader, local: datetime, *, note: bool = False) -> str:
        """ "2. [work] Bob Lee: Asks about November shipping. (3 d)", as the mail_digest tool gives it (Gemma's). `note`:
        the morning digest's, without the [mailbox] under its own heading, and the sender, the summary or the subject
        without links (owner, 21 Oct: never shown; a sender with only an address is named by the part before the @)."""
        received = datetime.fromisoformat(mail.received_at).astimezone(self._tz)
        days = (local.date() - received.date()).days
        age = "today" if days <= 0 else f"{days} d"
        if note:
            # one line each: a line break in a sender's name or a subject could start a line that looks like code's own
            local_part = mail.sender_address.partition("@")[0]
            who = clean_note(mail.sender_name, NOTE_CHARS) or clean_note(local_part, NOTE_CHARS) or NO_NAME
            what = clean_note(mail.summary, NOTE_CHARS) or clean_note(mail.subject, NOTE_CHARS) or "(no subject)"
            return f"{self._numbers.number(mail.id)}. {' '.join(who.split())}: {' '.join(what.split())} ({age})"
        who = " ".join((mail.sender_name or mail.sender_address).split())
        what = " ".join((mail.summary or mail.subject or "(no subject)").split())
        return f"{self._numbers.number(mail.id)}. [{mail.account}] {who}: {what} ({age})"
