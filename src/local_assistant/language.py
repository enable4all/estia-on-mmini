"""The one language she listens and speaks in: the 11 she offers, their names and their yes words.

Only the languages Whisper large-v3 hears best (OpenAI's published word error rates, all at most 5.3 %) that also have
a voice; English first, then from the lowest error rate. One at a time: Whisper is told the language on every
request (no detection) and Gemma answers in it. The language changes only by the user's typed /language.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str  # what the voice service and the config take, like "nl"
    english: str  # "Dutch"
    own: str  # "Nederlands"


LANGUAGES: dict[str, Language] = {
    language.code: language
    for language in (
        Language("en", "English", "English"),  # Whisper's error rate 4.1 %
        Language("es", "Spanish", "Español"),  # 2.8 %
        Language("it", "Italian", "Italiano"),  # 3.0 %
        Language("ko", "Korean", "한국어"),  # 3.1 %
        Language("pt", "Portuguese", "Português"),  # 4.1 %
        Language("nl", "Dutch", "Nederlands"),  # 4.3 %
        Language("pl", "Polish", "Polski"),  # 4.6 %
        Language("de", "German", "Deutsch"),  # 4.9 %
        Language("ja", "Japanese", "日本語"),  # 4.9 %
        Language("ru", "Russian", "Русский"),  # 5.0 %
        Language("fr", "French", "Français"),  # 5.3 %
    )
}
CODES = tuple(LANGUAGES)
ENGLISH = "en"
LANGUAGE_KEY = "language"  # the state table's key for the one chosen with /language; wins over the config's

# a device's "ask first" yes, in each language: the whole message must be one of these (casefolded, trailing
# punctuation dropped, commas as spaces; see devices._is_yes)
_YES: dict[str, tuple[str, ...]] = {
    "en": ("yes", "y", "ok", "okay", "yes please", "do it"),
    "es": ("sí", "si", "sí por favor", "si por favor", "vale"),
    "it": ("sì", "si", "sì grazie", "si grazie", "va bene"),
    "ko": ("네", "예", "응", "네 해 주세요"),
    "pt": ("sim", "sim por favor"),
    "nl": ("ja", "ja graag", "ja hoor", "doe maar", "oké"),
    "pl": ("tak", "tak proszę"),
    "de": ("ja", "ja bitte", "jawohl"),
    "ja": ("はい", "ええ", "うん", "はい お願いします"),
    "ru": ("да", "да пожалуйста", "давай"),
    "fr": ("oui", "oui merci", "d'accord", "vas-y"),
}
YES_WORDS = frozenset(word for words in _YES.values() for word in words)

# on a call, one of these said alone over her is an interruption ("wait", "stop", "no"), like a yes word
_INTERRUPT: dict[str, tuple[str, ...]] = {
    "en": ("stop", "wait", "no", "hold"),
    "es": ("espera", "para", "no"),
    "it": ("aspetta", "basta", "fermati", "no"),
    "ko": ("잠깐", "잠깐만", "아니요", "아니", "그만"),
    "pt": ("espera", "espere", "para", "pare", "não"),
    "nl": ("wacht", "stop", "nee"),
    "pl": ("czekaj", "poczekaj", "stop", "nie"),
    "de": ("warte", "stopp", "halt", "nein"),
    "ja": ("待って", "ちょっと", "いいえ"),
    "ru": ("стоп", "подожди", "погоди", "нет"),
    "fr": ("attends", "arrête", "stop", "non"),
}
INTERRUPTIONS = frozenset(word for words in _INTERRUPT.values() for word in words)


def _plain(text: str) -> str:
    """Casefolded, without accents: "Français" and "francais" are the same name."""
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def find(name: str) -> Language | None:
    """The language by its code, English name or own name (any case, accents optional), or None."""
    wanted = _plain(name)
    for language in LANGUAGES.values():
        if wanted in (language.code, _plain(language.english), _plain(language.own)):
            return language
    return None
