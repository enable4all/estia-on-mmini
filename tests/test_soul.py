import os
from pathlib import Path

import pytest

from local_assistant.soul import SOUL_MAX_CHARS, load_soul


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
