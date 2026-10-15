"""README / social-preview artwork (1280 x 640): a raked stone garden whose stone is a Mac mini, nothing else.

The mini is a simple flat shape seen from above. The rings around it are true offsets of its outline (the rake follows
the stone), so their spacing is even; the straight lines end at the outermost ring. Each rake line is a groove: a shadow
stroke with a highlight just below.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from palette import ALU_DARK, ALU_LIGHT, ALU_MID, GARDEN_DARK, GARDEN_LIGHT, RINGS_DARK, RINGS_LIGHT

W, H = 1280, 640
FIELD = (34.0, 34.0, 1246.0, 606.0)  # the gravel field: x0, y0, x1, y1
CX, CY = 640.0, 320.0  # the mini's footprint centre
HALF, R = 75.0, 20.0  # footprint half side and corner radius (a mini is about 12-14 % rounded)
GAP = 13.0
RINGS = 7


def rounded_rect_path(cx: float, cy: float, half: float, r: float) -> str:
    x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
    return (
        f"M{x0 + r:.1f},{y0:.1f} H{x1 - r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x1:.1f},{y0 + r:.1f} V{y1 - r:.1f} "
        f"A{r:.1f},{r:.1f} 0 0 1 {x1 - r:.1f},{y1:.1f} H{x0 + r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x0:.1f},{y1 - r:.1f} "
        f"V{y0 + r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x0 + r:.1f},{y0:.1f} Z"
    )


def half_width(dy: float, half: float, r: float) -> float | None:
    """Half the width of a rounded square (centred at 0) at height dy, or None outside it."""
    straight = half - r
    a = abs(dy)
    if a > half:
        return None
    if a <= straight:
        return half
    return straight + math.sqrt(max(r * r - (a - straight) ** 2, 0.0))


def wobble(x: float, phase: float) -> float:
    return 1.4 * math.sin(x / 190.0 + phase)  # the raker's hand: shared, slow, so the lines stay parallel


def line(x0: float, x1: float, y: float, phase: float) -> str:
    xs = [x0 + (x1 - x0) * i / max(1, int((x1 - x0) / 32)) for i in range(int((x1 - x0) / 32) + 1)]
    return "M" + " L".join(f"{x:.1f},{y + wobble(x, phase):.1f}" for x in xs)


def rake(rng: random.Random) -> list[str]:
    outer_half, outer_r = HALF + 18 + (RINGS - 1) * GAP + GAP, R + 18 + (RINGS - 1) * GAP + GAP
    paths = []
    phase = rng.uniform(0, math.tau)
    y = FIELD[1] + 14
    while y < FIELD[3] - 10:
        dx = half_width(y - CY, outer_half, outer_r)
        if dx is None:
            paths.append(line(FIELD[0] + 12, FIELD[2] - 12, y, phase))
        else:
            if CX - dx - (FIELD[0] + 12) > 4:
                paths.append(line(FIELD[0] + 12, CX - dx, y, phase))
            if FIELD[2] - 12 - (CX + dx) > 4:
                paths.append(line(CX + dx, FIELD[2] - 12, y, phase))
        y += GAP
    for i in range(RINGS):
        paths.append(rounded_rect_path(CX, CY, HALF + 18 + i * GAP, R + 18 + i * GAP))
    return paths


def mini(dark: bool) -> str:
    """The Mac mini seen straight from above, as a simple flat 2D shape: an aluminium-grey rounded square with a thin
    outline (no shadow, no shading, no marks). Its footprint is the one the rake rings are offset from."""
    x0, y0, side = CX - HALF, CY - HALF, 2 * HALF
    return (
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{side}" height="{side}" rx="{R}" fill="{ALU_MID}" '
        f'stroke="{ALU_DARK}" stroke-width="1.5"/>'
    )


def rgb(colour: str) -> str:
    """'#rrggbb' as feColorMatrix's three 0-1 fractions."""
    return "  ".join(f"0 0 0 0 {int(colour[i : i + 2], 16) / 255:.3f}" for i in (1, 3, 5))


def hero(*, dark: bool = False) -> str:
    """The garden: a sun-yellow rake on warm white (dark: on night), the rings around the Mac in rainbow colours,
    enable4all's orange first, each with a soft glint; the Mac mini in aluminium in the middle (owner, 15 Oct)."""
    rng = random.Random(5)
    garden, rings = (GARDEN_DARK, RINGS_DARK) if dark else (GARDEN_LIGHT, RINGS_LIGHT)
    paths = rake(rng)
    straight, around = paths[:-RINGS], paths[-RINGS:]
    defs = (
        '<defs><filter id="soft" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="7"/></filter>'
        f'<linearGradient id="top" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{ALU_LIGHT}"/>'
        f'<stop offset="0.6" stop-color="{ALU_MID}"/><stop offset="1" stop-color="{ALU_DARK}"/></linearGradient>'
        '<linearGradient id="edge" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8d9298"/>'
        '<stop offset="1" stop-color="#6c7177"/></linearGradient>'
        '<filter id="brushed" x="0" y="0" width="100%" height="100%">'
        '<feTurbulence type="fractalNoise" baseFrequency="0.003 0.8" numOctaves="2" seed="5"/>'
        '<feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.14 0"/>'
        '<feComposite in2="SourceGraphic" operator="in"/></filter>'
        '<filter id="sand" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="2"/>'
        f'<feColorMatrix values="{rgb(garden["sand"])}  0 0 0 0.16 0"/></filter></defs>'
    )

    def strokes(group: list[str], colour: str) -> str:
        joined = " ".join(group)
        return (
            f'<path d="{joined}" stroke="{garden["glint"]}" stroke-width="1.1" transform="translate(1.0 1.4)"/>'
            f'<path d="{joined}" stroke="{colour}" stroke-width="1.4"/>'
        )

    body = strokes(straight, garden["rake"]) + "".join(
        strokes([p], rings[i % len(rings)]) for i, p in enumerate(around)
    )
    fx0, fy0, fx1, fy1 = FIELD
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{defs}'
        f'<rect width="{W}" height="{H}" fill="{garden["ground"]}"/>'
        f'<rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" rx="6" filter="url(#sand)"/>'
        f'<rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" rx="6" fill="none" stroke="{garden["rake"]}" '
        'stroke-opacity="0.7" stroke-width="1.4"/>'
        f'<g fill="none" stroke-linecap="round">{body}</g>'
        f"{mini(dark)}"
        "</svg>"
    )


if __name__ == "__main__":
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    (out / "hero-light.svg").write_text(hero())
    (out / "hero-dark.svg").write_text(hero(dark=True))
    print("written")
