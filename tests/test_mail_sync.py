import asyncio
import sqlite3
from pathlib import Path

import pytest

from local_assistant.config import Config, MailAccount
from local_assistant.graph import GRAPH, Graph
from local_assistant.mail_auth import AccessTokens, TokenStore
from local_assistant.mail_sync import MailSync, initial_delta_url
from local_assistant.part_status import PartStatus, Record
from local_assistant.store import MailFolder, Store
from tests.conftest import ME, TZ
from tests.fakes.fake_http import FakeHttp

TENANT = "11111111-2222-4333-8444-555555555555"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
NOW = 1_791_452_400.0  # 2026-10-08 09:40 UTC (11:40 Amsterdam)
FAMILY = MailAccount("family", "info@example.com")
WORK = MailAccount("work", "contact@example.app")


def config(tmp_path: Path) -> Config:
    return Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        mail_tenant_id=TENANT,
        mail_client_id="66666666-7777-4888-9999-000000000000",
        mail_accounts=(FAMILY, WORK),
    )


def mail(graph_id: str, **extra: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": graph_id,
        "conversationId": f"conv-{graph_id}",
        "internetMessageId": f"<{graph_id}@example.com>",
        "from": {"emailAddress": {"name": "Anna Smith", "address": "Anna@Example.com"}},
        "toRecipients": [
            {"emailAddress": {"address": "info@example.com"}},
            {"emailAddress": {"address": "b@example.com"}},
        ],
        "subject": "Moving Friday's meeting",
        "receivedDateTime": "2026-10-08T07:00:00Z",
        "sentDateTime": "2026-10-08T06:59:00Z",
        "isRead": False,
        "hasAttachments": True,
        "importance": "high",
        "inferenceClassification": "other",
    }
    item.update(extra)
    return item


class Harness:
    def __init__(self, tmp_path: Path, store: Store) -> None:
        self.cfg = config(tmp_path)
        self.http = FakeHttp()
        self.tokens = TokenStore(self.cfg.mail_token_path)
        self.tokens.save(FAMILY.address, "rt-family")
        self.tokens.save(WORK.address, "rt-work")
        self.http.add(TOKEN_URL, 200, {"access_token": "at", "expires_in": 3600})
        self.notes: list[str] = []

        async def notify(text: str) -> None:
            self.notes.append(text)

        async def sleep(seconds: float) -> None:
            return None

        graph = Graph(
            AccessTokens(self.cfg, self.tokens, http=self.http, clock=lambda: NOW), http=self.http, sleep=sleep
        )
        self.status = PartStatus(tmp_path / "status.json", clock=lambda: NOW)
        self.sync = MailSync(store, graph, self.cfg, self.tokens, notify=notify, clock=lambda: NOW, status=self.status)

    def folder(self, folder: MailFolder, *pages: dict[str, object]) -> str:
        """Script the delta pages of both mailboxes' folder (same answers for each mailbox)."""
        url = GRAPH + initial_delta_url(folder, NOW, 30)
        for page in pages:
            self.http.add(url, 200, page)
        return url


def test_initial_delta_url_selects_headers_only_for_30_days() -> None:
    url = initial_delta_url("inbox", NOW, 30)
    assert url.startswith("/me/mailFolders/inbox/messages/delta?$select=id,conversationId,internetMessageId,from,")
    assert "body" not in url and "bodyPreview" not in url
    assert url.endswith("&$filter=receivedDateTime+ge+2026-09-08T09:40:00Z")
    assert initial_delta_url("sent", NOW, 30).startswith("/me/mailFolders/sentitems/messages/delta?")


async def test_first_sync_stores_headers_follows_pages_and_keeps_the_delta_link(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    next_link = f"{GRAPH}/me/mailFolders/inbox/messages/delta?$skiptoken=p2"
    h.folder("inbox", {"value": [mail("g1")], "@odata.nextLink": next_link})
    h.http.add(
        next_link, 200, {"value": [mail("g2", subject="Invoice")], "@odata.deltaLink": f"{GRAPH}/inbox-delta?t=1"}
    )
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/sent-delta?t=1"})
    await h.sync.sync_all()
    stored = sorted(store.search_mail("anna", account="family"), key=lambda m: m.graph_id)
    assert [m.graph_id for m in stored] == ["g1", "g2"]
    first = stored[0]
    assert (first.sender_address, first.recipients, first.received_at) == (
        "anna@example.com",
        "info@example.com,b@example.com",
        "2026-10-08T07:00:00+00:00",
    )
    assert (first.importance, first.focused, first.has_attachments, first.is_read) == ("high", False, True, False)
    assert store.mail_delta_link("family", "inbox") == f"{GRAPH}/inbox-delta?t=1"
    assert store.mail_delta_link("work", "sent") == f"{GRAPH}/sent-delta?t=1"
    assert h.sync.status_line(TZ) == "Mail: family ok (11:40) · work ok (11:40)"


async def test_next_rounds_use_the_delta_link_removals_and_answers(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.folder("inbox", {"value": [mail("g1")], "@odata.deltaLink": f"{GRAPH}/inbox-delta?t=1"})
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/sent-delta?t=1"})
    await h.sync.sync_all()
    h.http.add(
        f"{GRAPH}/inbox-delta?t=1",
        200,
        {
            "value": [{"id": "g1", "@removed": {"reason": "deleted"}}, mail("g3", conversationId="conv-x")],
            "@odata.deltaLink": f"{GRAPH}/inbox-delta?t=2",
        },
    )
    reply = mail(
        "s1", conversationId="conv-x", sentDateTime="2026-10-08T08:00:00Z", receivedDateTime="2026-10-08T08:00:00Z"
    )
    h.http.add(f"{GRAPH}/sent-delta?t=1", 200, {"value": [reply], "@odata.deltaLink": f"{GRAPH}/sent-delta?t=2"})
    await h.sync.sync_all()
    family = {m.graph_id: m for m in store.search_mail("anna", account="family")}
    assert "g1" not in family  # archived or deleted in Outlook: gone here too
    assert family["g3"].answered_at == "2026-10-08T08:00:00+00:00"
    assert family["s1"].folder == "sent"


async def test_an_expired_sign_in_pauses_one_mailbox_with_one_note(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.http.answers[TOKEN_URL] = []
    h.http.add(TOKEN_URL, 400, {"error": "invalid_grant"})
    h.folder("inbox", {"value": [], "@odata.deltaLink": f"{GRAPH}/d"})
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/d"})
    await h.sync.sync_all()
    await h.sync.sync_all()
    assert h.notes == [
        "Mailbox family needs a new sign-in: run scripts/mail-login.sh info@example.com on the Mac.",
        "Mailbox work needs a new sign-in: run scripts/mail-login.sh contact@example.app on the Mac.",
    ]
    assert h.sync.status_line(TZ) == "Mail: family needs sign-in · work needs sign-in"
    token_requests = len([r for r in h.http.requests if r[1] == TOKEN_URL])
    await h.sync.sync_all()
    assert len([r for r in h.http.requests if r[1] == TOKEN_URL]) == token_requests  # paused: no more tries
    h.tokens.save(FAMILY.address, "rt-new")  # the user signed in again
    h.http.answers[TOKEN_URL] = []
    h.http.add(TOKEN_URL, 200, {"access_token": "at", "expires_in": 3600})
    await h.sync.sync_all()
    assert h.sync.status_line(TZ).startswith("Mail: family ok")


async def test_an_expired_delta_link_starts_the_folder_afresh(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    store.set_mail_delta_link("family", "inbox", f"{GRAPH}/old-delta")
    store.set_mail_delta_link("family", "sent", f"{GRAPH}/old-sent")
    h.http.add(f"{GRAPH}/old-delta", 410, {"error": {"code": "SyncStateNotFound"}})
    h.folder("inbox", {"value": [mail("g1")], "@odata.deltaLink": f"{GRAPH}/new-delta"})
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/new-sent"})
    await h.sync.sync_account(FAMILY)
    assert store.mail_delta_link("family", "inbox") is None  # cleared: the next round starts afresh
    await h.sync.sync_account(FAMILY)
    assert store.mail_delta_link("family", "inbox") == f"{GRAPH}/new-delta"


async def test_errors_leave_the_last_good_sync_and_old_mail_is_pruned(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.folder(
        "inbox", {"value": [mail("old", receivedDateTime="2026-08-01T07:00:00Z")], "@odata.deltaLink": f"{GRAPH}/d1"}
    )
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/d2"})
    await h.sync.sync_all()
    assert store.search_mail("anna") == []  # older than 30 days: pruned right away
    h.http.answers[f"{GRAPH}/d1"] = []
    h.http.fail(f"{GRAPH}/d1", OSError("network down"))
    await h.sync.sync_account(FAMILY)
    assert h.sync.status_line(TZ).startswith("Mail: family error (OSError), last sync 11:40")


async def test_a_surprise_in_one_mailbox_is_recorded_not_raised(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.http.fail(GRAPH + initial_delta_url("inbox", NOW, 30), RuntimeError("odd page"))
    await h.sync.sync_all()  # the bridge's task group must not see this
    assert h.sync.status_line(TZ) == "Mail: family error (RuntimeError) · work error (RuntimeError)"


async def test_the_sync_loop_keeps_going_after_a_surprise(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path, store)
    rounds: list[int] = []

    async def broken() -> None:
        rounds.append(1)
        raise sqlite3.OperationalError("disk I/O error")

    async def sleep(seconds: float) -> None:
        if len(rounds) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(h.sync, "sync_all", broken)
    monkeypatch.setattr(h.sync, "_sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await h.sync.run_forever()
    assert len(rounds) == 2


async def test_finished_rounds_are_counted(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.folder("inbox", {"value": [], "@odata.deltaLink": f"{GRAPH}/d1"})
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/d2"})
    assert h.sync.rounds == 0
    await h.sync.sync_all()
    assert h.sync.rounds == 1


async def test_cc_of_sent_mail_makes_correspondents_but_cc_of_received_mail_does_not(
    tmp_path: Path, store: Store
) -> None:
    h = Harness(tmp_path, store)
    cc = [{"emailAddress": {"address": "Cara@Example.com"}}]
    h.folder(
        "inbox",
        {
            "value": [mail("g1", ccRecipients=[{"emailAddress": {"address": "dan@example.com"}}])],
            "@odata.deltaLink": f"{GRAPH}/i",
        },
    )
    h.folder("sent", {"value": [mail("s1", ccRecipients=cc)], "@odata.deltaLink": f"{GRAPH}/s"})
    await h.sync.sync_all()
    assert store.has_written_to("cara@example.com") and store.has_written_to("b@example.com")
    assert not store.has_written_to("dan@example.com")


async def test_old_sorting_records_are_pruned_with_old_mail(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.folder("inbox", {"value": [], "@odata.deltaLink": f"{GRAPH}/i"})
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/s"})
    store.record_triage(1, "gemma", 0.2, "reply")
    store.record_triage(2, "jev", 0.95, None)
    store._conn.execute("UPDATE mail_triage SET created_at = '2026-08-01T00:00:00+00:00' WHERE mail_id = 1")
    await h.sync.sync_all()
    assert store.triage_since("2000-01-01T00:00:00+00:00") == [("jev", 0.95, None)]


async def test_each_mailbox_records_its_real_syncs(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.folder("inbox", {"value": [], "@odata.deltaLink": f"{GRAPH}/d1"})
    h.folder("sent", {"value": [], "@odata.deltaLink": f"{GRAPH}/d2"})
    await h.sync.sync_all()
    assert h.status.snapshot() == {"mail:family": Record(ok=NOW), "mail:work": Record(ok=NOW)}


async def test_a_refused_sign_in_is_recorded_as_expired(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.http.answers[TOKEN_URL] = []
    h.http.add(TOKEN_URL, 400, {"error": "invalid_grant"})
    await h.sync.sync_account(FAMILY)
    assert h.status.snapshot()["mail:family"] == Record(failed=NOW, reason="sign_in")


@pytest.mark.parametrize(
    ("answer", "reason", "code"),
    [
        (OSError("network down"), "no_answer", 0),
        (TimeoutError(), "no_answer", 0),
        ((429, {"error": {"code": "TooManyRequests"}}), "busy", 429),
        ((403, {"error": {"code": "ErrorAccessDenied"}}), "refused", 403),
        ((200, {"value": []}), "bad_answer", 0),  # neither a next nor a delta link
        (RuntimeError("odd page"), "error", 0),
    ],
)
async def test_a_failed_sync_is_recorded_with_its_reason(
    tmp_path: Path, store: Store, answer: object, reason: str, code: int
) -> None:
    h = Harness(tmp_path, store)
    url = GRAPH + initial_delta_url("inbox", NOW, 30)
    if isinstance(answer, BaseException):
        h.http.fail(url, answer)
    else:
        assert isinstance(answer, tuple)
        h.http.add(url, *answer)
    await h.sync.sync_account(FAMILY)
    assert h.status.snapshot()["mail:family"] == Record(failed=NOW, reason=reason, code=code)


async def test_a_token_endpoint_failure_is_a_refusal(tmp_path: Path, store: Store) -> None:
    h = Harness(tmp_path, store)
    h.http.answers[TOKEN_URL] = []
    h.http.add(TOKEN_URL, 500, {"error": "server_error"})
    await h.sync.sync_account(FAMILY)
    assert h.status.snapshot()["mail:family"] == Record(failed=NOW, reason="refused")
