"""One answer: build Gemma's input, run the tool loop (at most 5 tool calls), return the reply."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_assistant import prompts
from local_assistant.context import ContextBuilder, without_fake_notices
from local_assistant.gemma import Completion, GemmaClient
from local_assistant.store import Conversation, Message
from local_assistant.tools import Tools, Turn
from local_assistant.web_search import sources

log = logging.getLogger(__name__)


INSTANT_TOOLS = frozenset({"remember", "forget", "end_call"})  # no "let me check" on a call for these


@dataclass(frozen=True)
class Reply:
    text: str
    tool_calls: int
    prompt_tokens: int
    notice: str = ""  # profile changes in this answer, written by code so Gemma cannot hide or invent them
    end_call: bool = False  # on a call: hang up after speaking it
    sources: str = ""  # the web results' links, written by code


# web addresses, also bare ones with a path; a link's trailing punctuation stays in the sentence
_LINK = re.compile(r"(?:https?://|www\.)[^\s()<>]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s()<>]*", re.IGNORECASE)


def without_links(text: str) -> str:
    """An answer that searched the web: its links come only in the code-written Sources note, so a link a web page
    planted (a fake login page) never reaches the user in her words."""

    def keep_punctuation(match: re.Match[str]) -> str:
        link = match.group(0)
        return link[len(link.rstrip(".,;:!?")) :]

    return re.sub(r"[ \t]{2,}", " ", _LINK.sub(keep_punctuation, text)).strip()


def _links(turn: Turn) -> list[tuple[str, str]]:
    """The web results' links, then the map links, without repeats, at most 5."""
    links = sources(turn.web)
    links += [link for link in turn.links if all(link[1] != url for _, url in links)]
    return links[:5]


NOTHING_SAVED = 'Profile changes: none. Nothing was saved; say "remember that ..." to try again.'


def _claims_a_save(text: str) -> bool:
    """The rules make Gemma confirm a save with "Noted: ..." (a plain "Noted!" acknowledgement does not count)."""
    return re.match(r"\s*noted\s*:", text, re.IGNORECASE) is not None


def _has_images(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(message.get("content"), list) for message in messages)


def profile_notice(turn: Turn) -> str:
    lines = [f"Saved #{fid}: {text} (/forget {fid} undoes it)" for fid, text in turn.saved]
    lines += [f"Updated #{fid}: {text}" for fid, text in turn.updated]
    lines += [f"Removed #{fid}: {text}" for fid, text in turn.removed]
    return "Profile changes:\n" + "\n".join(lines) if lines else ""


def _assistant_tool_message(completion: Completion) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": completion.content,
        "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
            for call in completion.tool_calls
        ],
    }


class Brain:
    def __init__(
        self,
        gemma: GemmaClient,
        context: ContextBuilder,
        tools: Tools,
        *,
        max_tool_calls: int = 5,
        learn_tool_calls: int = 10,
        reply_max_tokens: int = 600,
    ) -> None:
        self._gemma = gemma
        self._context = context
        self._tools = tools
        self._max_tool_calls = max_tool_calls
        self._learn_tool_calls = learn_tool_calls  # one remember per fact after learn_link
        self._reply_max_tokens = reply_max_tokens

    async def answer(
        self,
        conversation: Conversation,
        message: Message,
        images: Sequence[str] = (),
        *,
        recent: Mapping[int, Sequence[str]] | None = None,
        max_tokens: int | None = None,
        on_tools: Callable[[], None] | None = None,
    ) -> Reply:
        """Gemma's reply to the newest message of the conversation (already stored), with its photos.

        `recent`: photos of an earlier message that are still shown (a follow-up question about them).
        On a call: `max_tokens` keeps spoken answers short; `on_tools` is called once, when the first tool that
        takes a moment runs (the call says "let me check")."""
        turn = Turn(message=message)
        limit_tokens = max_tokens or self._reply_max_tokens
        shown = {**(recent or {}), **({message.id: images} if images else {})}
        messages = self._context.build(conversation, shown or None)
        calls = 0
        corrected = False
        while True:
            limit = self._learn_tool_calls if turn.learned_document_id is not None else self._max_tool_calls
            allow_tools = calls < limit
            completion = await self._complete(messages, allow_tools, limit_tokens)
            if completion.tool_calls and allow_tools:
                if on_tools is not None and any(call.name not in INSTANT_TOOLS for call in completion.tool_calls):
                    on_tools()
                    on_tools = None
                messages.append(_assistant_tool_message(completion))
                for call in completion.tool_calls:
                    if calls >= limit:
                        result = "Error: tool limit reached for this message; answer with what you have."
                    else:
                        calls += 1
                        result = await self._tools.call(turn, call.name, call.arguments)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                continue
            text = without_fake_notices(completion.content).strip()
            if not corrected and _claims_a_save(text) and not turn.saved and allow_tools:
                # "Noted" without a remember call means nothing was saved: send it back once
                corrected = True
                messages.append({"role": "assistant", "content": completion.content})
                messages.append({"role": "user", "content": prompts.NOTED_NUDGE})
                log.warning("message %d: 'Noted' without a save; asking Gemma to correct it", message.id)
                continue
            if turn.forgot:
                self._context.refresh_snapshot()
            if turn.used_web:
                text = without_links(text)
            text = text or prompts.CONFUSED
            notice = profile_notice(turn) or (NOTHING_SAVED if _claims_a_save(text) else "")
            return Reply(
                text=text,
                tool_calls=calls,
                prompt_tokens=completion.prompt_tokens,
                notice=notice,
                end_call=turn.end_call,
                sources=prompts.sources_note(_links(turn)),
            )

    async def _complete(self, messages: list[dict[str, Any]], allow_tools: bool, max_tokens: int) -> Completion:
        """One Gemma call; an answer with neither text nor a usable tool call is retried once."""
        tools = self._tools.schemas if allow_tools else None
        completion = await self._gemma.chat(messages, max_tokens=max_tokens, tools=tools)
        if not _has_images(messages):  # image tokens are counted by Ollama but not in our characters
            self._context.calibrate(messages, completion.prompt_tokens)
        if completion.content.strip() or (completion.tool_calls and allow_tools):
            return completion
        log.warning("empty answer from Gemma; retrying once")
        nudged = [
            *messages,
            {"role": "user", "content": prompts.EMPTY_NUDGE},
        ]  # an identical request may come back empty again
        return await self._gemma.chat(nudged, max_tokens=max_tokens, tools=tools)
