"""Gemma through Ollama's OpenAI-compatible API (standard-library HTTP, cancellable).

Cancelling a call shuts its socket down, so Ollama stops generating and frees the single
model slot: needed when a background learning pass is aborted because a message came in.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import logging
import socket
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
_TRANSIENT = frozenset({500, 502, 503, 504})


class GemmaError(Exception):
    """Gemma answered with an error, garbage, or not in time."""


class GemmaUnavailableError(GemmaError):
    """Ollama could not be reached within the retry window."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Completion:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    prompt_tokens: int = 0
    finish_reason: str = ""


class GemmaClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "none",
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion: ...

    async def status(self) -> str: ...


def parse_completion(body: dict[str, Any]) -> Completion:
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GemmaError("unexpected response from Ollama") from exc
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        arguments = function.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{len(calls)}"), name=str(function.get("name", "")), arguments=arguments
            )
        )
    usage = body.get("usage") or {}
    return Completion(
        content=str(message.get("content") or ""),
        tool_calls=tuple(calls),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        finish_reason=str(choice.get("finish_reason") or ""),
    )


class _Call:
    """One HTTP request in a worker thread that the event loop can abort."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.conn = http.client.HTTPConnection(host, port, timeout=timeout)
        self.cancelled = threading.Event()

    def run(self, method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
        self.conn.connect()
        if self.cancelled.is_set():
            raise ConnectionAbortedError("cancelled")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        self.conn.request(method, path, body=body, headers=headers)
        response = self.conn.getresponse()
        return response.status, response.read()

    def abort(self) -> None:
        self.cancelled.set()
        sock = self.conn.sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)


class OllamaGemma:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        unreachable_seconds: float = 120,
        request_timeout: float = 600,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("base_url must look like http://127.0.0.1:11434/v1")
        self._host = parts.hostname
        self._port = parts.port or 80
        self._prefix = parts.path.rstrip("/")
        self._model = model
        self._unreachable = unreachable_seconds
        self._timeout = request_timeout
        self._sleep = sleep
        self._clock = clock

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "none",
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema, "strict": True},
            }
        status, body = await self._with_retries("POST", f"{self._prefix}/chat/completions", payload)
        if status != 200:
            raise GemmaError(f"Ollama HTTP {status}: {body[:200].decode(errors='replace')}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise GemmaError("Ollama sent invalid JSON") from exc
        return parse_completion(data)

    async def status(self) -> str:
        root = self._prefix.removesuffix("/v1")
        try:
            status, body = await self._request("GET", f"{root}/api/ps", None, 5.0)
        except (OSError, http.client.HTTPException):
            return "Ollama unreachable"
        if status != 200:
            return f"Ollama HTTP {status}"
        try:
            models = json.loads(body).get("models") or []
        except (json.JSONDecodeError, AttributeError):
            return "Ollama sent invalid JSON"
        return "loaded" if models else "not loaded (loads with the next message, ~8 s)"

    async def _with_retries(self, method: str, path: str, payload: dict[str, Any]) -> tuple[int, bytes]:
        deadline = self._clock() + self._unreachable
        delay = 2.0
        while True:
            try:
                status, body = await self._request(method, path, payload, self._timeout)
            except TimeoutError as exc:
                raise GemmaError(f"no answer within {self._timeout:.0f} s") from exc
            except (OSError, http.client.HTTPException) as exc:
                problem = str(exc) or type(exc).__name__
            else:
                if status not in _TRANSIENT:
                    return status, body
                problem = f"HTTP {status}"
            if self._clock() + delay > deadline:
                raise GemmaUnavailableError(f"Ollama unreachable for {self._unreachable:.0f} s ({problem})")
            log.warning("Ollama not available (%s); retrying in %.0f s", problem, delay)
            await self._sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None, seconds: float
    ) -> tuple[int, bytes]:
        call = _Call(self._host, self._port, seconds)
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            return await asyncio.to_thread(call.run, method, path, body)
        except asyncio.CancelledError:
            call.abort()
            raise
        finally:
            with contextlib.suppress(Exception):
                call.conn.close()
