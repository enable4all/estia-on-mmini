"""Render SVG files to PNG with resvg (full SVG support: masks, filters, any aspect ratio)."""

import sys
from pathlib import Path

import resvg_py

for name in sys.argv[1:]:
    src = Path(name)
    png = resvg_py.svg_to_bytes(svg_string=src.read_text())
    src.with_suffix(".png").write_bytes(bytes(png))
    print("rendered", src.with_suffix(".png").name)
