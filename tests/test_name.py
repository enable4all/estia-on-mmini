"""Every home names its own assistant (`name` in the config, default Estia): every text with her name takes it."""

import pytest

from local_assistant import prompts
from local_assistant.calls import is_goodbye, is_interruption, is_lone_stop
from local_assistant.config import DEFAULT_NAME
from local_assistant.language import CODES

LATIN = ("en", "es", "it", "pt", "nl", "pl", "de", "fr")  # her name only where the voice reads Latin letters


def every_text(name: str) -> str:
    """Every text that names her, for one name."""
    lines = [prompts.call_lines(code, name=name) for code in CODES]
    return "\n".join(
        [
            prompts.rules(name=name, calendar=True, calls=True, music=True, web=True, maps=True),
            prompts.welcome(name=name, mail=True, calls=True),
            prompts.signal_profile_name(name),
            prompts.BUSY.format(name=name),
            prompts.UNAVAILABLE.format(name=name),
            prompts.SUMMARY_RULES.format(name=name, words=300),
            prompts.CALL_SUMMARY_RULES.format(name=name),
            prompts.SOUL_HEADER.format(name=name),
            prompts.SETUP_WORD_MISSING.format(name=name, word="estia-1234", number="+306900000000", seconds=20),
            prompts.SETUP_DONE.format(name=name),
            prompts.SETUP_LANGUAGE.format(name=name),
            *(greeting for spoken in lines for greeting in spoken.greetings),
        ]
    )


def test_her_name_is_estia_by_default() -> None:
    assert DEFAULT_NAME == "Estia"
    assert prompts.rules(name=DEFAULT_NAME).startswith("You are Estia, a private personal assistant.")
    assert "Your name is Estia; the language model underneath is Google's Gemma" in prompts.rules(name=DEFAULT_NAME)
    assert prompts.welcome(name=DEFAULT_NAME).startswith("Hi, I'm Estia, your private thinking partner")
    assert prompts.signal_profile_name(DEFAULT_NAME) == "Estia (AI)"
    assert prompts.call_greetings(DEFAULT_NAME)[0] == "Hi, it's Estia. What's on your mind?"
    assert "Tiffany" not in every_text(DEFAULT_NAME)


def test_the_owners_tiffany_is_tiffany_everywhere() -> None:
    text = every_text("Tiffany")
    assert "Estia" not in text and "{name}" not in text and "<name>" not in text
    assert prompts.rules(name="Tiffany").startswith("You are Tiffany, a private personal assistant.")
    assert prompts.welcome(name="Tiffany").startswith("Hi, I'm Tiffany, your private thinking partner")
    assert prompts.signal_profile_name("Tiffany") == "Tiffany (AI)"
    assert prompts.call_greetings("Tiffany")[0] == "Hi, it's Tiffany. What's on your mind?"
    assert prompts.call_lines("nl", name="Tiffany").greetings == ("Hoi, met Tiffany. Wat kan ik voor je doen?",)
    assert prompts.spoken("nl", name="Tiffany").greeting == "Hoi, met Tiffany. Wat kan ik voor je doen?"
    assert prompts.BUSY.format(name="Tiffany").startswith("Tiffany is busy")


@pytest.mark.parametrize("code", LATIN)
def test_each_latin_greeting_says_the_configured_name(code: str) -> None:
    assert "Zoë" in prompts.spoken(code, name="Zoë").greeting
    assert "Zoë" in prompts.call_lines(code, name="Zoë").greetings[0]


def test_the_rules_take_the_name_and_the_host_apart() -> None:
    named = prompts.rules(name="Mary-Jane", host="the Mini")
    assert named.startswith("You are Mary-Jane, a private personal assistant. You run locally on the user's own Mac")
    assert "You can check the Mini's health" in named and "<" not in named.split("\n", 1)[0]


@pytest.mark.parametrize("name", ["Estia", "Tiffany", "Mary Jane"])
def test_her_name_counts_in_a_stop_or_a_goodbye_whatever_it_is(name: str) -> None:
    assert is_lone_stop(f"stop {name}", name=name) and is_lone_stop(f"wait {name.lower()}", name=name)
    assert is_goodbye(f"okay bye {name}", name=name) and is_goodbye(f"goodbye {name.lower()}", name=name)
    assert is_interruption(name.split()[0], name=name)


def test_another_name_is_no_stop_or_goodbye() -> None:
    assert not is_lone_stop("stop Tiffany", name="Estia")
    assert not is_goodbye("okay bye Tiffany", name="Estia")
    assert not is_interruption("Tiffany", name="Estia")
