"""Logo lockups: the e. mark beside the name "estia" in lower case (outlined text), on paper and at night."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mark import mark_group
from palette import INK, NIGHT, PAPER
from wordmark import FONT, text_path

OUT = Path(__file__).parent / "out"


def horizontal(*, dark: bool, tagline: bool = True) -> str:
    """Mark (100 units) + gap + the name, its x-height centred on the mark, and the tagline below it."""
    fg, bg = (PAPER, NIGHT) if dark else (INK, PAPER)
    word, width = text_path("estia", FONT, 66, 122, 62)
    parts = [mark_group(), f'<path d="{word}" fill="{fg}"/>']
    tag_w = 0.0
    if tagline:
        tag, tag_w = text_path("A THINKING PARTNER LIVING AT HOME", FONT, 8.6, 124.5, 84, tracking=0.14)
        parts.append(f'<path d="{tag}" fill="{fg}" opacity="0.72"/>')
    total = 124 + max(width, tag_w) + 8
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total * 6:.0f}" height="600" viewBox="-4 0 {total + 4:.1f} 100">'
        f'<rect x="-4" width="{total + 8:.1f}" height="100" fill="{bg}"/>{"".join(parts)}</svg>'
    )


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for dark in (False, True):
        (OUT / f"logo-{'dark' if dark else 'light'}.svg").write_text(horizontal(dark=dark))
    print("written")
