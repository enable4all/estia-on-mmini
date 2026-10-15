"""Signal avatars for a name other than Estia: its first letter, a to z, in lower case with Estia's orange full stop
("t." for ours, Tiffany), on full-bleed white (Signal crops it round). The letter is ink, the stop enable4all's orange,
as in Estia's e. Outlines from Shippori Mincho B1 SemiBold (SIL Open Font License 1.1), put in `fonts/` as for lockup.py: the
files need no font. One scale for every letter: the one at which "t." is 50 units tall, so a round letter (a, e, o) is
about 36, as tall as Estia's e.; each "x." is centred on its own ink (owner, 15 Oct: a small tool, letters a to z only,
the stop always orange)."""

from __future__ import annotations

import string
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONT = Path(__file__).parent / "fonts" / "ofl_shipporiminchob1_ShipporiMinchoB1-SemiBold.ttf"
INK = "#1c1a17"  # palette.py's sumi ink
ORANGE = "#ff5c35"  # enable4all's full stop, as in Estia's e.
WHITE = "#ffffff"
T_HEIGHT = 50.0  # "t." this tall sets the one scale


def shaped(text: str) -> list[tuple[str, float]]:
    """Each glyph of `text` and its x position, in font units, as HarfBuzz sets them (kerning included)."""
    font = hb.Font(hb.Face(hb.Blob.from_file_path(str(FONT))))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": True})
    order = TTFont(str(FONT), lazy=True).getGlyphOrder()
    placed, cursor = [], 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        placed.append((order[info.codepoint], cursor + pos.x_offset))
        cursor += pos.x_advance
    return placed


def draw(text: str, k: float, x: float, y: float, pens: list[object]) -> None:
    """Glyph i of `text` into pens[i], scaled by k (y flipped: SVG grows down), its origin at (x, y)."""
    glyphs = TTFont(str(FONT), lazy=True).getGlyphSet()
    for (name, at), pen in zip(shaped(text), pens, strict=True):
        glyphs[name].draw(TransformPen(pen, (k, 0, 0, -k, x + at * k, y)))


def ink_box(text: str) -> tuple[float, float, float, float]:
    box = BoundsPen(None)
    draw(text, 1.0, 0.0, 0.0, [box] * len(text))
    assert box.bounds is not None
    return box.bounds


def scale() -> float:
    _, y0, _, y1 = ink_box("t.")
    return T_HEIGHT / (y1 - y0)


def avatar(letter: str, k: float, size: int = 512) -> str:
    """`letter` and the stop, the pair's ink centred on (50, 50) of a 100 x 100 grid."""
    text = f"{letter}."
    x0, y0, x1, y1 = ink_box(text)
    ox, oy = 50 - (x0 + x1) / 2 * k, 50 - (y0 + y1) / 2 * k
    pens = [SVGPathPen(None), SVGPathPen(None)]
    draw(text, k, ox, oy, pens)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">'
        f'<rect width="100" height="100" fill="{WHITE}"/>'
        f'<path d="{pens[0].getCommands()}" fill="{INK}"/><path d="{pens[1].getCommands()}" fill="{ORANGE}"/></svg>'
    )


if __name__ == "__main__":
    out = Path(__file__).parent / "out" / "letters"
    out.mkdir(parents=True, exist_ok=True)
    k = scale()
    for letter in string.ascii_lowercase:
        (out / f"{letter}.svg").write_text(avatar(letter, k))
    print(f"written {len(string.ascii_lowercase)} letters to {out}")
