"""Statements that several files make must say the same thing."""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE_LOGIN = (
    "Older Kasa devices need no login; newer Kasa firmware and Tapo devices need the TP-Link login (Tapo is untested)."
)


def prose(path: Path) -> str:
    """The file's words on one line: comment marks, list dashes and line breaks left out."""
    lines = (re.sub(r"^\s*(#+|-)?\s*", "", line) for line in path.read_text(encoding="utf-8").splitlines())
    return " ".join(line for line in lines if line)


@pytest.mark.parametrize(
    "name", ["scripts/tapo-discover.sh", "setup_assistant.md", "config.example.toml", "tools/tapo/helper.py"]
)
def test_the_device_login_is_explained_with_one_sentence(name: str) -> None:
    text = prose(ROOT / name)
    assert DEVICE_LOGIN in text
    assert "Tapo devices need the TP-Link login first" not in text and "Kasa devices need none" not in text


def test_the_readme_still_says_only_kasa_is_tested() -> None:
    assert "Only Kasa devices are tested." in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_says_estia_and_that_every_home_names_its_own() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Estia\n")
    assert "Name her what you like. Ours is called Tiffany." in readme
    assert "Estia listens, thinks and speaks in **one language at a time**." in readme
    assert 'name = "Estia"' in (ROOT / "config.example.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["README.md", "SETUP-WITH-YOUR-AI.md", "setup_assistant.md", "AGENTS.md"])
def test_the_docs_call_her_estia_and_tiffany_only_as_ours(name: str) -> None:
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        if "Tiffany" in line:
            assert "ours" in line.lower() or "Ours" in line, line


def test_the_notices_credit_the_fonts_and_name_no_brand_we_do_not_use() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "\nNot affiliated with Signal, Spotify, Google, Microsoft, Bose or TP-Link.\n" in readme
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    brand = (ROOT / "docs" / "brand" / "source" / "README.md").read_text(encoding="utf-8")
    for text in (notices, brand):
        assert "Manrope ExtraBold" in text and "Shippori Mincho B1" in text and "SIL Open Font License 1.1" in text


def test_the_tagline_is_a_thinking_partner_living_at_home() -> None:
    """The owner's tagline, the same in the README's opening line, the package and the logo's lockup."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "\n**A private AI thinking partner living at home, on your own Mac.** " in readme
    assert "thinking partner living at home, on your own Mac" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lockup = (ROOT / "docs" / "brand" / "source" / "lockup.py").read_text(encoding="utf-8")
    assert '"A THINKING PARTNER LIVING AT HOME"' in lockup


def test_every_link_names_the_new_repo() -> None:
    """The project lives at enable4all/estia-on-mmini; the old repo's name is gone from every tracked file."""
    old = "tiffany-on-" + "mmini"  # split, so this file does not name it either
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    spec = importlib.util.spec_from_file_location("privacy_scan", ROOT / "scripts" / "privacy_scan.py")
    assert spec and spec.loader
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    private = scan.load_skip_patterns(ROOT)  # a private fork's own notes (.privacy-scan-skip) may name the old repo
    for name in filter(None, tracked.decode().split("\0")):
        if scan.skipped(name, private):
            continue
        path = ROOT / name
        if path.is_file() and path.suffix not in {".png", ".ttf"}:
            assert old not in path.read_text(encoding="utf-8", errors="replace"), name
    clone = "git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia"
    for name in ("README.md", "setup_assistant.md"):
        assert clone in (ROOT / name).read_text(encoding="utf-8"), name
    advisories = "https://github.com/enable4all/estia-on-mmini/security/advisories/new"
    assert advisories in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
