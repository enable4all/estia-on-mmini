import unicodedata
from dataclasses import astuple

import pytest

from local_assistant import language, prompts
from local_assistant.config import DEFAULT_NAME
from local_assistant.language import CODES, LANGUAGES, YES_WORDS, find, is_yes

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


@pytest.mark.parametrize(
    ("said", "code", "expected"),
    [
        ("[call] yes please", "en", True),
        ("[call] Yes.", "en", True),
        ("Ja, graag!", "nl", True),
        ("[call] yes", "nl", True),  # English is code's language: a yes in it counts on any call
        ("はい。", "ja", True),
        ("ja", "en", False),  # another language's yes: not the call's
        ("[call] yes, and what about the weather?", "en", False),  # the whole message must be the yes
        ("[call] not now", "en", False),
        ("[audio file] yes", "en", False),  # someone else's voice is no yes
    ],
)
def test_a_yes_in_the_calls_language_or_english(said: str, code: str, expected: bool) -> None:
    assert is_yes(said, code) is expected


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


# /babble: one practice line replaces the language line; every rules text says practice exists


def _changed(first: str, second: str) -> list[tuple[str, str]]:
    return [(old, new) for old, new in zip(first.splitlines(), second.splitlines(), strict=True) if old != new]


@pytest.mark.parametrize("language", ["en", "de"])
def test_practice_puts_its_line_in_place_of_the_language_line(language: str) -> None:
    chat = prompts.rules(name=NAME, language=language)
    practice = prompts.rules(name=NAME, language=language, practice=("nl", "A2"))
    old = prompts._ANY_LANGUAGE if language == "en" else prompts.LANGUAGE_RULE.format(own="Deutsch", english="German")
    assert old in chat and old not in practice
    [line] = [line for line in practice.splitlines() if line.startswith("- Language practice (Dutch, A2)")]
    assert "Nederlands (Dutch)" in line and prompts.PRACTICE_LEVELS["A2"] in line
    for needed in ("→", "[voice]", "[call]", "one to three short sentences", "exactly one question"):
        assert needed in line, needed


def test_the_practice_line_keeps_the_tested_prompts_lines() -> None:
    # the tutor test measured "→" precision and false corrections with these lines (prompt.txt)
    line = prompts.PRACTICE_RULE
    assert "a warm, patient conversation partner" in line
    assert "Most replies have no such line." in line
    assert (
        "No explanations, grammar terms, translations or lists during the session: explanations come in the recap "
        "at the end." in line
    )
    assert "no joke or pun" in line  # her built-in style ends banter with a joke: never after the question


def test_the_arrow_clause_is_the_tested_one_without_a_placeholder() -> None:
    # the tested clause (prompt.txt); a "<...>" placeholder was copied into replies ("ho andato <sono andato>")
    line = prompts.PRACTICE_RULE.format(english="Italian", own="Italiano", level="A2", level_line="A2.")
    assert "only the user's few words, corrected, in Italiano, and nothing else on that line." in line
    assert "Add it only when you are sure the words are wrong" in line
    assert "and only when the error changes the meaning or the user repeats it" in line
    assert "<" not in line and ">" not in line
    assert "none in a reply to [voice] or [call]" in line and "Never more than one." in line


@pytest.mark.parametrize("on", [False, True])
def test_practice_rules_leave_out_every_tool_instruction(on: bool) -> None:
    practice = prompts.rules(
        name=NAME, practice=("nl", "A2"), calendar=on, web=on, maps=on, calls=on, music=on, reminders=True
    )
    assert practice == prompts.rules(name=NAME, practice=("nl", "A2"))  # no tool section, whatever is configured
    for tool in (
        "call remember",
        "call forget",
        "search_history",
        "learn_link",
        "read_link",
        "mini_status",
        "project_list",
        "mail_digest",
        "device_set",
        "calendar_agenda",
        "web_search",
        "set_reminder",
        "music_play",
        "places_search",
        "end_call",
        '"Noted: ..."',
        "with your tools",
        "Save facts from a photo",
    ):
        assert tool not in practice, tool
    for heading in ("Memory:\n", "Photos:\n", "Voice notes:\n", "Safety:\n"):
        assert heading in practice, heading
    for heading in ("The Mac and the projects:", "Mail:", "Lights and plugs:", "Calendar:", "Reminders:"):
        assert heading not in practice, heading
    no_tools = prompts.PRACTICE_NO_TOOLS.format(own="Nederlands")
    assert no_tools in practice and "/babble off" in no_tools
    assert "nothing is saved, switched, read, searched or set" in no_tools
    assert len(practice) < 4_500  # the tested stand-alone prompt was about 1,300 characters; her chat rules 6,000+


def test_each_level_has_its_one_cefr_line() -> None:
    assert set(prompts.PRACTICE_LEVELS) == {"A2", "B1"}
    assert prompts.PRACTICE_LEVELS["A2"].startswith("A2 (elementary): short, simple sentences")
    assert prompts.PRACTICE_LEVELS["B1"].startswith("B1 (intermediate): clear, standard language")
    b1 = prompts.rules(name=NAME, practice=("ja", "B1"))
    assert prompts.PRACTICE_LEVELS["B1"] in b1 and "日本語 (Japanese)" in b1


def test_every_rules_text_says_practice_exists_and_the_last_setting() -> None:
    for text in (
        prompts.rules(name=NAME),
        prompts.rules(name=NAME, language="nl", calendar=True, web=True),
        prompts.rules(name=NAME, practice=("de", "B1")),
    ):
        lines = text.splitlines()
        can = next(index for index, line in enumerate(lines) if line.startswith("- You know what you learned"))
        assert lines[can + 1].startswith("- Language practice")  # the chat rules add that it is one of her things
        assert prompts.PRACTICE_CAN.removeprefix("- Language practice:") in lines[can + 1]
    last = prompts.practice_last(("nl", "A2"))
    assert last == "Last practised: Dutch, A2."
    assert prompts.practice_last(None) == "Not practised yet."
    assert f"{prompts.PRACTICE_CAN.removeprefix('- Language practice:')} {last}" in prompts.rules(
        name=NAME, practice_last=last
    )
    for english in ("English", "Dutch", "German", "Spanish", "French", "Portuguese", "Japanese", "Russian", "Italian"):
        assert english in prompts.PRACTICE_CAN, english
    for typed in ("/babble nl", "/babble nl b1", "/babble off"):
        assert typed in prompts.PRACTICE_CAN, typed


def test_the_practice_line_says_it_is_one_of_the_things_she_can_do() -> None:
    """Right after what she can do, the practice line says it is one of those things (evals, 13 Oct: "What can you
    do?" listed her tools and left practice out; with this wording 25 of 25). The sentence of her tools stays as it
    was: practice named inside it lost her end_call on a goodbye (call_goodbye 16 of 30 and 23 of 30, against 38 of 45
    as it was; this wording 36 of 45)."""
    for text in (
        prompts.rules(name=NAME),
        prompts.rules(name=NAME, projects=True, calendar=True, web=True, maps=True),
        prompts.rules(name=NAME, language="nl", web=True),
    ):
        lines = text.splitlines()
        can = next(index for index, line in enumerate(lines) if line.startswith("- You know what you learned"))
        assert lines[can + 1].startswith("- Language practice (one of the things you can do): the user can practise")
        assert "practise a language" not in lines[can]
    assert "plugs with your tools. You cannot" in prompts.rules(name=NAME)
    practice = prompts.rules(name=NAME, practice=("de", "B1"))  # the practice rules as they were (their evals)
    assert prompts.PRACTICE_CAN.startswith("- Language practice: the user") and prompts.PRACTICE_CAN in practice
    assert "one of the things you can do" not in practice


def test_every_offered_language_has_a_short_fallback_question() -> None:
    from local_assistant.babble import OFFERED

    assert set(prompts.PRACTICE_FALLBACK) == set(OFFERED)
    for code, question in prompts.PRACTICE_FALLBACK.items():
        assert "\n" not in question and question.strip() == question and len(question) < 60, code
        assert question.endswith(("?", "？")) and " ?" not in question, code
    assert prompts.PRACTICE_FALLBACK["en"] == "Can you tell me more about that?"


def test_every_language_has_its_words_that_ask_for_a_code_or_a_password() -> None:
    """initiative.check_text refuses her own message that asks for one, in any of the 11 languages."""
    assert set(language.SECRET_WORDS) == set(CODES)
    assert all(language.SECRET_WORDS[code] for code in CODES)
