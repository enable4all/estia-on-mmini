"""The pin on a real TLS connection: a fake bridge on this Mac only (127.0.0.1, a certificate made for the test).
With the saved fingerprint the request goes out with its key; with another one, no request reaches the server."""

import datetime
import hashlib
import http.client
import http.server
import json
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import hue
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

KEY = "made-up-application-key-0123456789abcdef"


def certificate(folder: Path) -> tuple[Path, Path, bytes]:
    """A self-signed certificate like a bridge's own (its id as the name)."""
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ecb5fafffe000000")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(private, hashes.SHA256())
    )
    cert_path, key_path = folder / "bridge.crt", folder / "bridge.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return cert_path, key_path, cert.public_bytes(serialization.Encoding.DER)


class Server:
    def __init__(self, folder: Path) -> None:
        cert, key, self.der = certificate(folder)
        self.requests: list[tuple[str, dict[str, str]]] = []
        seen = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen.append((self.path, dict(self.headers)))
                body = json.dumps({"errors": [], "data": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def open(self, address: str, timeout: float) -> hue.Channel:
        return hue._Tls(address, timeout, port=self.port)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    running = Server(tmp_path)
    yield running
    running.stop()


def test_the_saved_fingerprint_lets_the_request_through_with_its_key(server: Server) -> None:
    pin = hashlib.sha256(server.der).hexdigest()
    bridge = hue.Bridge(hue.Pairing("127.0.0.1", KEY, pin), open=server.open)
    assert hue.lights(bridge) == []
    [(path, headers)] = server.requests
    assert path == "/clip/v2/resource/light" and headers["hue-application-key"] == KEY


def test_another_certificate_never_gets_a_request(server: Server) -> None:
    bridge = hue.Bridge(hue.Pairing("127.0.0.1", KEY, "0" * 64), open=server.open)
    with pytest.raises(hue.HueError) as caught:
        hue.lights(bridge)
    assert caught.value.code == "certificate"
    assert server.requests == []  # the connection closed after the handshake: no key, no request


def test_a_closed_channel_never_opens_another_connection(server: Server) -> None:
    """http.client would reconnect on its own and send the key on a connection whose certificate nobody checked."""
    channel = server.open("127.0.0.1", 2.0)
    channel.close()
    with pytest.raises(http.client.NotConnected):
        channel.exchange("GET", "/clip/v2/resource/light", {"hue-application-key": KEY}, None)
    assert server.requests == []


def test_nothing_answering_is_unreachable(tmp_path: Path) -> None:
    def closed_port(address: str, timeout: float) -> hue.Channel:
        return hue._Tls(address, timeout, port=1)

    bridge = hue.Bridge(hue.Pairing("127.0.0.1", KEY, "0" * 64), open=closed_port)
    with pytest.raises(hue.HueError) as caught:
        hue.lights(bridge)
    assert caught.value.code == "unreachable"
