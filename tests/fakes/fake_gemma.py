"""Scripted GemmaClient for tests: returns the queued items in order and records every call."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from local_assistant.gemma import Completion, ToolCall

Scripted = Completion | BaseException | asyncio.Future[Completion]


class FakeGemma:
    def __init__(self, *items: Scripted) -> None:
        self.queue: list[Scripted] = list(items)
        self.calls: list[dict[str, Any]] = []
        self.status_text = "loaded"

    def add(self, *items: Scripted) -> None:
        self.queue.extend(items)

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
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "max_tokens": max_tokens,
                "tools": tools,
                "reasoning_effort": reasoning_effort,
                "temperature": temperature,
                "json_schema": json_schema,
            }
        )
        if not self.queue:
            raise AssertionError("FakeGemma: no scripted completion left")
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, asyncio.Future):
            return await item
        return item

    async def status(self) -> str:
        return self.status_text


def text(content: str, prompt_tokens: int = 100) -> Completion:
    return Completion(content=content, prompt_tokens=prompt_tokens, finish_reason="stop")


def tool(name: str, call_id: str | None = None, **arguments: Any) -> Completion:
    call = ToolCall(id=call_id or f"call_{name}", name=name, arguments=json.dumps(arguments))
    return Completion(content="", tool_calls=(call,), prompt_tokens=100, finish_reason="tool_calls")
