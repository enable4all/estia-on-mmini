"""One answer: build Gemma's input, run the tool loop (at most 5 tool calls), return the reply."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_assistant import prompts
from local_assistant.claims import claiming_clauses, mentions_reminder, restates, without_reminder_talk
from local_assistant.context import ContextBuilder, without_fake_notices
from local_assistant.gemma import Completion, GemmaClient
from local_assistant.store import Conversation, Message
from local_assistant.tools import ReminderAttempt, Tools, Turn
from local_assistant.web_search import sources

log = logging.getLogger(__name__)


INSTANT_TOOLS = frozenset({"remember", "forget", "end_call"})  # no "let me check" on a call for these


@dataclass(frozen=True)
class Reply:
    text: str  # what the user gets: her words, then code's line(s) when no reminder was set (`not_set`)
    tool_calls: int
    prompt_tokens: int
    notice: str = ""  # profile changes in this answer, written by code so Gemma cannot hide or invent them
    end_call: bool = False  # on a call: hang up after speaking it
    sources: str = ""  # the web results' links, written by code
    reminder: str = ""  # reminders set or cancelled in this answer (or that none was, after a claim), written by code
    reminder_spoken: str = ""  # the same, as a line to say on a call (English, code's words)
    # set_reminder stored nothing in this answer: code's line(s) why, at the end of `text`, which keeps only her
    # sentences that mention no reminder, waking or call (in any language); kept in the history with her words
    not_set: str = ""
    not_set_spoken: str = ""  # the same to say on a call, in English (the spoken reasons: nothing to type)

    @property
    def hers(self) -> str:
        """Her own words in `text`, without code's line(s) after them ("" when only code's line is left)."""
        return self.text.removesuffix(self.not_set).rstrip() if self.not_set else self.text


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


def _reminder_text(arguments: str) -> str:
    """The text of a set_reminder whose arguments never reached the tool, when there is one to read."""
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return ""
    return str(args.get("text") or "").strip() if isinstance(args, dict) else ""


def _same(first: str, second: str) -> bool:
    return " ".join(first.casefold().split()) == " ".join(second.casefold().split())


def _retried(attempt: ReminderAttempt, later: ReminderAttempt) -> bool:
    """A later try stored what this refused one meant: the same text (she corrected the time), or anything after a
    try whose arguments could not be read (no text to compare: she tried again)."""
    unreadable = attempt.reason == prompts.REMINDER_WHY_UNREADABLE and not attempt.text
    return not later.reason and (unreadable or _same(later.text, attempt.text))


def unset(attempts: Sequence[ReminderAttempt]) -> list[ReminderAttempt]:
    """The set_reminder tries of an answer that stored nothing, without repeats; a refused try that a later stored try
    replaced is left out (`_retried`)."""
    missed: list[ReminderAttempt] = []
    for number, attempt in enumerate(attempts):
        retried = any(_retried(attempt, later) for later in attempts[number + 1 :])
        if attempt.reason and not retried and attempt not in missed:
            missed.append(attempt)
    return missed


def _not_set_lines(attempts: Sequence[ReminderAttempt], *, spoken: bool = False) -> str:
    """Code's answer when no set_reminder of an answer stored anything: why, one line per reason (`spoken`: the
    reasons to say on a call)."""
    whys = (attempt.said if spoken else attempt.reason for attempt in attempts if attempt.reason)
    return "\n".join(prompts.REMINDER_NOT_SET.format(reason=why) for why in dict.fromkeys(whys)) or (
        prompts.REMINDER_GUARD_SPOKEN
    )


def _quoted(text: str, limit: int = 80) -> str:
    """A refused reminder's text for its note: in quotes, cut at `limit` characters; none: "one reminder"."""
    if not text:
        return prompts.REMINDER_NOT_SET_WHAT
    return f'"{text if len(text) <= limit else text[: limit - 1].rstrip() + "…"}"'


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
        said = [str(m["content"]) for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
        # the user asked for a reminder, a wake-up or a call now or in their message before ("and at 17:00?")
        asked_before = any(mentions_reminder(text) for text in said[-2:]) or mentions_reminder(message.text)
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
                    asked = call.name == "set_reminder"
                    tried = len(turn.reminder_attempts)
                    turn.reminder_tried |= asked  # before the tool runs: unreadable arguments and the limit count too
                    if calls >= limit:
                        result = "Error: tool limit reached for this message; answer with what you have."
                        why = prompts.REMINDER_WHY_LIMIT
                    else:
                        calls += 1
                        result = await self._tools.call(turn, call.name, call.arguments)
                        why = prompts.REMINDER_WHY_UNREADABLE
                    if asked and len(turn.reminder_attempts) == tried:  # it never reached the reminder checks
                        turn.reminder_attempts.append(ReminderAttempt(_reminder_text(call.arguments), why))
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
            reminder = list(turn.reminder_notes)
            spoken = list(turn.reminder_spoken)
            # per answer, never across turns: a set_reminder that stored nothing gets code's line whatever she wrote;
            # so does a claim when none was stored: asked for one (this message or the one before, or she listed or
            # tried one): the strict, broad reading in all 11 languages; not asked: the precise one, and none at all
            # when she summarised outside text (a mail's payment reminder is not hers); a restated listed one is fine
            saved = bool(turn.saved or turn.updated)
            asked_for_one = asked_before or turn.reminders_listed or turn.reminder_tried
            if asked_for_one:  # asked: the strict reading
                clauses = claiming_clauses(text, broad=True, saved=saved)
            elif turn.untrusted or turn.read_mail_or_calendar or turn.used_web:  # a summary of outside text: others'
                clauses = []
            else:  # not asked, nothing outside: an unasked promise ("Noted, and I'll remind you at 8:30")
                clauses = claiming_clauses(text, saved=saved)
            claimed = any(not restates(clause, turn.listed_reminders) for clause in clauses)
            not_set = not_set_spoken = ""
            if self._tools.reminders_on and not turn.reminder_set and turn.reminder_tried:
                # in any language: her sentences about a reminder, waking or a call go (a translated "it's set" too),
                # her others stay, and code's line why follows them (only that line when nothing is left)
                not_set = _not_set_lines(turn.reminder_attempts)
                not_set_spoken = " ".join(_not_set_lines(turn.reminder_attempts, spoken=True).splitlines())
                text = "\n".join(part for part in (without_reminder_talk(text, saved=saved), not_set) if part)
                log.warning("message %d: no reminder was set (refused); her reminder talk left out", message.id)
            elif self._tools.reminders_on and not turn.reminder_set and claimed:
                new = turn.reminders_listed  # beside a listing: one more that was never set
                reminder.append(prompts.REMINDER_GUARD_NEW if new else prompts.REMINDER_GUARD)
                spoken.append(prompts.REMINDER_GUARD_NEW if new else prompts.REMINDER_GUARD_SPOKEN)
                log.warning("message %d: no reminder was set (claimed)", message.id)
            elif self._tools.reminders_on and turn.reminder_set:  # one set, another refused: that one gets a note
                for missed in unset(turn.reminder_attempts):
                    reminder.append(
                        prompts.REMINDER_NOT_SET_ONE.format(what=_quoted(missed.text), reason=missed.reason)
                    )
                    spoken.append(prompts.REMINDER_NOT_SET_ONE_SPOKEN.format(reason=missed.said))
                    log.warning("message %d: one of its reminders was not set", message.id)
            text = text or prompts.CONFUSED
            return Reply(
                text=text,
                tool_calls=calls,
                prompt_tokens=completion.prompt_tokens,
                notice=profile_notice(turn) or (NOTHING_SAVED if _claims_a_save(text) else ""),
                end_call=turn.end_call,
                sources=prompts.sources_note(_links(turn)),
                reminder="\n".join(reminder),
                reminder_spoken=" ".join(spoken),
                not_set=not_set,
                not_set_spoken=not_set_spoken,
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
