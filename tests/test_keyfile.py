import os
from pathlib import Path

from local_assistant.keyfile import read_keys

NAMES = ("BRAVE_API_KEY", "GOOGLE_MAPS_API_KEY")


def write(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "web.env"
    path.write_text(text)
    os.chmod(path, mode)
    return path


def test_only_the_asked_names_with_values(tmp_path: Path) -> None:
    path = write(tmp_path, '# note\nBRAVE_API_KEY=b\nOLLAMA_API_KEY=o\nGOOGLE_MAPS_API_KEY="g"\nEMPTY=\n')
    assert read_keys(path, NAMES) == {"BRAVE_API_KEY": "b", "GOOGLE_MAPS_API_KEY": "g"}


def test_a_file_others_can_read_or_a_missing_file_gives_nothing(tmp_path: Path) -> None:
    assert read_keys(write(tmp_path, "BRAVE_API_KEY=b\n", mode=0o644), NAMES) == {}
    assert read_keys(tmp_path / "missing.env", NAMES) == {}
