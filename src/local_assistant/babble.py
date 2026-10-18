"""Language practice (/babble): a session in one of eight languages at A2 or B1, then a short recap.

Started and ended only by the user's typed /babble (never a voice note, a call or Gemma: no tool). A session has its
own conversation (store kind "practice"), so the tutor style and the roleplay never reach her normal history; learning,
check-ins, the look back and the history search skip it. No tools are offered during practice. Her reply is checked in
code (the tutor test's shared fixes): a reply that is empty, only "---" or only a "→" line is asked again once, then
replaced by a short question in the target language; "---" lines go; one "→" line at most, first, and none in a reply
to a voice note or a call (one that shows the wrong form next to the right one keeps only the right one). The recap is
one structured Gemma call over the user's own practice turns (an answer cut off keeps its complete points, else it is
asked once more); code keeps a point only when the user wrote it, the change is real and small, a negation is kept and
the "why" is a rule (not "it is correct" or the model thinking aloud). Nothing new is stored: the state table holds the
last setting and the session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from datetime import time as day_time
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.gemma import GemmaClient, GemmaError
from local_assistant.initiative import in_quiet_hours
from local_assistant.language import LANGUAGES, negations
from local_assistant.store import Message, Store

log = logging.getLogger(__name__)

# owner 2026-10-13, option A: the tutor test passed these (in the owner's order); Polish and Korean are listed as not yet
OFFERED = ("en", "nl", "de", "es", "fr", "pt", "ja", "it")
NOT_YET = ("ru", "pl", "ko")  # ru: purity below 95% in both real-model runs (13 Oct)
LEVELS = ("A2", "B1")  # only these were tested enough
START_LEVEL = "A2"  # every language starts here
IDLE_S = 1800  # 30 minutes without a practice message ends the session
TICK_S = 30.0  # the idle loop
MAX_TURNS = 40  # the recap reads the latest turns: at most these many ...
MAX_CHARS = 12_000  # ... and characters
MAX_POINTS = 3
BASE_TOKENS = 200  # the recap's answer: room for the frame ...
TOKENS_PER_TURN = 60  # ... and per turn read
# speech recognition can write the right form for a learner's mistake in these (tutor test: Whisper survival)
HEARD = ("nl", "fr", "ru", "ja", "ko")
LAST_KEY = "babble:last"  # the state table: the last language and level, {"code", "level"}
SESSION_KEY = "babble:session"  # the running (or held) session, "" when none

ARROW = "→"
_RULER = "---"
_TYPED_ARROW = "->"


@dataclass(frozen=True)
class PracticeTurn:
    """What Brain needs for one practice answer: the practice rules (in place of her own, for this answer only), the
    language's fallback question, and whether the reply is spoken (a voice note or a call: no "→" line)."""

    rules: str
    fallback: str
    spoken: bool
    language: str = ""  # the practised language's code (the log's language code)


# her reply: the broken-turn guard and the tidy text


def _arrow(line: str) -> str | None:
    """The words of a "→" line ("" when nothing follows the arrow), or None for any other line."""
    stripped = line.strip()
    if stripped.startswith(_TYPED_ARROW):
        stripped = ARROW + stripped[len(_TYPED_ARROW) :]
    if not stripped.startswith(ARROW):
        return None
    return stripped[len(ARROW) :].strip()


def _is_ruler(line: str) -> bool:
    return line.strip().startswith(_RULER)


def broken(text: str) -> bool:
    """Nothing left once the "---" and "→" lines are gone (an empty reply too): ask once more, then a fallback."""
    return not any(line.strip() and not _is_ruler(line) and _arrow(line) is None for line in text.splitlines())


def users_words(words: str, said: str) -> bool:
    """Whether `words` are what the user `said` (only capitals or punctuation differ): a "→" line of their own words
    is no correction, and a sentence of theirs that she repeats is no promise of hers (brain.py). Whole words;
    Japanese and Korean by characters; never when nothing was said."""
    if not said:
        return False
    if _east_asian(words):
        plain, text = loose(words).replace(" ", ""), loose(said).replace(" ", "")
        return bool(plain) and plain in text
    plain = loose(words)
    return bool(plain) and f" {plain} " in f" {loose(said)} "


# a "→" line that shows the user's wrong form next to the right one (the practice evals, 13 Oct): "abita <abitano>",
# "ho andato <sono andato>", "Levanto -> Te levantas", "gli è piaciuto → è piaciuto loro"
_MARKED_PAIR = (
    re.compile(r"(?P<wrong>[^<>]+?)\s*<(?P<right>[^<>]+)>"),
    re.compile(rf"(?P<wrong>.+?)\s*(?:{re.escape(_TYPED_ARROW)}|{ARROW})\s*(?P<right>.+)"),
)
_PAIR_MARKS = ("<", ">", _TYPED_ARROW, ARROW)


def _corrected(words: str, said: str) -> str | None:
    """A "→" line's words as shown: only the right form when the line shows the user's wrong one next to it (marked
    "wrong <right>" or "wrong -> right"; or "wrong, right" when the part before the comma is the user's words and the
    part after it is not: "la problema, il problema"), the words as they are when they show no such pair, and None
    when a marked pair cannot be read safely (its wrong part is not what the user `said`, or more than one pair)."""
    for pattern in _MARKED_PAIR:
        found = pattern.fullmatch(words)
        if found is None:
            continue
        wrong, right = found["wrong"].strip(" ,;:"), found["right"].strip()
        if not wrong or not right or any(mark in right for mark in _PAIR_MARKS):
            return None
        return right if users_words(wrong, said) else None
    if any(mark in words for mark in _PAIR_MARKS):
        return None  # a mark that is no single pair: unread
    parts = [part.strip() for part in words.split(",")]
    if len(parts) == 2 and all(parts) and users_words(parts[0], said) and not users_words(parts[1], said):
        return parts[1]
    return words


def tidy(text: str, *, spoken: bool, said: str = "") -> str:
    """Her reply as sent: "---" lines dropped; the first "→" line with words kept and moved to the top, every other one
    dropped, and none whose words are already in what the user `said`; one that shows the user's wrong form next to the
    right one keeps only the right one, or goes when that cannot be read safely (`_corrected`); `spoken` (a reply to a
    voice note or a call): no "→" line at all (it would be read aloud)."""
    arrow = ""
    kept: list[str] = []
    for line in text.splitlines():
        if _is_ruler(line):
            continue
        words = _arrow(line)
        if words is None:
            kept.append(line)
            continue
        words = _corrected(words, said) if words else None
        if words and not arrow and not users_words(words, said):
            arrow = f"{ARROW} {words}"
    if arrow and not spoken:
        kept.insert(0, arrow)
    return "\n".join(kept).strip()


# the user's words for the recap

_MARKS = (prompts.VOICE_MARK, prompts.CALL_MARK)  # the user's own words, spoken
_NOT_OWN = (prompts.AUDIO_MARK, "[photo]")  # someone else's voice; a photo


def spoken_turn(message: Message) -> bool:
    """A voice note or a call turn: the words are what speech recognition heard."""
    return message.text.startswith(_MARKS)


def practice_words(message: Message) -> str | None:
    """The user's own words in a practice message, without the voice or call mark and "(cutting in)"; None for an
    audio file, a photo, a command or her own message."""
    if message.direction != "in" or message.kind != "text" or message.text.startswith(_NOT_OWN):
        return None
    text = message.text
    for mark in _MARKS:
        if text.startswith(mark):
            text = text[len(mark) :].strip().removeprefix(prompts.CUTTING_IN)
            break
    return " ".join(text.split()) or None


# the recap's points, checked in code (tutor test: shared fix 5)


@dataclass(frozen=True)
class Point:
    said: str  # the user's words, as written
    better: str  # the smallest change that makes them right
    why: str  # one short sentence in the user's own language


# why a point is dropped: fixed words, logged as counts (never text)
DROP_REASONS = ("said_empty", "not_verbatim", "no_change", "too_big", "negation", "no_why", "why_marker")

# A "why" is dropped when it shows the model thinking aloud or says the user's form is right. Three lists, English
# always and the user's own language (code's word lists, never shown; they need a native check):
# 1. thinking aloud, only at the start of a sentence and never quoted ("Wait, actually ..."; not "'Wait' takes 'for'")
_THINKING_ENGLISH = ("wait", "actually", "hmm", "let me", "let's", "oops", "on second thought")
_THINKING: dict[str, tuple[str, ...]] = {
    "nl": ("wacht", "even kijken"),
    "de": ("warte", "lass mich"),
    "es": ("espera", "déjame"),
    "fr": ("attends", "laisse-moi"),
    "pt": ("espera", "deixe-me"),
    "it": ("aspetta", "fammi"),
    "ja": ("待って", "ちょっと待って"),
    "ru": ("подожди", "погоди"),
    "pl": ("czekaj", "chwileczkę"),
    "ko": ("잠깐", "잠깐만", "잠시만"),
}
# 2. no mistake at all, anywhere in the why, unless it quotes the correction ("no error in the verb; 'het station'")
_NO_ERROR_ENGLISH = (
    "no mistake",
    "no error",
    "not a mistake",
    "not an error",
    "nothing wrong",
    "already correct",
    "also correct",
    "grammatically correct",
    "this is correct",
    "this is right",
    "that is correct",
    "that's correct",
    "both are correct",
    "correct as it is",
)
_NO_ERROR: dict[str, tuple[str, ...]] = {
    "nl": ("geen fout", "dit is correct", "dat is correct", "dit klopt", "dat klopt", "is al correct", "ook correct"),
    "de": ("kein fehler", "das ist korrekt", "das ist richtig", "ist korrekt so", "ist schon richtig", "auch richtig"),
    "es": ("no hay error", "no es un error", "esto es correcto", "ya es correcto", "también es correcto"),
    "fr": ("pas d'erreur", "pas une erreur", "c'est correct", "déjà correct", "aussi correct"),
    "pt": ("não há erro", "não é um erro", "isto está correto", "já está correto", "também está correto"),
    "it": ("nessun errore", "non è un errore", "questo è corretto", "è già corretto", "è anche corretto"),
    "ja": ("誤りはありません", "間違いではありません", "間違いはありません", "問題ありません"),
    "ru": ("ошибки нет", "нет ошибки", "это правильно", "это верно", "тоже правильно", "уже правильно"),
    "pl": ("nie ma błędu", "to jest poprawne", "jest już poprawne", "też jest poprawne"),
    "ko": ("틀리지 않", "문제없습니다", "문제 없습니다"),
}
# 3. a form called correct: only when the why quotes the user's words, not the correction, and nothing negates it
# ("'I goed' is correct" is dropped; "'went' is right", "Правильно говорить «в Москве»", "'Gekoopt' klopt niet" stay)
_CORRECT_ENGLISH = ("is correct", "is right", "is fine", "is acceptable")
_CORRECT: dict[str, tuple[str, ...]] = {
    "nl": ("is correct", "is juist", "klopt", "is goed"),
    "de": ("ist korrekt", "ist richtig", "stimmt"),
    "es": ("es correcto", "es correcta", "está bien"),
    "fr": ("est correct", "est correcte", "est juste"),
    "pt": ("está correto", "está correta", "é correto", "está certo"),
    "it": ("è corretto", "è corretta", "è giusto", "va bene"),
    "ja": ("正しい", "問題ない"),
    "ru": ("правильно", "верно", "корректно"),
    "pl": ("jest poprawne", "jest poprawny", "jest poprawna", "jest dobrze"),
    "ko": ("맞습니다", "올바릅니다", "맞아요"),
}
_NO_SPACES = ("ja",)  # compared by characters
_WORD = re.compile(r"\w+(?:'\w+)*")
_SENTENCE_START = r"(?:^|(?<=[.!?。！？…\n])\s*)"  # a marker right here, never after a quote mark
# quoted words in a why: 'x', "x", ‘x’, “x”, «x», „x“, 「x」, 『x』 (an apostrophe inside a word is no quote)
_QUOTED = re.compile(
    r"(?<!\w)'([^']+)'(?!\w)|\"([^\"]+)\"|‘([^’]+)’|“([^”]+)”|«([^»]+)»|„([^“”]+)[“”]|「([^」]+)」|『([^』]+)』"
)


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("’", "'")


def loose(text: str) -> str:
    """Compared loosely: NFKC, casefolded, punctuation out, spaces collapsed ("Ik ben." and "ik ben" are the same)."""
    folded = _nfkc(text).casefold()
    kept = "".join(" " if unicodedata.category(char).startswith("P") else char for char in folded)
    return " ".join(kept.split())


def _units(text: str, code: str) -> list[str]:
    """What an edit is counted in: words, or characters for Japanese."""
    plain = loose(text)
    return list(plain.replace(" ", "")) if code in _NO_SPACES else plain.split()


def edit_distance(first: Sequence[str], second: Sequence[str]) -> int:
    """Levenshtein distance over units (words or characters)."""
    previous = list(range(len(second) + 1))
    for i, unit in enumerate(first, 1):
        current = [i]
        for j, other in enumerate(second, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (unit != other)))
        previous = current
    return previous[-1]


def _has_negation(text: str, code: str) -> bool:
    words = negations(code)
    folded = _nfkc(text).casefold()
    if code in _NO_SPACES:
        return any(word in folded for word in words)
    return bool(set(_WORD.findall(folded)) & set(words))


def _small(said: list[str], better: list[str], code: str) -> bool:
    """An edit the recap may show: a pure reorder (the same words, word order), or at most max(3, half the words)
    changed; Japanese, counted in characters, max(6, half of them) (its i-adjective past: いいでした → よかったです)."""
    if sorted(said) == sorted(better):
        return True
    floor = 6 if code in _NO_SPACES else 3
    return edit_distance(said, better) <= max(floor, len(said) / 2)


def _inside(part: str, text: str, code: str) -> bool:
    """`part` appears in `text`, compared loosely (whole words; Japanese by characters); an empty part never does."""
    if code in _NO_SPACES:
        part, text = loose(part).replace(" ", ""), loose(text).replace(" ", "")
        return bool(part) and part in text
    part = loose(part)
    return bool(part) and f" {part} " in f" {loose(text)} "


def _changed(said: str, better: str, code: str) -> str:
    """The part of `better` that differs from `said` (the same start and end left out); all of it when nothing new."""
    first, second = _units(said, code), _units(better, code)
    start = 0
    while start < min(len(first), len(second)) and first[start] == second[start]:
        start += 1
    end = 0
    while end < min(len(first), len(second)) - start and first[-1 - end] == second[-1 - end]:
        end += 1
    middle = second[start : len(second) - end]
    return ("" if code in _NO_SPACES else " ").join(middle or second)


def _quotes_correction(why: str, said: str, better: str, code: str) -> bool:
    """The why quotes the correction: all of `better`, or its changed words in quote marks ("use 'het' with ...")."""
    if _inside(better, why, code):
        return True
    changed = _changed(said, better, code)
    quoted = (next(group for group in match.groups() if group is not None) for match in _QUOTED.finditer(why))
    return any(_inside(changed, span, code) for span in quoted)


def _east_asian(text: str) -> bool:
    """Japanese or Korean characters: such a marker is found inside a longer run (no spaces between words)."""
    return any("぀" <= char <= "鿿" or "가" <= char <= "힯" for char in text)


def _found(markers: Sequence[str], why: str, *, start: bool = False) -> list[re.Match[str]]:
    """Where these markers are in the folded why: whole words (Japanese and Korean ones: inside any characters), at
    the start of a sentence only when `start` (then never right after a quote mark)."""
    found: list[re.Match[str]] = []
    for marker in markers:
        whole = not _east_asian(marker)
        before = _SENTENCE_START if start else (r"(?<!\w)" if whole else "")
        after = r"(?!\w)" if whole else ""
        found += re.finditer(f"{before}{re.escape(marker)}{after}", why)
    return found


def _negated(match: re.Match[str], why: str, user_language: str) -> bool:
    """A negation word among the three words before the marker or the one after it ("No está bien", "klopt niet")."""
    words = set(negations(user_language)) | set(negations("en"))
    before = _WORD.findall(why[: match.start()])[-3:]
    after = _WORD.findall(why[match.end() :])[:1]
    return any(word in words for word in (*before, *after))


def _marked(why: str, said: str, better: str, code: str, user_language: str) -> bool:
    """The why thinks aloud, or says the user's form is right (see the three lists above)."""
    folded = _nfkc(why).casefold()
    thinking = (*_THINKING_ENGLISH, *_THINKING.get(user_language, ()))
    if _found(thinking, folded, start=True):
        return True
    if _quotes_correction(why, said, better, code):
        return False
    if _found((*_NO_ERROR_ENGLISH, *_NO_ERROR.get(user_language, ())), folded):
        return True
    if not _inside(said, why, code):
        return False
    correct = _found((*_CORRECT_ENGLISH, *_CORRECT.get(user_language, ())), folded)
    return any(not _negated(match, folded, user_language) for match in correct)


def keep_point(said: str, better: str, why: str, turns: Sequence[str], code: str, user_language: str) -> str:
    """Why a recap point is dropped (one of DROP_REASONS), or "" to keep it: `said` must be the user's own words in one
    turn (whitespace aside), `better` a real change (not only punctuation or capitals) and a small one (`_small`), a
    negation in `said` kept in `better`, and `why` not empty, not the model thinking aloud and not saying the user's
    form is right (`_marked`: English and the user's language)."""
    plain_said = " ".join(_nfkc(said).split())
    if not plain_said:
        return "said_empty"
    if not any(plain_said in " ".join(_nfkc(turn).split()) for turn in turns):
        return "not_verbatim"
    if loose(better) == loose(said):
        return "no_change"
    if not _small(_units(said, code), _units(better, code), code):
        return "too_big"
    if _has_negation(said, code) and not _has_negation(better, code):
        return "negation"
    if not why.strip():
        return "no_why"
    if _marked(why, said, better, code, user_language):
        return "why_marker"
    return ""


# when the recap goes out


def due_after(now: float, tz: ZoneInfo, quiet_hours: tuple[int, int] | None) -> float:
    """Now, or the end of the quiet hours when now falls inside them (tomorrow's end when they began before midnight)."""
    local = datetime.fromtimestamp(now, tz)
    if not in_quiet_hours(local, quiet_hours):
        return now
    assert quiet_hours is not None
    start, end = quiet_hours
    day = local.date()
    if start > end and local.hour * 60 + local.minute >= start:  # past the start, before midnight
        day += timedelta(days=1)
    return datetime.combine(day, day_time(end // 60, end % 60), tzinfo=tz).timestamp()


# the recap note


def recap_header(code: str, level: str) -> str:
    return prompts.BABBLE_RECAP_HEADER.format(english=LANGUAGES[code].english, level=level)


def recap_text(code: str, level: str, points: Sequence[Point], heard: bool, *, dropped: bool = False) -> str:
    """The recap note: its header, the points (or "well done"; only "no mistakes to note" when the model named some
    and code's checks `dropped` them all), and a line when speech recognition heard the turns."""
    none = prompts.BABBLE_RECAP_NONE_KEPT if dropped else prompts.BABBLE_RECAP_NONE
    lines = [recap_header(code, level)]
    lines += [prompts.BABBLE_RECAP_POINT.format(said=p.said, better=p.better, why=p.why) for p in points] or [none]
    if heard:
        lines.append(prompts.BABBLE_RECAP_HEARD)
    return "\n".join(lines)


def recap_other(code: str, level: str, line: str) -> str:
    """A recap note with code's one line instead of points (no practice turns, or no recap this time)."""
    return f"{recap_header(code, level)}\n{line}"


def listing_lines(last: tuple[str, str] | None) -> str:
    """The offered languages ("• Nederlands, Dutch (nl)", the last one marked), then those not offered yet."""
    lines = [
        f"• {prompts.language_label(code)} ({code}){prompts.BABBLE_LAST if last and last[0] == code else ''}"
        for code in OFFERED
    ]
    lines += [prompts.BABBLE_NOT_YET_LINE.format(name=prompts.language_label(code), code=code) for code in NOT_YET]
    return "\n".join(lines)


def listing(last: tuple[str, str] | None) -> str:
    """The list (`listing_lines`) with how to start."""
    return prompts.BABBLE_LIST.format(lines=listing_lines(last))


# the recap call (a module function: the evals use it too)


_ITEMS_START = re.compile(r'"items"\s*:\s*\[')


def recap_items(content: str) -> tuple[list[dict[str, object]], bool] | None:
    """The recap answer's complete points, read one by one, and whether their list was closed; None when the answer
    starts no list of points. An answer cut off at its token limit keeps the points before the cut (the practice
    evals: the last "why" looped until the limit)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        pass
    else:
        items = data.get("items") if isinstance(data, dict) else None
        return ([item for item in items if isinstance(item, dict)], True) if isinstance(items, list) else None
    start = _ITEMS_START.search(content)
    if start is None:
        return None
    decoder = json.JSONDecoder()
    found: list[dict[str, object]] = []
    position = start.end()
    while True:
        while position < len(content) and (content[position].isspace() or content[position] == ","):
            position += 1
        if position >= len(content):
            return found, False
        if content[position] == "]":
            return found, True
        try:
            item, position = decoder.raw_decode(content, position)
        except json.JSONDecodeError:
            return found, False  # the point the answer was cut in
        if isinstance(item, dict):
            found.append(item)


async def ask_recap(
    gemma: GemmaClient, code: str, level: str, turns: Sequence[str], user_language: str
) -> list[Point] | None:
    """Gemma's recap of these practice turns: at most 3 points as she wrote them (unchecked: `checked` keeps the good
    ones). An answer cut off at its token limit keeps its complete points; one with none, or that is not the schema's
    JSON, is asked once more with code's line for short whys (`PRACTICE_RECAP_SHORT_WHY`). None when there is none
    this time (Gemma failed, or the second answer has no point either): the note then says so."""
    target = LANGUAGES[code]
    explain = LANGUAGES[user_language].english if user_language in LANGUAGES else "English"
    system = prompts.PRACTICE_RECAP_RULES.format(own=target.own, english=target.english, level=level, explain=explain)
    numbered = "\n".join(f"{number}. {turn}" for number, turn in enumerate(turns, 1))
    for again in (False, True):
        rules = f"{system}\n\n{prompts.PRACTICE_RECAP_SHORT_WHY}" if again else system
        try:
            completion = await gemma.chat(
                [
                    {"role": "system", "content": rules},
                    {"role": "user", "content": f"{prompts.PRACTICE_RECAP_INPUT}\n{numbered}"},
                ],
                max_tokens=BASE_TOKENS + TOKENS_PER_TURN * len(turns),
                reasoning_effort="none",
                temperature=0,
                json_schema=prompts.PRACTICE_RECAP_SCHEMA,
            )
        except GemmaError as exc:
            log.warning("practice: the recap failed: %s", type(exc).__name__)
            return None
        read = recap_items(completion.content)
        items, closed = read if read is not None else ([], False)
        cut = completion.finish_reason == "length"
        if read is not None and (closed or items):
            if cut and not closed:
                log.warning("practice: the recap was cut off: %d complete points kept", len(items))
            return [
                Point(str(item.get("said") or ""), str(item.get("better") or ""), str(item.get("why") or ""))
                for item in items[:MAX_POINTS]
            ]
        why = "cut off before one complete point" if cut else "not the schema's JSON"
        log.warning("practice: the recap was %s%s", why, "" if again else "; asking once more")
    return None


def checked(points: Sequence[Point], turns: Sequence[str], code: str, user_language: str) -> list[Point]:
    """The points the code checks keep (keep_point); the dropped ones are logged as counts per reason, never text."""
    kept: list[Point] = []
    dropped: Counter[str] = Counter()
    for point in points:
        reason = keep_point(point.said, point.better, point.why, turns, code, user_language)
        if reason:
            dropped[reason] += 1
        else:
            kept.append(point)
    log.info(
        "practice: recap %d points, %d kept, dropped %s",
        len(points),
        len(kept),
        ", ".join(f"{reason} {count}" for reason, count in sorted(dropped.items())) or "none",
    )
    return kept


# the session


@dataclass(frozen=True)
class Session:
    """A practice session as the state table keeps it (SESSION_KEY): running while `ended` is 0, then held until its
    recap goes out at `due`."""

    code: str
    level: str
    aci: str
    started: float
    conversation_id: int
    ended: float = 0.0
    due: float = 0.0
    reason: str = ""  # why it ended: off, idle, call, switch ("" while running)


def _session(raw: str | None) -> Session | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        session = Session(
            code=str(data["code"]),
            level=str(data["level"]),
            aci=str(data["aci"]),
            started=float(data["started"]),
            conversation_id=int(data["conversation"]),
            ended=float(data["ended"]),
            due=float(data["due"]),
            reason=str(data.get("reason") or ""),
        )
    except (ValueError, TypeError, KeyError):
        log.warning("practice: the session in the state table is unreadable; none is running")
        return None
    return session if session.code in OFFERED and session.level in LEVELS else None


def _saved(session: Session) -> str:
    data = asdict(session)
    data["conversation"] = data.pop("conversation_id")
    return json.dumps(data)


class Babble:
    """The practice session: started and ended by /babble (commands.py), its turns answered by Brain with `turn()`'s
    rules (app.py), its recap written by Gemma and checked here. One session at a time, for the user who started it.
    It ends with /babble off, a practice call's end, or 30 minutes without a practice message; that recap waits for the
    end of quiet hours. /quiet and /initiative never hold it (the user started it). /lock does: locked means no answers
    at all, so the idle loop does nothing and a recap that falls due meanwhile waits until /unlock."""

    def __init__(
        self,
        store: Store,
        gemma: GemmaClient,
        tz: ZoneInfo,
        *,
        send: Callable[[int], Awaitable[None]],
        user_language: Callable[[], str],
        quiet_hours: tuple[int, int] | None,
        rules: Callable[[str, str], str],
        on_change: Callable[[], None] = lambda: None,
        in_call: Callable[[], bool] = lambda: False,
        busy: Callable[[], bool] = lambda: False,
        locked: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._gemma = gemma
        self._tz = tz
        self._send = send
        self._user_language = user_language
        self._quiet_hours = quiet_hours
        self._rules = rules
        self._on_change = on_change
        self._in_call = in_call
        self._busy = busy
        self._locked = locked  # /lock: no recap is written or sent until /unlock
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()  # the idle loop and /babble off never both send a recap

    def session(self) -> Session | None:
        return _session(self._store.get_state(SESSION_KEY))

    def running(self, aci: str | None = None) -> Session | None:
        """The running session (of this user, when `aci` is given: other allowed users stay in chat)."""
        session = self.session()
        if session is None or session.ended:
            return None
        return session if aci is None or session.aci.lower() == aci.lower() else None

    def held(self) -> Session | None:
        """An ended session whose recap has not gone out yet."""
        session = self.session()
        return session if session is not None and session.ended else None

    def last(self) -> tuple[str, str] | None:
        """The last language and level practised (a plain /babble resumes them)."""
        try:
            data = json.loads(self._store.get_state(LAST_KEY) or "")
            code, level = str(data["code"]), str(data["level"])
        except (ValueError, TypeError, KeyError):
            return None
        return (code, level) if code in OFFERED and level in LEVELS else None

    def start(self, aci: str, code: str, level: str) -> Session:
        """A new session in its own practice conversation; the chat conversation stays as it is."""
        conversation = self._store.start_practice_conversation()
        session = Session(code, level, aci, self._clock(), conversation.id)
        with self._store.tx():
            self._store.set_state(LAST_KEY, json.dumps({"code": code, "level": level}))
            self._store.set_state(SESSION_KEY, _saved(session))
        log.info("practice: started %s %s (conversation %d)", code, level, conversation.id)
        self._on_change()
        return session

    def turn(self, *, spoken: bool) -> PracticeTurn:
        """What Brain needs for a practice answer: the practice rules, the fallback question, spoken or typed."""
        session = self.running()
        if session is None:
            raise RuntimeError("no practice session is running")
        return PracticeTurn(
            self._rules(session.code, session.level), prompts.PRACTICE_FALLBACK[session.code], spoken, session.code
        )

    def end(self, reason: str) -> Session | None:
        """End the running session now (reason: off, idle, call, switch); its recap is due at once, or after quiet hours
        for an idle end. Returns the ended session (None when none was running)."""
        session = self.running()
        if session is None:
            return None
        now = self._clock()
        due = due_after(now, self._tz, self._quiet_hours) if reason == "idle" else now
        ended = replace(session, ended=now, due=due, reason=reason)
        self._store.set_state(SESSION_KEY, _saved(ended))
        log.info("practice: ended (%s), recap due in %.0f s", reason, due - now)
        self._on_change()
        return ended

    async def finish(self, session: Session) -> None:
        """Send the recap of an ended session, once: Gemma's points checked by code, as a note in the practice
        conversation, stored in one transaction with the session cleared and its conversation ended. It is never
        written twice; a failed send is a failed note, resent only by the recovery at the next start, and only within
        its window (recover_window_seconds), like any failed note. While locked (/lock) nothing is written or sent: the
        session stays held and the idle loop sends its recap after /unlock (also when the lock came while Gemma wrote
        it)."""
        if self._locked():
            log.info("practice: locked; the recap waits for /unlock")
            return
        async with self._lock:
            current = self.session()
            if current is None or current.conversation_id != session.conversation_id:
                return  # already sent
            messages = [m for m in self._store.user_texts(session.conversation_id) if practice_words(m) is not None]
            latest: list[Message] = []
            size = 0
            for message in reversed(messages):
                words = practice_words(message) or ""
                if len(latest) >= MAX_TURNS or size + len(words) > MAX_CHARS:
                    break
                latest.insert(0, message)
                size += len(words)
            turns = [practice_words(message) or "" for message in latest]
            if not turns:
                note = recap_other(session.code, session.level, prompts.BABBLE_RECAP_NOTHING)
            else:
                language = self._user_language()
                points = await ask_recap(self._gemma, session.code, session.level, turns, language)
                if points is None:
                    note = recap_other(session.code, session.level, prompts.BABBLE_RECAP_FAILED)
                else:
                    heard = session.code in HEARD and any(spoken_turn(message) for message in latest)
                    kept = checked(points, turns, session.code, language)
                    note = recap_text(session.code, session.level, kept, heard, dropped=bool(points))
            if session.reason == "idle":  # the user may not know it ended
                note = f"{note}\n{prompts.BABBLE_RECAP_IDLE}"
            if self._locked():  # locked while Gemma wrote it: kept held, written again after /unlock
                log.info("practice: locked during the recap; it waits for /unlock")
                return
            with self._store.tx():
                out_id = self._store.add_outgoing(session.conversation_id, session.aci, "note", note, None)
                self._store.set_state(SESSION_KEY, "")
                self._store.end_conversation(session.conversation_id)
            log.info("practice: recap stored as message %d (%d turns read)", out_id, len(turns))
            try:
                await self._send(out_id)
            except Exception:  # never sent twice: the note is failed, recovery resends it
                log.exception("practice: the recap could not be sent")
                self._store.set_status(out_id, "failed")
        self._on_change()

    async def tick(self) -> None:
        """Nothing during a call, an answer or a lock (/lock); a running session without a practice message for 30
        minutes ends; an ended session's recap goes out once it is due."""
        if self._in_call() or self._busy() or self._locked():
            return
        now = self._clock()
        session = self.running()
        if session is not None:
            newest = self._store.last_user_ts(session.conversation_id)
            last = newest / 1000 if newest is not None else session.started
            if now - last >= IDLE_S:
                self.end("idle")
        held = self.held()
        if held is not None and held.due <= now:
            await self.finish(held)

    async def run_forever(self) -> None:
        while True:
            await self._sleep(TICK_S)
            try:
                await self.tick()
            except Exception:  # a TaskGroup task that raises would take the bridge down
                log.exception("practice: a tick failed")
