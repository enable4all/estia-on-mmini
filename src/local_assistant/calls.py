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
from local_assistant.call_audio import (
    EARLY_BARGE_S,
    EARLY_KINDS,
    FRAME_S,
    LISTEN_FLOOR_FACTOR,
    LISTEN_MIN_RMS,
    RATE,
    Barge,
    CallAudio,
    TurnEnd,
    faint_noise,
    pcm48_to_wav16k,
    wav_to_pcm48,
)
from local_assistant.config import DEFAULT_NAME
from local_assistant.language import (
    AND,
    BUT,
    BUT_WORDS,
    CONTINUING,
    ENDING,
    ENDING_SOFT,
    ENGLISH,
    FAREWELLS,
    FUNCTION_WORDS,
    GOODBYES,
    GOODBYES_UNTIL,
    INTERRUPTIONS,
    JAPANESE_CALLER_WORDS,
    LEAVING,
    LEAVING_NOT_ALONE,
    LEAVING_SOFT,
    LEAVING_UNCLEAR,
    MORE_COMING,
    NEGATIONS,
    THANKS,
    YES_WORDS,
    acknowledgements,
    is_yes,
    made_of,
    negations,
    stop_words,
)
from local_assistant.voice import (
    Transcript,
    VoiceError,
    detector_state,
    sentences,
    spoken_text,
    without_stage_directions,
)
from local_assistant.wake import says_name

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

    async def turn(self, window: bytes) -> float | str: ...


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

    async def call_warm_up(self, aci: str, *, practice: bool = False) -> None:
        """While the call rings, or once a call she placed is picked up: the model loads and reads the first turn's
        prompt up to the caller's words, so that turn finds it ready (`practice`: the call's own flag). Run once a call
        in the background: the call never waits for it, and its end leaves it to finish."""

    async def call_turn(
        self,
        aci: str,
        text: str,
        on_tools: Callable[[], None],
        *,
        practice: bool = False,
        on_retry: Callable[[], None] | None = None,
    ) -> Turn:
        """What the user said: the answer (`practice`: a turn of a practice call, the call's own flag). `on_retry` is
        called once when the model's first try came back empty, before it is asked again."""
        ...

    def call_interrupted(self, reply_id: int, heard: str) -> None: ...

    async def call_drop_waiting(self, aci: str) -> None:
        """A device action waiting for the user's yes is dropped (they never heard the question whole, said
        stop or wait, or the answer was abandoned); a device that did switch is still reported."""

    async def call_note(self, aci: str, text: str) -> None: ...

    async def call_summary(self, aci: str, lines: list[str], minutes: int) -> None: ...

    async def call_chords(self, aci: str) -> None:
        """[chords]: after the call, one message from code with the exact chords the call worked out and their
        source (sent only when a transpose ran); before the summary."""

    async def practice_call_ended(self, aci: str) -> None:
        """A practice call (/babble) the user spoke in ended: the session ends and its recap goes out at once, instead
        of the call summary."""


@dataclass(frozen=True)
class CallSettings:
    socket_dir: Path
    end_of_turn_s: float = 2.3  # owner, 17 Oct: 0.5 s less than 2.8
    idle_prompt_s: float = 90.0  # silence before the check-in and standby (owner, 18 Oct)
    # the check-in once, then at most this often (owner, 16 Oct); in standby "I'm still here." this often (18 Oct)
    idle_repeat_s: float = 600.0
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
    # after her plain goodbye, this much quiet ends the call; anything the caller says meanwhile keeps it (owner, 18 Oct:
    # "between me and gemma, we will be more succesful")
    goodbye_grace_s: float = 3.0
    # what stops her when the caller talks over her ([calls] interrupt): "speech" ("only speech", the default) or
    # "loudness" ("any sound"); call_audio.Barge's names
    barge: str = "speech"
    # the early end of a turn (owner, 17 Oct; call_audio.TurnEnd): Smart Turn asked once a pause at early_end_pause_s
    # ([calls] early_end, early_end_pause_seconds), and what keeps a turn open ([calls] pause): "speech" ("no speech",
    # the default: noise in a pause counts as pause) or "loudness" ("quiet": any sound)
    early_end: bool = True
    early_end_pause_s: float = 0.6
    pause: str = "speech"


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
    return not _asked(text) and made_of(_units(text, language), *_acknowledging(language))


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


def is_goodbye(text: str, *, name: str) -> bool:
    """A plain goodbye ("okay bye bye", "talk to you later", "goodbye Estia"): she says bye and hangs up without
    asking Gemma. Anything subtler ("that's all for today, thanks") goes to Gemma, which can end the call too."""
    return bool(_goodbye(name).match(" ".join(real_words(text))))


# a sentence's or a comma's end (Whisper writes fast speech with commas only); Japanese breaks without a space
_BREAK = re.compile(r"(?<=[.!?,;:])\s+|(?<=[、。！？])")
_SENTENCE_END = (".", "!", "?", "。", "！", "？")
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
def _holding_on(language: str) -> tuple[frozenset[tuple[str, ...]], tuple[str, ...]]:
    """A call language's words that say more is coming, as compared: its and English's language.MORE_COMING ("wait",
    "one more thing", "noch eine Frage"), its "but" and its stop words that are no negation ("maar", "wacht"; "nee" is
    often only "no thanks"); and the Korean ones, matched inside words ("잠깐만요")."""
    words = [
        *(more for code in dict.fromkeys((language, ENGLISH)) for more in MORE_COMING.get(code, ())),
        *BUT.get(language, ()),
        *(word for word in stop_words(language) if word not in negations(language)),
    ]
    inside = tuple(" ".join(_units(word, language)) for word in words) if language == "ko" else ()
    return _phrases(words, language), inside


def _says_more(part: str, language: str) -> bool:
    """Whether a part of the turn holds a word that says more is coming (_holding_on)."""
    phrases, inside = _holding_on(language)
    units = _units(part, language)
    if any(units[at : at + len(more)] == more for more in phrases for at in range(len(units) - len(more) + 1)):
        return True
    joined = " ".join(units)
    return any(more in joined for more in inside)


def _closing_talk(parts: Sequence[str], language: str = ENGLISH) -> bool:
    """Talk after a goodbye that only closes the call ("I'm going to check the log now", "talk later", a request such as
    "remind me at five"): no question (a question mark, or on an English call a part that starts like one after soft
    words such as "and", "so" or "oh": Whisper may leave the mark out), and no word that says more is coming in the
    call's language or English (language.MORE_COMING: "wait", "actually", "one more thing", "before you go", "Moment",
    "nog één ding", "una cosa más"; a "but" or a stop word of the call's language: "maar", "wacht", でも)."""
    for part in parts:
        words = real_words(part)
        if _asked(part) or _says_more(part, language):
            return False
        start = next((index for index, word in enumerate(words) if word not in _LEADING_SOFT), len(words))
        if language == ENGLISH and _ASKS_FIRST.match(" ".join(words[start:])):
            return False
    return True


def says_goodbye(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """A turn that says goodbye and more: other words before the goodbye ("No, that's good. Okay, thank you.
    Bye-bye."), or only closing talk after it ("ok goodbye, I'm going to check the log now"; _closing_talk, in the
    call's `language`: "Oké, bye, maar wacht even" is more). Gemma answers the rest (a reminder asked for is set), then
    the call ends once her answer is spoken, even when it asks something, unless a device question waits (owner, 14
    Oct); Gemma rarely calls end_call on a real call, so code decides. A goodbye followed by more ("Bye. Wait, what's
    the weather?", "Bye, and what about tomorrow") is none; so is one in quoted speech ("tell Alex: take care, …") or
    one a comma part goes on from ("See you, at eight": _turn_parts), and one asked and then turned down ("Hang up? No,
    tell me more": a later part starts with a negation); a plain goodbye alone is is_goodbye's. Commas count as breaks
    too ("Yeah, bye.")."""
    parts = _turn_parts(text, language)
    words = _ending(language, name)

    def turned_down(index: int) -> bool:
        """An asked goodbye that a later part turns down ("Hang up? No, tell me more": wants_hang_up's rule)."""
        later = (_units(part.text, language) for part in parts[index + 1 :])
        return _asked(parts[index].text) and any(_starts_with(units, words.turned_down, words.soft) for units in later)

    return len(parts) > 1 and any(
        not part.quoted
        and is_goodbye(part.text, name=name)
        and _closing_talk([later.text for later in parts[index + 1 :]], language)
        and not turned_down(index)
        for index, part in enumerate(parts)
    )


@dataclass(frozen=True)
class _Leaving:
    """A call's leave-taking words as compared, or its words that ask her to end the call: its language's and
    English."""

    phrases: frozenset[tuple[str, ...]]  # language.LEAVING (or language.ENDING)
    soft: frozenset[tuple[str, ...]]  # language.LEAVING_SOFT (and ENDING_SOFT), fillers, her name
    not_alone: frozenset[tuple[str, ...]]  # language.LEAVING_NOT_ALONE: only with a soft word beside it
    answers: frozenset[tuple[str, ...]]  # acknowledgements and negations: a part of only these answers her
    may_ask: bool = False  # it counts asked too ("Shall we stop here?")
    # asked, a later part that starts with one of these turns it down ("Hang up? No, tell me more"): negations
    turned_down: frozenset[tuple[str, ...]] = frozenset()
    # the ones of `phrases` that count only with nothing before them in the turn but `opening` words
    # (language.LEAVING_UNCLEAR: "Sorry I didn't call earlier. Something came up." is an apology)
    unclear: frozenset[tuple[str, ...]] = frozenset()
    opening: frozenset[tuple[str, ...]] = frozenset()  # the soft words and thanks (language.THANKS)


@functools.cache
def _leaving(language: str, name: str) -> _Leaving:
    codes = dict.fromkeys((language, ENGLISH))
    words = [word for code in codes for word in (*acknowledgements(code), *negations(code))]
    soft = [*(word for code in codes for word in LEAVING_SOFT.get(code, ())), *FILLERS, name]
    unclear = _phrases([phrase for code in codes for phrase in LEAVING_UNCLEAR.get(code, ())], language)
    return _Leaving(
        _phrases([phrase for code in codes for phrase in LEAVING.get(code, ())], language) | unclear,
        _phrases(soft, language),
        _phrases([phrase for code in codes for phrase in LEAVING_NOT_ALONE.get(code, ())], language),
        _phrases(words, language),
        unclear=unclear,
        opening=_phrases([*soft, *(word for code in codes for word in THANKS.get(code, ()))], language),
    )


_TURNED_DOWN = ("nah",)  # a "no" that is no negation word elsewhere (language.negations has "no", "nope", "not")


@functools.cache
def _ending(language: str, name: str) -> _Leaving:
    """A call's requests to end it as compared (language.ENDING) and the soft words beside them, leave-taking's and
    ENDING_SOFT's; no part before one answers her ("Yes, let's end the call" ends it too), and one may be asked, unless
    a later part turns it down: one that starts with a negation, the call language's or English ("Hang up? No, tell
    me more", "On raccroche ? Non, continue.")."""
    codes = dict.fromkeys((language, ENGLISH))
    soft = [word for code in codes for word in (*LEAVING_SOFT.get(code, ()), *ENDING_SOFT.get(code, ()))]
    return _Leaving(
        _phrases([phrase for code in codes for phrase in ENDING.get(code, ())], language),
        _phrases([*soft, *FILLERS, name], language),
        frozenset(),
        frozenset(),
        may_ask=True,
        turned_down=_phrases([*(word for code in codes for word in negations(code)), *_TURNED_DOWN], language),
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
    return _says(text, language, _leaving(language, name))


def wants_hang_up(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """The caller asks her to end the call ("shall we hang up?", "you can hang up now", "let's end the call", "we can
    stop here", "let's call it a day", "laten we ophangen", "lass uns Schluss machen"): a part of the turn that is one
    of the call language's or English requests (language.ENDING), with only soft words beside it (LEAVING_SOFT,
    ENDING_SOFT: "let's", "can we", "please"; a filler, her name), followed only by closing talk; asked too ("Shall we
    stop here?"), never in quoted speech, and in Japanese with only soft words before it in its sentence. She answers,
    then hangs up, even when her answer asks something (owner, 18 Oct: a request to end the call kept a call open). None:
    "Let's stop here, but what about the heater?", "Let's end the call after the weather", "Don't end the call yet",
    and "Stop here." or "Can you wrap it up?" (her reading, her answer: "stop here" and "wrap up" count only with
    "let's" or "we can")."""
    return _says(text, language, _ending(language, name))


def _says(text: str, language: str, words: _Leaving) -> bool:
    """Whether a part of the turn is one of `words`' phrases with only their soft words in it and in the parts beside
    it (takes_leave's and wants_hang_up's rules)."""
    parts = _turn_parts(text, language)
    units = [_units(part.text, language) for part in parts]
    clear = words.phrases - words.unclear

    def soft(index: int, sentence: int) -> bool:
        """Part `index` is one of `sentence` and has only soft words: the leave-taking beside it takes it in."""
        in_sentence = 0 <= index < len(parts) and parts[index].sentence == sentence
        return in_sentence and made_of(units[index], words.soft, words.soft)

    for index, part in enumerate(parts):
        first = last = index
        while soft(first - 1, part.sentence):
            first -= 1
        while soft(last + 1, part.sentence):
            last += 1
        said = tuple(unit for span in units[first : last + 1] for unit in span)
        before = first - 1 if first > 0 and parts[first - 1].sentence == part.sentence else None
        asked = any(_asked(parts[at].text) for at in range(first, last + 1))
        if (
            not part.quoted  # quoted speech (the parts before it are not: a colon quotes the rest of its sentence)
            and (words.may_ask or not asked)  # "So, I have to go?" asks; "Shall we stop here?" asks to stop
            and made_of(said, words.phrases, words.soft)
            and (
                made_of(said, clear, words.soft)
                or all(made_of(units[at], words.opening, words.opening) for at in range(first))
            )
            and said not in words.not_alone
            and (before is None or not made_of(units[before], words.answers, words.soft))
            and (language != "ja" or before is None)  # Japanese: only soft words before it in its sentence
            and _closing_talk([later.text for later in parts[last + 1 :]], language)
            and not (
                asked
                and any(_starts_with(units[at], words.turned_down, words.soft) for at in range(last + 1, len(parts)))
            )
        ):
            return True
    return False


def _starts_with(
    units: tuple[str, ...], phrases: Collection[tuple[str, ...]], soft: Collection[tuple[str, ...]]
) -> bool:
    """Whether `units` start with one of `phrases`, maybe after `soft` ones ("Please no": a no)."""
    reached = {0}
    for start in range(len(units)):
        if start not in reached:
            continue
        if any(units[start : start + len(option)] == option for option in phrases):
            return True
        reached.update(start + len(option) for option in soft if units[start : start + len(option)] == option)
    return False


@functools.cache
def _her_goodbyes(language: str, name: str) -> tuple[frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]:
    """Her plain goodbyes as compared (language.GOODBYES), and the soft words beside them, the call language's and
    English's."""
    codes = dict.fromkeys((language, ENGLISH))
    return (
        _phrases([bye for code in codes for bye in GOODBYES.get(code, ())], language),
        _phrases([*(word for code in codes for word in LEAVING_SOFT.get(code, ())), *FILLERS, name], language),
    )


@functools.cache
def _closing(language: str, name: str) -> tuple[frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]:
    """A caller's closing words as compared: acknowledgements, thanks (language.THANKS) and plain goodbyes
    (language.GOODBYES), the call language's and English; and the soft words beside them."""
    codes = dict.fromkeys((language, ENGLISH))
    words = [
        word for code in codes for word in (*acknowledgements(code), *THANKS.get(code, ()), *GOODBYES.get(code, ()))
    ]
    return _phrases(words, language), _leaving(language, name).soft


def answers_question(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """The caller's reply only answers her yes-or-no question: a yes (language.is_yes: "yes please", "ja graag") or
    only acknowledgements, negations and thanks, with soft words ("No, thanks.", "Okay.", "Nee hoor.")."""
    if is_yes(text, language):
        return True
    parts = [_units(part.text, language) for part in _turn_parts(text, language)]
    closing, soft = _closing(language, name)
    words = closing | _leaving(language, name).answers
    return bool(parts) and all(made_of(units, words, soft) for units in parts)


def ends_with_goodbye(text: str, *, name: str, language: str = ENGLISH) -> bool:
    """Her answer ends with a plain goodbye and asks nothing ("Good luck with it! Bye!", "Enjoy, talk soon!", "Prima,
    tot later!"): its last part (_turn_parts) is one of the call language's or English goodbyes (language.GOODBYES)
    with only soft words in it, or a name after such a part in its sentence: one or two capitalised words ("Bye,
    Alex!", "See you soon, Alex Example!"), one word in a script without capitals; never in quoted speech ("Tell Alex:
    bye."), never in an answer that asks (a question mark, or Japanese's か: "他に何かありますか。またね。"). Only plain
    goodbyes: "Have a great day!" is none, and so is a word that is also "hello" ("Ciao!", "Dag!", "Salut !", "Boa
    noite"); one that is also "until <a time>" (language.GOODBYES_UNTIL) only with nothing but soft words,
    acknowledgements or thanks before it in its sentence ("Es bleibt bewölkt, bis morgen." is the weather; review 18
    Oct: "Bye, though." and "Take care, okay." are none either)."""
    if _asked(text) or asks(re.split(r"(?<=[。！？.!?])\s*", text)):
        return False
    parts = _turn_parts(text, language)
    phrases, soft = _her_goodbyes(language, name)
    until = _phrases(
        [bye for code in dict.fromkeys((language, ENGLISH)) for bye in GOODBYES_UNTIL.get(code, ())], language
    )
    closing, _ = _closing(language, name)

    def bye(index: int) -> bool:
        part = parts[index]
        units = _units(part.text, language)
        if part.quoted or not made_of(units, phrases, soft):
            return False
        if made_of(units, phrases - until, soft):
            return True
        same = [_units(earlier.text, language) for earlier in parts[:index] if earlier.sentence == part.sentence]
        return all(made_of(earlier, closing | soft, closing | soft) for earlier in same)

    if not parts:
        return False
    *before, last = parts
    return (
        bye(len(parts) - 1)
        or (bool(before) and before[-1].sentence == last.sentence and _a_name(last.text, soft) and bye(len(parts) - 2))
        or _farewell(text, language)
    )


_FAREWELL_SENTENCES = re.compile(r"(?<=[.!?…。！？])\s*")
_FAREWELL_PARTS = re.compile(r"\s*(?:[,;:—–]|\s-\s)\s*")


def _farewell(text: str, language: str) -> bool:
    """Her answer's last sentence is a closing wish or an offer to wait ("Enjoy your evening!", "Sleep well, and I'll
    catch you in the morning!", "I'll be here whenever you're ready."): every part of it starts like one of the call
    language's or English's (language.FAREWELLS), a leading "and" left out, within that pattern's words (Japanese,
    without spaces, counts one)."""
    sentences = [sentence for sentence in _FAREWELL_SENTENCES.split(text.strip()) if real_words(sentence)]
    if not sentences:
        return False
    patterns = [
        (re.compile(pattern), most)
        for code in dict.fromkeys((language, ENGLISH))
        for pattern, most in FAREWELLS.get(code, ())
    ]
    joins = {AND[code] for code in dict.fromkeys((language, ENGLISH)) if code in AND}
    parts = [part for part in _FAREWELL_PARTS.split(sentences[-1].rstrip(".!?…。！？ ").casefold()) if part.strip()]

    def closing(part: str) -> bool:
        words = part.replace("’", "'").split()
        if words and words[0] in joins:
            words = words[1:]
        said = " ".join(words)
        return bool(said) and any(match.match(said) and len(words) <= most for match, most in patterns)

    return bool(parts) and all(closing(part) for part in parts)


def _a_name(text: str, soft: Collection[tuple[str, ...]]) -> bool:
    """Whether a part is a name: one or two capitalised words that are no soft word ("Alex", "Alex Example"; never
    "though" or "Okay"), or one word in a script without capitals."""
    words = _WORD.findall(_SOUND_LABEL.sub(" ", text))
    if not words or len(words) > 2 or any((_form(word),) in soft for word in words):
        return False
    return all(word[0].isupper() for word in words) or (len(words) == 1 and not _cased_word(words[0]))


def _cased_word(word: str) -> bool:
    return word.lower() != word.upper()


# An early end's words that end like more is coming (owner, 17 Oct): a comma, an ellipsis or a dash at the end (Whisper's
# marks of a sentence left open; 、 is Japanese's comma), or a last word that goes on (language.CONTINUING: "to",
# "naar"), a "but" (language.BUT) or a filler ("um", "äh"), in the call's language or English
_OPEN_ENDS = (",", "、", "…", "...", "-", "–", "—")
AT_ONCE = "at once"  # _call_over: the call ends once her answer is spoken
AFTER_QUIET = "after quiet"  # _call_over: her plain goodbye ends it after goodbye_grace_s of quiet (_after_goodbye)
HOLD_MAX_S = 120.0  # a turn joined in holds is at most this long (one listen's cap; Whisper takes up to 3 minutes)


@functools.cache
def _open_words(language: str) -> frozenset[tuple[str, ...]]:
    codes = dict.fromkeys((language, ENGLISH))
    words = [word for code in codes for word in (*CONTINUING.get(code, ()), *BUT.get(code, ()))]
    return _phrases([*words, *FILLERS], language)


def goes_on(text: str, language: str = ENGLISH) -> bool:
    """Whether the caller's words end like more is coming (see _OPEN_ENDS): "I want to call," "I want to go to", "It is
    nice but", "Ik ga naar". A sentence that ends with a word such as "home" or "on" counts too: holding it only waits
    out today's full pause."""
    end = text.rstrip().rstrip("\"')]”’»」』").rstrip()
    if end.endswith(_OPEN_ENDS):
        return True
    units = _units(end, language)
    return any(len(units) >= len(words) and units[-len(words) :] == words for words in _open_words(language))


def _parts(text: str) -> list[str]:
    """Text to speak on a call, sentence by sentence (even a one-word answer); never a stage direction ("(laughs)")."""
    plain = without_stage_directions(text).strip()
    return sentences(spoken_text(text, min_chars=1, rest="")) or ([plain] if plain else [])


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
    # the call's end-of-turn rules, given to every listen of hers (never the one after a barge-in); None: both off
    turn_end: TurnEnd | None = None
    warm_up: asyncio.Task[None] | None = None  # the model's warm-up for this call (one at most)
    # the caller asked her to end the call (or took leave) while her answer asked a device question: the call ends
    # once her answer to the caller's reply is spoken, when that reply only answers the question ("yes", "no thanks")
    ends_after_question: bool = False


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
        # warm-ups still running, held here so that one outlives its call (asyncio keeps only weak references)
        self._warm_ups: set[asyncio.Task[None]] = set()
        self._audio: CallAudio | None = None
        self._clips: dict[tuple[str, str], bytes] = {}  # canned lines at 48 kHz by (language, text) (no user data)
        self._last: tuple[float, int] | None = None  # the last call: wall start, minutes
        self._not_ready_at: float | None = None
        self._detector = "not ready"  # the speech detector's state at the last readiness check (detector_state)
        self._turn_detector = "not ready"  # the turn detector's (Smart Turn), read with it
        self.saying = ""  # the clip being spoken now
        self.said: list[str] = []  # clips finished (played whole or cut off), latest last; the newest 20 kept

    @property
    def in_call(self) -> bool:
        return self._call is not None

    @property
    def speaking(self) -> bool:
        return self._audio is not None and self._audio.speaking.is_set()

    def status_line(self, next_call: str = "", detector: str = "", turn: str = "") -> str:
        """/status: whether she is in a call, the last one, the speech detector's state (`detector`, when speech stops
        her or holds the pause), the turn detector's (`turn`, with early end) and (from the reminders) the next call
        she will place."""
        if self._call is not None:
            line = f"Calls: in a call since {self._hhmm(self._call.started_wall)}"
        else:
            line = "Calls: ready"
            if self._last is not None:
                line += f" (last call {self._hhmm(self._last[0])}, {self._last[1]} min)"
        if detector:
            line += f" · speech detector: {detector}"
        if turn:
            line += f" · turn detector: {turn}"
        return f"{line} · next call {next_call}" if next_call and self._call is None else line

    async def detectors(self) -> tuple[str, str]:
        """The two detectors' states for /status, from one health read (voice.detector_state: ready, loading,
        unavailable, old voice service or not ready): the speech detector's when speech stops her ([calls] interrupt =
        "only speech") or holds the pause (pause = "no speech"), the turn detector's with early end; "" for one that
        nothing waits for."""
        s = self.settings
        speech, early = s.barge == "speech" or s.pause == "speech", s.early_end
        if not speech and not early:
            return "", ""
        health = await self._voice.health()
        return (detector_state(health) if speech else "", detector_state(health, part="turn") if early else "")

    async def detector(self) -> str:
        """The speech detector's state for /status (see `detectors`); "" when nothing waits for it."""
        return (await self.detectors())[0]

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
        # checked just before every call: the call's barge-in and end of turn use them
        self._detector = detector_state(health)
        self._turn_detector = detector_state(health, part="turn")
        return voice_ready(health)

    def _barge(self, stream: str) -> Barge:
        """The call's barge-in rules: the speech detector on the call's `stream` (one detector state per call) when the
        service has it ready, else loudness, said once when speech should have stopped her or held the pause."""
        s = self.settings
        if self._detector != "ready":
            if s.barge == "speech" or s.pause == "speech":
                log.info("calls: speech detector %s; loudness decides this call", self._detector)
            return Barge(s.barge)
        return Barge(s.barge, functools.partial(self._voice.vad, stream=stream))

    def _turn_end(self, stream: str) -> TurnEnd | None:
        """The call's end-of-turn rules (call_audio.TurnEnd): Smart Turn when early end is on and its detector is
        ready (else the full pause, said once), and the pause by speech on the call's barge-in `stream` when it is on
        and the speech detector is ready (else loudness: `_barge` said so). None with both off: today's listen."""
        s = self.settings
        if not s.early_end and s.pause != "speech":
            return None
        judge = None
        if s.early_end and self._turn_detector == "ready":
            judge = self._voice.turn
        elif s.early_end:
            log.info("calls: turn detector %s; the full pause decides this call", self._turn_detector)
        detect = None
        if s.pause == "speech" and self._detector == "ready":
            detect = functools.partial(self._voice.vad, stream=stream)
        return TurnEnd(judge, detect, early_pause_s=s.early_end_pause_s, early_end=s.early_end, pause=s.pause)

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
        self._warm_up(call)  # after the start (the app's glance is read from it), before the ringing
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
            stream = uuid.uuid4().hex  # one speech detector state for the call: barge-in and the pause by speech
            audio = CallAudio(reader, writer, self._barge(stream))
            call.turn_end = self._turn_end(stream)
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
                if call.turn_end is not None:
                    call.turn_end.close()
                    for line in call.turn_end.summary():
                        log.info("%s", line)
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

    def _warm_up(self, call: _Call) -> None:
        """The model's warm-up for this call (Host.call_warm_up), once, in its own task: the call never awaits it, and
        its end never cancels it (an abort while Ollama loads the model stops Ollama's runner; a finished read is
        harmless)."""
        if call.warm_up is not None:
            return
        task = asyncio.create_task(self._warming(call), name="call-warm-up")
        call.warm_up = task
        self._warm_ups.add(task)
        task.add_done_callback(self._warm_ups.discard)

    async def _warming(self, call: _Call) -> None:
        try:
            await self._host.call_warm_up(call.aci, practice=call.practice)
        except Exception as exc:  # never into the call: one line, the kind only
            log.warning("calls: model warm-up failed (%s)", type(exc).__name__)

    async def _after(self, call: _Call, minutes: int) -> None:
        with contextlib.suppress(Exception):
            if call.practice and call.user_turns:  # first: the texts typed meanwhile as practice, then the recap
                await self._host.practice_call_ended(call.aci)
            if call.dropped:
                await self._host.call_note(call.aci, prompts.CALL_DROPPED)
            if call.unfinished:  # the caller may think it was done: say it was not
                await self._host.call_note(call.aci, prompts.CALL_UNFINISHED)
            if not call.practice:  # code's exact chords before Gemma's summary, which can fail or be skipped
                await self._host.call_chords(call.aci)
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
        # standby (owner, 18 Oct): after the check-in only a turn with her name is answered (wake.says_name), so the
        # caller can do other things, noise and all; every idle_repeat_s of call time from her last line she says
        # she is still here, until the length cap: silence never ends a call
        standby = False
        line_at = 0.0  # call time of her last check-in or still-here line, or of standby's start
        idle_said_at: float | None = None  # call time of the last check-in ("Take your time, I'm here. Say …")
        question_waiting = False  # her last answer asked for a device's yes, and it was heard whole
        hearing_trouble = 0
        went_on = False  # the caller went on after her goodbye: their words are a turn, no cut-in
        while not call.ended.is_set():
            left = s.max_s - audio.frames_in * FRAME_S  # call time, like the idle timers
            if left <= 0:
                await self._say_line(audio, prompts.CALL_MAX_BYE, ENGLISH)
                return
            cutting_in = pending is not None and not went_on
            went_on = False
            if pending is None:
                now = audio.frames_in * FRAME_S
                wait = max(1.0, line_at + s.idle_repeat_s - now) if standby else s.idle_prompt_s
                if waking:
                    wait = s.wake_silence_s
                audio.unwatch()  # a watch after an earlier end is over: what comes now is this listen's
                clip, info = await audio.listen(
                    end_silence_s=s.end_of_turn_s, no_speech_s=min(wait, left), turn=call.turn_end
                )
                if not clip and waking and wait <= left:
                    log.info("calls: no answer to the wake-up line in %.0f s; goodbye", wait)
                    await self._say_line(audio, self._pick(lines.bye), call.language)
                    return
                waking = False
                if not clip:
                    next_step = "listening on" if wait > left else "still here" if standby else "checking in"
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
                    now = audio.frames_in * FRAME_S
                    if standby:
                        line_at = now
                        await self._say_line(audio, lines.still_here, call.language)
                        continue
                    standby, line_at = True, now
                    log.info("calls: standby: only a turn with her name is answered")
                    if idle_said_at is not None and now - idle_said_at < s.idle_repeat_s:
                        log.info("calls: said take-your-time %.0f s ago; listening on", now - idle_said_at)
                        continue  # said a short while ago: quiet, in standby all the same
                    idle_said_at = now
                    await self._say_line(audio, lines.idle, call.language)
                    continue
                voiced_s = float(info.get("voiced_s", 99.0))
                if voiced_s < MIN_VOICED_S:  # a click or a bump: not worth asking speech recognition
                    log.info(
                        "calls: noise (voiced %.2f s, peak %s, floor %s); listening on",
                        voiced_s,
                        info.get("peak"),
                        info.get("floor"),
                    )
                    self._no_turn(call)
                    continue
                self._watch_after(call, audio, info)
                try:
                    text = await self._hear(call, clip, info)
                    # typing or noise heard as "thank" or "*music*": silence
                    noise = is_noise_transcript(text, float(info.get("sustained_s", voiced_s)))
                    if not noise:
                        text = await self._hold(call, audio, text, clip, info)
                except VoiceError:
                    hearing_trouble += 1
                    if hearing_trouble >= 2:
                        raise
                    await self._say_line(audio, lines.hearing, call.language)
                    continue
                hearing_trouble = 0
                if noise:
                    log.info("calls: noise heard as words; listening on")
                    self._no_turn(call)
                    continue
            else:
                text, pending = pending, None
            if not real_words(text):  # a cough, a noise, a breath: she just keeps listening (no "sorry?")
                log.info("calls: a sound without words; listening on")
                if not cutting_in:
                    self._no_turn(call)
                continue
            if standby:
                if not says_name(text, self.name):  # said to someone else, or the TV: heard, never answered or kept
                    log.info("calls: standby: a turn without her name (%d words); listening on", turn_words(text))
                    continue
                standby = False
                log.info("calls: standby over: her name")
            if is_lone_stop(text, name=self.name):
                await self._host.call_drop_waiting(call.aci)  # like any answer but yes, a stop drops the question
                question_waiting = call.ends_after_question = False
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
            may = s.acknowledge and not question_waiting and not call.acknowledged_last
            acknowledge = may and turn_words(text) >= ACK_MIN_WORDS
            call.acknowledged_last = acknowledge
            # the model's first try came back empty (research, 17 Oct: about 10 s of silence, then the retry): the
            # same rules but the few words', which would leave only turns that had one at their start; one a turn
            turn = await self._turn(call, audio, said, acknowledge=acknowledge, on_retry=may and not acknowledge)
            interruption, heard, taken_at = await self._speak(call, audio, turn, waiting=turn.asks_yes)
            call.lines.append(f"{self.name}: {heard}")
            question_waiting = turn.asks_yes and interruption is None
            if interruption is not None and not cutting_in:
                self._taken_over(call, interruption, taken_at)
            if interruption is not None:
                if turn.reply_id is not None:
                    self._host.call_interrupted(turn.reply_id, heard)
                if turn.asks_yes:  # cut off before the question was asked whole: no yes may act on it
                    await self._host.call_drop_waiting(call.aci)
                pending = interruption
            elif over := self._call_over(call, text, turn):
                if over == AFTER_QUIET:  # her plain goodbye: the call ends unless the caller goes on
                    later = await self._after_goodbye(call, audio)
                    if later is not None:
                        pending, went_on = later, True
                        continue
                return  # her answer said whole, the call over (_call_over): hung up in _run_call

    async def _hear(self, call: _Call, clip: bytes, info: Mapping[str, Any]) -> str:
        """What the caller said in `clip` (Whisper, told the call's language); the log has its numbers only."""
        text = (await self._voice.transcribe(pcm48_to_wav16k(clip), language=call.language)).text
        voiced_s = float(info.get("voiced_s", 99.0))
        log.info(
            "calls: heard %d words (speech %.1f s, voiced %.2f s, sustained %.2f s, peak %s, floor %s)",
            len(text.split()),
            float(info.get("speech_s", 0.0)),
            voiced_s,
            float(info.get("sustained_s", voiced_s)),
            info.get("peak"),
            info.get("floor"),
        )
        return text

    async def _hold(self, call: _Call, audio: CallAudio, text: str, clip: bytes, info: Mapping[str, Any]) -> str:
        """An early end whose words end the call or end like they go on (`_hold_reason`) waits out the full pause
        after all: a listen for the rest of it in call time (what came in meanwhile is heard first). Nobody speaks: the
        turn stands. The caller goes on: that is joined to the turn's clip and heard whole, and checked again, up to
        HOLD_MAX_S of audio (call_audio.TurnEnd: `held`, then `joined`). Returns the turn's words."""
        turn_end, s = call.turn_end, self.settings
        while turn_end is not None and info.get("ended") in EARLY_KINDS:
            room = HOLD_MAX_S - len(clip) / (2 * RATE)
            reason = self._hold_reason(call, text)
            if reason is None or room < 1.0:
                break
            audio.unwatch()
            turn_end.held(reason)
            rest = max(0.0, s.end_of_turn_s - float(info.get("quiet_s", 0.0)))
            more, more_info = await audio.listen(
                end_silence_s=s.end_of_turn_s, no_speech_s=rest, max_s=room, turn=turn_end
            )
            if not more:
                log.info("calls: early end held (%s); the turn stands", reason)
                break
            log.info("calls: early end held (%s); the caller went on", reason)
            turn_end.joined()
            self._watch_after(call, audio, more_info)
            clip += more
            info = more_info | {
                "speech_s": round(len(clip) / (2 * RATE), 2),
                "voiced_s": round(float(info.get("voiced_s", 0.0)) + float(more_info.get("voiced_s", 0.0)), 2),
                "sustained_s": round(float(info.get("sustained_s", 0.0)) + float(more_info.get("sustained_s", 0.0)), 2),
                "peak": max(int(info.get("peak", 0)), int(more_info.get("peak", 0))),
            }
            text = await self._hear(call, clip, info)
        return text

    def _hold_reason(self, call: _Call, text: str) -> str | None:
        """Why an early end waits out the full pause after all (call_audio.HOLDS): its words would end the call (a
        goodbye, leave-taking, a wrap-up, asking her to hang up), or they end like more is coming (`goes_on`); None:
        they stand. A hold only costs today's full pause."""
        if self._ends_call(call, text):
            return "ends the call"
        return "goes on" if goes_on(text, call.language) else None

    @staticmethod
    def _no_turn(call: _Call) -> None:
        """The latest end held noise or no words: no turn for the end lines (call_audio.TurnEnd.noise)."""
        if call.turn_end is not None:
            call.turn_end.noise()

    def _watch_after(self, call: _Call, audio: CallAudio, info: Mapping[str, Any]) -> None:
        """After an early end, while Whisper and the answer work: the caller going on, sustained (as loud as the listen
        needs: max(4x floor, 450)) and heard as speech by the speech detector when the call has one, within what would
        have been the full pause was cut off by it (TurnEnd.went_on; CallAudio.watch). Over when she answers, at the
        next listen or in a hold."""
        if call.turn_end is None or info.get("ended") not in EARLY_KINDS:
            return
        rest = round((self.settings.end_of_turn_s - float(info.get("quiet_s", 0.0))) / FRAME_S)
        level = max(LISTEN_FLOOR_FACTOR * float(info.get("floor", 0.0)), LISTEN_MIN_RMS)
        audio.watch(rest, level, call.turn_end.went_on)

    @staticmethod
    def _taken_over(call: _Call, words: str, at_s: float | None) -> None:
        """The caller took over her answer to a turn `at_s` into it (after its lead): within EARLY_BARGE_S it counts
        against the end before it, early or full (TurnEnd.barged_early). Noise and her own voice are no takeover."""
        if call.turn_end is not None and at_s is not None and at_s <= EARLY_BARGE_S and real_words(words):
            call.turn_end.barged_early(at_s)

    def _call_over(self, call: _Call, text: str, turn: Turn) -> str:
        """Whether the call ends once her answer `turn` to the caller's `text` is spoken whole (no takeover): her
        end_call; else never while a device question waits for its yes (`turn.asks_yes`: the caller asking her to hang
        up, a goodbye with more or leave-taking then waits for the answer, `call.ends_after_question`, and ends the call
        after it when the caller's reply only answers the question: `answers_question`; review 18 Oct); the caller
        asking her to hang up, a goodbye with more or leave-taking, even when her answer asks something (those checks already leave
        out a turn that goes on with a question, "wait", "but" or "one more thing"); a wrap-up unless her answer asks
        something; and her answer ending with a plain goodbye that asks nothing (`ends_with_goodbye`) after a caller's
        turn that closes the call itself (_closing_cue: only thanks or an okay, a wrap-up, leaving words; review 18
        Oct: her "Take care!" after "I have a bad cold today." is sympathy) and is closing talk (_closing_talk: no
        question, "wait", "but", "actually", "one more thing"), on any call but a practice call, where a goodbye may be
        what is practised (owner, 18 Oct: her "Bye!" after the caller's leaving ended nothing, and the caller hung up
        himself)."""
        held, call.ends_after_question = call.ends_after_question, False
        if turn.end_call:
            return AT_ONCE
        asked_to_end = wants_hang_up(text, name=self.name, language=call.language) or self._leaving(call, text)
        if turn.asks_yes:  # a yes to a device must still count: the request waits for the answer
            call.ends_after_question = asked_to_end
            return ""
        if asked_to_end or (held and answers_question(text, name=self.name, language=call.language)):
            return AT_ONCE
        hers = f"{turn.text} {turn.code_text}".strip()
        if is_wrap_up(text):
            return "" if hers.endswith("?") else AT_ONCE
        if not call.practice and ends_with_goodbye(hers, name=self.name, language=call.language):
            return AFTER_QUIET
        return ""

    async def _after_goodbye(self, call: _Call, audio: CallAudio) -> str | None:
        """After her plain goodbye: a listen of goodbye_grace_s. The caller's words, when they go on (not a noise, a
        sound without words, or a goodbye or leaving of their own), are the next turn; else None: the call ends."""
        s = self.settings
        log.info("calls: her goodbye; listening %.1f s before the end", s.goodbye_grace_s)
        audio.unwatch()
        audio.drain_incoming()  # the quiet counts from the end of her goodbye, never from frames queued while she spoke
        clip, info = await audio.listen(end_silence_s=s.end_of_turn_s, no_speech_s=s.goodbye_grace_s)
        if not clip or float(info.get("voiced_s", 99.0)) < MIN_VOICED_S:
            return None
        try:
            text = await self._hear(call, clip, info)
        except VoiceError:
            return None  # heard nothing usable: both said goodbye, the call ends
        if is_noise_transcript(text, float(info.get("sustained_s", 99.0))) or not real_words(text):
            return None
        if self._ends_call(call, text):
            log.info("calls: the caller said goodbye too")
            return None
        log.info("calls: the caller went on after her goodbye (%d words)", turn_words(text))
        return text

    def _ends_call(self, call: _Call, text: str) -> bool:
        """The caller's words end the call: a goodbye, leave-taking, a wrap-up or asking her to hang up."""
        return (
            is_goodbye(text, name=self.name)
            or self._leaving(call, text)
            or is_wrap_up(text)
            or wants_hang_up(text, name=self.name, language=call.language)
        )

    def _leaving(self, call: _Call, text: str) -> bool:
        """The user said goodbye with more than the goodbye, or takes leave: she hangs up once her reply is spoken."""
        return says_goodbye(text, name=self.name, language=call.language) or takes_leave(
            text, name=self.name, language=call.language
        )

    async def _turn(
        self, call: _Call, audio: CallAudio, text: str, *, acknowledge: bool, on_retry: bool = False
    ) -> Turn:
        """The app's answer. A short "Got it." first when `acknowledge` (the request was taken in), or with
        `on_retry` as soon as the model's first try came back empty and it is asked again (the caller is not left in
        silence; it counts as this turn's, so the next request gets none), unless a thinking or checking line came
        before it. A varied thinking line when the answer takes a moment after that (then a short pause before the
        answer), a "let me check" line when a tool runs (each at most once). An answer that takes too long, outlives
        the call or is left by an error is cancelled (never left running beside the next turn or after the call) and
        any device question it raised is dropped; when the call ended meanwhile, the user gets a note that it was not
        done."""
        s = self.settings
        tools = asyncio.Event()
        retry = asyncio.Event()
        task = asyncio.create_task(
            self._host.call_turn(call.aci, text, tools.set, practice=call.practice, on_retry=retry.set)
        )
        ended = asyncio.create_task(call.ended.wait())
        thought = checked = not s.fillers  # without fillers she simply answers when the answer is ready
        filled = False  # a thinking or checking line was said: no acknowledgement for a retry after it
        line_end: float | None = None
        started = think_from = self._clock()
        lines = call.spoken
        try:
            if acknowledge:  # "Got it." while the answer is already being worked on: the request was taken in
                ack = self._pick_acknowledgement(lines.acknowledge)
                await self._say_line(audio, ack, call.language, watch=True)  # the caller may go on over it
                line_end = think_from = self._clock()  # a thinking line only after a real wait beyond it
            while not task.done():
                now = self._clock()
                if now - started >= s.turn_timeout_s:
                    log.warning("calls: an answer took over %.0f s", s.turn_timeout_s)
                    await self._abandon(call, task)
                    return Turn(lines.lost)
                if on_retry and retry.is_set():  # asked again: the acknowledgement now, while the retry runs
                    on_retry = False
                    if not filled:  # after "Let me think." or "Let me check." it would come in the wrong order
                        call.acknowledged_last = True
                        log.info("calls: an acknowledgement while an empty answer is asked again")
                        ack = self._pick_acknowledgement(lines.acknowledge)
                        await self._say_line(audio, ack, call.language, watch=True)
                        line_end = think_from = self._clock()
                        continue
                check = tools.is_set() and not checked
                think = not thought and not checked and now - think_from >= s.thinking_after_s
                if check or think:
                    checked = checked or check
                    thought = filled = True
                    line = self._pick(lines.checking if check else lines.thinking)
                    await self._say_line(audio, line, call.language, watch=True)
                    line_end = self._clock()
                    continue
                waits: set[asyncio.Future[Any]] = {task, ended}
                waiters: list[asyncio.Task[bool]] = []
                if not checked and not tools.is_set():
                    waiters.append(asyncio.create_task(tools.wait()))
                if on_retry:
                    waiters.append(asyncio.create_task(retry.wait()))
                waits.update(waiters)
                limit = started + s.turn_timeout_s - now if thought else think_from + s.thinking_after_s - now
                await asyncio.wait(waits, timeout=max(0.0, limit), return_when=asyncio.FIRST_COMPLETED)
                for waiter in waiters:
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
        self,
        audio: CallAudio,
        text: str,
        language: str,
        *,
        barge: bool = False,
        keep: bool = True,
        watch: bool = False,
    ) -> str | None:
        """A code-written line in `language`, spoken whole; with barge-in it returns what the caller said over it.
        `keep`: the clip may be kept for next time (canned lines only, never one with the user's words). `watch`: a
        line while her answer is worked out, so the watch after an early end goes on (`CallAudio.say`)."""
        self.saying = text
        clip = await self._clip(text, language, keep=keep)
        result = await self._say_checked(audio, faint_noise(LEAD_MS) + clip, barge, hers=(text,), watch=watch)
        self._done(text)
        return result[0] if result else None

    def _done(self, text: str) -> None:
        self.said = [*self.said[-19:], text]
        self.saying = ""

    async def _speak(
        self, call: _Call, audio: CallAudio, turn: Turn, *, waiting: bool
    ) -> tuple[str | None, str, float | None]:
        """Gemma's answer sentence by sentence (the next one is made while one plays), then code's own words: in
        English, so a device question is always clear (on an English call they simply follow). On a call in another
        language a turn that waits for a device's yes says only code's question: Gemma's words about the device are
        left out (in Dutch it claimed a switch that was still waiting for the yes), and app.call_turn keeps only that
        question in the history too; a turn whose reminder was not set comes with no words of hers at all, only code's
        line why. `waiting`: a device question waits for the yes (turn.asks_yes): its last sentence is said whole, and
        a yes word over the ones before answers it. Returns what the caller said over it (or None), what they heard
        of it, and how far into her answer (after the lead) they took over (or None)."""
        if call.language == ENGLISH:
            spoken = [(_parts(f"{turn.text} {turn.code_text}"), ENGLISH)]
        elif turn.asks_yes and turn.code_text.strip():
            spoken = [(_parts(turn.code_text), ENGLISH)]
        else:
            spoken = [(_parts(turn.text), call.language), (_parts(turn.code_text), ENGLISH)]
        said = [(part, language) for parts_, language in spoken for part in parts_]
        parts = [part for part, _ in said]
        if not parts:
            return None, "", None
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
        played_s = 0.0  # her answer's sentences before this one, whole, in seconds
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
                    text, fraction, stop_s = result
                    cut = part[: int(len(part) * fraction)].rsplit(" ", 1)[0] if fraction < 1 else part
                    heard = " ".join([*parts[:index], cut]).strip()
                    return text, f"{heard} …".strip(), max(0.0, played_s + stop_s - len(lead) / (2 * RATE))
                played_s += len(item) / (2 * RATE)
            return None, " ".join(parts), None
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
        watch: bool = False,
    ) -> tuple[str, float, float] | None:
        """Say `pcm` (her sentences `hers`: the one before and the one being said); when the caller talks over it,
        listen and check the words: an interruption returns (what they said, how much of the clip went out, and the
        stop's place in the clip in seconds); a cough,
        a noise or her own voice (compared with what she played of `hers` up to the stop) resumes a little before the
        stop. Her own voice also raises the level a stop needs, once a call (call_audio.Barge.her_own_voice); it never
        reaches Gemma. A listener's acknowledgement alone ("okay", "mm-hm") resumes too, unless `hers` asks something
        or a device question is `waiting`: then it answers her (owner, 14 Oct). Each acknowledgement takes her further
        on: a little before the stop, never back to where she resumed (a stop that soon would replay the same audio
        for good), and after MAX_FALSE_BARGE of them in one clip from the stop itself. `watch` (without barge-in): the
        watch after an early end goes on over it (`CallAudio.say`)."""
        offset = 0
        false_alarms = 0
        carried_on = 0  # acknowledgements in this clip
        resumed = False
        while True:
            said = await audio.say(pcm[offset:], barge=barge, fresh=fresh and not resumed, watch=watch and not barge)
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
                return taken, fraction, stop / (2 * RATE)
            false_alarms += 1
            log.info("calls: her own voice heard; carrying on" if own else "calls: a sound without words; carrying on")
            offset = max(0, stop - 2 * int(RESUME_BACK_S * RATE))
