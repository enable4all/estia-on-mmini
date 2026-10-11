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

from local_assistant.gemma import GemmaError, GemmaUnavailableError, OllamaGemma, parse_completion


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
        self._reply(200, {"models": FakeOllama.loaded})

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def base_url() -> Iterator[str]:
    FakeOllama.replies, FakeOllama.requests, FakeOllama.delay, FakeOllama.loaded = [], [], 0.0, []
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
