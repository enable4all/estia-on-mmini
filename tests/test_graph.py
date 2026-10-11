import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from local_assistant.config import Config, MailAccount
from local_assistant.graph import GRAPH, DeltaExpired, Graph, GraphError
from local_assistant.mail_auth import CALENDAR_SCOPES, SCOPES, AccessTokens, SignInExpired, TokenStore
from local_assistant.mail_http import http
from tests.conftest import ME
from tests.fakes.fake_http import FakeHttp

TENANT = "11111111-2222-4333-8444-555555555555"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
ADDRESS = "info@example.com"


def make(tmp_path: Path, http: FakeHttp, **options: Any) -> tuple[Graph, list[float]]:
    cfg = Config(
        bot_number="+306900000000",
        allowed_acis=frozenset({ME}),
        data_dir=tmp_path / "data",
        mail_tenant_id=TENANT,
        mail_client_id="66666666-7777-4888-9999-000000000000",
        mail_accounts=(MailAccount("family", ADDRESS),),
    )
    store = TokenStore(cfg.mail_token_path)
    store.save(ADDRESS, "rt")
    http.add(TOKEN_URL, 200, {"access_token": "at-1", "expires_in": 3600})
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    return Graph(AccessTokens(cfg, store, http=http), http=http, sleep=sleep, **options), waits


async def test_get_sends_the_token_and_returns_json(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, _ = make(tmp_path, fake)
    fake.add(f"{GRAPH}/me/mailFolders/inbox", 200, {"id": "inbox-id"})
    assert await graph.get(ADDRESS, "/me/mailFolders/inbox") == {"id": "inbox-id"}
    assert fake.requests[-1][2]["authorization"] == "Bearer at-1"


async def test_401_refreshes_the_token_once(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, _ = make(tmp_path, fake)
    fake.answers[TOKEN_URL] = []
    fake.add(TOKEN_URL, 200, {"access_token": "at-1", "expires_in": 3600})
    fake.add(TOKEN_URL, 200, {"access_token": "at-2", "expires_in": 3600})
    url = f"{GRAPH}/me"
    fake.add(url, 401, {"error": {"code": "InvalidAuthenticationToken"}})
    fake.add(url, 200, {"mail": ADDRESS})
    assert await graph.get(ADDRESS, "/me") == {"mail": ADDRESS}
    assert [r[2]["authorization"] for r in fake.requests if r[1] == url] == ["Bearer at-1", "Bearer at-2"]


async def test_throttling_waits_as_asked_then_gives_up(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, waits = make(tmp_path, fake)
    url = f"{GRAPH}/me"
    fake.add(url, 429, None, {"retry-after": "7"})
    fake.add(url, 200, {"mail": ADDRESS})
    assert await graph.get(ADDRESS, "/me") == {"mail": ADDRESS}
    assert waits == [7]
    fake.answers[url] = []
    fake.add(url, 503, None)
    with pytest.raises(GraphError, match="HTTP 503"):
        await graph.get(ADDRESS, "/me")
    assert waits[1:] == [10, 10, 10]  # default wait without Retry-After, 3 retries


async def test_a_graph_for_the_calendar_uses_its_scope_and_short_waits(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, waits = make(tmp_path, fake, scope=CALENDAR_SCOPES, retries=1, max_wait=3.0)
    url = f"{GRAPH}/me/calendarView"
    fake.add(url, 429, None, {"retry-after": "60"})
    fake.add(url, 503, None)
    with pytest.raises(GraphError, match="HTTP 503"):
        await graph.get(ADDRESS, "/me/calendarView")
    assert waits == [3.0]  # one retry, waiting at most 3 s
    assert [form["scope"] for method, _, form in fake.requests if method == "POST"] == [CALENDAR_SCOPES]
    mail, _ = make(tmp_path, fake)
    fake.add(f"{GRAPH}/me", 200, {"mail": ADDRESS})
    await mail.get(ADDRESS, "/me")
    assert [form["scope"] for method, _, form in fake.requests if method == "POST"][-1] == SCOPES


async def test_gone_delta_and_other_errors(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, _ = make(tmp_path, fake)
    fake.add(f"{GRAPH}/delta-gone", 410, {"error": {"code": "SyncStateNotFound"}})
    with pytest.raises(DeltaExpired):
        await graph.get(ADDRESS, "/delta-gone")
    fake.add(f"{GRAPH}/forbidden", 403, {"error": {"code": "ErrorAccessDenied"}})
    with pytest.raises(GraphError, match="ErrorAccessDenied"):
        await graph.get(ADDRESS, "/forbidden")


async def test_urls_outside_graph_are_refused(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, _ = make(tmp_path, fake)
    for url in (
        "https://evil.example/v1.0/me",
        "http://graph.microsoft.com/v1.0/me",
        "https://graph.microsoft.com.evil.example/x",
    ):
        with pytest.raises(GraphError, match="outside Microsoft Graph"):
            await graph.get(ADDRESS, url)
    assert fake.requests == []


async def test_expired_sign_in_passes_through(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, _ = make(tmp_path, fake)
    fake.answers[TOKEN_URL] = []
    fake.add(TOKEN_URL, 400, {"error": "invalid_grant"})
    with pytest.raises(SignInExpired):
        await graph.get(ADDRESS, "/me")


async def test_message_text_and_search_urls(tmp_path: Path) -> None:
    fake = FakeHttp()
    graph, _ = make(tmp_path, fake)
    fake.add(
        f"{GRAPH}/me/messages/AAMk%3D%2Fx?$select=subject,from,toRecipients,receivedDateTime,body,internetMessageHeaders",
        200,
        {"subject": "Hi", "body": {"contentType": "text", "content": "Hello"}},
    )
    message = await graph.message_text(ADDRESS, "AAMk=/x")
    assert message["body"]["content"] == "Hello"
    assert fake.requests[-1][2]["prefer"] == 'outlook.body-content-type="text"'
    search_url = (
        f"{GRAPH}/me/messages?$search=%22olive%20harvest%22&$top=10"
        "&$select=id,conversationId,from,subject,receivedDateTime,parentFolderId"
    )
    fake.add(search_url, 200, {"value": [{"id": "m1"}]})
    assert await graph.search(ADDRESS, 'olive "harvest"') == [{"id": "m1"}]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"path": self.path}).encode()
        self.send_response(200 if self.path != "/missing" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "3")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        data = self.rfile.read(length).decode()
        self.send_response(400)
        self.end_headers()
        self.wfile.write(json.dumps({"got": data}).encode())

    def log_message(self, format: str, *args: object) -> None:
        return None


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


async def test_real_http_client_against_a_local_server(server: str) -> None:
    ok = await http("GET", f"{server}/v1.0/me", {"Accept": "application/json"}, None)
    assert (ok.status, ok.body, ok.headers["retry-after"]) == (200, {"path": "/v1.0/me"}, "3")
    missing = await http("GET", f"{server}/missing", {}, None)
    assert missing.status == 404
    posted = await http("POST", f"{server}/token", {"Content-Type": "application/x-www-form-urlencoded"}, b"a=1")
    assert (posted.status, posted.body) == (400, {"got": "a=1"})
