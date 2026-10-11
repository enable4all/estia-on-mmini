import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from local_assistant.mail_prefilter import Jev, header_noise, is_bounce, needs_attention


def headers(**pairs: str) -> list[dict[str, str]]:
    return [{"name": name.replace("_", "-"), "value": value} for name, value in pairs.items()]


@pytest.mark.parametrize(
    ("given", "hint"),
    [
        (headers(List_Id="<club.example.com>"), ("mailing list", True)),
        (headers(Precedence="bulk"), ("bulk mail", True)),
        (headers(Precedence="List"), ("bulk mail", True)),
        (headers(List_Unsubscribe="<mailto:x@example.com>"), ("newsletter", False)),
        (headers(Auto_Submitted="auto-generated"), ("auto-reply", False)),
        (headers(X_Autoreply="yes"), ("auto-reply", False)),
        (headers(List_Unsubscribe="<mailto:x@example.com>", List_Id="<club>"), ("mailing list", True)),
        (headers(Auto_Submitted="no"), None),
        (headers(Precedence="first-class"), None),
        (headers(Subject="hello"), None),
        ([], None),
        (None, None),
        ([{"name": 5}, "junk"], None),
    ],
)
def test_header_hints_say_whether_they_decide_alone(given: Any, hint: tuple[str, bool] | None) -> None:
    """Only list and bulk headers decide alone: bills and security notices also carry List-Unsubscribe or
    Auto-Submitted."""
    assert header_noise(given) == hint


@pytest.mark.parametrize(
    ("given", "bounce"),
    [
        (headers(X_MS_Exchange_Message_Is_Ndr=""), True),
        (headers(Content_Type='multipart/report; report-type=delivery-status; boundary="b"'), True),
        (headers(Content_Type="text/plain"), False),
        ([], False),
    ],
)
def test_bounces_are_recognised(given: Any, bounce: bool) -> None:
    assert is_bounce(given) is bounce


@pytest.mark.parametrize(
    ("subject", "body", "attention"),
    [
        ("Your invoice for October", "", True),
        ("Payment failed", "", True),
        ("New sign-in to your account", "", True),
        ("Hello", "Please verify your email address.", True),
        ("Herinnering", "Uw factuur staat nog open.", True),
        ("Afspraak bevestigd", "", True),
        ("Wachtwoord gewijzigd", "", True),
        ("Autumn sale: 50% off", "New arrivals in the shop.", False),
        ("Weekly digest", "Top stories this week.", False),
        ("Unpaywalled stories", "", False),  # a whole word only
        ("Betalingsherinnering", "", True),  # Dutch compounds: the stem counts anywhere in the word
        ("Openstaande betalingen", "", True),
        ("Factuurnummer 123", "", True),
        ("Je jaarfactuur staat klaar", "", True),
        ("Beveiligingsmelding", "", True),
        ("Nieuwe inlogpoging op je account", "", True),
        ("Je afspraken voor morgen", "", True),
        ("Did you just sign in?", "", True),
        ("Please log in to continue", "", True),
    ],
)
def test_bills_security_and_appointments_need_attention(subject: str, body: str, attention: bool) -> None:
    assert needs_attention(subject, body) is attention


@pytest.fixture
def jev_server() -> Iterator[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    seen: list[dict[str, Any]] = []
    reply: dict[str, Any] = {"body": {"results": [{"answers": [{"value": True, "probability": 0.93}]}]}, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            seen.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
            data = json.dumps(reply["body"]).encode() if not isinstance(reply["body"], bytes) else reply["body"]
            self.send_response(reply["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", seen, reply
    httpd.shutdown()
    httpd.server_close()


async def test_jev_gives_the_probability_that_a_mail_needs_nothing(
    jev_server: tuple[str, list[dict[str, Any]], dict[str, Any]],
) -> None:
    url, seen, _ = jev_server
    p = await Jev(url).noise_probability("Shop <news@shop.example>", "50% off", "Our autumn sale " * 500)
    assert p == pytest.approx(0.93)
    [request] = seen
    assert request["path"] == "/api/evaluate"
    [question] = request["body"]["questions"]
    assert question["type"] == "boolean" and set(question["criteria"]) == {"true", "false"}
    state = request["body"]["state"]
    assert state.startswith("From: Shop <news@shop.example>\nSubject: 50% off\n\n") and len(state) <= 1_700


async def test_a_no_from_jev_is_one_minus_its_probability(
    jev_server: tuple[str, list[dict[str, Any]], dict[str, Any]],
) -> None:
    url, _, reply = jev_server
    reply["body"] = {"results": [{"answers": [{"value": False, "probability": 0.2}]}]}
    assert await Jev(url).noise_probability("Anna <anna@example.com>", "Dinner?", "Shall we?") == pytest.approx(0.2)


@pytest.mark.parametrize("problem", ["status", "junk", "shape"])
async def test_jev_trouble_means_no_answer(
    jev_server: tuple[str, list[dict[str, Any]], dict[str, Any]], problem: str
) -> None:
    url, _, reply = jev_server
    if problem == "status":
        reply["status"] = 500
    elif problem == "junk":
        reply["body"] = b"not json"
    else:
        reply["body"] = {"results": []}
    assert await Jev(url).noise_probability("a", "b", "c") is None


async def test_jev_not_running_means_no_answer() -> None:
    assert await Jev("http://127.0.0.1:9").noise_probability("a", "b", "c") is None
