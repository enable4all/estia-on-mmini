"""Text as SVG outlines: shaped with HarfBuzz (kerning, ligatures), outlines from the font (OFL fonts only)."""

from __future__ import annotations

from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.basePen import AbstractPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONTS = Path(__file__).parent / "fonts"
FONT = "ofl_shipporiminchob1_ShipporiMinchoB1-SemiBold.ttf"


def units_per_em(font_file: str) -> int:
    return int(TTFont(str(FONTS / font_file), lazy=True)["head"].unitsPerEm)


def draw_text(
    text: str, font_file: str, k: float, x: float, y: float, pen: AbstractPen, tracking: float = 0.0
) -> float:
    """Draw `text` into `pen`, `k` output units per font unit, baseline at (x, y); returns the advance width."""
    path = FONTS / font_file
    font = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": True})
    tt = TTFont(str(path), lazy=True)
    glyph_set = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    upem = tt["head"].unitsPerEm
    if getattr(pen, "glyphSet", glyph_set) is None:  # composite glyphs are drawn from their parts
        pen.glyphSet = glyph_set  # type: ignore[attr-defined]
    cursor = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        gx = x + (cursor + pos.x_offset) * k
        gy = y - pos.y_offset * k
        glyph_set[order[info.codepoint]].draw(TransformPen(pen, (k, 0, 0, -k, gx, gy)))
        cursor += pos.x_advance + tracking * upem
    return cursor * k


def text_path(
    text: str, font_file: str, size: float, x: float = 0.0, y: float = 0.0, tracking: float = 0.0
) -> tuple[str, float]:
    """An SVG path for `text` with its baseline at (x, y), `size` units tall per em; returns (path, advance width)."""
    pen = SVGPathPen(None)
    width = draw_text(text, font_file, size / units_per_em(font_file), x, y, pen, tracking)
    return pen.getCommands(), width
