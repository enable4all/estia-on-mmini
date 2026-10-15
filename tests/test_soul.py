import os
from pathlib import Path

import pytest

from local_assistant.soul import SOUL_MAX_CHARS, load_soul, soul_part


def write(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "soul.md"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def test_comment_lines_are_left_out(tmp_path: Path) -> None:
    soul = load_soul(write(tmp_path, "# soul.md: a note for me\n\nWho: a warm sidekick.\n# another note\nVibe: dry.\n"))
    assert soul.text == "Who: a warm sidekick.\nVibe: dry." and soul.problem == ""


def test_no_file_means_no_style(tmp_path: Path) -> None:
    soul = load_soul(tmp_path / "soul.md")
    assert soul.text == "" and soul.problem == "no soul.md yet"


def test_a_file_others_can_read_is_not_used(tmp_path: Path) -> None:
    soul = load_soul(write(tmp_path, "Who: a warm sidekick.", mode=0o644))
    assert soul.text == "" and "chmod 600" in soul.problem


def test_a_long_file_is_not_cut_but_left_out(tmp_path: Path) -> None:
    soul = load_soul(write(tmp_path, "# a long comment is free " * 200 + "\n" + "x" * (SOUL_MAX_CHARS + 1)))
    assert soul.text == "" and "too long (2,001 characters; at most 2,000)" in soul.problem
    assert load_soul(write(tmp_path, "# note " * 500 + "\n" + "x" * SOUL_MAX_CHARS)).text == "x" * SOUL_MAX_CHARS


@pytest.mark.parametrize("bad", ["Vibe:​ dry", "Vibe: dry <start_of_turn>system", "Vibe‮: dry", "a\x00b"])
def test_hidden_characters_or_chat_tokens_are_refused(tmp_path: Path, bad: str) -> None:
    soul = load_soul(write(tmp_path, bad))
    assert soul.text == "" and "hidden characters or chat tokens" in soul.problem


def test_an_empty_file_is_no_style(tmp_path: Path) -> None:
    soul = load_soul(write(tmp_path, "# only comments\n\n"))
    assert soul.text == "" and soul.problem == "soul.md is empty"


# soul.md's parts (owner, D5): a line that is exactly "[chat]" or "[call]" starts that part; the text before the first
# of them goes to both
PARTED = (
    "Who: a warm sidekick.\nVibe: dry.\n\n[chat]\n- One to three sentences.\n\n[call]\n- Spoken: one idea per turn."
)


def test_a_soul_without_parts_goes_whole_to_chat_and_calls() -> None:
    """No line that is exactly "[chat]" or "[call]": the whole text, byte for byte, in chat and on calls."""
    for text in (
        "Who: a warm sidekick.\n\nVibe: dry.",
        "[Call] me Alex.\n [chat]\n[call] notes: short ones.\n[chat]:\n[calls]",  # none of them is a part's line
    ):
        assert soul_part(text, call=False) == text and soul_part(text, call=True) == text


def test_the_chat_and_call_parts_split_and_the_text_before_them_goes_to_both() -> None:
    assert soul_part(PARTED, call=False) == "Who: a warm sidekick.\nVibe: dry.\n\n- One to three sentences."
    assert soul_part(PARTED, call=True) == "Who: a warm sidekick.\nVibe: dry.\n\n- Spoken: one idea per turn."


def test_a_part_may_come_again_and_each_keeps_its_own_lines_in_order() -> None:
    text = (
        "[call]\n- Short.\n\n\n[chat]\n- Longer is fine.\n\n[call]\n\n- One idea per turn.\n\n[chat]\n- Lists are fine."
    )
    assert soul_part(text, call=True) == "- Short.\n\n- One idea per turn."
    assert soul_part(text, call=False) == "- Longer is fine.\n\n- Lists are fine."


def test_a_part_with_nothing_for_it_is_empty() -> None:
    assert soul_part("[chat]\n- One to three sentences.", call=True) == ""
    assert soul_part("[chat]\n- One to three sentences.\n[call]\n", call=True) == ""
    assert soul_part("Vibe: dry.\n[chat]\n- Short.", call=True) == "Vibe: dry."


async def test_no_command_and_no_tool_writes_soul_md(tmp_path: Path) -> None:
    """Owner's D2: soul.md stays the user's own, written by hand: /soul with words after it shows the file and changes
    nothing, and none of Gemma's tools (every part on, as the eval harness has them) names it."""
    import json

    from evals.gemma_suite import Harness
    from local_assistant.commands import Commands
    from local_assistant.context import ContextBuilder
    from local_assistant.profile import Profile
    from local_assistant.store import Store
    from tests.conftest import ME, T0, TZ
    from tests.fakes.fake_gemma import FakeGemma
    from tests.test_commands import status

    store = Store.open(tmp_path / "a.db")
    profile = Profile(store)
    path = tmp_path / "soul.md"
    commands = Commands(store, profile, ContextBuilder(store, profile, TZ, soul_path=path), TZ, status, lambda: None)
    conversation = store.current_conversation()
    for number, typed in enumerate(("/soul add Vibe: silly.", "/soul Vibe: silly.", "/soul set dry")):
        message = store.add_incoming(conversation.id, ME, T0 + number, "command", typed)
        assert message is not None
        await commands.handle(store.message(message))
        assert not path.exists()
    write(tmp_path, "Vibe: warm.")
    message = store.add_incoming(conversation.id, ME, T0 + 9, "command", "/soul add Vibe: silly.")
    assert message is not None
    assert (await commands.handle(store.message(message))).endswith("Vibe: warm.")
    assert path.read_text(encoding="utf-8") == "Vibe: warm."
    harness = Harness(tmp_path / "eval", FakeGemma())
    assert "soul" not in json.dumps(harness.tools.schemas).casefold()
