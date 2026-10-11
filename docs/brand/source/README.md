# Estia's logo and artwork

Estia's mark is the enable4all "e.": a lower-case e in ink and an orange full stop inside a white circle. `../estia-mark.svg` is enable4all's own file (enable4all-hub, `public/brand/enable4all-mark.svg`), byte for byte; nothing here redraws it. Its "e" and dot are outlines from Manrope ExtraBold (SIL Open Font License 1.1), as enable4all's brand notes say, so the file needs no font. The logo and the README artwork are drawn by these scripts:

- `mark.py`: takes the circle and the e. from `../estia-mark.svg`; writes the mark as it is and the Signal avatar (the e. on full-bleed white, which Signal crops round). Standard library only.
- `wordmark.py`: text as outlines.
- `lockup.py`: the mark beside the name, "estia" in lower case, with the line "A thinking partner living at home".
- `hero.py`: the stone garden whose stone is a Mac mini (unchanged from the first artwork).
- `palette.py`: the colours.

Palette: sumi ink `#1c1a17`, washi paper `#f2ede3`, night `#1b1a18`, white for the avatar, and brushed aluminium `#e4e6e8` → `#979ca2` for the Mac mini. The mark keeps enable4all's colours from its file: ink `#111210`, orange `#ff5c35` for the full stop, and a thin `#c9c8bd` edge that keeps the circle visible on a white page.

To rebuild them:

1. For the lockup only: put the font [Shippori Mincho B1](https://github.com/fontdasu/ShipporiMincho) SemiBold (SIL Open Font License 1.1) in a `fonts/` folder next to the scripts, named `ofl_shipporiminchob1_ShipporiMinchoB1-SemiBold.ttf`. The font is not included here.
2. Run the scripts:

```sh
uv run --no-project python mark.py                                          # needs no font
uv run --no-project --with fonttools --with uharfbuzz python lockup.py      # also hero.py
uv run --no-project --with resvg-py python render.py out/*.svg              # SVG to PNG
```

3. The scripts write to `out/` (not in git). Copy each render to the brand folder with the `estia-` prefix:
   `out/<name>.svg` and `out/<name>.png` become `../estia-<name>.svg` and `../estia-<name>.png`
   (for example `out/hero-light.svg` → `../estia-hero-light.svg`):

```sh
for f in out/*.svg out/*.png; do cp "$f" "../estia-$(basename "$f")"; done
```

`tests/test_brand.py` checks that the mark is still enable4all's file, and that `mark.py` and `hero.py` rebuild the committed mark, avatar and hero exactly.
