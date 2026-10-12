import unicodedata
from dataclasses import astuple

import pytest

from local_assistant import prompts
from local_assistant.config import DEFAULT_NAME
from local_assistant.language import CODES, LANGUAGES, YES_WORDS, find

NAME = DEFAULT_NAME  # her name in these tests (a fresh install's)


def test_the_eleven_languages_english_first_then_by_whisper_error_rate() -> None:
    assert CODES == ("en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr")
    assert list(LANGUAGES) == list(CODES)
    assert LANGUAGES["nl"].english == "Dutch" and LANGUAGES["nl"].own == "Nederlands"
    assert LANGUAGES["ja"].own == "日本語" and LANGUAGES["ko"].own == "한국어" and LANGUAGES["ru"].own == "Русский"
    assert all(language.code == code for code, language in LANGUAGES.items())


@pytest.mark.parametrize(
    ("said", "code"),
    [
        ("nl", "nl"),
        ("NL", "nl"),
        ("dutch", "nl"),
        ("Nederlands", "nl"),
        ("español", "es"),
        ("espanol", "es"),  # accents are optional
        ("Français", "fr"),
        ("francais", "fr"),
        ("português", "pt"),
        ("русский", "ru"),
        ("日本語", "ja"),
        ("한국어", "ko"),
        ("  German ", "de"),
    ],
)
def test_a_language_is_found_by_code_english_name_or_own_name(said: str, code: str) -> None:
    found = find(said)
    assert found is not None and found.code == code


@pytest.mark.parametrize("said", ["", "klingon", "greek", "e", "english please"])
def test_anything_else_is_no_language(said: str) -> None:
    assert find(said) is None


def test_the_yes_words_of_every_language() -> None:
    for word in ("yes", "ok", "ja", "sí", "si", "sì", "oui", "tak", "да", "はい", "ええ", "네", "예", "sim"):
        assert word in YES_WORDS, word
    assert all(word == word.casefold() and "," not in word for word in YES_WORDS)  # stored as they are compared
    assert not {"no", "nee", "nein", "non", "nie", "нет", "いいえ", "아니요", "não"} & YES_WORDS


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


@pytest.mark.parametrize("calendar", [False, True])
def test_english_adds_nothing_to_the_rules(calendar: bool) -> None:
    every = prompts.rules(name=NAME, calendar=calendar, calls=True, music=True, web=True, maps=True)
    assert (
        prompts.rules(name=NAME, calendar=calendar, calls=True, music=True, web=True, maps=True, language="en") == every
    )
    assert prompts.rules(name=NAME, language="en") == prompts.rules(name=NAME)


def test_another_language_is_one_code_written_line_in_the_rules() -> None:
    english, dutch = prompts.rules(name=NAME).splitlines(), prompts.rules(name=NAME, language="nl").splitlines()
    changed = [(old, new) for old, new in zip(english, dutch, strict=True) if old != new]
    assert changed == [
        (
            "- Reply in the language the user writes in.",
            "- Answer only in Nederlands (Dutch), whatever language the user writes in. Keep names, code and quotes "
            "as they are.",
        )
    ]
    assert "Answer only in 日本語 (Japanese)" in prompts.rules(name=NAME, language="ja", calls=True)


def test_every_language_has_its_eight_spoken_call_lines() -> None:
    assert set(prompts.CALL_SPOKEN) == set(CODES)
    for code in CODES:
        spoken = prompts.spoken(code, name=NAME)
        lines = astuple(spoken)
        assert len(lines) == 8 and len(set(lines)) == 8, code
        assert all(line.strip() == line and 0 < len(line) < 90 and "\n" not in line for line in lines), code
        assert _plain(LANGUAGES[code].own)[:5] in _plain(spoken.now_speaking), code  # "from now on I speak Dutch"
    for code in ("es", "it", "pt", "nl", "pl", "de", "fr"):  # Latin letters: the voice can say her name
        assert NAME in prompts.spoken(code, name=NAME).greeting, code


def test_english_calls_keep_the_richer_lines() -> None:
    english = prompts.call_lines("en", name=NAME)
    assert english.greetings == prompts.call_greetings(NAME) and english.acknowledge == prompts.CALL_ACK_LINES
    assert english.thinking == prompts.CALL_THINKING_LINES and english.checking == prompts.CALL_CHECKING_LINES
    assert (english.idle, english.lost, english.hearing) == (prompts.CALL_IDLE, prompts.CALL_LOST, prompts.CALL_HEARING)
    assert english.bye == prompts.CALL_BYE_LINES


def test_other_languages_use_their_table_and_their_own_got_it() -> None:
    dutch, spoken = prompts.call_lines("nl", name=NAME), prompts.spoken("nl", name=NAME)
    assert dutch.greetings == (spoken.greeting,) and dutch.acknowledge == ("Begrepen.", "Oké, begrepen.")
    assert dutch.thinking == dutch.checking == (spoken.moment,)
    assert (dutch.idle, dutch.lost, dutch.hearing, dutch.bye) == (
        spoken.take_your_time,
        spoken.again,
        spoken.again,
        (spoken.bye,),
    )


# owner 2026-10-11: the acknowledgement is the language's natural short "got it"; a voice garbles humming sounds
GOT_IT = {
    "en": "Got it.",
    "nl": "Begrepen.",
    "de": "Verstanden.",
    "fr": "Compris.",
    "es": "Entendido.",
    "it": "Capito.",
    "pt": "Entendi.",
    "pl": "Rozumiem.",
    "ru": "Понятно.",
    "ja": "わかりました。",
    "ko": "알겠어요.",
}
HUMS = {"hmm", "hm", "mm", "mhm", "mm-hm", "uh-huh", "uh", "um", "ah", "äh", "euh", "эм", "мм"}


@pytest.mark.parametrize("code", CODES)
def test_every_language_acknowledges_with_its_own_got_it_never_a_hum(code: str) -> None:
    acknowledge = prompts.call_lines(code, name=NAME).acknowledge
    assert acknowledge[0] == GOT_IT[code]
    assert all(line.strip() == line and 0 < len(line) < 24 for line in acknowledge)
    words = {word.strip(".,!?。") for line in acknowledge for word in line.lower().split()}
    assert not words & HUMS, code
    if code != "en":
        assert not set(acknowledge) & set(prompts.CALL_ACK_LINES), code  # its own language, not English


def test_the_examples_variations_are_kept() -> None:
    assert "Alles klar." in prompts.call_lines("de", name=NAME).acknowledge
    assert "D'accord, compris." in prompts.call_lines("fr", name=NAME).acknowledge


@pytest.mark.parametrize("code", CODES)
def test_every_language_opens_a_wake_up_call_and_a_call_reminder_with_the_time(code: str) -> None:
    """The opening lines of the calls she places (spoken in the call's language; translations need a native check)."""
    lines = prompts.call_lines(code, name=NAME)
    for line in (lines.wake, lines.call_reminder):
        assert line.count("{time}") == 1 and line.strip() == line, code
        said = line.format(time="7:00")
        assert "7:00" in said and " ?" not in said and " !" not in said, code
    assert lines.wake != lines.call_reminder


def test_the_english_opening_lines() -> None:
    english = prompts.call_lines("en", name=NAME)
    assert english.wake.format(time="7:00") == "Good morning! It's 7:00."
    assert english.call_reminder.format(time="17:00") == "It's 17:00. You asked me to call you."
