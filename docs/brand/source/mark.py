"""The e. mark: a lower-case e in ink and an orange full stop inside a white circle (on a 100 x 100 grid).

It is enable4all's own mark, Estia's too: `../estia-mark.svg` is enable4all's file byte for byte (enable4all-hub,
public/brand/enable4all-mark.svg). This script draws nothing itself: it takes the circle and the letters from that
file, for the Signal avatar here and for the lockup (lockup.py). Standard library only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from palette import WHITE

MARK = Path(__file__).resolve().parent.parent / "estia-mark.svg"


def _inner() -> str:
    """Everything inside the mark's <svg> element: the circle, its hairline edge, the e and the full stop."""
    found = re.fullmatch(r"\s*<svg\b[^>]*>(.*)</svg>\s*", MARK.read_text(encoding="utf-8"), re.DOTALL)
    if found is None:
        raise SystemExit(f"{MARK.name} is not a single <svg> element")
    return found.group(1)


def mark_group() -> str:
    """The circle (r 46), its hairline edge and the e., as SVG elements on the 100 grid."""
    return _inner()


def letters() -> str:
    """The e and the full stop alone (their paths), centred on (50, 50)."""
    return "".join(re.findall(r"<path\b[^>]*/>", _inner()))


def mark_svg() -> str:
    """The mark itself: enable4all's file as it is."""
    return MARK.read_text(encoding="utf-8")


def avatar_svg(size: int = 512) -> str:
    """The Signal avatar: full-bleed white (Signal crops it round) with the e. in the middle, as big as in the mark."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">'
        f'<rect width="100" height="100" fill="{WHITE}"/>{letters()}</svg>'
    )


if __name__ == "__main__":
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    (out / "mark.svg").write_text(mark_svg(), encoding="utf-8")
    (out / "avatar.svg").write_text(avatar_svg(), encoding="utf-8")
    print("written")
