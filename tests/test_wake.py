"""Her name in a call turn (`wake.says_name`), which ends standby on a call (owner, 18 Oct: after the check-in "lets
respond only if we hear the name"). Whisper writes a name as it hears it: "Tiffani", and in Russian, Japanese and
Korean in their own letters ("Тиффани", "ティファニー", "티파니"). A turn said to someone else, without it, must never
count; a word that only looks like it ("esta" for Estia) neither."""

import pytest

from local_assistant.wake import says_name


@pytest.mark.parametrize(
    "turn",
    [
        "Tiffany, what time is it?",
        "tiffany",
        "TIFFANY!",
        "Hey Tiffany, are you there?",
        "Okay, Tiffany. Next question.",
        "Tiffani, are you there?",  # Whisper's spellings
        "Tifany?",
        "Tiffanie, hello.",
        "Tiffany's still there?",
        "Тиффани, который час?",  # Russian
        "Тифани, ты здесь?",
        "ティファニー、今何時？",  # Japanese
        "ティファニーさん、聞こえる？",
        "티파니, 지금 몇 시야?",  # Korean
        "티파니야, 들려?",  # the Korean vocative
    ],
)
def test_tiffany_is_heard_in_every_script(turn: str) -> None:
    assert says_name(turn, "Tiffany")


@pytest.mark.parametrize(
    "turn",
    [
        "I was talking to my sister.",
        "Tiff, come here.",  # a short form is not her name
        "Stephanie is at the door.",
        "Can you pass the salt?",
        "",
        "Um, uh.",
        "Я сейчас приду.",
        "テスト中です。",
        "테스트 중이야.",
    ],
)
def test_a_turn_without_tiffany_is_not_her_name(turn: str) -> None:
    assert not says_name(turn, "Tiffany")


@pytest.mark.parametrize(
    "turn",
    [
        "Estia, are you there?",
        "Éstia?",
        "Hey estia.",
        "Эстия, привет.",
        "エスティア、聞こえる？",
        "에스티아, 들려?",
        "에스티아야.",
    ],
)
def test_estia_is_heard_in_every_script(turn: str) -> None:
    assert says_name(turn, "Estia")


@pytest.mark.parametrize(
    "turn",
    [
        "Esta noche vamos al cine.",  # Spanish: one letter from Estia
        "Está bien, gracias.",
        "Ostia, che freddo!",  # Italian
        "Testa questo.",
        "Есть время?",  # Russian "есть" sounds like the start of Estia
        "Это всё.",
    ],
)
def test_a_word_one_letter_from_a_short_name_is_not_her_name(turn: str) -> None:
    assert not says_name(turn, "Estia")


@pytest.mark.parametrize(("turn", "heard"), [("Mary Jane, hi!", True), ("Mary-Jane?", True), ("Maryjane.", True)])
def test_a_name_of_two_words_is_heard_whole(turn: str, heard: bool) -> None:
    assert says_name(turn, "Mary-Jane") is heard


def test_one_word_of_a_name_of_two_is_not_her_name() -> None:
    assert not says_name("Mary, come here.", "Mary-Jane")
    assert not says_name("Jane said so.", "Mary Jane")


def test_a_name_without_letters_is_never_heard() -> None:
    assert not says_name("Tiffany, hi", "")
    assert not says_name("Tiffany, hi", "123")


@pytest.mark.parametrize("turn", ["Sam, hi!", "サム、聞こえる？", "샘, 들려?"])
def test_a_short_name_is_heard_in_every_script(turn: str) -> None:
    assert says_name(turn, "Sam")


def test_a_short_name_with_another_vowel_is_missed() -> None:
    """Known limit: Russian writes Sam "Сэм" ("sem"), and a name under six letters must match exactly (Spanish "esta"
    must never count for Estia), so it is missed; the caller can hang up and call again."""
    assert not says_name("Сэм, ты тут?", "Sam")
