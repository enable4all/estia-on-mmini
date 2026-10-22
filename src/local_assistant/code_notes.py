"""Code's notes in her history (owner, 21-22 Oct: hard rule 6 removed, "in the messaging conversation, Estia/Tiffany is
aware"; the form he chose: "as her tool's results").

Every message code sent the user and marked for her history (a note, a command's answer: `messages.history`, schema
v14) comes into Gemma's input as the result of a code_notes tool call that code writes: never her own words, in the
words the user saw (Signal's styles, the footer after them), with the time it was sent, in conversation order, within
the window and keep_days like any message. The tool is never offered and her own call of it is refused (tools), so she
can neither call nor forge one. A note that quotes outside text (mail, calendars, files, music, pages: `outside`) says
so, and its words count as outside text for the guards on every turn whose input holds it (`drawn_from_notes`, and
`ContextBuilder.after_outside`); one quoting private text (mail, calendars, documents: `private`) never feeds a web
search, Google Maps or Spotify. No chat-template token of a note reaches her input (`result`)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.fetcher import strip_control_tokens
from local_assistant.language import ENGLISH, function_words
from local_assistant.signal_text import plain
from local_assistant.store import Message
from local_assistant.timefmt import local_stamp

TOOL = "code_notes"
NOTE_MAX_CHARS = 3_000  # one note in her input: a longer one (a long /help) is cut there, the user saw it whole
COMMAND_MAX_CHARS = 200  # the command a note answers, as named in its line
# all the notes in her input: past NOTES_MAX_TOKENS the notes' start moves on to keep the newest NOTES_KEEP_TOKENS, in
# one step (Ollama's prompt cache is rebuilt once, not at every note); a quiet user's digests never pile up
NOTES_MAX_TOKENS = 4_000
NOTES_KEEP_TOKENS = 2_000
OUTSIDE_COMMANDS = frozenset({"/learn", "/docs"})  # their answers quote a page's title and facts, or PDF names
PRIVATE_COMMANDS = frozenset({"/docs"})  # PDF names: never to a web search, Google Maps or Spotify


@dataclass(frozen=True)
class Note:
    """One message code sent the user, as it enters her history; `command`: the user's command it answers, if any."""

    message: Message
    command: str = ""

    @property
    def outside(self) -> bool:
        return self.message.history in ("outside", "private")

    @property
    def private(self) -> bool:
        """It quotes mail, a calendar or documents: never to a web search, Google Maps or Spotify."""
        return self.message.history == "private"

    @property
    def text(self) -> str:
        """The words the user saw: the text as Signal shows it (markers gone), its footer after it (app._send)."""
        sent = f"{self.message.text}\n*{self.message.footer}*" if self.message.footer else self.message.text
        try:
            return plain(sent)
        except Exception:  # the text as written, as a send whose styles failed
            return sent


def quotes_outside(command: str) -> bool:
    """Whether a command's answer quotes outside text (a page's title and facts, PDF names)."""
    words = command.split()
    return bool(words) and words[0].lower() in OUTSIDE_COMMANDS


def quotes_private(command: str) -> bool:
    """Whether a command's answer quotes private outside text (PDF names)."""
    words = command.split()
    return bool(words) and words[0].lower() in PRIVATE_COMMANDS


def result(note: Note, tz: ZoneInfo) -> str:
    """The code_notes result: the time, the line that says what it is, then the words the user saw, without
    chat-template tokens or hidden characters (a mail's sender, a PDF's or a playlist's name could forge a turn; the
    user's copy keeps them, and the result is the same every time, so the prompt cache holds)."""
    stamp = local_stamp(note.message.signal_ts, tz) if note.message.signal_ts else ""
    command = " ".join(strip_control_tokens(note.command).split())[:COMMAND_MAX_CHARS]
    head = prompts.CODE_NOTE.format(
        stamp=stamp,
        what=prompts.CODE_NOTE_COMMAND.format(command=command) if command else "",
        how=prompts.CODE_NOTE_OUTSIDE if note.outside else ":",
    ).strip()
    body = strip_control_tokens(note.text)
    if len(body) > NOTE_MAX_CHARS:
        body = body[:NOTE_MAX_CHARS] + prompts.CODE_NOTE_CUT
    return f"{head}\n{body}"


def pair(note: Note, tz: ZoneInfo) -> list[dict[str, Any]]:
    """The note as a code_notes call and its result: the same bytes in every request that holds it (its id is the
    message's), so Ollama's prompt cache keeps it."""
    call_id = f"note_{note.message.id}"
    call = {"id": call_id, "type": "function", "function": {"name": TOOL, "arguments": "{}"}}
    return [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call_id, "content": result(note, tz)},
    ]


def in_order(window: Sequence[Message], notes: Sequence[Note], answered: int | None) -> list[Message | Note]:
    """The window's messages with code's notes among them, in conversation order. A note sent while she was answering
    never splits her turn: one about that answer (the busy note, a document's or a voice note's line before it) is
    left out, any other (a reminder that fired meanwhile) comes after her answer; a note after the message being
    answered (`answered`, not answered yet) is that answer's own, never in its request. The same note right after
    itself (one alert to each allowed account) comes once."""
    answer_at = {m.reply_to_id: m.seq or 0 for m in window if m.direction == "out" and m.reply_to_id is not None}
    last = next((m.seq for m in window if m.id == answered and m.id not in answer_at), None)
    items: list[tuple[int, Message | Note]] = [(m.seq or 0, m) for m in window]
    items += [(n.message.seq or 0, n) for n in notes]
    ordered: list[Message | Note] = []
    held: list[Note] = []
    open_turn: int | None = None  # an incoming message whose answer comes later in the window
    for seq, item in sorted(items, key=lambda found: found[0]):
        if isinstance(item, Note):
            if last is not None and seq > last:
                continue
            if open_turn is None:
                _add(ordered, item)
            elif item.message.reply_to_id != open_turn:
                held.append(item)
            continue
        ordered.append(item)
        if item.direction == "in":
            open_turn = item.id if answer_at.get(item.id, 0) > seq else None
        elif open_turn is not None and item.reply_to_id == open_turn:
            open_turn = None
            for found in held:
                _add(ordered, found)
            held = []
    for found in held:
        _add(ordered, found)
    return ordered


def _add(ordered: list[Message | Note], note: Note) -> None:
    before = ordered[-1] if ordered else None
    if not (isinstance(before, Note) and (before.text, before.command) == (note.text, note.command)):
        ordered.append(note)


# words for `drawn_from_notes`: links and mail addresses whole, else words of 3 letters or more or with a digit; never
# English's function words, "user" or a fact's category (the remember tool's own words)
_LINK = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)+/\S*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
_CATEGORIES = {"about", "family", "work", "study", "music", "social", "home", "humour", "other"}
_SKIP = frozenset({"user", "users", *_CATEGORIES, *(word.casefold() for word in function_words(ENGLISH))})


def _words(text: str) -> set[str]:
    found = {link.casefold().rstrip(".,;:!?") for link in _LINK.findall(text)}
    for word in re.findall(r"\w+", _LINK.sub(" ", text).casefold()):
        if (len(word) >= 3 or any(char.isdigit() for char in word)) and word not in _SKIP:
            found.add(word)
    return found


def _strong(word: str) -> bool:
    """A link, a mail address or a number of 3 digits or more (an account, a phone, a code): never by chance."""
    return any(char in word for char in "./@") or sum(char.isdigit() for char in word) >= 3


def drawn_from_notes(argument: str, said: str, notes: Sequence[str], *, about: str = "") -> bool:
    """Whether a tool's argument (a fact to remember, what to forget or cancel, a device) takes its words from code's
    notes that quote outside text (`notes`) instead of the user's message (`said`): a link, mail address or long
    number of it that a note holds and the message does not, or more than half of its words so. Never when the message
    shares a word with `about` (what a number names: the fact, the reminder). A translation of the user's words
    ("onthoud dat mijn zus Maria heet": "The user's sister is Maria.") takes nothing from a note, so it passes."""
    words = _words(argument)
    if not notes or not words:
        return False
    theirs = _words(said)
    if about and _words(about) & theirs:
        return False
    noted = set().union(*(_words(text) for text in notes))
    taken = {word for word in words if word in noted and word not in theirs}
    return any(_strong(word) for word in taken) or 2 * len(taken) > len(words)


# With a note quoting outside text in her input, a profile change or a cancel needs the user's own message to ask for
# it (owner, 21-22 Oct: "refused after outside text unless in the user's own words"). The words that ask, in the 11
# languages (stems): to remember or forget something (the remember tool: "only when the user asks you to remember"),
# and to cancel, stop or delete one. A word here only lets a call through; drawn_from_notes still refuses a fact whose
# words come from a note.
PROFILE_WORDS: dict[str, str] = {
    "en": r"\bremember|\bforget|\bforgot|\bmemori[sz]e|\bkeep in mind|\bnote (?:that|this|down)|\bmake a note"
    r"|\bsave (?:that|this)|\bprofile\b|\bfacts?\b",
    "nl": r"onthoud|\bvergeet|\bvergeten\b|\bnoteer|\bbewaar|\bprofiel|\bfeit(?:en)?\b",
    "de": r"\bmerk (?:dir|es)|\bmerken\b|\bvergiss|\bvergessen\b|\bnotier|\bspeicher|\bprofil|\bfakt",
    "fr": r"souviens|rappelle-toi|\bretiens|\bretenir\b|\boubli|\bmémoris|\bnote[rz]?\b|\benregistre|\bprofil",
    "es": r"\brecuerda|acuérdate|\bolvid|\bmemoriza|\bapunta|\banota|\bguarda|\bperfil\b",
    "it": r"\bricorda|\bdimentica|\bmemorizza|\bannota|\bsegna(?:ti)?\b|\bsalva|\bprofilo\b",
    "pt": r"\blembr|\besque[cç]|\bmemoriz|\banota|\bguarda|\bsalva|\bperfil\b",
    "pl": r"zapamięt|\bpamiętaj|\bzapomn|\bzapisz|zanotuj|\bprofil",
    "ru": r"запомн|\bпомни|забудь|забыть|запиши|сохрани|профил",
    "ja": r"覚え|忘れ|メモ|記憶|保存|プロフィール",
    "ko": r"기억|잊어|잊지|메모|저장|프로필",
}
CANCEL_WORDS: dict[str, str] = {
    "en": r"\bcancel|\bdelete|\bremove|\bdrop\b|\bstop\b|\bscrap|\bnever ?mind|\bno longer\b|\bdon'?t need|\bundo\b"
    r"|\bclear\b|\bskip\b|\bcall (?:it|that) off\b",
    "nl": r"annuleer|annuleren|verwijder|\bschrap|\bstop\b|\blaat maar|\bhoeft niet|\bniet meer nodig|\bwis\b",
    "de": r"\bstornier|\blösch|\babbrech|\bentfern|\bstopp|\bstreich|\bbrauche? (?:ich )?(?:es |sie |das )?nicht",
    "fr": r"\bannul|\bsupprim|\benlève|\bretire|\barrête|\blaisse tomber|\befface",
    "es": r"\bcancel|\bborra|\belimin|\bquita|\banula|\bolvídalo|\bdéjalo\b",
    "it": r"\bannulla|\bcancella|\belimina|\btogli|\blascia perdere|\bferma",
    "pt": r"\bcancel|\bapag|\bremov|\bexclu|\btira\b|\banula|\bdeixa pra lá",
    "pl": r"\banuluj|\bodwołaj|\busuń|\bskasuj|\bzrezygn|\bnie trzeba",
    "ru": r"отмени|отменить|удали|убери|не надо|не нужно|сотри",
    "ja": r"キャンセル|取り消|削除|消して|やめ|止め|いらない",
    "ko": r"취소|삭제|지워|그만|필요 없|없애",
}
_PROFILE = re.compile("|".join(f"(?:{pattern})" for pattern in PROFILE_WORDS.values()), re.IGNORECASE)
_CANCEL = re.compile("|".join(f"(?:{pattern})" for pattern in CANCEL_WORDS.values()), re.IGNORECASE)


def asks_profile(text: str) -> bool:
    """Whether the user's message asks to remember or forget something, in any of the 11 languages."""
    return _PROFILE.search(text) is not None


def asks_to_cancel(text: str) -> bool:
    """Whether the user's message asks to cancel, stop or delete something, in any of the 11 languages."""
    return _CANCEL.search(text) is not None


def names_reminder(said: str, number: str, words: str) -> bool:
    """Whether the user's message names a reminder: its number ("cancel 4", "#4", "c4" for a check-in) or a word of its
    text ("the dentist one")."""
    digits = number.lstrip("c")
    return (bool(digits) and digits in re.findall(r"\d+", said)) or bool(words and _words(words) & _words(said))
