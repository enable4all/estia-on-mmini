"""Voice calls with her (the assistant, by the config's name) over Signal.

CallManager reads signal-cli's call events and answers only allowed callers; strangers are ignored, never rejected
(a reject would send them something). It connects to the call helper's audio socket in a private folder and runs a
laid-back conversation: listen (long thinking pauses are fine), transcribe, one turn through the app (the same tools
as text), speak sentence by sentence, and let the caller talk over her: loudness or the voice service's speech
detector stops her ([calls] interrupt, see call_audio.Barge), and the words decide what the stop was (a cough, her own
voice from a loud speaker, a listener's "okay", or the caller's turn). Audio lives only in memory; logs hold metadata
only. The app is the Host: it stores the turns, runs Gemma and sends notes.

She places a call only for a reminder the user set (`place`, from the reminders' scheduler, which sends the text
first): to an allowed account only, one call at a time, and the call starts for the app only when it is picked up
(texts are not held while it rings). It rings for signal-cli's own minute; the bridge waits 75 s for the end and sorts
it into answered, no_answer, declined, busy or call_failed. A picked-up call opens with a code-written line in the
call's language ("Good morning! It's 7:00.") and the reminder's text, then goes on like any call.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import random
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from local_assistant import prompts
from local_assistant.call_audio import FRAME_S, RATE, Barge, CallAudio, faint_noise, pcm48_to_wav16k, wav_to_pcm48
from local_assistant.config import DEFAULT_NAME
from local_assistant.language import (
    BUT,
    BUT_WORDS,
    CONTINUING,
    ENGLISH,
    FUNCTION_WORDS,
    INTERRUPTIONS,
    JAPANESE_CALLER_WORDS,
    LEAVING,
    LEAVING_NOT_ALONE,
    LEAVING_SOFT,
    NEGATIONS,
    YES_WORDS,
    acknowledgements,
    negations,
    stop_words,
)
from local_assistant.voice import Transcript, VoiceError, detector_state, sentences, spoken_text

log = logging.getLogger(__name__)

LEAD_MS = 300  # faint noise before an answer: wakes the call's audio before the first word
PAUSE_AFTER_FILLER_S = 0.35  # "Got it." … a short pause … the answer
INTERRUPTION_END_S = 1.2  # the end of what the caller says over her (shorter than a turn: a cough resumes sooner)
RESUME_BACK_S = 0.5  # after a cough she carries on a little before where she stopped
# false stops (no words, her own voice) in one clip before the next stop is taken as the caller's; acknowledgements in
# one clip before she carries on from the stop itself, no longer a little back
MAX_FALSE_BARGE = 3
CONNECT_SECONDS = 10.0
NOT_READY_NOTE_SECONDS = 600.0
PLACED_OUTCOMES = ("answered", "no_answer", "declined", "busy", "call_failed")

FILLERS = {"uh", "um", "umm", "hmm", "mm", "mhm", "ah", "oh", "huh", "eh", "er"} | {
    "äh", "ähm", "öhm", "euh", "ehm", "ehh", "eee", "yyy", "ээ", "эм", "мм", "음", "어", "えー", "えっと", "あの"
}  # fmt: skip
# one word alone over her that stops her: "stop", "wait", "but" ... in every language (and her name: name_words)
_STOP_ALONE = frozenset({"stop", "wait", "no", "hold", "sorry", "hey"} | INTERRUPTIONS)
ONE_WORD_INTERRUPTIONS = _STOP_ALONE | BUT_WORDS
# a yes word alone, in any language, stops her only when it answers her: she asked something or a device question
# waits (owner, 14 Oct: a listener's "okay" ended her answers)
ANSWER_WORDS = frozenset({"yes", "yeah", "okay", "ok"} | {word for word in YES_WORDS if " " not in word})
_WORD = re.compile(r"(?:[^\W\d_]|')+")  # letters of any script (and apostrophes): "don't", "café", "привет"
_NO_SPACES = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")  # Japanese is written without spaces
_STOP = {"stop", "wait", "hold"}
_STOP_FILL = {"on", "please", "a", "second", "sec", "minute", "moment", "ok", "okay", "just"}  # and her name


class CallSignal(Protocol):
    def call_events(self) -> AsyncIterator[dict[str, Any]]: ...

    async def accept_call(self, aci: str, call_id: int) -> dict[str, Any]: ...

    async def hangup_call(self, aci: str, call_id: int) -> None: ...

    async def start_call(self, aci: str) -> dict[str, Any]: ...


class Voice(Protocol):
    async def transcribe(self, audio: bytes, *, language: str | None = None) -> Transcript: ...

    async def speak(self, text: str, *, language: str | None = None) -> bytes: ...

    async def health(self) -> Mapping[str, object] | None: ...

    async def vad(self, chunk: bytes, *, stream: str) -> float | str: ...


@dataclass(frozen=True)
class Turn:
    """What to say after the user spoke."""

    text: str  # Gemma's words, in the call's language
    reply_id: int | None = None  # the stored answer, cut where the user talked over it
    asks_yes: bool = False  # a device waits for the user's yes: the last sentence is spoken whole
    end_call: bool = False  # hang up once this was spoken (unless the user talked over it)
    # code's own words after Gemma's (a device question or result, why no reminder was set): always said in English
    code_text: str = ""


class Host(Protocol):
    def call_started(self, aci: str, *, practice: bool = False) -> None:
        """A call starts (`practice`: a practice call, the call's own flag)."""

    def call_ended(self) -> None: ...

    async def call_turn(self, aci: str, text: str, on_tools: Callable[[], None], *, practice: bool = False) -> Turn:
        """What the user said: the answer (`practice`: a turn of a practice call, the call's own flag)."""
        ...

    def call_interrupted(self, reply_id: int, heard: str) -> None: ...

    async def call_drop_waiting(self, aci: str) -> None:
        """A device action waiting for the user's yes is dropped (they never heard the question whole, said
        stop or wait, or the answer was abandoned); a device that did switch is still reported."""

    async def call_note(self, aci: str, text: str) -> None: ...

    async def call_summary(self, aci: str, lines: list[str], minutes: int) -> None: ...

    async def practice_call_ended(self, aci: str) -> None:
        """A practice call (/babble) the user spoke in ended: the session ends and its recap goes out at once, instead
        of the call summary."""


@dataclass(frozen=True)
class CallSettings:
    socket_dir: Path
    end_of_turn_s: float = 2.3  # owner, 17 Oct: 0.5 s less than 2.8
    idle_prompt_s: float = 90.0
    idle_hangup_s: float = 600.0
    idle_repeat_s: float = 600.0  # "Take your time, I'm here." once, then at most this often (owner, 16 Oct)
    max_s: float = 3600.0
    summary: bool = True
    turn_timeout_s: float = 45.0
    thinking_after_s: float = 0.7  # a thinking line comes first when the answer takes longer than this
    fillers: bool = False  # "hmm, let me think" / "let me check" lines while she thinks (user: off, sounded unnatural)
    acknowledge: bool = True  # a short "Got it." as soon as a request of 5+ words is taken in (user: on)
    greetings: tuple[str, ...] = ()  # English: one of these, never the same twice in a row; () = hers by name
    answer_after_s: float = 5.0  # let it ring about twice before answering (user: more natural)
    ring_wait_s: float = 75.0  # a call she places: signal-cli and RingRTC ring for 60 s; then it is a failed call
    wake_silence_s: float = 60.0  # after a wake-up call's opening, this much silence ends the call with a goodbye
    # what stops her when the caller talks over her ([calls] interrupt): "speech" ("only speech", the default) or
    # "loudness" ("any sound"); call_audio.Barge's names
    barge: str = "speech"


# Whisper writes noise as words: sound labels (*sad music*, [Music]) and, on a short sound, its favourite fillers
# ("Thank you" is about a quarter of its output on noise). Some phrases are never said on a call at all.
_SOUND_LABEL = re.compile(r"[*\[(♪][^*\])♪]*[*\])♪]")
_NOISE_WORDS = frozenset({"thank you", "thank", "thanks", "you", "so", "i", "oh", "hmm", "mm", "the", "a"}) | {
    "merci", "danke", "gracias", "grazie", "obrigado", "obrigada", "bedankt", "dank je", "dank je wel", "dziękuję",
    "спасибо", "감사합니다", "ありがとう", "ありがとうございます", "ありがとうございました",
}  # fmt: skip
_NEVER_SAID = (
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subtitles by",
    "like and subscribe",
    # the same in the other languages: subtitle credits and sign-offs from Whisper's training videos
    "tv gelderland",
    "sous-titrage",
    "sous-titres réalisés",
    "продолжение следует",
    "субтитры сделал",
    "ご視聴",
    "시청해 주셔서",
    "시청해주셔서",
)
NEVER_SAID_MAX_WORDS = 6  # such a phrase is noise only when it is (nearly) all that was heard
# subtitle credits nobody says on a call: noise however long the transcript ("Sous-titres réalisés par la communauté
# d'Amara.org" has 8 words)
_CREDITS = ("amara.org", "im auftrag des zdf", "a cura di qtss", "dimatorzok")
NOISE_VOICED_S = 0.6  # a filler word on less sustained voice than this (key clicks are none) is noise
# Less voiced sound than this is not sent to speech recognition at all: just above the 10-frame start run, so a brisk
# "yes" or "no" (0.15-0.3 s of voice) still counts; is_noise_transcript sorts out the rest.
MIN_VOICED_S = 0.12


def voice_ready(health: Mapping[str, object] | None) -> bool:
    """Whether the voice service can take a call: it answers and both engines (speech in, speech out) are ready."""
    return health is not None and health.get("stt") == "ready" and health.get("tts") == "ready"


def is_noise_transcript(text: str, sustained_s: float) -> bool:
    """Whether a transcript is noise Whisper turned into words, in any language: only sound labels, a subtitle credit,
    a short transcript that is a phrase never said on a call (inside a longer sentence it is real), or a lone filler
    word on little sustained voice (`listen`'s sustained_s: typing is loud often, never for long). A short real answer
    ("yes", "stop") is never noise."""
    heard = " ".join(_SOUND_LABEL.sub(" ", text.lower()).split())
    found = _WORD.findall(heard)  # words in any language ("Привет" is words, "*music*" is none)
    if not found:
        return True
    words = " ".join(found)
    if any(credit in heard for credit in _CREDITS):
        return True
    if len(found) <= NEVER_SAID_MAX_WORDS and any(phrase in heard or phrase in words for phrase in _NEVER_SAID):
        return True
    return words in _NOISE_WORDS and sustained_s < NOISE_VOICED_S


def real_words(text: str) -> list[str]:
    """The words of a transcript, in any language, without fillers ("um", "äh") and without Whisper's sound labels
    ("*sad music*" is no word)."""
    return [word for word in _WORD.findall(_SOUND_LABEL.sub(" ", text.lower())) if word not in FILLERS]


ACK_MIN_WORDS = 5  # a shorter turn ("how are you?", "yes", "thanks") gets its answer without a "got it" (owner)


def turn_words(text: str) -> int:
    """How many words the caller said: fillers and sound labels left out; Japanese, written without spaces, counts
    about two characters a word."""
    return sum(max(1, len(word) // 2) if _NO_SPACES.search(word) else 1 for word in real_words(text))


@functools.cache
def name_words(name: str) -> frozenset[str]:
    """Her name as a transcript's words: "Mary-Jane" is "mary" and "jane"."""
    return frozenset(real_words(name))


def _form(word: str) -> str:
    """A word as compared for an acknowledgement or leave-taking: lower case, a straight apostrophe, Latin letters
    without accents ("oké" is "oke"); other scripts as written (Japanese keeps its voicing marks)."""
    word = word.lower().replace("’", "'")
    if all(ord(char) < 0x250 for char in word):
        return "".join(char for char in unicodedata.normalize("NFKD", word) if not unicodedata.combining(char))
    return unicodedata.normalize("NFC", word)


def _units(text: str, language: str) -> tuple[str, ...]:
    """A text's words as compared (_form), in order; Japanese, written without spaces, its characters."""
    words = [_form(word) for word in _WORD.findall(_SOUND_LABEL.sub(" ", text.replace("’", "'")))]
    return tuple("".join(words)) if language == "ja" else tuple(words)


def _phrases(texts: Collection[str], language: str) -> frozenset[tuple[str, ...]]:
    return frozenset(_units(text, language) for text in texts) - {()}


def _made_of(units: tuple[str, ...], phrases: Collection[tuple[str, ...]], soft: Collection[tuple[str, ...]]) -> bool:
    """Whether `units` are wholly `phrases` and `soft` ones in any order, with at least one of `phrases`."""
    reached = {0: False}  # how far they are made of them: whether one of `phrases` came before that point
    for start in range(len(units)):
        if start not in reached:
            continue
        for options, counts in ((phrases, True), (soft, False)):
            for option in options:
                end = start + len(option)
                if units[start:end] == option:
                    reached[end] = reached.get(end, False) or reached[start] or counts
    return reached.get(len(units), False)


@functools.cache
def _acknowledging(language: str) -> tuple[frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]:
    """A call language's acknowledgements and the fillers allowed beside them, as compared."""
    return _phrases(acknowledgements(language), language), _phrases(FILLERS, language)


def _asked(text: str) -> bool:
    return "?" in text or "？" in text


def is_acknowledgement(text: str, language: str) -> bool:
    """A listener's acknowledgement over her: only the call language's acknowledgement words and the hums Whisper
    writes (language.acknowledgements), fillers allowed beside them ("Okay.", "Oh, I see.", "Mm-hmm.", "Ja, klopt.",
    "はい、なるほど"). Anything more ("okay, and tomorrow?", "yes but") is none, and so is one asked back ("네?",
    "はい？", "Right?": "pardon?" or "really?"). Japanese by characters."""
    return not _asked(text) and _made_of(_units(text, language), *_acknowledging(language))


def asks(hers: Sequence[str]) -> bool:
    """Whether her sentence being said, or the one before it (`hers`), asks something: it ends with a question mark,
    or in Japanese with か, a polite question often written without one ("明日にしますか。")."""
    ends = (sentence.rstrip().rstrip("\"')]”’»」』").rstrip("。") for sentence in hers)
    return any(end.endswith(("?", "？", "か")) for end in ends)


def is_interruption(text: str, *, name: str, language: str = ENGLISH, asking: bool = False) -> bool:
    """Talking over her counts only with real words: two or more, or one clear word like "stop", "but" or her name (in
    any of the languages; Japanese, without spaces, counts its characters). A cough, a noise or "um" transcribes as
    nothing, "-" or a filler, and she carries on; so does a listener's acknowledgement alone ("okay", "yeah, right",
    "mm-hm" in the call's `language`: is_acknowledgement), unless she is `asking` (her sentence or the one before it
    asks something, or a device question waits): then it, or a yes word alone in any language, answers her. One word
    asked back ("Really?", "네?") always counts."""
    if is_acknowledgement(text, language):
        return asking
    words = real_words(text)
    units = sum(len(word) if _NO_SPACES.search(word) else 1 for word in words)
    alone = ONE_WORD_INTERRUPTIONS | name_words(name) | (ANSWER_WORDS if asking else frozenset())
    return units >= 2 or (len(words) == 1 and (words[0] in alone or _asked(text)))


# Her own voice: a loud speaker next to the caller's microphone (a laptop on Signal Desktop) plays her back, and the
# stop's transcript is her own sentence. Only what she already played counts (the sentence before and this one up to
# the stop): at least OWN_VOICE_MIN_WORDS of her content words (short function words left out), at least
# OWN_VOICE_SHARE of the transcript's content words, and OWN_VOICE_RUN words or more in a row exactly as she said
# them; a stop, yes or no word she did not say makes it the caller's. Then she carries on as after a cough. Japanese,
# written without spaces, by characters: pairs for the share and a run of OWN_VOICE_RUN_CHARS (about as many words).
# Review of 13 Oct: "the dentist at three", "turn it off" over "Should I turn the heater off?", "no, not Amsterdam" over
# "... in Amsterdam" and "wait, it is at five" are the caller's.
OWN_VOICE_MIN_WORDS = 3
OWN_VOICE_SHARE = 0.6
OWN_VOICE_RUN = 3
OWN_VOICE_RUN_CHARS = 2 * OWN_VOICE_RUN  # Japanese counts about two characters a word


def _plain_word(word: str) -> str:
    """A word as compared for her own voice: lower case, without apostrophes or accents."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", word.lower()) if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", plain).replace("'", "").replace("’", "")


_FUNCTION_PLAIN = frozenset(_plain_word(word) for word in FUNCTION_WORDS)
# words that are the caller's unless her played text has them too: stop and yes words, a negation
_CALLER_PLAIN = frozenset(_plain_word(word) for word in _STOP_ALONE | ANSWER_WORDS | NEGATIONS)


def _compared(text: str) -> list[str]:
    """A text's real words as compared for her own voice (_plain_word), in order."""
    return [_plain_word(word) for word in real_words(text.replace("’", "'"))]


def _longest_run(heard: Sequence[str], hers: Sequence[str]) -> int:
    """How many words (or characters) in a row the transcript has exactly as in her text."""
    best = 0
    previous = [0] * (len(hers) + 1)
    for unit in heard:
        current = [0] * (len(hers) + 1)
        for index, her_unit in enumerate(hers, 1):
            if unit == her_unit:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def played_part(hers: Sequence[str], fraction: float) -> tuple[str, ...]:
    """Her sentences as far as the caller can have heard them: the ones before whole, the last up to `fraction` of it
    (the stop's place in the clip), without a word cut in half (Japanese, without spaces, by characters)."""
    if not hers:
        return ()
    *before, last = hers
    end = int(len(last) * fraction)
    if end >= len(last):
        return (*before, last)
    cut = last[:end]
    if not last[end].isspace() and " " in cut:  # inside a word: that word is not said yet
        cut = cut.rsplit(" ", 1)[0]
    return (*before, cut.rstrip())


def _own_voice_japanese(text: str, played: Sequence[str]) -> bool:
    heard = "".join(real_words(text))
    hers = "".join(word for sentence in played for word in real_words(sentence))
    if any(word in heard and word not in hers for word in JAPANESE_CALLER_WORDS):
        return False
    pairs = [heard[i : i + 2] for i in range(len(heard) - 1)]
    her_pairs = {hers[i : i + 2] for i in range(len(hers) - 1)}
    if not pairs or sum(pair in her_pairs for pair in pairs) < OWN_VOICE_SHARE * len(pairs):
        return False
    return _longest_run(heard, hers) >= OWN_VOICE_RUN_CHARS


def is_own_voice(text: str, played: Sequence[str]) -> bool:
    """Whether a transcript of what stopped her is her own voice: a long part of what she already `played`, in her
    order (see OWN_VOICE_MIN_WORDS), in any of the languages. A word only the caller says ("no", "wait", "yes") that
    she did not say makes it the caller's."""
    if turn_words(text) < OWN_VOICE_MIN_WORDS:
        return False
    if _NO_SPACES.search(text):
        return _own_voice_japanese(text, played)
    heard = _compared(text)
    hers = [word for sentence in played for word in _compared(sentence)]
    said = set(hers)
    if any(word in _CALLER_PLAIN and word not in said for word in heard):
        return False
    content = [word for word in heard if word not in _FUNCTION_PLAIN]
    mine = sum(word in said for word in content)
    if mine < OWN_VOICE_MIN_WORDS or mine < OWN_VOICE_SHARE * len(content):
        return False
    return _longest_run(heard, hers) >= OWN_VOICE_RUN


def is_lone_stop(text: str, *, name: str) -> bool:
    """ "Stop", "wait", "hold on", "wait a second", "stop Estia": she goes quiet and listens; Gemma is not asked."""
    words = real_words(text)
    return (
        bool(words)
        and len(words) <= 3 + len(name_words(name))  # four words, a longer name counted as one
        and any(w in _STOP for w in words)
        and all(w in _STOP | _STOP_FILL | name_words(name) for w in words)
    )


_LAST_SENTENCE_END = re.compile(r"""[.!?…]["')\]]*(?=\s)""")


def whole_sentences(text: str) -> str:
    """An answer that was cut off at the token limit ends at its last whole sentence (she never stops mid-sentence);
    an answer that ends properly, or has no sentence end at all, stays as it is."""
    stripped = text.rstrip()
    if not stripped or stripped[-1] in ".!?…\"')]":
        return stripped
    ends = list(_LAST_SENTENCE_END.finditer(stripped))
    return stripped[: ends[-1].end()] if ends else stripped


def speakable(note: str) -> str:
    """Code's own line said aloud: a device note "Devices: heater off" becomes "Done: heater off."; others (a reminder
    line, written with its spoken reasons) stay as they are."""
    if note.startswith("Devices: "):
        return "Done: " + note.removeprefix("Devices: ").replace(" · ", ", ").rstrip(".") + "."
    return note


@functools.cache
def _goodbye(name: str) -> re.Pattern[str]:
    """A plain goodbye, maybe after "okay", "no thanks" or her name, maybe with her name after it. Never after a
    "yes" or "yeah": that may answer a waiting device question, so Gemma's turn handles it."""
    her = re.escape(" ".join(real_words(name)))
    return re.compile(
        r"^(?:(?:ok|okay|alright|all right|right|well|thanks|thank you|very much|so much|a lot|great|cool|so|perfect"
        rf"|no|nope|{her})\s+)*"
        r"(?:bye(?: bye)*|good ?bye|bye for now|(?:talk|speak) (?:to you )?(?:later|soon)|catch you later"
        r"|see you(?: later| soon| tomorrow)?|take care|good ?night|hang up)"
        rf"(?:\s+(?:{her}|then|now|for now))*$"
    )


_WRAP_UP = re.compile(
    r"\b(?:that (?:will|'ll|should|would) do(?: it)?|that'?s? (?:is )?(?:all|it|everything|enough)(?: for (?:now|today))?"
    r"|that is (?:all|it|everything)|nothing (?:else|more)|no more questions|i'?m done|we'?re done)\b"
)


def is_wrap_up(text: str) -> bool:
    """A short "that will do for now" / "that's all" / "nothing else": the user is finishing. After such a turn the
    call ends once her answer is spoken, unless the answer asks something ("I'm good" is not one: it also answers
    "how are you?"). Longer turns that only contain such words ("that's all I know, what do you think?") are not."""
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 8 or "?" in text:
        return False
    return bool(_WRAP_UP.search(" ".join(words)))


_HANG_UP = re.compile(
    r"\b(?:shall we|should we|let'?s|you can|you may|please|can you|could you|go ahead and|ok|okay)\s+hang\s+up\b"
)


def wants_hang_up(text: str) -> bool:
    """The user asks her to end the call ("shall we hang up?", "you can hang up now"): she answers, then hangs up,
    even when her answer asks something."""
    return bool(_HANG_UP.search(" ".join(re.findall(r"[a-z']+", text.lower()))))


def is_goodbye(text: str, *, name: str) -> bool:
    """A plain goodbye ("okay bye bye", "talk to you later", "goodbye Estia"): she says bye and hangs up without
    asking Gemma. Anything subtler ("that's all for today, thanks") goes to Gemma, which can end the call too."""
    return bool(_goodbye(name).match(" ".join(real_words(text))))


# a sentence's or a comma's end (Whisper writes fast speech with commas only); Japanese breaks without a space
_BREAK = re.compile(r"(?<=[.!?,;:])\s+|(?<=[、。！？])")
_SENTENCE_END = (".", "!", "?", "。", "！", "？")
# after a goodbye, talk that is more than closing: a question, or "wait", "but", "actually", "hold on", "before you go"
_MORE_AFTER = re.compile(
    r"\b(?:wait|but|actually|hold on|hang on|one more|one thing|before (?:you|i) go|quick question)\b"
)
_ASKS_FIRST = re.compile(
    r"^(?:what|what's|when|where|who|why|how|which|can|could|would|will|do|does|did|is|are|should|shall|may)\b"
)
_LEADING_SOFT = frozenset({"and", "so", "oh", "also", "ok", "okay", "well", "then", "um"})  # "And what about…"


@dataclass(frozen=True)
class _Part:
    """A part of the caller's turn with words: a sentence, or a part of one between commas, semicolons, colons or 、."""

    text: str
    sentence: int  # which sentence of the turn it is in
    quoted: bool  # it comes after a colon in its sentence: quoted speech ("tell Alex: take care")


@functools.cache
def _going_on(language: str) -> frozenset[str]:
    """The first words of a comma part that goes on from the part before it (language.CONTINUING), as compared."""
    return frozenset(_form(word) for code in (language, ENGLISH) for word in CONTINUING.get(code, ()))


def _turn_parts(text: str, language: str) -> list[_Part]:
    """A turn's parts with words, in order: sentences (split at . ! ? 。！？), each split at commas, semicolons, colons
    and 、 (Whisper writes fast speech and a hesitation with commas). A part that starts with a word that goes on from
    the part before it in its sentence ("to", "back", "naar", "zum": language.CONTINUING, the call's language and
    English) is joined to that part: "I have to go, uh, to the dentist" is one part, an errand; "Bye, for now" one
    goodbye. The parts after a colon in a sentence are quoted speech."""
    going_on = _going_on(language)
    parts: list[_Part] = []
    sentence = 0
    quoted = False
    for piece in _BREAK.split(text.strip()):
        words = real_words(piece)
        if words:
            last = parts[-1] if parts and parts[-1].sentence == sentence else None
            if last is not None and not last.text.rstrip().endswith(":") and _form(words[0]) in going_on:
                parts[-1] = _Part(f"{last.text} {piece}", sentence, last.quoted)
            else:
                parts.append(_Part(piece, sentence, quoted))
        quoted = quoted or piece.rstrip().endswith(":")
        if piece.rstrip().endswith(_SENTENCE_END):
            sentence += 1
            quoted = False
    return parts


@functools.cache
def _holding_on(language: str) -> tuple[str, ...]:
    """A call language's words that say more is coming: its "but" and its stop words that are no negation ("maar",
    "wacht"; "nee" is often only "no thanks")."""
    return (*BUT.get(language, ()), *(word for word in stop_words(language) if word not in negations(language)))


def _closing_talk(parts: Sequence[str], language: str = ENGLISH) -> bool:
    """Talk after a goodbye that only closes the call ("I'm going to check the log now", "talk later", a request such as
    "remind me at five"): no question (a question mark, or on an English call a part that starts like one after soft
    words such as "and", "so" or "oh": Whisper may leave the mark out), no "wait", "but", "actually", "hold on" or
    "before you go", and no "but" or stop word in the call's language ("maar", "wacht", でも)."""
    for part in parts:
        words = real_words(part)
        joined = " ".join(words)
        if _asked(part) or _MORE_AFTER.search(joined):
            return False
        start = next((index for index, word in enumerate(words) if word not in _LEADING_SOFT), len(words))
        if language == ENGLISH and _ASKS_FIRST.match(" ".join(words[start:])):
            return False
        if any(more in "".join(words) if _NO_SPACES.search(more) else more in words for more in _holding_on(language)):
            return False
    return True


def says_goodbye(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """A turn that says goodbye and more: other words before the goodbye ("No, that's good. Okay, thank you.
    Bye-bye."), or only closing talk after it ("ok goodbye, I'm going to check the log now"; _closing_talk, in the
    call's `language`: "Oké, bye, maar wacht even" is more). Gemma answers the rest (a reminder asked for is set), then
    the call ends once her answer is spoken, even when it asks something, unless a device question waits (owner, 14
    Oct); Gemma rarely calls end_call on a real call, so code decides. A goodbye followed by more ("Bye. Wait, what's
    the weather?", "Bye, and what about tomorrow") is none; so is one in quoted speech ("tell Alex: take care, …") or
    one a comma part goes on from ("See you, at eight": _turn_parts); a plain goodbye alone is is_goodbye's. Commas
    count as breaks too ("Yeah, bye.")."""
    parts = _turn_parts(text, language)
    return len(parts) > 1 and any(
        not part.quoted
        and is_goodbye(part.text, name=name)
        and _closing_talk([later.text for later in parts[index + 1 :]], language)
        for index, part in enumerate(parts)
    )


@dataclass(frozen=True)
class _Leaving:
    """A call's leave-taking words as compared: its language's and English."""

    phrases: frozenset[tuple[str, ...]]  # language.LEAVING
    soft: frozenset[tuple[str, ...]]  # language.LEAVING_SOFT, fillers, her name
    not_alone: frozenset[tuple[str, ...]]  # language.LEAVING_NOT_ALONE: only with a soft word beside it
    answers: frozenset[tuple[str, ...]]  # acknowledgements and negations: a part of only these answers her


@functools.cache
def _leaving(language: str, name: str) -> _Leaving:
    codes = dict.fromkeys((language, ENGLISH))
    words = [word for code in codes for word in (*acknowledgements(code), *negations(code))]
    return _Leaving(
        _phrases([phrase for code in codes for phrase in LEAVING.get(code, ())], language),
        _phrases([*(word for code in codes for word in LEAVING_SOFT.get(code, ())), *FILLERS, name], language),
        _phrases([phrase for code in codes for phrase in LEAVING_NOT_ALONE.get(code, ())], language),
        _phrases(words, language),
    )


def takes_leave(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """Leave-taking ("I have to go", "I'm hanging up", "ik moet gaan", "ich lege auf", "じゃあ、切るね"): a part of the
    turn (_turn_parts) that is one of the call language's or English phrases (language.LEAVING), with only soft words
    in it and in the parts beside it that it takes in (LEAVING_SOFT, a filler, her name: "Okay, so, I have to go now"),
    not asked ("So, I have to go?"), followed only by closing talk (_closing_talk). The call ends once her reply is
    spoken (owner, 14 Oct). None: "I have to go to the dentist", "I have to go, uh, to the dentist" (the part goes on),
    a part in quoted speech ("He said: I have to go"), one right after a part that answers her ("Yes, I have to go.",
    "No, I'm off.": only acknowledgement or negation words), "I'm off" with no soft word beside it (a day off too:
    LEAVING_NOT_ALONE), and in Japanese one with anything but soft words before it in its sentence ("電気、切るね": 切る
    is also "switch off")."""
    words = _leaving(language, name)
    parts = _turn_parts(text, language)
    units = [_units(part.text, language) for part in parts]

    def soft(index: int, sentence: int) -> bool:
        """Part `index` is one of `sentence` and has only soft words: the leave-taking beside it takes it in."""
        in_sentence = 0 <= index < len(parts) and parts[index].sentence == sentence
        return in_sentence and _made_of(units[index], words.soft, words.soft)

    for index, part in enumerate(parts):
        first = last = index
        while soft(first - 1, part.sentence):
            first -= 1
        while soft(last + 1, part.sentence):
            last += 1
        said = tuple(unit for span in units[first : last + 1] for unit in span)
        before = first - 1 if first > 0 and parts[first - 1].sentence == part.sentence else None
        if (
            not part.quoted  # quoted speech (the parts before it are not: a colon quotes the rest of its sentence)
            and not any(_asked(parts[at].text) for at in range(first, last + 1))  # "So, I have to go?" asks
            and _made_of(said, words.phrases, words.soft)
            and said not in words.not_alone
            and (before is None or not _made_of(units[before], words.answers, words.soft))
            and (language != "ja" or before is None)  # Japanese: only soft words before it in its sentence
            and _closing_talk([later.text for later in parts[last + 1 :]], language)
        ):
            return True
    return False


def _parts(text: str) -> list[str]:
    """Text to speak on a call, sentence by sentence (even a one-word answer)."""
    return sentences(spoken_text(text, min_chars=1, rest="")) or ([text.strip()] if text.strip() else [])


def ended_outcome(reason: str) -> str:
    """A call she placed that ended before it was picked up, by signal-cli's reason (its words to be confirmed live):
    no_answer (ring timeout), declined (hung up on the phone), busy, answered (picked up on another of the user's
    linked devices), else call_failed (the call helper, signal-cli or the network)."""
    key = re.sub(r"[^a-z]", "", reason.lower())
    if "busy" in key:
        return "busy"
    if "timeout" in key:
        return "no_answer"
    if "accepted" in key:
        return "answered"
    if "declined" in key or key == "remotehangup":
        return "declined"
    return "call_failed"


def socket_path(accepted: dict[str, Any], connected: dict[str, Any], folder: Path, call_id: int) -> Path:
    """The call helper's audio socket: the "unix:<path>" it reported, else its documented name (the call id as an
    unsigned number). It must lie in our private folder (ValueError otherwise)."""
    for source in (accepted, connected):
        for key in ("inputDeviceName", "outputDeviceName"):
            value = source.get(key)
            if isinstance(value, str) and value.startswith("unix:"):
                path = Path(value.removeprefix("unix:"))
                if not path.is_absolute() or path.parent.resolve() != folder.resolve():
                    raise ValueError("the call's audio socket is outside the private folder")
                return path
    return folder / f"signal-call-{call_id & 0xFFFFFFFFFFFFFFFF}.sock"


class _Ended(Exception):
    """The call ended (hung up, timed out or signal-cli restarted)."""


@dataclass
class _Dial:
    """A call she is placing: its events (the ringing one comes before signal-cli tells the call id) until it is
    picked up or ends."""

    aci: str
    call_id: int | None = None
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)


@dataclass(frozen=True)
class _Opening:
    """A placed call that was picked up: startCall's result, the CONNECTED event and her first line."""

    started: dict[str, Any]
    connected: dict[str, Any]
    line: str
    wake: bool


@dataclass
class _Call:
    aci: str
    call_id: int
    started: float
    started_wall: float
    language: str  # a whole call is in one language (/language waits until it ends)
    spoken: prompts.CallLines  # her code-written lines in that language
    practice: bool = False  # a call during language practice: in the practised language, its end ends practice
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    lines: list[str] = field(default_factory=list)
    user_turns: int = 0
    dropped: bool = False
    unfinished: bool = False  # the call ended while an answer was still being worked on
    acknowledged_last: bool = False  # the last request got a "got it": the next one gets none


class CallManager:
    def __init__(
        self,
        signal: CallSignal,
        voice: Voice,
        host: Host,
        allowed: frozenset[str],
        settings: CallSettings,
        *,
        tz: ZoneInfo | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        language: str = ENGLISH,
        name: str = DEFAULT_NAME,
        practice: Callable[[str], str | None] = lambda aci: None,
        locked: Callable[[], bool] = lambda: False,
        missed: Callable[[str, bool], None] = lambda aci, promised: None,
        reached: Callable[[str], None] = lambda aci: None,
    ) -> None:
        self._signal = signal
        self._locked = locked  # /lock: no call is answered or placed until /unlock
        # a call that rang while the voice was not ready, and whether its "not ready" note (a promise) went out: the
        # voice-ready note follows
        self._missed = missed
        self._reached = reached  # a call connected (answered, or placed and picked up): that caller heard her
        self._voice = voice
        self._host = host
        self._allowed = frozenset(aci.lower() for aci in allowed)
        self.settings = settings
        self._tz = tz
        self._clock = clock
        self._wall = wall
        self._rng = rng or random.Random()
        self.language = language  # the next call's language (/language sets it)
        self.name = name  # hers: greetings, "stop <name>", the summary's transcript
        self._practice = practice  # the language this user is practising (/babble), read once when a call starts
        self._last_line = ""
        self._last_greeting = ""  # kept apart: one greeting per call, and the next call's must differ
        self._recent_acknowledgements: list[str] = []  # the last two "got it"s, never said again next
        self._call: _Call | None = None
        self._dial: _Dial | None = None  # a call she is placing, until it is picked up or ends
        self._task: asyncio.Task[None] | None = None
        self._audio: CallAudio | None = None
        self._clips: dict[tuple[str, str], bytes] = {}  # canned lines at 48 kHz by (language, text) (no user data)
        self._last: tuple[float, int] | None = None  # the last call: wall start, minutes
        self._not_ready_at: float | None = None
        self._detector = "not ready"  # the speech detector's state at the last readiness check (detector_state)
        self.saying = ""  # the clip being spoken now
        self.said: list[str] = []  # clips finished (played whole or cut off), latest last; the newest 20 kept

    @property
    def in_call(self) -> bool:
        return self._call is not None

    @property
    def speaking(self) -> bool:
        return self._audio is not None and self._audio.speaking.is_set()

    def status_line(self, next_call: str = "", detector: str = "") -> str:
        """/status: whether she is in a call, the last one, the speech detector's state (`detector`, when speech stops
        her) and (from the reminders) the next call she will place."""
        if self._call is not None:
            line = f"Calls: in a call since {self._hhmm(self._call.started_wall)}"
        else:
            line = "Calls: ready"
            if self._last is not None:
                line += f" (last call {self._hhmm(self._last[0])}, {self._last[1]} min)"
        if detector:
            line += f" · speech detector: {detector}"
        return f"{line} · next call {next_call}" if next_call and self._call is None else line

    async def detector(self) -> str:
        """The speech detector's state for /status when speech stops her ([calls] interrupt = "only speech", the
        default): ready, loading, unavailable, old voice service or not ready (voice.detector_state); "" with any sound
        (loudness)."""
        if self.settings.barge != "speech":
            return ""
        return detector_state(await self._voice.health())

    def _hhmm(self, stamp: float) -> str:
        return datetime.fromtimestamp(stamp, self._tz).strftime("%H:%M")

    async def run_forever(self) -> None:
        folder = self.settings.socket_dir
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        for stale in folder.glob("signal-call-*.sock"):
            stale.unlink(missing_ok=True)
        async for event in self._signal.call_events():
            try:
                await self._on_event(event)
            except Exception:
                log.exception("calls: a call event failed")

    async def hangup(self) -> None:
        """Hang up a running call, or one she is placing that still rings (the bridge is stopping)."""
        call = self._call
        if call is not None and not call.ended.is_set():
            with contextlib.suppress(Exception):
                await self._signal.hangup_call(call.aci, call.call_id)
        dial = self._dial
        if dial is not None and dial.call_id is not None:
            with contextlib.suppress(Exception):
                await self._signal.hangup_call(dial.aci, dial.call_id)

    async def can_place(self, aci: str) -> str:
        """ "" when she can place a call now, else why not: locked (/lock), in_call (one call at a time) or not_ready
        (the voice)."""
        if self._locked():
            return "locked"
        if self._call is not None or self._dial is not None:
            return "in_call"
        if not await self._voice_ready():
            return "not_ready"
        return ""

    async def place(self, aci: str, *, wake: bool, time: str, text: str) -> str:
        """Call the user for a reminder they set and wait until it is picked up or ends (PLACED_OUTCOMES). Picked up,
        the call goes on in its own task like an incoming one, opening with the wake-up or reminder line ({time}) and
        the reminder's text instead of a greeting."""
        aci = aci.lower()
        if aci not in self._allowed:
            log.error("calls: refused to call an account that is not allowed")
            return "call_failed"
        why = await self.can_place(aci)
        if why:
            log.info("calls: no call placed (%s)", why)
            return "call_failed"
        dial = _Dial(aci)
        self._dial = dial
        try:
            try:
                started = await self._signal.start_call(aci)
            except Exception as exc:
                log.warning("calls: placing a call failed (%s)", type(exc).__name__)
                return "call_failed"
            call_id = started.get("callId")
            if not isinstance(call_id, int):
                log.warning("calls: startCall gave no call id")
                return "call_failed"
            dial.call_id = call_id
            log.info("calls: placing a call (%s)", "wake-up" if wake else "reminder")
            outcome, event = await self._ring(dial)
            if outcome != "answered" or event.get("state") != "CONNECTED":
                return outcome  # not picked up (or picked up on another device): nothing more here
            call = self._new_call(aci, call_id, practice_ok=False)  # her call for a reminder is never practice
            lines = call.spoken
            line = f"{(lines.wake if wake else lines.call_reminder).format(time=time)} {text}".strip()
            while not dial.events.empty():  # whatever came after CONNECTED belongs to the call now
                call.events.put_nowait(dial.events.get_nowait())
            self._call = call
            self._task = asyncio.create_task(self._run_call(call, _Opening(started, event, line, wake)), name="call")
            return "answered"
        finally:
            self._dial = None

    async def _ring(self, dial: _Dial) -> tuple[str, dict[str, Any]]:
        """Wait (ring_wait_s) until the call she placed is picked up (CONNECTED) or ends; CONNECTING only means it
        reached the phone. No end in time: hung up, a failed call."""
        deadline = self._clock() + self.settings.ring_wait_s
        while (left := deadline - self._clock()) > 0:
            try:
                event = await asyncio.wait_for(dial.events.get(), left)
            except TimeoutError:
                break
            call_id = event.get("callId")
            if isinstance(call_id, int) and call_id != dial.call_id:
                continue
            state = event.get("state")
            if state == "CONNECTED":
                return "answered", event
            if state == "ENDED":
                reason = str(event.get("reason") or "")
                outcome = ended_outcome(reason)
                log.info("calls: the call placed ended before it was picked up (%s: %s)", reason[:40], outcome)
                return outcome, event
        log.warning("calls: the call placed did not end in %.0f s; hanging up", self.settings.ring_wait_s)
        if dial.call_id is not None:
            with contextlib.suppress(Exception):
                await self._signal.hangup_call(dial.aci, dial.call_id)
        return "call_failed", {}

    async def _on_event(self, event: dict[str, Any]) -> None:
        state = event.get("state")
        call_id = event.get("callId")
        call = self._call
        if call is not None and (call_id == call.call_id or (call_id is None and state == "ENDED")):
            call.events.put_nowait(event)
            return
        dial = self._dial
        if (
            dial is not None
            and state != "RINGING_INCOMING"
            and (dial.call_id is None or call_id in (None, dial.call_id))
        ):
            dial.events.put_nowait(event)  # the ringing event comes before startCall's reply names the call id
            return
        if state != "RINGING_INCOMING" or not isinstance(call_id, int):
            return
        aci = str(event.get("uuid") or "").lower()
        if aci not in self._allowed:
            log.info("calls: a call from an account that is not allowed was ignored")
            return
        if self._locked():  # ignored, never rejected (a reject is a reply): it rings out
            log.info("calls: a call was ignored while locked (/lock)")
            return
        if call is not None or dial is not None:
            log.info("calls: a second call was ignored")
            return
        if not await self._voice_ready():
            await self._not_ready(aci)
            return
        self._call = self._new_call(aci, call_id, practice_ok=True)
        self._task = asyncio.create_task(self._run_call(self._call), name="call")

    def _new_call(self, aci: str, call_id: int, *, practice_ok: bool) -> _Call:
        """A call's language: the one practised (/babble) when practice runs for this user and the user made the call
        (`practice_ok`), else the user's own. A call she places for a reminder or a wake-up is never a practice call:
        its turns may set a reminder, and its end leaves practice running."""
        practised = self._practice(aci) if practice_ok else None
        language = practised or self.language
        lines = prompts.call_lines(language, name=self.name)
        if practised:
            log.info("calls: a practice call (%s)", practised)
        return _Call(aci, call_id, self._clock(), self._wall(), language, lines, practice=bool(practised))

    async def _rings_out(self, call: _Call) -> bool:
        """Let it ring (about twice) before answering; True when the caller hung up meanwhile."""
        deadline = self._clock() + self.settings.answer_after_s
        while (left := deadline - self._clock()) > 0:
            try:
                event = await asyncio.wait_for(call.events.get(), left)
            except TimeoutError:
                return False
            if event.get("state") == "ENDED":
                log.info("calls: the caller hung up while it rang")
                call.ended.set()
                return True
        return False

    async def _voice_ready(self) -> bool:
        health = await self._voice.health()
        self._detector = detector_state(health)  # checked just before every call: the call's barge-in uses it
        return voice_ready(health)

    def _barge(self) -> Barge:
        """The call's barge-in rules: the speech detector (one detector state per call) when the service has it ready,
        else loudness, said once when speech should have stopped her."""
        if self._detector != "ready":
            if self.settings.barge == "speech":
                log.info("calls: speech detector %s; loudness decides this call", self._detector)
            return Barge(self.settings.barge)
        return Barge(self.settings.barge, functools.partial(self._voice.vad, stream=uuid.uuid4().hex))

    async def _not_ready(self, aci: str) -> None:
        now = self._clock()
        promised = self._not_ready_at is None or now - self._not_ready_at >= NOT_READY_NOTE_SECONDS
        self._missed(aci, promised)  # every ring counts, also one whose note is held back: the voice-ready note follows
        if not promised:
            log.info("calls: a call rang while the voice service was not ready (note held back: sent minutes ago)")
            return
        self._not_ready_at = now
        log.info("calls: a call rang while the voice service was not ready (note sent)")
        await self._host.call_note(aci, prompts.CALL_NOT_READY)

    async def _run_call(self, call: _Call, opening: _Opening | None = None) -> None:
        """An incoming call (rings, then answered), or one she placed that was picked up (`opening`)."""
        self._host.call_started(call.aci, practice=call.practice)
        log.info("calls: answering" if opening is None else "calls: the call placed was picked up")
        audio: CallAudio | None = None
        tasks: list[asyncio.Task[Any]] = []
        path: Path | None = None
        try:
            if opening is None:
                if await self._rings_out(call):
                    return
                accepted = await self._signal.accept_call(call.aci, call.call_id)
                connected = await self._wait_connected(call)
            else:
                accepted, connected = opening.started, opening.connected
            self._reached(call.aci)  # connected: no "voice ready" note owed to this caller
            path = socket_path(accepted, connected, self.settings.socket_dir, call.call_id)
            reader, writer = await self._connect(path)
            audio = CallAudio(reader, writer, self._barge())
            self._audio = audio
            tasks = [asyncio.create_task(audio.pump()), asyncio.create_task(self._watch(call, audio))]
            await asyncio.wait_for(audio.started.wait(), CONNECT_SECONDS)
            await self._converse(call, audio, opening)
        except (_Ended, ConnectionError) as exc:
            if not call.ended.is_set():
                call.dropped = True
                log.warning("calls: the call's audio ended (%s)", type(exc).__name__)
        except VoiceError:
            call.dropped = True
            log.warning("calls: the voice service failed during the call")
        except Exception:
            call.dropped = True
            log.exception("calls: the call failed")
        finally:
            for task in tasks:
                task.cancel()
            if audio is not None:
                audio.barge.close()
                log.info("%s", audio.barge.summary())
                audio.close()
            if not call.ended.is_set():
                with contextlib.suppress(Exception):
                    await self._signal.hangup_call(call.aci, call.call_id)
            if path is not None:
                path.unlink(missing_ok=True)
            minutes = max(1, round((self._clock() - call.started) / 60))
            self._audio = None
            self._call = None
            self.saying = ""
            self._last = (call.started_wall, minutes)
            self._host.call_ended()
            log.info("calls: ended after %d min, %d turns", minutes, call.user_turns)
            await self._after(call, minutes)

    async def _after(self, call: _Call, minutes: int) -> None:
        with contextlib.suppress(Exception):
            if call.practice and call.user_turns:  # first: the texts typed meanwhile as practice, then the recap
                await self._host.practice_call_ended(call.aci)
            if call.dropped:
                await self._host.call_note(call.aci, prompts.CALL_DROPPED)
            if call.unfinished:  # the caller may think it was done: say it was not
                await self._host.call_note(call.aci, prompts.CALL_UNFINISHED)
            if self.settings.summary and call.user_turns >= 2 and not call.practice:  # practice: the recap instead
                await self._host.call_summary(call.aci, call.lines, minutes)

    async def _wait_connected(self, call: _Call) -> dict[str, Any]:
        async with asyncio.timeout(30):
            while True:
                event = await call.events.get()
                if event.get("state") == "CONNECTED":
                    return event
                if event.get("state") == "ENDED":
                    call.ended.set()
                    raise _Ended(str(event.get("reason")))

    async def _connect(self, path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        deadline = self._clock() + CONNECT_SECONDS
        while True:
            try:
                return await asyncio.open_unix_connection(str(path))
            except (FileNotFoundError, ConnectionRefusedError):
                if self._clock() >= deadline:
                    raise
                await asyncio.sleep(0.1)

    async def _watch(self, call: _Call, audio: CallAudio) -> None:
        while True:
            event = await call.events.get()
            if event.get("state") == "ENDED":
                reason = str(event.get("reason") or "signal-cli")
                log.info("calls: ended by %s", reason)
                if reason.startswith("tunnel") or reason == "signal_cli_restarted":
                    call.dropped = True  # the user gets the "dropped on my side" note
                call.ended.set()
                audio.stop_speaking()
                audio.close()
                return

    async def _converse(self, call: _Call, audio: CallAudio, opening: _Opening | None = None) -> None:
        s = self.settings
        lines = call.spoken
        if opening is None:
            greetings = (s.greetings or lines.greetings) if call.language == ENGLISH else lines.greetings
            self._last_greeting = greeting = self._pick(greetings, last=self._last_greeting)
            pending = await self._say_line(audio, greeting, call.language, barge=True)
        else:  # a call she placed: the reminder's line (its text is the user's: never kept with the canned clips)
            pending = await self._say_line(audio, opening.line, call.language, barge=True, keep=False)
        waking = opening is not None and opening.wake  # silence after "Good morning!" ends the call with a goodbye
        prompted = False
        idle_said_at: float | None = None  # call time of the last "Take your time, I'm here."
        question_waiting = False  # her last answer asked for a device's yes, and it was heard whole
        hearing_trouble = 0
        while not call.ended.is_set():
            left = s.max_s - audio.frames_in * FRAME_S  # call time, like the idle timers
            if left <= 0:
                await self._say_line(audio, prompts.CALL_MAX_BYE, ENGLISH)
                return
            cutting_in = pending is not None
            if pending is None:
                wait = s.idle_prompt_s if not prompted else max(1.0, s.idle_hangup_s - s.idle_prompt_s)
                if waking:
                    wait = s.wake_silence_s
                clip, info = await audio.listen(end_silence_s=s.end_of_turn_s, no_speech_s=min(wait, left))
                if not clip and waking and wait <= left:
                    log.info("calls: no answer to the wake-up line in %.0f s; goodbye", wait)
                    await self._say_line(audio, self._pick(lines.bye), call.language)
                    return
                waking = False
                if not clip:
                    next_step = "listening on" if wait > left else "goodbye" if prompted else "checking in"
                    # the line's levels (metadata): a caller too quiet to start an utterance shows up here
                    log.info(
                        "calls: no speech in %.0f s (floor %s, peak %s); %s",
                        min(wait, left),
                        info.get("floor"),
                        info.get("peak"),
                        next_step,
                    )
                    if wait > left:  # the length cap, not silence
                        continue
                    if not prompted:
                        prompted = True
                        now = audio.frames_in * FRAME_S
                        if idle_said_at is not None and now - idle_said_at < s.idle_repeat_s:
                            log.info("calls: said take-your-time %.0f s ago; listening on", now - idle_said_at)
                            continue  # said a short while ago: quiet, the goodbye after silence stays as it was
                        idle_said_at = now
                        await self._say_line(audio, lines.idle, call.language)
                        continue
                    await self._say_line(audio, prompts.CALL_IDLE_BYE, ENGLISH)
                    return
                prompted = False
                voiced_s = float(info.get("voiced_s", 99.0))
                if voiced_s < MIN_VOICED_S:  # a click or a bump: not worth asking speech recognition
                    log.info(
                        "calls: noise (voiced %.2f s, peak %s, floor %s); listening on",
                        voiced_s,
                        info.get("peak"),
                        info.get("floor"),
                    )
                    continue
                try:
                    text = (await self._voice.transcribe(pcm48_to_wav16k(clip), language=call.language)).text
                except VoiceError:
                    hearing_trouble += 1
                    if hearing_trouble >= 2:
                        raise
                    await self._say_line(audio, lines.hearing, call.language)
                    continue
                hearing_trouble = 0
                sustained_s = float(info.get("sustained_s", voiced_s))
                log.info(
                    "calls: heard %d words (speech %.1f s, voiced %.2f s, sustained %.2f s, peak %s, floor %s)",
                    len(text.split()),
                    float(info.get("speech_s", 0.0)),
                    voiced_s,
                    sustained_s,
                    info.get("peak"),
                    info.get("floor"),
                )
                if is_noise_transcript(text, sustained_s):  # typing or noise heard as "thank" or "*music*": silence
                    log.info("calls: noise heard as words; listening on")
                    continue
            else:
                text, pending = pending, None
            if not real_words(text):  # a cough, a noise, a breath: she just keeps listening (no "sorry?")
                log.info("calls: a sound without words; listening on")
                continue
            if is_lone_stop(text, name=self.name):
                await self._host.call_drop_waiting(call.aci)  # like any answer but yes, a stop drops the question
                question_waiting = False
                continue  # quiet, listening
            if is_goodbye(text, name=self.name):
                call.lines.append(f"User: {text}")
                await self._say_line(audio, self._pick(lines.bye), call.language)
                return  # hung up in _run_call
            call.lines.append(f"User: {text}")
            call.user_turns += 1
            said = f"{prompts.CUTTING_IN} {text}" if cutting_in else text
            # a "got it" only for a request: never before the answer to a device question (it comes at once, and a
            # sound must not seem a yes), never after a few words ("how are you?" needs no "got it"), never on two
            # turns in a row (owner, 16 Oct: repetitive)
            acknowledge = (
                s.acknowledge
                and not question_waiting
                and not call.acknowledged_last
                and turn_words(text) >= ACK_MIN_WORDS
            )
            call.acknowledged_last = acknowledge
            turn = await self._turn(call, audio, said, acknowledge=acknowledge)
            interruption, heard = await self._speak(call, audio, turn, waiting=turn.asks_yes)
            call.lines.append(f"{self.name}: {heard}")
            question_waiting = turn.asks_yes and interruption is None
            if interruption is not None:
                if turn.reply_id is not None:
                    self._host.call_interrupted(turn.reply_id, heard)
                if turn.asks_yes:  # cut off before the question was asked whole: no yes may act on it
                    await self._host.call_drop_waiting(call.aci)
                pending = interruption
            elif (
                turn.end_call
                or wants_hang_up(text)
                or (self._leaving(call, text) and not turn.asks_yes)  # a yes to a device must still count
                or (is_wrap_up(text) and not f"{turn.text} {turn.code_text}".rstrip().endswith("?"))
            ):
                return  # she said goodbye (or answered the user's wrap-up or goodbye) whole: hung up in _run_call

    def _leaving(self, call: _Call, text: str) -> bool:
        """The user said goodbye with more than the goodbye, or takes leave: she hangs up once her reply is spoken."""
        return says_goodbye(text, name=self.name, language=call.language) or takes_leave(
            text, name=self.name, language=call.language
        )

    async def _turn(self, call: _Call, audio: CallAudio, text: str, *, acknowledge: bool) -> Turn:
        """The app's answer. A short "Got it." first when `acknowledge` (the request was taken in). A varied
        thinking line when the answer takes a moment after that (then a short pause before the answer), a "let me
        check" line when a tool runs (each at most once). An answer that takes too long, outlives the call or is
        left by an error is cancelled (never left running beside the next turn or after the call) and any device
        question it raised is dropped; when the call ended meanwhile, the user gets a note that it was not done."""
        s = self.settings
        tools = asyncio.Event()
        task = asyncio.create_task(self._host.call_turn(call.aci, text, tools.set, practice=call.practice))
        ended = asyncio.create_task(call.ended.wait())
        thought = checked = not s.fillers  # without fillers she simply answers when the answer is ready
        line_end: float | None = None
        started = think_from = self._clock()
        lines = call.spoken
        try:
            if acknowledge:  # "Got it." while the answer is already being worked on: the request was taken in
                await self._say_line(audio, self._pick_acknowledgement(lines.acknowledge), call.language)
                line_end = think_from = self._clock()  # a thinking line only after a real wait beyond it
            while not task.done():
                now = self._clock()
                if now - started >= s.turn_timeout_s:
                    log.warning("calls: an answer took over %.0f s", s.turn_timeout_s)
                    await self._abandon(call, task)
                    return Turn(lines.lost)
                check = tools.is_set() and not checked
                think = not thought and not checked and now - think_from >= s.thinking_after_s
                if check or think:
                    checked = checked or check
                    thought = True
                    line = self._pick(lines.checking if check else lines.thinking)
                    await self._say_line(audio, line, call.language)
                    line_end = self._clock()
                    continue
                waits: set[asyncio.Future[Any]] = {task, ended}
                waiter: asyncio.Task[bool] | None = None
                if not checked and not tools.is_set():
                    waiter = asyncio.create_task(tools.wait())
                    waits.add(waiter)
                limit = started + s.turn_timeout_s - now if thought else think_from + s.thinking_after_s - now
                await asyncio.wait(waits, timeout=max(0.0, limit), return_when=asyncio.FIRST_COMPLETED)
                if waiter is not None:
                    waiter.cancel()
                if task.done():
                    break
                if call.ended.is_set():
                    raise _Ended("ended while answering")
        except BaseException:  # the call ended, the voice failed, or the call is being stopped
            if not task.done():
                call.unfinished = True
                await self._abandon(call, task)
            raise
        finally:
            ended.cancel()
        if line_end is not None:
            gap = PAUSE_AFTER_FILLER_S - (self._clock() - line_end)
            if gap > 0:
                await asyncio.sleep(gap)
        try:
            return task.result()
        except Exception:
            log.exception("calls: the answer failed")
            await self._host.call_drop_waiting(call.aci)
            return Turn(lines.lost)

    def _pick(self, lines: tuple[str, ...], *, last: str | None = None) -> str:
        """A line from `lines`, never the one said last (or never `last`, for lines with their own memory)."""
        avoid = self._last_line if last is None else last
        choices = [line for line in lines if line != avoid] or list(lines)
        picked = self._rng.choice(choices)
        if last is None:
            self._last_line = picked
        return picked

    def _pick_acknowledgement(self, lines: tuple[str, ...]) -> str:
        """A "got it" that is neither of the last two said (fewer lines: not the last one), kept across calls."""
        recent = self._recent_acknowledgements
        choices = [line for line in lines if line not in recent] or [line for line in lines if line not in recent[-1:]]
        picked = self._rng.choice(choices or list(lines))
        self._recent_acknowledgements = [*recent[-1:], picked]
        return picked

    async def _abandon(self, call: _Call, task: asyncio.Task[Turn]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        await self._host.call_drop_waiting(call.aci)

    async def _clip(self, text: str, language: str, *, keep: bool = True) -> bytes:
        clip = self._clips.get((language, text))
        if clip is None:
            clip = wav_to_pcm48(await self._voice.speak(text, language=language))
            if keep and len(self._clips) < 32:
                self._clips[(language, text)] = clip
        return clip

    async def _say_line(
        self, audio: CallAudio, text: str, language: str, *, barge: bool = False, keep: bool = True
    ) -> str | None:
        """A code-written line in `language`, spoken whole; with barge-in it returns what the caller said over it.
        `keep`: the clip may be kept for next time (canned lines only, never one with the user's words)."""
        self.saying = text
        clip = await self._clip(text, language, keep=keep)
        result = await self._say_checked(audio, faint_noise(LEAD_MS) + clip, barge, hers=(text,))
        self._done(text)
        return result[0] if result else None

    def _done(self, text: str) -> None:
        self.said = [*self.said[-19:], text]
        self.saying = ""

    async def _speak(self, call: _Call, audio: CallAudio, turn: Turn, *, waiting: bool) -> tuple[str | None, str]:
        """Gemma's answer sentence by sentence (the next one is made while one plays), then code's own words: in
        English, so a device question is always clear (on an English call they simply follow). On a call in another
        language a turn that waits for a device's yes says only code's question: Gemma's words about the device are
        left out (in Dutch it claimed a switch that was still waiting for the yes), and app.call_turn keeps only that
        question in the history too; a turn whose reminder was not set comes with no words of hers at all, only code's
        line why. `waiting`: a device question waits for the yes (turn.asks_yes): its last sentence is said whole, and
        a yes word over the ones before answers it. Returns what the caller said over it (or None) and what they heard
        of it."""
        if call.language == ENGLISH:
            spoken = [(_parts(f"{turn.text} {turn.code_text}"), ENGLISH)]
        elif turn.asks_yes and turn.code_text.strip():
            spoken = [(_parts(turn.code_text), ENGLISH)]
        else:
            spoken = [(_parts(turn.text), call.language), (_parts(turn.code_text), ENGLISH)]
        said = [(part, language) for parts_, language in spoken for part in parts_]
        parts = [part for part, _ in said]
        if not parts:
            return None, ""
        clips: asyncio.Queue[bytes | VoiceError] = asyncio.Queue(maxsize=1)

        async def produce() -> None:
            for part, language in said:
                try:
                    await clips.put(wav_to_pcm48(await self._voice.speak(part, language=language)))
                except VoiceError as exc:
                    await clips.put(exc)
                    return
                except Exception as exc:  # e.g. a body that is not a WAV: the call must not wait forever
                    await clips.put(VoiceError(f"speech failed: {type(exc).__name__}"))
                    return

        producer = asyncio.create_task(produce())
        try:
            for index, part in enumerate(parts):
                item = await self._next_clip(clips, audio)
                if isinstance(item, VoiceError):
                    raise item
                self.saying = part
                lead = faint_noise(LEAD_MS) if index == 0 else b""
                whole = waiting and index == len(parts) - 1
                hers = tuple(parts[max(0, index - 1) : index + 1])  # the sentence and the one before it
                result = await self._say_checked(
                    audio, lead + item, barge=not whole, fresh=index == 0, hers=hers, waiting=waiting
                )
                self._done(part)
                if result is not None:
                    text, fraction = result
                    cut = part[: int(len(part) * fraction)].rsplit(" ", 1)[0] if fraction < 1 else part
                    heard = " ".join([*parts[:index], cut]).strip()
                    return text, f"{heard} …".strip()
            return None, " ".join(parts)
        finally:
            producer.cancel()
            self.saying = ""

    @staticmethod
    async def _next_clip(clips: asyncio.Queue[bytes | VoiceError], audio: CallAudio) -> bytes | VoiceError:
        while True:
            try:
                return await asyncio.wait_for(clips.get(), 1.0)
            except TimeoutError:
                if audio.closed.is_set():
                    raise ConnectionError("the call's audio ended") from None

    async def _say_checked(
        self,
        audio: CallAudio,
        pcm: bytes,
        barge: bool,
        *,
        fresh: bool = True,
        hers: tuple[str, ...] = (),
        waiting: bool = False,
    ) -> tuple[str, float] | None:
        """Say `pcm` (her sentences `hers`: the one before and the one being said); when the caller talks over it,
        listen and check the words: an interruption returns (what they said, how much of the clip went out); a cough,
        a noise or her own voice (compared with what she played of `hers` up to the stop) resumes a little before the
        stop. Her own voice also raises the level a stop needs, once a call (call_audio.Barge.her_own_voice); it never
        reaches Gemma. A listener's acknowledgement alone ("okay", "mm-hm") resumes too, unless `hers` asks something
        or a device question is `waiting`: then it answers her (owner, 14 Oct). Each acknowledgement takes her further
        on: a little before the stop, never back to where she resumed (a stop that soon would replay the same audio
        for good), and after MAX_FALSE_BARGE of them in one clip from the stop itself."""
        offset = 0
        false_alarms = 0
        carried_on = 0  # acknowledgements in this clip
        resumed = False
        while True:
            said = await audio.say(pcm[offset:], barge=barge, fresh=fresh and not resumed)
            if not said.barged:
                return None
            stop = offset + 2 * int(said.played_s * RATE)
            clip, info = await audio.listen(
                preroll=said.preroll, floor=said.floor, end_silence_s=INTERRUPTION_END_S, max_s=60.0
            )
            try:
                language = self._call.language if self._call is not None else self.language
                text = (await self._voice.transcribe(pcm48_to_wav16k(clip), language=language)).text if clip else ""
            except VoiceError:
                text = ""
            noise = is_noise_transcript(text, float(info.get("sustained_s", 0.0)))  # typing heard as "*music*"
            fraction = min(1.0, stop / max(1, len(pcm)))
            own = not noise and is_own_voice(text, played_part(hers, fraction))
            if own:
                audio.barge.her_own_voice(said.level)
            asking = waiting or asks(hers)
            words = not noise and not own
            acknowledged = words and not asking and is_acknowledgement(text, language)
            caller = (
                words and not acknowledged and is_interruption(text, name=self.name, language=language, asking=asking)
            )
            resumed = True
            if acknowledged:  # words: no false stop, and never a takeover however often, but always further on
                audio.barge.acknowledgement()
                log.info("calls: an acknowledgement; carrying on")
                carried_on += 1
                back = stop - 2 * int(RESUME_BACK_S * RATE)
                offset = back if back > offset and carried_on <= MAX_FALSE_BARGE else stop
                continue
            if not caller:
                audio.barge.false_stop(said)  # no words or her own voice: the shadow that fired too would have erred
            if caller or false_alarms >= MAX_FALSE_BARGE:
                taken = "" if noise or own else text  # noise and her own words never reach Gemma
                if real_words(taken):
                    log.info("calls: taken over (%d words)", turn_words(taken))
                return taken, fraction
            false_alarms += 1
            log.info("calls: her own voice heard; carrying on" if own else "calls: a sound without words; carrying on")
            offset = max(0, stop - 2 * int(RESUME_BACK_S * RATE))
