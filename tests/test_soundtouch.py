import http.server
import threading
from collections.abc import Iterator

import pytest

from local_assistant.soundtouch import wake


class Speaker:
    """A pretend SoundTouch speaker on localhost: /now_playing and /key, like port 8090 of the real one."""

    def __init__(self, standby: bool) -> None:
        self.standby = standby
        self.keys: list[str] = []
        speaker = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                source = "STANDBY" if speaker.standby else "SPOTIFY"
                body = f'<nowPlaying deviceID="X" source="{source}"></nowPlaying>'.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                speaker.keys.append(self.rfile.read(length).decode())
                if 'state="release"' in speaker.keys[-1]:
                    speaker.standby = False
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                return None

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def speaker() -> Iterator[Speaker]:
    s = Speaker(standby=True)
    yield s
    s.server.shutdown()


async def test_a_speaker_in_standby_gets_the_power_key(speaker: Speaker) -> None:
    assert await wake("127.0.0.1", port=speaker.port)
    assert [("press" in k, "release" in k, ">POWER<" in k) for k in speaker.keys] == [
        (True, False, True),
        (False, True, True),
    ]
    assert not speaker.standby


async def test_a_speaker_that_is_on_is_left_alone(speaker: Speaker) -> None:
    speaker.standby = False
    assert not await wake("127.0.0.1", port=speaker.port)
    assert speaker.keys == []


async def test_a_speaker_that_does_not_answer_is_not_an_error() -> None:
    assert not await wake("127.0.0.1", port=9)  # nothing listens there


async def test_the_wake_never_follows_a_redirect() -> None:
    hits: list[str] = []

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert not await wake("127.0.0.1", port=server.server_address[1])
        assert hits == ["/now_playing"]  # only the configured address was asked
    finally:
        server.shutdown()
