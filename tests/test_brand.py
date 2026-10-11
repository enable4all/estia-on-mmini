"""The brand scripts in docs/brand/source rebuild the committed artwork (only those that need no font or library)."""

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BRAND = Path(__file__).resolve().parent.parent / "docs" / "brand"
# enable4all's "e." mark (enable4all-hub, public/brand/enable4all-mark.svg), Estia's mark: kept byte for byte
ENABLE4ALL_MARK_SHA256 = "e901cac81c3902c80a960c00b5a6d190d4f5120690d07aca5352223f4907f275"


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_hero_runs_on_a_fresh_clone_and_rebuilds_the_committed_hero(tmp_path: Path, variant: str) -> None:
    """out/ is not in git: hero.py makes it itself, also when it runs first or alone."""
    for name in ("hero.py", "palette.py"):
        shutil.copy2(BRAND / "source" / name, tmp_path / name)
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "hero.py")], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    built = (tmp_path / "out" / f"hero-{variant}.svg").read_bytes()
    assert built == (BRAND / f"estia-hero-{variant}.svg").read_bytes()


def test_the_mark_is_enable4alls_e_mark_byte_for_byte() -> None:
    mark = (BRAND / "estia-mark.svg").read_bytes()
    assert hashlib.sha256(mark).hexdigest() == ENABLE4ALL_MARK_SHA256
    assert b'fill="#ff5c35"' in mark  # the orange full stop


def test_mark_py_rebuilds_the_mark_and_the_avatar_from_the_mark_file(tmp_path: Path) -> None:
    """The avatar is the e. from the mark file on full-bleed white; mark.py needs no font for it."""
    (tmp_path / "source").mkdir()
    for name in ("mark.py", "palette.py"):
        shutil.copy2(BRAND / "source" / name, tmp_path / "source" / name)
    shutil.copy2(BRAND / "estia-mark.svg", tmp_path / "estia-mark.svg")
    result = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "source" / "mark.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = tmp_path / "source" / "out"
    assert (out / "mark.svg").read_bytes() == (BRAND / "estia-mark.svg").read_bytes()
    avatar = (out / "avatar.svg").read_bytes()
    assert avatar == (BRAND / "estia-avatar.svg").read_bytes()
    assert b"<circle" not in avatar and b'<rect width="100" height="100" fill="#ffffff"/>' in avatar
    paths = re.findall(rb"<path\b[^>]*/>", (BRAND / "estia-mark.svg").read_bytes())
    assert len(paths) == 2 and all(path in avatar for path in paths)  # the e and the full stop, unchanged


def test_only_estias_artwork_is_left() -> None:
    names = sorted(path.name for path in BRAND.iterdir() if path.is_file())
    assert names == [
        f"estia-{name}.{ext}"
        for name in ("avatar", "hero-dark", "hero-light", "logo-dark", "logo-light", "mark")
        for ext in ("png", "svg")
    ]
