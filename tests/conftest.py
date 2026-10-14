import http.server
import socketserver
from collections.abc import Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from local_assistant import config
from local_assistant.store import Store


def _bind_without_dns(self: http.server.HTTPServer) -> None:
    """HTTPServer.server_bind looks up the host's full name (socket.getfqdn): on a network with slow
    reverse DNS that costs ~35 s per test server. Local test servers don't need the name."""
    socketserver.TCPServer.server_bind(self)
    host, port = self.server_address[:2]
    self.server_name, self.server_port = str(host), int(port)


setattr(http.server.HTTPServer, "server_bind", _bind_without_dns)  # noqa: B010 (patching a stdlib method for tests)

ME = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
STRANGER = "11111111-2222-4333-8444-555555555555"
TZ = ZoneInfo("Europe/Amsterdam")
# 2026-10-08 09:32:23 Europe/Amsterdam
T0 = 1791444743502


@pytest.fixture(autouse=True)
def amsterdam_time(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests' fixed times are Amsterdam times: pin the "Mac's own time zone" (config's default) to it, so the
    suite passes on a Mac set to any zone."""
    link = tmp_path_factory.getbasetemp() / "localtime"
    if not link.is_symlink():
        link.symlink_to("/usr/share/zoneinfo/Europe/Amsterdam")
    monkeypatch.setattr(config, "LOCALTIME", link)


@pytest.fixture(autouse=True)
def no_live_system(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read the owner's Mac: system paths (/Library/LaunchDaemons, /usr/local/estia, /var/estia) resolve
    under an empty root of their own (ESTIA_ROOT), and the live voice service is never asked. A test that needs "/"
    says so (monkeypatch.delenv("ESTIA_ROOT")) and reads nothing there."""
    from local_assistant import ownuser

    monkeypatch.setenv("ESTIA_ROOT", str(tmp_path_factory.mktemp("estia-root")))
    real = ownuser.voice_health

    def voice_health(live: bool) -> object:
        if live:
            raise AssertionError("a test asked the live voice service (127.0.0.1:8150)")
        return real(live)

    monkeypatch.setattr(ownuser, "voice_health", voice_health)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    db = Store.open(tmp_path / "data" / "assistant.db")
    yield db
    db.close()
