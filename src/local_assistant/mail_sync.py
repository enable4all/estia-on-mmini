"""Keep the mail headers of each mailbox up to date with Graph delta queries (no bodies).

Every few minutes, per mailbox, Inbox and Sent Items are asked for what changed since the last delta
link: new and changed mails are stored (headers only), removed ones (archived, deleted, moved out of
the Inbox) are deleted here too, then "answered" is recomputed and mail older than the retention
period is pruned. An expired sign-in pauses that mailbox with one note until the user signs in again.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.config import Config, MailAccount
from local_assistant.graph import DeltaExpired, Graph, GraphError
from local_assistant.mail_auth import MailAuthError, SignInExpired, TokenStore
from local_assistant.part_status import PartStatus, Reason, mail_part
from local_assistant.store import MailFields, MailFolder, Store
from local_assistant.timefmt import local_hhmm

log = logging.getLogger(__name__)

SELECT = (
    "id,conversationId,internetMessageId,from,toRecipients,ccRecipients,subject,receivedDateTime,sentDateTime,"
    "isRead,hasAttachments,importance,inferenceClassification"
)
_FOLDERS: dict[MailFolder, str] = {"inbox": "inbox", "sent": "sentitems"}
Notify = Callable[[str], Awaitable[None]]


def initial_delta_url(folder: MailFolder, now: float, days: int) -> str:
    """The first delta query of a folder: headers only, mail of the last `days` days."""
    since = datetime.fromtimestamp(now - days * 86_400, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"/me/mailFolders/{_FOLDERS[folder]}/messages/delta?$select={SELECT}&$filter=receivedDateTime+ge+{since}"


def _iso(value: Any) -> str:
    """Graph's "2026-10-08T07:00:00Z" as the database's UTC ISO form."""
    return datetime.fromisoformat(str(value)).astimezone(UTC).isoformat(timespec="seconds")


def _address(entry: Any) -> tuple[str, str]:
    email = entry.get("emailAddress", {}) if isinstance(entry, dict) else {}
    return str(email.get("name") or ""), str(email.get("address") or "").lower()


def _fields(item: dict[str, Any], folder: MailFolder) -> MailFields | None:
    when = item.get("sentDateTime") if folder == "sent" else None
    when = when or item.get("receivedDateTime")
    if not isinstance(item.get("id"), str) or not when:
        return None
    sender_name, sender_address = _address(item.get("from"))
    copied = item.get("ccRecipients") or [] if folder == "sent" else []  # Cc'd people are correspondents too
    recipients = [_address(entry)[1] for entry in [*(item.get("toRecipients") or []), *copied]]
    return MailFields(
        graph_id=item["id"],
        conversation_id=str(item.get("conversationId") or ""),
        internet_message_id=str(item.get("internetMessageId") or ""),
        sender_name=sender_name,
        sender_address=sender_address,
        recipients=",".join(address for address in recipients if address),
        subject=str(item.get("subject") or ""),
        received_at=_iso(when),
        is_read=bool(item.get("isRead")),
        has_attachments=bool(item.get("hasAttachments")),
        importance=str(item.get("importance") or "normal"),
        focused=item.get("inferenceClassification", "focused") != "other",
    )


def _reason(exc: Exception) -> tuple[Reason, int]:
    """A failed sync as a fixed reason and the HTTP status (part_status): never Graph's text."""
    if isinstance(exc, GraphError) and exc.status:
        return ("busy" if exc.status == 429 else "refused"), exc.status
    if isinstance(exc, GraphError | ValueError):
        return "bad_answer", 0
    if isinstance(exc, MailAuthError):
        return "refused", 0  # the token endpoint failed without saying the sign-in expired
    return "no_answer", 0  # OSError, TimeoutError


class MailSync:
    def __init__(
        self,
        store: Store,
        graph: Graph,
        config: Config,
        tokens: TokenStore,
        *,
        notify: Notify,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        status: PartStatus | None = None,
    ) -> None:
        self._store = store
        self._graph = graph
        self._config = config
        self._tokens = tokens
        self._notify = notify
        self._clock = clock
        self._sleep = sleep
        self._status = status or PartStatus(None)  # what each mailbox's last real sync did (doctor, /status)
        self._paused: dict[str, str | None] = {}  # account name -> refresh token when it was paused
        self._last_ok: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self.rounds = 0  # finished sync rounds since the start (the digest waits for the first)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.sync_all()
            except Exception:  # never take the bridge's task group down
                log.exception("mail sync failed; trying again next round")
            await self._sleep(self._config.mail_sync_minutes * 60)

    async def sync_all(self) -> None:
        for account in self._config.mail_accounts:
            await self.sync_account(account)
        self.rounds += 1
        cutoff = self._clock() - self._config.mail_keep_days * 86_400
        before = datetime.fromtimestamp(cutoff, UTC).isoformat(timespec="seconds")
        removed = self._store.prune_mail(before)
        self._store.prune_triage(before)  # how mail was sorted is kept as long as the mail itself
        if removed:
            log.info("mail: pruned %d headers older than %d days", removed, self._config.mail_keep_days)

    async def sync_account(self, account: MailAccount) -> None:
        if account.name in self._paused:
            if self._tokens.refresh_token(account.address) == self._paused[account.name]:
                return  # still waiting for a new sign-in
            del self._paused[account.name]
        try:
            changes = 0
            for folder in _FOLDERS:
                changes += await self._sync_folder(account, folder)
            self._store.update_answered(account.name)
            self._last_ok[account.name] = self._clock()
            self._errors.pop(account.name, None)
            self._status.ok(mail_part(account.name))
            if changes:
                log.info("mail: %s synced, %d changes", account.name, changes)
        except SignInExpired:
            self._paused[account.name] = self._tokens.refresh_token(account.address)
            self._errors.pop(account.name, None)
            self._status.failed(mail_part(account.name), "sign_in")
            log.warning("mail: %s needs a new sign-in; paused", account.name)
            await self._notify(prompts.MAIL_SIGN_IN.format(name=account.name, address=account.address))
        except DeltaExpired:
            self._store.clear_mail_sync(account.name)
            log.warning("mail: %s delta link expired; the next round syncs afresh", account.name)
        except (GraphError, MailAuthError, OSError, TimeoutError, ValueError) as exc:
            self._errors[account.name] = type(exc).__name__
            self._status.failed(mail_part(account.name), *_reason(exc))
            log.warning("mail: %s sync failed: %s", account.name, exc)
        except Exception as exc:  # an odd page or a database error: this mailbox only, recorded for mini_status
            self._errors[account.name] = type(exc).__name__
            self._status.failed(mail_part(account.name), "error")
            log.exception("mail: %s sync failed unexpectedly", account.name)

    async def _sync_folder(self, account: MailAccount, folder: MailFolder) -> int:
        link = self._store.mail_delta_link(account.name, folder) or initial_delta_url(
            folder, self._clock(), self._config.mail_first_sync_days
        )
        changes = 0
        while True:
            page = await self._graph.get(account.address, link, prefer="odata.maxpagesize=50")
            for item in page.get("value") or []:
                if not isinstance(item, dict):
                    continue
                if "@removed" in item:
                    changes += self._store.remove_mail(account.name, folder, str(item.get("id")))
                    continue
                fields = _fields(item, folder)
                if fields is not None:
                    self._store.upsert_mail(account.name, folder, fields)
                    changes += 1
            if isinstance(page.get("@odata.nextLink"), str):
                link = page["@odata.nextLink"]
                continue
            if isinstance(page.get("@odata.deltaLink"), str):
                self._store.set_mail_delta_link(account.name, folder, page["@odata.deltaLink"])
                return changes
            raise GraphError("delta page without a next or delta link")

    def status_line(self, tz: ZoneInfo) -> str:
        """For mini_status: "Mail: family ok (09:10) · work needs sign-in"."""
        parts = []
        for account in self._config.mail_accounts:
            last = self._last_ok.get(account.name)
            if account.name in self._paused:
                parts.append(f"{account.name} needs sign-in")
            elif account.name in self._errors:
                since = f", last sync {local_hhmm(last, tz)}" if last else ""
                parts.append(f"{account.name} error ({self._errors[account.name]}){since}")
            elif last is not None:
                parts.append(f"{account.name} ok ({local_hhmm(last, tz)})")
            else:
                parts.append(f"{account.name} not synced yet")
        return "Mail: " + " · ".join(parts)
