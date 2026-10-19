"""What Gemma sees: rules, the style, the frozen profile snapshot and the summary, then the recent conversation.

Fixed parts come first and changing parts last, so Ollama can reuse its prompt cache. The
snapshot changes only at /new, after a summary, after /forget or /fix and at service start.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.config import DEFAULT_NAME, DEFAULT_STYLE
from local_assistant.profile import Profile
from local_assistant.soul import Soul, load_soul, soul_part
from local_assistant.store import Conversation, Message, Store, ToolCallRecord
from local_assistant.timefmt import local_stamp

log = logging.getLogger(__name__)

SNAPSHOT_KEY = "profile_snapshot"
RATIO_KEY = "chars_per_token"
DEFAULT_CHARS_PER_TOKEN = 3.5
_MESSAGE_OVERHEAD = 8  # role markers and the time stamp
_TRACE_RESULT_CHARS = 300


def without_fake_notices(text: str) -> str:
    """Drop paragraphs that look like the bridge's own notices, in any markup ("**Profile changes**": code's is bold).
    Used on Gemma's new answers (it may imitate them) and on stored replies (before the fix, notices were sent inside
    the reply text)."""
    paragraphs = text.split("\n\n")
    notices = ("profile changes", "(while we were quiet")
    kept = [p for p in paragraphs if not p.lstrip(" \t\n#*_~").lower().startswith(notices)]
    return "\n\n".join(kept)


PHOTO_PREFIX = "[photo]"  # how a photo message is stored: "[photo] <caption>"
GONE_PREFIX = "[photo, no longer visible]"  # in Gemma's input once the photo is not shown any more


def _with_notes(chat: dict[str, Any], block: str) -> dict[str, Any]:
    """The user's message with her notes before its words (in its text part when it carries a photo): the question
    comes last (evals, 16 Oct: with the notes after it, an old note won over the PROFILE)."""
    if not block:
        return chat
    content = chat["content"]
    if isinstance(content, str):
        return {**chat, "content": f"{block}\n\n{content}"}
    parts = [{**part, "text": f"{block}\n\n{part['text']}"} if part.get("type") == "text" else part for part in content]
    return {**chat, "content": parts}


def without_notes(text: str) -> str:
    """A user message's text as the user wrote it: her notes block before it (`_with_notes`) left out."""
    if text.startswith(prompts.NOTES_HEADER) and "\n\n" in text:
        return text.split("\n\n", 1)[1]
    return text


def _text_of(content: Any) -> str:
    """The text of a chat message's content; image parts do not count (base64 would distort the ratio)."""
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


_OUTSIDE_TEXT = frozenset({"read_link", "search_history", "search_documents", "read_document"})

# code's mark on today's agenda lines (calendar_tools._mark), right after the times: true only when it was read, so a
# replay keeps the times and drops the mark ("(in 6 h)" from 09:00 is wrong at 14:30); a title's own words stay
_AGENDA_MARK = re.compile(
    r"^(- [^\n]*?(?:\d{2}:\d{2}|\(\d{2}:\d{2}(?:–\d{2}:\d{2})? [^()\n]+\)))"  # the times, a trip's zone time last
    r" \((?:over|now(?:, until \d{2}:\d{2})?|in \d+ (?:min|h(?: \d+ min)?))\)",
    re.MULTILINE,
)


def _short_result(call: ToolCallRecord) -> str:
    """Earlier tool results are replayed short: a learned page can be 6,000 tokens."""
    if call.name == "learn_link" and call.ok and call.result.startswith("Page content"):
        return "Page read and stored (text not repeated here)."
    if call.name in _OUTSIDE_TEXT or (call.name.startswith("project_") and call.name != "project_list"):
        # outside text is never replayed: a line planted in it could otherwise act on a later "thanks"
        return f"({call.name} result not repeated here: outside text; read it again if it is needed)"
    result = _AGENDA_MARK.sub(r"\1", call.result) if call.name == "calendar_agenda" else call.result
    if len(result) <= _TRACE_RESULT_CHARS:
        return result
    return result[:_TRACE_RESULT_CHARS] + " …"


class ContextBuilder:
    def __init__(
        self,
        store: Store,
        profile: Profile,
        tz: ZoneInfo,
        *,
        window_tokens: int = 12_000,
        emergency_tokens: int = 20_000,
        rules: str | None = None,
        soul_path: Path | None = None,
        name: str = DEFAULT_NAME,
        style: str = DEFAULT_STYLE,
        calm_on_worry: bool = True,
    ) -> None:
        """`style`: her built-in style while there is no usable soul.md ([personality] style; KeyError for a name
        prompts.BUILT_IN_STYLES does not have). `calm_on_worry`: code's line right after a usable soul.md
        ([personality] calm_on_worry)."""
        self._store = store
        self.name = name  # hers (the config's `name`): the style's header, the summary's transcript
        self._rules = prompts.rules(name=name) if rules is None else rules
        self._soul_path = soul_path
        self._built_in = prompts.BUILT_IN_STYLES[style]
        self._built_in_call = prompts.BUILT_IN_CALL_STYLES[style]  # its short call version
        self.built_in_style = style  # its name, for /soul
        self.calm_on_worry = calm_on_worry
        self._soul_logged: tuple[str, int, int] | None = None  # logged once per change (and at the first read)
        self._profile = profile
        self._tz = tz
        self._window_tokens = window_tokens
        self._emergency_tokens = emergency_tokens

    def set_rules(self, rules: str) -> None:
        """New rules from the next answer on (another language)."""
        self._rules = rules

    def refresh_snapshot(self) -> str:
        snapshot = self._profile.snapshot()
        self._store.set_state(SNAPSHOT_KEY, snapshot)
        return snapshot

    def snapshot(self) -> str:
        return self._store.get_state(SNAPSHOT_KEY) or self.refresh_snapshot()

    def chars_per_token(self) -> float:
        raw = self._store.get_state(RATIO_KEY)
        return float(raw) if raw else DEFAULT_CHARS_PER_TOKEN

    def estimate(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token())

    def calibrate(self, messages: list[dict[str, Any]], prompt_tokens: int) -> None:
        """Learn the real characters-per-token ratio from Ollama's usage numbers.

        Greek text costs more tokens per character than English. Tool schemas and template tokens
        are not in the character count, which pushes the ratio down: estimates err on the high side.
        """
        chars = sum(len(_text_of(message.get("content"))) for message in messages)
        if prompt_tokens <= 0 or chars <= 0:
            return
        measured = max(1.5, min(5.0, chars / prompt_tokens))
        smoothed = 0.7 * self.chars_per_token() + 0.3 * measured
        self._store.set_state(RATIO_KEY, f"{smoothed:.3f}")

    def window(self, conversation: Conversation) -> list[Message]:
        return self._store.window_messages(conversation.id, after_seq=conversation.summary_upto_seq)

    def after_outside(self, conversation: Conversation, message_id: int) -> bool:
        """Whether her context for this message still holds a reply or summary that followed outside text: an earlier
        message of the conversation in the window, or summarised, whose answer read outside text (or that was an audio
        file or a photo). A new conversation (/new) starts without it."""
        for found_id, seq in self._store.outside_seqs(conversation.id):
            if found_id == message_id:
                continue
            if seq > conversation.summary_upto_seq or conversation.summary:
                return True
        return False

    def turn_before(self, message: Message) -> list[str]:
        """The texts of the turn before this message (the user's, hers and code's notes)."""
        if message.seq is None:
            return []
        return [found.text for found in self._store.turn_before(message.conversation_id, message.seq)]

    def window_tokens_of(self, messages: list[Message]) -> int:
        return sum(self.estimate(message.text) + _MESSAGE_OVERHEAD for message in messages)

    def soul(self) -> Soul:
        """The user's soul.md (style only), read now: an edit counts from the next answer."""
        if self._soul_path is None:
            return Soul("", "no style file is set up")
        soul = load_soul(self._soul_path)
        texts, calls = len(soul_part(soul.text, call=False)), len(soul_part(soul.text, call=True))
        seen = (soul.problem, texts, calls)
        if seen != self._soul_logged:
            if soul.problem:
                log.warning("soul.md not used: %s", soul.problem)
            else:  # what texts and calls each get (a part with nothing for a kind leaves it the built-in style)
                zero = " (0: the built-in style)" if 0 in (texts, calls) else ""
                log.info("soul.md in use: %d characters for texts%s, %d for calls", texts, zero, calls)
        self._soul_logged = seen
        return soul

    def style(self, *, call: bool = False) -> str:
        """Her style, placed below the rules: the user's soul.md with code's calm-on-worry line right after it (unless
        turned off), or without a usable one her built-in style, hearth or playful, which carries that line itself
        (also below the rules of a message she starts herself). `call`: a call's turn, which gets soul.md's "[call]"
        part instead of its "[chat]" part (soul.soul_part: the text before them goes to both, and a soul.md without
        them goes whole to both), or the built-in style's short call version in place of the style; a soul.md with
        nothing for this kind leaves it the built-in one."""
        soul = self.soul()
        text = soul_part(soul.text, call=call) if soul.text else ""
        if text:
            calm = f"\n\n{prompts.SOUL_CALM}" if self.calm_on_worry else ""
            return f"{prompts.SOUL_HEADER.format(name=self.name)}\n{text}{calm}"
        built_in = self._built_in_call if call else self._built_in
        return f"{prompts.BUILT_IN_STYLE_HEADER.format(name=self.name)}\n{built_in}"

    def system_prompt(
        self, conversation: Conversation, rules: str | None = None, glance: str = "", *, call: bool = False
    ) -> str:
        """The rules (or `rules`, for one answer: a practice turn), then the style (below the rules); then the profile
        and the summary; on a call, last, the call's glance (code's counts at its start: `glance`). `call`: a call's
        turn, whose rules leave out the list of what she can do (prompts.without_capabilities) and whose style is the
        call's (`style(call=True)`)."""
        chosen = self._rules if rules is None else rules
        parts = [prompts.without_capabilities(chosen) if call else chosen, self.style(call=call), self.snapshot()]
        if conversation.summary:
            parts.append(f"EARLIER IN THIS CONVERSATION (summary)\n{conversation.summary}")
        if glance:  # last: the same for the whole call, so the prompt cache keeps everything above it
            parts.append(glance)
        return "\n\n".join(parts)

    def to_chat(self, message: Message, images: Sequence[str] = (), *, newest: bool = False) -> dict[str, Any]:
        """One message as Gemma's input. The user's carry their local time; the newest one also the year, so a
        date she writes (set_reminder) has the right one, while the system prompt stays the same every day."""
        if message.direction == "in":
            stamp = local_stamp(message.signal_ts, self._tz, year=newest) if message.signal_ts else ""
            body = message.text
            if not images and body.startswith(PHOTO_PREFIX):  # said plainly, or Gemma guesses what it showed
                body = GONE_PREFIX + body[len(PHOTO_PREFIX) :]
            text = f"{stamp} {body}".strip()
            if images:  # OpenAI-style parts; Ollama passes the image to Gemma's vision encoder
                parts = [{"type": "image_url", "image_url": {"url": uri}} for uri in images]
                return {"role": "user", "content": [{"type": "text", "text": text}, *parts]}
            return {"role": "user", "content": text}
        return {"role": "assistant", "content": without_fake_notices(message.text)}

    def build(
        self,
        conversation: Conversation,
        images: Mapping[int, Sequence[str]] | None = None,
        *,
        rules: str | None = None,
        glance: str = "",
        call: bool = False,
        notes: bool = True,
        newest: bool = True,
    ) -> list[dict[str, Any]]:
        """System prompt, then the conversation. Earlier tool calls are replayed after the message that caused
        them: without them Gemma saw "remember X" → "Noted: X" and learned it could skip the remember call.
        Photos (data URIs per message id) are only given for the message being answered (Hermes: images are
        turn-scoped); in history a photo message is its "[photo] caption" text. `rules`: other rules for this answer
        only (a practice turn); the style and the profile stay below them. `glance`: a call's counts at its start.
        `call`: a call's turn (its rules without the list of what she can do). `notes`: her notes that may matter
        for the newest message come in it, before its words (the profile's second part, owner 16 Oct), so everything
        before it stays the same from one message to the next; off for a practice turn. `newest`: the window ends with
        the message being answered; False for the input before the next message is stored (a call's warm-up): every
        message comes as it will once that one is (no year, no notes), so the next answer's input is this one and the
        new message."""
        system = self.system_prompt(conversation, rules, glance, call=call)
        built: list[dict[str, Any]] = [{"role": "system", "content": system}]
        window = self.window(conversation)
        # the message being answered: the last of the user's in the window's order (a text held during a call is stored
        # first, a lower id, and gets its place only when answered), not the highest id; none before it is stored
        found = (message.id for message in reversed(window) if message.direction == "in")
        answered = next(found, None) if newest else None
        for message in window:
            chat = self.to_chat(message, (images or {}).get(message.id, ()), newest=message.id == answered)
            if notes and message.id == answered:
                chat = _with_notes(chat, self._profile.notes_block(message.text))
            built.append(chat)
            if message.direction == "in":
                built.extend(self._trace(message.id))
        return built

    def _trace(self, message_id: int) -> list[dict[str, Any]]:
        calls = self._store.tool_calls(message_id)
        if not calls:
            return []
        assistant = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": f"call_{c.id}", "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ],
        }
        results = [{"role": "tool", "tool_call_id": f"call_{c.id}", "content": _short_result(c)} for c in calls]
        return [assistant, *results]

    def enforce_limit(self, conversation: Conversation) -> Conversation:
        """Emergency trim: far over budget, drop the oldest messages without a summary (logged)."""
        messages = self.window(conversation)
        total = self.window_tokens_of(messages)
        if total <= self._emergency_tokens or len(messages) < 2:
            return conversation
        keep_from = len(messages) - 1
        kept = self.window_tokens_of(messages[keep_from:])
        while keep_from > 0:
            previous = self.window_tokens_of([messages[keep_from - 1]])
            if kept + previous > self._window_tokens:
                break
            keep_from -= 1
            kept += previous
        if keep_from == 0:
            return conversation
        self._store.move_window_start(conversation.id, messages[keep_from - 1].seq or 0)
        log.warning("window at %d tokens: dropped %d oldest messages without a summary", total, keep_from)
        return self._store.conversation(conversation.id)

    def is_fresh(self, conversation: Conversation) -> bool:
        """No messages yet in this conversation's window (so no prompt cache to protect)."""
        return not self.window(conversation)
