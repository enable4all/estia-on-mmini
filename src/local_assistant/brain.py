"""One answer: build Gemma's input, run the tool loop (at most 5 tool calls), return the reply."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_assistant import prompts
from local_assistant.babble import PracticeTurn, broken, tidy, users_words
from local_assistant.claims import (
    claiming_clauses,
    clauses,
    mentions_reminder,
    restates,
    without_reminder_talk,
)
from local_assistant.context import ContextBuilder, without_fake_notices, without_notes
from local_assistant.gemma import Completion, GemmaClient, ToolCall
from local_assistant.signal_text import MARKERS, unmarked
from local_assistant.store import Conversation, Message
from local_assistant.tools import ReminderAttempt, Tools, Turn
from local_assistant.video import without_copies
from local_assistant.web_search import sources

log = logging.getLogger(__name__)


INSTANT_TOOLS = frozenset({"remember", "forget", "end_call"})  # no "let me check" on a call for these
DOCUMENT_REPLY_TOKENS = 2_000  # an answer after read_document may run this long: a section in full (16 Oct)


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
    # she said she switched a light or plug while nothing was switched in this answer: code's line, a note (and said on a
    # call), never in her history
    device: str = ""
    device_spoken: str = ""

    @property
    def hers(self) -> str:
        """Her own words in `text`, without code's line(s) after them ("" when only code's line is left)."""
        return self.text.removesuffix(self.not_set).rstrip() if self.not_set else self.text


# web addresses, also bare ones with a path; a link's trailing punctuation stays in the sentence
_LINK = re.compile(r"(?:https?://|www\.)[^\s()<>]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s()<>]*", re.IGNORECASE)


def _split_links(text: str, pattern: re.Pattern[str], change: Callable[[str], str], allowed: str = "") -> str:
    """Links that only markers split ("**evil**.com", "evil.**com**", "`evil`.com"): the phone's text styles drop
    markers (signal_text), so the text is read again with every marker left out, and each link found only there, which
    the user did not send (`allowed`), is changed where it stands (`change`: defanged or left out), the markers inside
    it dropped. A link the markup shows whole is the first pass's."""
    kept = [index for index, char in enumerate(text) if char not in MARKERS]
    said = (allowed.casefold(), unmarked(allowed).casefold())
    for match in reversed(list(pattern.finditer("".join(text[index] for index in kept)))):
        core = match.group(0).rstrip(".,;:!?")
        if not core or any(core.casefold() in user for user in said):
            continue
        start, end = kept[match.start()], kept[match.start() + len(core) - 1] + 1
        if any(char in MARKERS for char in text[start:end]):
            text = text[:start] + change(core) + text[end:]
    return text


def without_links(text: str) -> str:
    """An answer that searched the web: its links come only in the code-written Sources note, so a link a web page
    planted (a fake login page) never reaches the user in her words, also one that markup splits."""

    def keep_punctuation(match: re.Match[str]) -> str:
        link = match.group(0)
        return link[len(link.rstrip(".,;:!?")) :]

    text = _split_links(_LINK.sub(keep_punctuation, text), _LINK, lambda core: "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# a bare domain Signal would make tappable (common top-level domains only, so file names stay as they are)
_BARE_DOMAIN = re.compile(
    r"(?<![\w@.\[\]/-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|net|org|info|biz|io|co|cc|app|dev|xyz|me|link|click|top|site|online|shop|store|live|icu|buzz|ru|cn|tk|"
    r"nl|de|uk|gr|eu|fr|it|es|be|pl|pt|ch|at|us|ai|ly)(?![\w@-]|\.\w)",
    re.IGNORECASE,
)


def defang_links(text: str, allowed: str) -> str:
    """After outside text: links in her answer that the user did not send (`allowed`: their message) lose what makes
    them tappable ("https://evil.example/login" -> "evil[.]example/login"), also one that markup splits
    ("**evil**.com" -> "**evil[.]com"); the Sources note stays code's."""
    said = allowed.casefold()

    def defang(match: re.Match[str]) -> str:
        link = match.group(0)
        core = link.rstrip(".,;:!?")
        if core.casefold() in said:
            return link
        host, slash, rest = re.sub(r"^https?://", "", core, flags=re.IGNORECASE).partition("/")
        return host.replace(".", "[.]") + slash + rest + link[len(core) :]

    text = _LINK.sub(defang, text)
    text = _BARE_DOMAIN.sub(
        lambda match: match.group(0) if match.group(0).casefold() in said else match.group(0).replace(".", "[.]"), text
    )
    for pattern in (_LINK, _BARE_DOMAIN):  # then a link that only markup splits ("**evil**.com"), as the phone joins it
        text = _split_links(text, pattern, lambda core: defang_links(core, ""), allowed)
    return text


# the end_call tool's name written into her answer ("Bye!  end_call", "(end_call)", "`end_call()`")
_WRITTEN_END_CALL = re.compile(r"[ \t]*[(\[`*]*\bend_call\b(?:\(\))?[)\]`*]*", re.IGNORECASE)


THINKING_END = "<channel|>"  # the end of the model's thinking channel ("<|channel>thought\n…<channel|>")


_THINKING_LABEL = re.compile(r"\A\s*thought[ \t]*\n", re.IGNORECASE)  # the channel's label alone on her first line


def without_thinking(text: str) -> str:
    """Her answer without a thinking channel that leaked into it (evals, 15 Oct, end_call turns: "thought\n<channel|>
    Goodbye!", or the label alone, "thought\nGoodbye!"; a caller would hear "thought"): everything up to its last end
    marker is left out, else a first line that is only the label."""
    if THINKING_END in text:
        return text.rsplit(THINKING_END, 1)[1].lstrip()
    return _THINKING_LABEL.sub("", text, count=1).lstrip() if _THINKING_LABEL.match(text) else text


def without_written_end_call(text: str) -> tuple[str, bool]:
    """Gemma sometimes writes end_call after her goodbye instead of calling it (live 2026-10-10): the name is left
    out wherever it is (never said or sent), and whether it ended her answer is returned (on a call that counts as
    ending it; a mention in the middle is talk)."""
    ended = bool(re.search(_WRITTEN_END_CALL.pattern + r"\s*$", text, re.IGNORECASE))
    return _WRITTEN_END_CALL.sub("", text).strip(), ended


def _links(turn: Turn) -> list[tuple[str, str]]:
    """The web results' links, then the map links, without repeats, at most 5."""
    links = sources(turn.web)
    links += [link for link in turn.links if all(link[1] != url for _, url in links)]
    return links[:5]


NOTHING_SAVED = 'Profile changes: none. Nothing was saved; say "remember that ..." to try again.'


def _claims_a_save(text: str) -> bool:
    """The rules make Gemma confirm a save with "Noted: ..." (a plain "Noted!" acknowledgement does not count)."""
    return re.match(r"\s*noted\s*:", text, re.IGNORECASE) is not None


def _practice_claims(words: str, users: Sequence[str]) -> list[str]:
    """The clauses of her practice words that claim a reminder or a call (the broad set), leaving out a roleplay
    sentence the user wrote that she repeats ("Mañana te llamo, mamá"): a clause that is the user's words, or whose
    every claiming piece (split again at dashes and colons) is. A claim of hers around the user's words ("And I will
    call you at 7") stays."""

    def theirs(clause: str) -> bool:
        return any(users_words(clause, line) for line in users)

    found: list[str] = []
    for clause in claiming_clauses(words, broad=True, saved=False):
        pieces = [piece for piece, _ in clauses(clause, fine=True) if claiming_clauses(piece, broad=True)]
        if not theirs(clause) and not (pieces and all(theirs(piece) for piece in pieces)):
            found.append(clause)
    return found


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


def _without_captions(text: str, turn: Turn) -> str:
    """A video's captions are never written out: her sentences that copy them go (video.without_copies), and code's
    line gives the user's own link to the video instead (counts in the log, never text)."""
    lines: list[str] = []
    for kept in turn.videos_read.values():
        text, copied = without_copies(text, kept.captions)
        if copied:
            lines.append(prompts.VIDEO_NOT_SENT.format(link=kept.link))
    if lines:
        log.warning("message %d: captions written out; those sentences left out", turn.message.id)
    return "\n".join(part for part in (text, *lines) if part)


def _has_images(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(message.get("content"), list) for message in messages)


def profile_notice(turn: Turn) -> str:
    lines = [f"Saved #{fid}: {text} (/forget {fid} undoes it)" for fid, text in turn.saved]
    lines += [f"Updated #{fid}: {text}" for fid, text in turn.updated]
    lines += [f"Removed #{fid}: {text}" for fid, text in turn.removed]
    lines += [f"Moved to notes #{fid}: {text}" for fid, text in turn.moved]  # making room: kept, not always sent
    return "**Profile changes**\n" + "\n".join(lines) if lines else ""  # bold in Signal (markup)


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
        practice: PracticeTurn | None = None,
        glance: str = "",
        reads: Sequence[tuple[str, str]] = (),
    ) -> Reply:
        """Gemma's reply to the newest message of the conversation (already stored), with its photos.

        `recent`: photos of an earlier message that are still shown (a follow-up question about them).
        On a call: `max_tokens` keeps spoken answers short; `on_tools` is called once, when the first tool that
        takes a moment runs (the call says "let me check"); `glance`: code's counts at the call's start (numbers and
        times only: no outside text, so nothing in the answer is refused for it); `reads`: tool calls (name, JSON
        arguments) code makes itself before she answers (a yes to her offer of the day's rundown): run through the same
        tools, logged and counted like hers and given to her as her own tool calls with their results, so what they
        read is outside text for this answer exactly as when she calls them. `practice`: a /babble turn
        (`_practise`)."""
        limit_tokens = max_tokens or self._reply_max_tokens
        shown = {**(recent or {}), **({message.id: images} if images else {})}
        if practice is not None:
            return await self._practise(conversation, message, practice, limit_tokens, shown)
        turn = Turn(message=message, after_outside=self._context.after_outside(conversation, message.id))
        # code's video lines, last in the prompt: a kept video's (reading it again), a YouTube link in this message's
        # (what read_link reads); none on a call, so never with a call's glance
        glance = "\n\n".join(part for part in (glance, self._tools.video_hint(message)) if part)
        # on a call her rules leave out the list of what she can do (spoken sentences, never a list)
        on_call = message.text.startswith(prompts.CALL_MARK)
        messages = self._context.build(conversation, shown or None, glance=glance, call=on_call)
        said = [  # the user's own words: her notes before the newest message are not theirs
            without_notes(str(m["content"]))
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        # the user asked for a reminder, a wake-up or a call now or in their message before ("and at 17:00?")
        asked_before = any(mentions_reminder(text) for text in said[-2:]) or mentions_reminder(message.text)
        calls = 0
        if reads:  # code's own reads, as if she had called them first
            if on_tools is not None:
                on_tools()
                on_tools = None
            read = tuple(ToolCall(f"code_{number}_{name}", name, args) for number, (name, args) in enumerate(reads))
            messages.append(_assistant_tool_message(Completion(content="", tool_calls=read)))
            for call in read:
                calls += 1
                result = await self._tools.call(turn, call.name, call.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        corrected = False
        while True:
            limit = self._learn_tool_calls if turn.learned_document_id is not None else self._max_tool_calls
            allow_tools = calls < limit
            # a section given in full after reading its pages (owner, 16 Oct: B5 was cut mid-sentence at 600); a
            # call keeps its own spoken cap
            room = DOCUMENT_REPLY_TOKENS if turn.read_documents and max_tokens is None else limit_tokens
            completion = await self._complete(messages, allow_tools, max(limit_tokens, room))
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
            text, wrote_end_call = without_written_end_call(without_fake_notices(without_thinking(completion.content)))
            if wrote_end_call and self._tools.calls_on and message.text.startswith(prompts.CALL_MARK):
                turn.end_call = True
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
            elif (
                turn.untrusted
                or turn.read_mail_or_calendar
                or turn.after_outside
                or message.text.startswith((prompts.AUDIO_MARK, "[photo]"))
            ):  # a link planted in outside text never reaches the user tappable, unless they sent it themselves
                text = defang_links(text, "\n".join([message.text, *turn.allowed_links]))
            text = _without_captions(text, turn)
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
            # a device change she claims that no device_set made (English first): code's line after her words; "else"
            # when this answer did switch one (its Devices note says which)
            device = ""
            if self._tools.unmade_switch(turn, text):
                device = prompts.DEVICE_GUARD_ELSE if turn.switched else prompts.DEVICE_GUARD
                log.warning("message %d: a device change claimed, none made", message.id)
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
                device=device,
                device_spoken=device,
            )

    async def _practise(
        self,
        conversation: Conversation,
        message: Message,
        practice: PracticeTurn,
        max_tokens: int,
        shown: Mapping[int, Sequence[str]],
    ) -> Reply:
        """A practice turn: the practice rules, no tools offered (none can run), no tool loop. A reply that is empty,
        only "---" or only a "→" line is asked again once, then replaced by the language's fallback question (the
        broken reply is never kept); "---" lines go and one "→" line at most comes first (none when spoken, none that
        changes nothing the user wrote). A written end_call on a call still ends it. Nothing can be saved or set here:
        a "Noted: ..." gets code's note, and so does a claimed reminder when the user asked for one (this message or
        the one before; a roleplay line such as a shop's "I'll call you tomorrow" is not a promise). The claim is read
        in her words only: never the "→" line (the user's own sentence, corrected) nor a claim-shaped sentence the user
        wrote that she repeats ("If I have time, I will call you": the practice evals' en-17). The injection-residue
        guards: no set_reminder can run, so the overlap rule has nothing to check; links are defanged as in a chat
        answer, after outside text in the practice conversation (an audio file or a photo) unless the user sent them."""
        # a practice call's turn gets the call's style too (its rules have no list of what she can do to leave out)
        on_call = message.text.startswith(prompts.CALL_MARK)
        messages = self._context.build(conversation, shown or None, rules=practice.rules, call=on_call, notes=False)
        completion = await self._gemma.chat(messages, max_tokens=max_tokens, tools=None)
        if not _has_images(messages):  # image tokens are counted by Ollama but not in our characters
            self._context.calibrate(messages, completion.prompt_tokens)
        if broken(completion.content):
            log.warning("message %d: a broken practice reply; asking once more", message.id)
            nudged = [*messages, {"role": "user", "content": prompts.EMPTY_NUDGE}]
            completion = await self._gemma.chat(nudged, max_tokens=max_tokens, tools=None)
        if broken(completion.content):
            log.warning("message %d: the practice reply stayed broken; the fallback question instead", message.id)
            text = practice.fallback
        else:
            text = tidy(
                without_fake_notices(without_thinking(completion.content)), spoken=practice.spoken, said=message.text
            )
        text, wrote_end_call = without_written_end_call(text)
        end_call = wrote_end_call and self._tools.calls_on and message.text.startswith(prompts.CALL_MARK)
        text = text or practice.fallback
        # the chat's link rule over the practice conversation (no tool runs here, so its outside text is an audio file
        # or a photo, in it or answered now); the chat's outside text is not in this context
        if self._context.after_outside(conversation, message.id) or message.text.startswith(
            (prompts.AUDIO_MARK, "[photo]")
        ):
            text = defang_links(text, message.text)
        said = [
            without_notes(str(m["content"]))
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        users = [*said[-2:], message.text]
        asked = any(mentions_reminder(line) for line in users)
        words = tidy(text, spoken=True)  # her words, without the "→" line (the user's own sentence, corrected)
        claimed = _practice_claims(words, users)
        reminder = spoken = ""
        if self._tools.reminders_on and asked and claimed:
            reminder, spoken = prompts.PRACTICE_NO_REMINDER, prompts.PRACTICE_NO_REMINDER_SPOKEN
            log.warning("message %d: no reminder was set (claimed in practice)", message.id)
        claimed_save = _claims_a_save(words)
        if claimed_save:
            log.warning("message %d: 'Noted' in practice, where nothing is saved", message.id)
        return Reply(
            text=text,
            tool_calls=0,
            prompt_tokens=completion.prompt_tokens,
            notice=prompts.PRACTICE_NOTHING_SAVED if claimed_save else "",
            end_call=end_call,
            reminder=reminder,
            reminder_spoken=spoken,
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
