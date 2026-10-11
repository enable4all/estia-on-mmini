"""Sort new mail while the bridge is idle: one structured Gemma call per mail (label + one-line summary).

The body is fetched live as text, earlier quoted replies cut, at most 4,000 characters, and dropped
right after the call: only the label and the checked summary are stored. "waiting" (the user replied
last) is set by code without Gemma. Like the profiler, sorting never runs while the bridge answers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from local_assistant import prompts
from local_assistant.config import Config
from local_assistant.fetcher import strip_control_tokens
from local_assistant.gemma import GemmaClient, GemmaError
from local_assistant.graph import Graph, GraphError
from local_assistant.mail_auth import MailAuthError
from local_assistant.mail_prefilter import header_noise, is_bounce, needs_attention
from local_assistant.profile import check_fact
from local_assistant.store import MailHeader, Store

log = logging.getLogger(__name__)

LABELS = ("urgent_reply", "reply", "action", "reference", "noise")
SORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": list(LABELS)}, "summary": {"type": "string"}},
    "required": ["label", "summary"],
}
BATCH = 10
BODY_CHARS = 4_000
SUMMARY_CHARS = 140
MAX_ATTEMPTS = 3
AUDIT_EVERY = 5  # one in five pre-filtered mails is double-checked by Gemma
AUDIT_KEY = "mail_prefilter_count"
JEV_REST_SECONDS = 300.0
_QUOTE_START = re.compile(
    r"^(?:>|-{3,}\s*Original Message|_{10,}|On .{0,200}wrote:\s*$|Op .{0,200}schreef.*:\s*$)", re.IGNORECASE
)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def clean_body(text: str) -> str:
    """The new part of a mail: stops at the first quoted earlier message, whitespace tidied, capped."""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line.strip()):
            break
        kept.append(re.sub(r"[ \t]+", " ", line).strip())
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return body[:BODY_CHARS]


def _summary(raw: str) -> str:
    text = re.sub(r"\s+", " ", _URL.sub("", raw)).strip()
    return "" if check_fact(text, SUMMARY_CHARS) else text


# Graph answers about this one mail (gone, refused): they count as a try; anything else is an outage that
# pauses sorting until the next round without using up the mail's tries
_THIS_MAIL = frozenset({400, 403, 404})


class _Outage(Exception):
    """Graph, the sign-in or the network is down for now."""

    def __init__(self, reason: str, *, mailbox: bool = False) -> None:
        super().__init__(reason)
        self.mailbox = mailbox  # the whole mailbox (its sign-in), not maybe this one mail


class NoiseJudge(Protocol):
    async def noise_probability(self, sender: str, subject: str, body: str) -> float | None: ...


class MailSorter:
    def __init__(
        self,
        store: Store,
        graph: Graph,
        gemma: GemmaClient,
        config: Config,
        *,
        busy: Callable[[], bool],
        poll_seconds: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jev: NoiseJudge | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._graph = graph
        self._gemma = gemma
        self._addresses = {account.name: account.address for account in config.mail_accounts}
        self._busy = busy
        self._poll = poll_seconds
        self._sleep = sleep
        self._jev = jev
        self._clock = clock
        self.prefilter = config.mail_prefilter  # headers mark bulk mail as noise without a model
        self._threshold = config.mail_jev_threshold
        self.audit_every = AUDIT_EVERY
        self._jev_rest_until = 0.0

    async def run_forever(self) -> None:
        while True:
            await self._sleep(self._poll)
            if self._busy():
                continue
            try:
                await self.sort_batch()
            except Exception:
                log.exception("mail sorting crashed; trying again later")

    async def sort_batch(self) -> int:
        """Label up to BATCH unlabelled Inbox mails, newest first across the mailboxes; stops when the bridge gets
        busy. An outage skips only that mailbox this round; a mail that fails while the next one of the same mailbox
        works is that mail's problem and counts as one of its tries."""
        candidates = [mail for name in self._addresses for mail in self._store.unlabelled_mail(BATCH, name)]
        candidates.sort(key=lambda mail: mail.received_at, reverse=True)  # each mailbox has its own share
        done = handled = 0
        skipped: set[str] = set()
        suspect: dict[str, MailHeader] = {}  # mailbox -> a mail that failed; the next one decides whose fault
        for mail in candidates:
            if handled >= BATCH or self._busy():
                break
            if mail.account in skipped:
                continue
            if mail.answered_at is not None:
                self._store.set_mail_label(mail.id, "waiting", "", mail.graph_id)
                done += 1
                handled += 1
                continue
            try:
                labelled = await self._sort(mail, self._addresses[mail.account])
            except GemmaError as exc:
                log.warning("mail sorting paused: Gemma failed (%s)", type(exc).__name__)
                break
            except _Outage as exc:
                if exc.mailbox or mail.account in suspect:  # the mailbox, not one mail: try again next round
                    skipped.add(mail.account)
                    suspect.pop(mail.account, None)
                    log.warning("mail sorting: %s skipped this round: %s", mail.account, exc)
                else:
                    suspect[mail.account] = mail
                continue
            handled += 1
            done += labelled
            broken = suspect.pop(mail.account, None)
            if broken is not None:  # the next mail worked: that one failing is its own problem
                done += self._failed(broken, "(could not be read)")
        return done

    async def _sort(self, mail: MailHeader, address: str) -> int:
        try:
            message = await self._graph.message_text(address, mail.graph_id)
        except GraphError as exc:
            if exc.status not in _THIS_MAIL:
                raise _Outage(f"Graph failed ({exc.status or type(exc).__name__})") from exc
            log.warning("mail %d: body not readable (%s)", mail.id, exc.status)
            return self._failed(mail, "(could not be read)")
        except MailAuthError as exc:  # the mailbox's sign-in: no mail's fault
            raise _Outage(type(exc).__name__, mailbox=True) from exc
        except (OSError, TimeoutError) as exc:  # the network: no mail's fault
            raise _Outage(type(exc).__name__) from exc
        body = clean_body(str((message.get("body") or {}).get("content") or ""))
        sender = f"{mail.sender_name} <{mail.sender_address}>".strip()
        jev_p: float | None = None
        headers = message.get("internetMessageHeaders")
        known = self._store.has_written_to(mail.sender_address) or self._store.in_users_thread(mail.conversation_id)
        if not known and not is_bounce(headers) and not needs_attention(mail.subject, body):
            hint = header_noise(headers) if self.prefilter else None
            if hint is None or not hint[1]:
                jev_p = await self._ask_jev(sender, mail.subject, body)
                if self._busy():  # the user wrote meanwhile: this mail waits for the next quiet moment
                    return 0
            tier = reason = ""
            if hint is not None and (hint[1] or (jev_p is not None and jev_p >= 0.5)):
                tier, reason = "headers", hint[0]
            elif jev_p is not None and jev_p >= self._threshold:
                tier, reason = "jev", "automated or promotional"
            if tier and not self._audit_due():
                return self._noise(mail, reason, tier, jev_p)
            audit_tier = tier  # Gemma double-checks this one, and its label wins
        else:
            audit_tier = ""
        content = strip_control_tokens(  # anyone can write a mail: no chat-template tokens or hidden characters
            prompts.MAIL_SORT_INPUT.format(
                sender=sender,
                subject=mail.subject,
                body=body or "(empty)",
            )
        )
        completion = await self._gemma.chat(
            [{"role": "system", "content": prompts.MAIL_SORT_RULES}, {"role": "user", "content": content}],
            max_tokens=200,
            temperature=0,
            json_schema=SORT_SCHEMA,
        )
        try:
            data = json.loads(completion.content)
        except json.JSONDecodeError:
            data = None
        label = data.get("label") if isinstance(data, dict) else None
        if label not in LABELS:
            log.warning("mail %d: unusable sorting answer", mail.id)
            return self._failed(mail, "(could not be sorted)")
        summary = data.get("summary") if isinstance(data, dict) else ""
        if not self._store.set_mail_label(mail.id, str(label), _summary(str(summary or "")), mail.graph_id):
            return 0  # removed by the sync while Gemma was reading it
        self._store.record_triage(mail.id, audit_tier or "gemma", jev_p, str(label))
        return 1

    async def _ask_jev(self, sender: str, subject: str, body: str) -> float | None:
        """Jev's probability that the mail needs nothing; after a failure Jev is left alone for 5 minutes."""
        if self._jev is None or self._clock() < self._jev_rest_until:
            return None
        p = await self._jev.noise_probability(sender, subject, body)
        if p is None:
            self._jev_rest_until = self._clock() + JEV_REST_SECONDS
            key = f"mail_jev_failed_{datetime.fromtimestamp(self._clock(), UTC):%Y-%m-%d}"
            self._store.set_state(key, str(int(self._store.get_state(key) or 0) + 1))
        return p

    def _audit_due(self) -> bool:
        """Every `audit_every`-th pre-filtered mail also goes to Gemma (whose label wins), to measure the pre-filter."""
        if self.audit_every <= 0:
            return False
        count = int(self._store.get_state(AUDIT_KEY) or 0) + 1
        self._store.set_state(AUDIT_KEY, str(count))
        return count % self.audit_every == 0

    def _noise(self, mail: MailHeader, reason: str, tier: str, jev_p: float | None) -> int:
        """Noise without Gemma: the summary says why (the subject is outside text, checked like any summary; when
        it fails the check, only the reason is kept)."""
        if not self._store.set_mail_label(
            mail.id, "noise", _summary(f"{reason}: {mail.subject}") or reason, mail.graph_id
        ):
            return 0  # removed by the sync meanwhile
        self._store.record_triage(mail.id, tier, jev_p, None)
        log.info("mail %d: noise by %s", mail.id, tier)
        return 1

    def status_line(self) -> str:
        """How the last 7 days of mail were sorted; how often Jev's own yes/no (at 0.5) matched Gemma's noise or not;
        how often Gemma's double-checks agreed with the pre-filter; Jev's failures today."""
        since = datetime.fromtimestamp(self._clock() - 7 * 86_400, UTC).isoformat(timespec="seconds")
        rows = self._store.triage_since(since)
        count = {tier: sum(1 for row in rows if row[0] == tier) for tier in ("headers", "jev", "gemma")}
        line = f"Mail sorting (7 days): {count['headers']} by headers, {count['jev']} by Jev, {count['gemma']} by Gemma"
        compared = [(p, label) for tier, p, label in rows if tier == "gemma" and p is not None and label is not None]
        if compared:
            agreed = sum(1 for p, label in compared if (p >= 0.5) == (label == "noise"))
            line += f"; Jev agreed with Gemma {agreed} of {len(compared)}"
        audits = [label for tier, _, label in rows if tier != "gemma" and label is not None]
        if audits:
            confirmed = sum(1 for label in audits if label == "noise")
            line += f"; Gemma checked the pre-filter {confirmed} of {len(audits)} agreed"
        failed = int(
            self._store.get_state(f"mail_jev_failed_{datetime.fromtimestamp(self._clock(), UTC):%Y-%m-%d}") or 0
        )
        if failed:
            line += f"; Jev did not answer {failed} time{'s' if failed != 1 else ''} today"
        return line

    def _failed(self, mail: MailHeader, summary: str) -> int:
        if self._store.bump_mail_attempts(mail.id, mail.graph_id) >= MAX_ATTEMPTS:
            return 1 if self._store.set_mail_label(mail.id, "reference", summary, mail.graph_id) else 0
        return 0
