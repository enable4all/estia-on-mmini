import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from local_assistant.mail_http import http


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            seen.append(f"{self.path} {self.headers.get('Authorization', '')}")
            if self.path == "/slow":
                time.sleep(1.0)
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    httpd.shutdown()
    httpd.server_close()


async def test_json_comes_back(server: tuple[str, list[str]]) -> None:
    url, _ = server
    result = await http("GET", f"{url}/ok", {"Authorization": "Bearer at"}, None)
    assert (result.status, result.body) == (200, {"ok": True})


async def test_redirects_are_not_followed_so_the_token_stays(server: tuple[str, list[str]]) -> None:
    # urllib would follow a redirect and send the Authorization header along to wherever it points
    url, seen = server
    result = await http("GET", f"{url}/moved", {"Authorization": "Bearer at"}, None)
    assert result.status == 302
    assert seen == ["/moved Bearer at"]


async def test_a_shorter_timeout_ends_the_request_itself(server: tuple[str, list[str]]) -> None:
    # web search passes its own limit: the request stops, not only the wait for it
    url, _ = server
    started = time.monotonic()
    with pytest.raises(OSError):
        await http("GET", f"{url}/slow", {}, None, socket_timeout=0.2)
    assert time.monotonic() - started < 0.8
