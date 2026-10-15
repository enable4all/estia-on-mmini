"""What she can do in this install (owner, 14 Oct): code's list of the parts that are on, one plain line and one example
each, with the levels and the current one where a part has levels. Her rules carry it and /help shows it word for
word; a part that is off is left out."""

from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts
from local_assistant.babble import OFFERED
from local_assistant.brain import Brain
from local_assistant.context import ContextBuilder
from local_assistant.documents import shown_folder
from local_assistant.profile import Profile
from local_assistant.store import Store
from local_assistant.tools import Tools
from tests.conftest import ME, T0, TZ
from tests.fakes.fake_gemma import FakeGemma, text
from tests.test_brain import no_fetch

EVERYTHING: dict[str, Any] = {
    "voice": "auto",
    "calls": "only speech",
    "mail": True,
    "calendar": ("Google",),
    "digest": "09:15",
    "digest_calendar": True,
    "reminders": True,
    "wake_calls": True,
    "initiative": False,
    "web": True,
    "maps": True,
    "device": "living room lamp",
    "music": True,
    "projects": "summary",
    "documents": "~/Estia-KB",
    "security": "hardened",
}


def named(lines: list[str]) -> list[str]:
    """Each line's part: the words before its first colon, without the bullet."""
    return [line.removeprefix("• ").split(":", 1)[0] for line in lines]


def test_a_full_install_names_every_part_once_with_one_example_each() -> None:
    lines = prompts.capabilities(**EVERYTHING)
    assert named(lines) == [
        "Chat and memory",
        "Links and YouTube videos",
        "Photos",
        "Voice notes",
        "Calls",
        "Mail",
        "Calendar",
        "Morning digest",
        "Reminders and wake-up calls",
        "Messages I start",
        "Lights and plugs",
        "Music",
        "Web search",
        "Places and routes",
        "Projects",
        "Documents",
        "The Mac",
        "Language practice",
        "Security",
    ]
    for line in lines:
        assert line.startswith("• ") and line.count("Example: ") == 1, line
        assert "\n" not in line and "default" not in line.lower(), line


def test_a_bare_install_names_only_what_is_always_there() -> None:
    lines = prompts.capabilities()
    assert named(lines) == [
        "Chat and memory",
        "Links and YouTube videos",
        "Photos",
        "Messages I start",
        "The Mac",
        "Language practice",
        "Security",
    ]
    for off in (
        "Mail", "Calendar", "Calls", "Voice", "Music", "Spotify", "Projects", "Documents", "/docs", "Web", "Places",
        "Lights",
    ):  # fmt: skip
        assert not any(off in line for line in lines), off


@pytest.mark.parametrize(
    ("settings", "words"),
    [
        ({"voice": "off"}, "Voice answers: auto, on or off (now: off)"),
        ({"calls": "any sound"}, "Interrupt: only speech or any sound (now: any sound)"),
        ({"calls": "only speech"}, "Interrupt: only speech or any sound (now: only speech)"),
        ({"initiative": True}, "On or off (now: on). Example: /quiet pauses them"),
        ({"initiative": False}, "On or off (now: off). Example: /initiative on"),
        ({"projects": "docs"}, "Levels: off, summary, docs or code (now: docs)"),
        ({"projects": "summary", "project_levels": ("code",)}, "(now: summary; some projects: code)"),
        ({"documents": "~/Estia-KB"}, "On or off (now: on, folder ~/Estia-KB)"),
        ({"security": "hardened"}, "Levels: hardened or nuclear (now: hardened)"),
        ({"security": "nuclear"}, "Levels: hardened or nuclear (now: nuclear)"),
        ({"security": ""}, "Levels: hardened or nuclear (now: not hardened yet)"),
        ({"security": None}, "Levels: hardened or nuclear (now: not read yet)"),
    ],
)
def test_a_part_with_levels_names_them_and_the_current_one(settings: dict[str, Any], words: str) -> None:
    lines = prompts.capabilities(**settings)
    assert any(words in line for line in lines), lines


def test_wake_up_calls_and_the_digest_follow_what_is_set_up() -> None:
    only_texts = prompts.capabilities(reminders=True)
    assert any(line.startswith("• Reminders: ") for line in only_texts)
    assert not any("wake-up" in line for line in only_texts)
    mail_only = prompts.capabilities(mail=True, digest="08:30")
    digest = next(line for line in mail_only if line.startswith("• Morning digest: "))
    assert "the mail that needs a reply, every day at 08:30" in digest and "calendar" not in digest
    assert not any(line.startswith("• Morning digest") for line in prompts.capabilities(mail=True))


def test_the_examples_name_her_host_and_a_device_of_this_install() -> None:
    lines = prompts.capabilities(host="the Mini", device="hall plug")
    assert (
        '• The Mini: its health in a few lines; real problems come unasked. Example: "how is the Mini doing?"' in lines
    )
    assert any('Example: "turn on hall plug"' in line for line in lines)


def test_documents_say_what_they_answer_from_where_a_sent_pdf_goes_and_the_folder() -> None:
    """The documents merge: an extra like Projects, so only with [documents]; its state names the folder."""
    lines = prompts.capabilities(host="the Mini", documents="~/Estia-KB")
    line = next(line for line in lines if line.startswith("• Documents: "))
    assert line == (
        "• Documents: answers from your PDFs that name the document and the page; PDFs sent on Signal are saved on the "
        "Mini, and /docs lists them. On or off (now: on, folder ~/Estia-KB). "
        'Example: "what does the boiler manual say about the pressure?"'
    )


def test_the_documents_example_is_no_question_an_eval_asks() -> None:
    """Review of 14 Oct: the list sits in her rules, so its example must not be the question a document case asks
    (document_page_named asks about pets in the lease), or the case would partly test the example."""
    from evals import gemma_suite

    line = next(line for line in prompts.capabilities(documents="~/Estia-KB") if "• Documents: " in line)
    example = line.split("Example: ", 1)[1].lower()
    for case in ("document_page_named", "document_invents_nothing", "document_injected"):
        assert case in {c.name for c in gemma_suite.CASES}, case
    for word in ("lease", "pets", "parking", "bank", "letter"):
        assert word not in example, word


@pytest.mark.parametrize(
    ("folder", "shown"),
    [
        ("/Users/alex/Estia-KB", "~/Estia-KB"),  # the owner's home, as the docs name it
        ("/Users/example/Papers", "~/Papers"),
        ("/Volumes/Data/Estia-KB", "/Volumes/Data/Estia-KB"),  # elsewhere: written out
        ("/Users/Shared/Estia-KB", "/Users/Shared/Estia-KB"),  # no one's home
        ("/var/estia/Estia-KB", "/var/estia/Estia-KB"),
    ],
)
def test_the_documents_folder_is_shown_from_the_owners_home(folder: str, shown: str) -> None:
    assert shown_folder(Path(folder)) == shown


def test_help_names_docs_with_documents_on_or_off() -> None:
    """/docs answers either way: with documents off it says how they are turned on (prompts.DOCS_OFF)."""
    for lines in (prompts.capabilities(), prompts.capabilities(documents="~/Estia-KB")):
        assert "\n/docs - your documents, newest first (/docs send N sends one back to you)\n" in prompts.help_text(
            lines
        )


def test_the_chat_line_says_what_is_kept_as_it_is() -> None:
    """Idle learning keeps facts too, each change told in a note: "only when you ask" would be untrue."""
    chat = prompts.capabilities()[0]
    assert "only when you ask" not in chat
    assert "every fact kept about you is told in a note (/profile lists them)" in chat


def test_the_calendar_line_names_each_service_once_read_only() -> None:
    """Each calendar's service by name (iCloud for a CalDAV calendar at Apple), in the config's order."""
    lines = prompts.capabilities(calendar=("Google", "iCloud", "Outlook"))
    calendar = next(line for line in lines if line.startswith("• Calendar: "))
    assert (
        calendar == '• Calendar: Google, iCloud and Outlook, read only, never changed. Example: "what\'s on tomorrow?"'
    )
    only = next(line for line in prompts.capabilities(calendar=("Fastmail",)) if line.startswith("• Calendar: "))
    assert only.startswith("• Calendar: Fastmail, read only, never changed.")


def test_mail_says_every_change_it_never_makes() -> None:
    mail = next(line for line in prompts.capabilities(mail=True) if line.startswith("• Mail: "))
    assert "read only, never sent, moved, deleted or marked" in mail


def test_without_calls_the_security_line_names_no_call() -> None:
    """Off parts are never named: /lock's line says what it stops without naming calls."""
    security = prompts.capabilities(security="hardened")[-1]
    assert security.startswith("• Security: /lock stops everything at once, until /unlock from the same device.")
    assert "call" not in security


def test_the_practice_line_counts_the_languages_babble_offers() -> None:
    practice = next(line for line in prompts.capabilities() if line.startswith("• Language practice"))
    assert f"one of {len(OFFERED)} languages" in practice.replace("eight", "8")


def test_help_shows_the_list_word_for_word_above_the_commands() -> None:
    lines = prompts.capabilities(**EVERYTHING)
    shown = prompts.help_text(lines)
    assert shown == "\n".join([prompts.CAPABILITIES_HEADER, *lines]) + "\n\n" + prompts.HELP
    assert prompts.help_text([]) == prompts.HELP


def test_her_rules_carry_the_same_lines_before_safety() -> None:
    lines = prompts.capabilities(**EVERYTHING)
    rules = prompts.rules(name="Estia", calls=True, reminders=True, capabilities=lines)
    block = "\n".join(lines)
    assert block in rules
    assert rules.index(prompts.CAPABILITIES_RULE) < rules.index(block) < rules.index("\n\nSafety:\n")
    assert rules.endswith(prompts.rules(name="Estia").split("\n\nSafety:\n", 1)[1])  # safety stays last


def test_without_a_list_the_rules_stay_as_they_were() -> None:
    before = prompts.rules(name="Estia", calls=True, reminders=True)
    assert prompts.CAPABILITIES_RULE not in before
    assert prompts.rules(name="Estia", calls=True, reminders=True, capabilities=()) == before


def test_practice_rules_carry_no_list() -> None:
    lines = prompts.capabilities(**EVERYTHING)
    practice = prompts.rules(name="Estia", practice=("nl", "A2"), capabilities=lines)
    assert prompts.CAPABILITIES_RULE not in practice and lines[0] not in practice


# On a call she answers in a few spoken sentences, never a list: the list in her rules cost her end_call on a goodbye
# (call_goodbye 24 of 30 without it, 9 of 30 with it, 14 Oct), so a call's rules leave it out, as they were before.


def test_without_the_list_the_rules_are_as_before() -> None:
    lines = prompts.capabilities(**EVERYTHING)
    before = prompts.rules(name="Estia", calls=True, reminders=True)
    listed = prompts.rules(name="Estia", calls=True, reminders=True, capabilities=lines)
    assert prompts.without_capabilities(listed) == before
    assert prompts.without_capabilities(before) == before


async def test_a_call_turn_gets_her_rules_without_the_list(store: Store) -> None:
    lines = prompts.capabilities(**EVERYTHING)
    rules = prompts.rules(name="Estia", calls=True, reminders=True, capabilities=lines)
    profile = Profile(store)
    gemma = FakeGemma(text("Here is what I can do."), text("Bye!"))
    brain = Brain(gemma, ContextBuilder(store, profile, TZ, rules=rules), Tools(store, profile, TZ, no_fetch))
    conversation = store.current_conversation()
    typed = store.add_incoming(conversation.id, ME, T0, "text", "What can you do?")
    assert typed is not None
    await brain.answer(conversation, store.message(typed))
    assert gemma.calls[0]["messages"][0]["content"].startswith(rules)
    said = store.add_incoming(conversation.id, ME, T0 + 1, "text", f"{prompts.CALL_MARK} thanks, that's all")
    assert said is not None
    await brain.answer(conversation, store.message(said))
    system = gemma.calls[1]["messages"][0]["content"]
    assert system.startswith(prompts.without_capabilities(rules)) and lines[0] not in system


def test_the_lights_line_names_each_brand_once_as_the_calendar_line_its_services() -> None:
    """The list reads as one (owner, 14 Oct): the lights line names the brands set up, each once in the config's order,
    as the calendar line names its services; with Hue lights it says what they take. A `kasa` device is named by its
    maker, TP-Link: the brand covers Tapo too, and a Tapo install's list must never read as if Tapo were not set up."""
    kasa = '• Lights and plugs: TP-Link, switched by name, each by its own rule. Example: "turn on hall plug"'
    assert kasa in prompts.capabilities(device="hall plug", device_brands=("TP-Link",))
    assert kasa in prompts.capabilities(device="hall plug")  # a device's brand when left out, as in the config
    both = prompts.capabilities(device="hall plug", device_brands=("TP-Link", "Philips Hue", "TP-Link"))
    assert (
        "• Lights and plugs: TP-Link and Philips Hue, switched by name, each by its own rule. Hue lights: on, off, "
        'brightness and whites, no colours. Example: "turn on hall plug"'
    ) in both
    only_hue = prompts.capabilities(device="hall light", device_brands=("Philips Hue",))
    assert any(line.startswith("• Lights and plugs: Philips Hue, switched by name") for line in only_hue)
    lines = prompts.capabilities(calendar=("Google", "iCloud"), device="hall plug", device_brands=("TP-Link",))
    named = [line.split(", ", 1)[0] for line in lines if line.startswith(("• Calendar: ", "• Lights and plugs: "))]
    assert named == ["• Calendar: Google and iCloud", "• Lights and plugs: TP-Link"]
    assert not any("Kasa" in line or "Tapo" in line for line in both + lines)  # the maker, never one product line
