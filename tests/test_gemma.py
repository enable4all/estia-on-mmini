import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from local_assistant.gemma import (
    Completion,
    GemmaError,
    GemmaUnavailableError,
    ModelEntry,
    ModelPausedError,
    ModelPin,
    OllamaGemma,
    PinnedGemma,
    parse_completion,
)


def completion_json(content: str = "ok", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 71, "completion_tokens": 5},
    }


class FakeOllama(BaseHTTPRequestHandler):
    replies: ClassVar[list[tuple[int, Any]]] = []
    requests: ClassVar[list[dict[str, Any]]] = []
    delay: ClassVar[float] = 0.0
    loaded: ClassVar[list[dict[str, Any]]] = []
    gets: ClassVar[list[str]] = []
    get_status: ClassVar[int] = 200

    def _reply(self, status: int, payload: Any) -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        with contextlib.suppress(OSError):  # the client may have gone away (cancellation test)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append({"path": self.path, "body": body})
        time.sleep(FakeOllama.delay)
        status, payload = FakeOllama.replies.pop(0) if FakeOllama.replies else (200, completion_json())
        self._reply(status, payload)

    def do_GET(self) -> None:
        FakeOllama.gets.append(self.path)
        if FakeOllama.get_status != 200:
            self._reply(FakeOllama.get_status, {"error": "fake"})
            return
        self._reply(200, {"models": FakeOllama.loaded})

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def base_url() -> Iterator[str]:
    FakeOllama.replies, FakeOllama.requests, FakeOllama.delay, FakeOllama.loaded = [], [], 0.0, []
    FakeOllama.gets, FakeOllama.get_status = [], 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    httpd.server_close()


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_chat_returns_text_and_usage(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    result = await gemma.chat([{"role": "user", "content": "hi"}], max_tokens=50)
    assert (result.content, result.prompt_tokens, result.finish_reason) == ("ok", 71, "stop")


async def test_payload_has_effort_tools_and_schema(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    tools = [{"type": "function", "function": {"name": "remember", "parameters": {"type": "object"}}}]
    schema = {"type": "object"}
    await gemma.chat(
        [{"role": "user", "content": "hi"}],
        max_tokens=50,
        tools=tools,
        reasoning_effort="high",
        temperature=0,
        json_schema=schema,
    )
    request = FakeOllama.requests[0]
    assert request["path"] == "/v1/chat/completions"
    body = request["body"]
    assert (body["model"], body["max_tokens"], body["reasoning_effort"], body["temperature"]) == (
        "local-worker",
        50,
        "high",
        0,
    )
    assert body["tools"] == tools
    assert body["response_format"]["json_schema"]["schema"] == schema
    assert body["stream"] is False


async def test_tool_calls_are_parsed(base_url: str) -> None:
    call = {"id": "call_1", "type": "function", "function": {"name": "remember", "arguments": '{"fact": "x"}'}}
    FakeOllama.replies = [(200, completion_json("", [call]))]
    result = await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    [tool] = result.tool_calls
    assert (tool.id, tool.name, json.loads(tool.arguments)) == ("call_1", "remember", {"fact": "x"})


async def test_transient_errors_are_retried(base_url: str) -> None:
    FakeOllama.replies = [(503, {"error": "loading"}), (503, {"error": "loading"}), (200, completion_json("hi"))]
    fake = FakeTime()
    gemma = OllamaGemma(base_url, "local-worker", sleep=fake.sleep, clock=fake.clock)
    result = await gemma.chat([], max_tokens=50)
    assert result.content == "hi"
    assert fake.sleeps == [2.0, 4.0]


async def test_gives_up_when_ollama_stays_down() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    fake = FakeTime()
    gemma = OllamaGemma(
        f"http://127.0.0.1:{port}/v1", "local-worker", unreachable_seconds=10, sleep=fake.sleep, clock=fake.clock
    )
    with pytest.raises(GemmaUnavailableError, match="unreachable"):
        await gemma.chat([], max_tokens=50)
    assert fake.sleeps == [2.0, 4.0]


async def test_client_errors_are_not_retried(base_url: str) -> None:
    FakeOllama.replies = [(400, {"error": "bad request"})]
    with pytest.raises(GemmaError, match="HTTP 400"):
        await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    assert len(FakeOllama.requests) == 1


async def test_cancelling_a_call_returns_at_once(base_url: str) -> None:
    FakeOllama.delay = 3.0
    gemma = OllamaGemma(base_url, "local-worker")
    task = asyncio.create_task(gemma.chat([], max_tokens=50))
    await asyncio.sleep(0.3)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 1.0
    assert len(FakeOllama.requests) == 1


async def test_status(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    assert await gemma.status() == "not loaded (loads with the next message, ~8 s)"
    FakeOllama.loaded = [{"name": "gemma4:26b-mlx"}]
    assert await gemma.status() == "loaded"
    assert await OllamaGemma("http://127.0.0.1:9/v1", "local-worker").status() == "Ollama unreachable"


def test_parse_completion_rejects_unexpected_shapes() -> None:
    with pytest.raises(GemmaError):
        parse_completion({"choices": []})
    dict_args = completion_json("", [{"id": "c", "function": {"name": "forget", "arguments": {"fact": "3"}}}])
    assert json.loads(parse_completion(dict_args).tool_calls[0].arguments) == {"fact": "3"}


# hardening: the model alias must keep the digest pinned in the config, else answers pause

DIGEST = "ab" * 32
OTHER = "cd" * 32


async def test_the_aliass_digest_comes_from_the_tags_list(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    FakeOllama.loaded = [
        {"name": "gemma4:26b", "model": "gemma4:26b", "digest": OTHER},
        {"name": "local-worker:latest", "model": "local-worker:latest", "digest": DIGEST.upper()},
    ]
    assert await gemma.model_digest() == DIGEST
    assert FakeOllama.gets[-1] == "/api/tags"
    FakeOllama.loaded = [{"name": "local-worker:v2", "digest": DIGEST}]  # another tag of the name is not the alias
    assert await gemma.model_digest() is None
    tagged = OllamaGemma(base_url, "local-worker:v2")
    assert await tagged.model_digest() == DIGEST
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": f"sha256:{OTHER}"}]
    assert await gemma.model_digest() == OTHER


async def test_the_tags_entry_says_whether_the_model_is_a_cloud_one(base_url: str) -> None:
    """Ollama marks a model that runs on its servers with remote_host and remote_model (0.40.0)."""
    gemma = OllamaGemma(base_url, "local-worker")
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": DIGEST}]
    assert await gemma.model_entry() == ModelEntry(DIGEST, remote=False)
    cloud = {"remote_model": "gpt-oss:120b", "remote_host": "https://ollama.com:443"}
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": OTHER, **cloud}]
    assert await gemma.model_entry() == ModelEntry(OTHER, remote=True)
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": OTHER, "remote_host": "https://ollama.com:443"}]
    assert (await gemma.model_entry()) == ModelEntry(OTHER, remote=True)
    FakeOllama.loaded = []
    assert await gemma.model_entry() is None


async def test_a_tags_list_that_cannot_be_read_is_an_error(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    FakeOllama.get_status = 500
    with pytest.raises(GemmaError):
        await gemma.model_digest()
    with pytest.raises(GemmaError):
        await OllamaGemma("http://127.0.0.1:9/v1", "local-worker").model_digest()


class Digests:
    def __init__(self, *answers: str | ModelEntry | Exception | None) -> None:
        self.answers = list(answers)
        self.reads = 0

    async def __call__(self) -> str | ModelEntry | None:
        self.reads += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_the_pin_pauses_on_a_mismatch_and_resumes_on_a_match() -> None:
    changes: list[bool] = []
    digests = Digests(DIGEST, OTHER, None, GemmaError("down"), DIGEST)
    pin = ModelPin(digests, DIGEST, on_change=changes.append)
    assert await pin.check() is True and not pin.paused
    assert await pin.check() is False and pin.paused  # another model under the alias
    assert await pin.check() is False and pin.paused  # the alias is gone: still no match
    assert await pin.check() is None and pin.paused  # Ollama silent: nothing known, the pause stays
    assert await pin.check() is True and not pin.paused
    assert changes == [True, False]  # told once each way


async def test_the_entry_is_read_before_every_chat() -> None:
    digests = Digests(DIGEST)
    pin = ModelPin(digests, DIGEST)
    assert await pin.ensure() and await pin.ensure() and await pin.ensure()
    assert digests.reads == 3


async def test_a_name_switched_to_a_cloud_model_gets_not_even_the_next_chat() -> None:
    """No minute's grace: the alias turned into a cloud model one second after a chat, the next chat is held."""
    inner = Inner()
    entries = Digests(ModelEntry(DIGEST, remote=False), ModelEntry(DIGEST, remote=True))
    gemma = PinnedGemma(inner, ModelPin(entries, ""))
    await gemma.chat([], max_tokens=5)
    with pytest.raises(ModelPausedError, match="cloud"):
        await gemma.chat([], max_tokens=5)
    assert inner.chats == 1


async def test_an_unreadable_entry_is_read_again_at_the_next_chat() -> None:
    entries = Digests(GemmaError("down"), ModelEntry(DIGEST, remote=True))
    pin = ModelPin(entries, "")
    assert await pin.ensure() is True  # nothing known: the chat itself fails while Ollama is down
    assert await pin.ensure() is False and pin.why == "cloud" and entries.reads == 2


async def test_an_unknown_digest_at_the_start_does_not_pause() -> None:
    pin = ModelPin(Digests(GemmaError("down")), DIGEST)
    assert await pin.ensure() is True and not pin.paused  # the chat itself will fail if Ollama is down


async def test_a_cloud_model_pauses_answers_with_or_without_a_pinned_digest() -> None:
    """The model always runs on the Mac: a cloud entry pauses everything, also on an install without model_digest."""
    changes: list[bool] = []
    local, cloud = ModelEntry(DIGEST, remote=False), ModelEntry(DIGEST, remote=True)
    pin = ModelPin(Digests(local, cloud, None, local), "", on_change=changes.append)
    assert await pin.check() is True and not pin.paused and pin.why == ""
    assert await pin.check() is False and pin.paused and pin.why == "cloud"
    assert await pin.check() is True and not pin.paused  # no pin and no such model: the chat itself fails
    assert await pin.check() is True and not pin.paused
    assert changes == [True, False]
    pinned = ModelPin(Digests(cloud), DIGEST)
    assert await pinned.check() is False and pinned.why == "cloud"  # even with the pinned digest


class Inner:
    def __init__(self) -> None:
        self.chats = 0

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int, **_: Any) -> Completion:
        self.chats += 1
        return Completion("hello")

    async def status(self) -> str:
        return "loaded"


async def test_a_paused_model_gets_no_message() -> None:
    inner = Inner()
    pin = ModelPin(Digests(DIGEST, OTHER), DIGEST)
    gemma = PinnedGemma(inner, pin)
    assert (await gemma.chat([], max_tokens=5)).content == "hello" and await gemma.status() == "loaded"
    with pytest.raises(ModelPausedError):
        await gemma.chat([], max_tokens=5)
    assert inner.chats == 1
    assert "paused" in await gemma.status()


async def test_a_cloud_model_gets_no_message() -> None:
    inner = Inner()
    gemma = PinnedGemma(inner, ModelPin(Digests(ModelEntry(DIGEST, remote=True)), ""))
    with pytest.raises(ModelPausedError, match="cloud"):
        await gemma.chat([], max_tokens=5)
    assert inner.chats == 0 and "cloud" in await gemma.status()
