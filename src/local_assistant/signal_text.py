"""Signal's text styles: simple markup in a text becomes plain text plus Signal's style ranges, at send time.

Signal shows Markdown as typed ("**bold**" with its asterisks). It has its own styles (BOLD, ITALIC, STRIKETHROUGH,
MONOSPACE, SPOILER), sent beside the text as ranges: signal-cli's `textStyle`, "start:length:STYLE", start and length
in UTF-16 code units, the unit Signal's apps count in (an emoji above U+FFFF counts 2, a variation selector 1 more).
`styled` turns the markup code and Gemma write into those ranges in one place, `App._deliver`, which every text goes
out through. The stored text keeps its markup: the history, the search and a resend after a restart see what was
written, and a resend converts again. `spoken` is the same reading for speech: no markers, struck words left out.

Converted, only when the markers are clear:
- **bold** and ~~strike~~: on one line, the inner text not starting or ending with a space nor ending with a slash or
  a backslash; the opening marker not after a Latin letter, a digit, a slash, a backslash, an underscore or another
  marker, the closing one not before them ("2**3", "x**2", "src/**/*.py" and "**/node_modules/**" stay as they are;
  Chinese or Japanese text right beside the markers is fine, those scripts have no spaces);
- `code` as monospace: one backtick on each side, not inside a word ("don`t" stays);
- a fenced block (closed by its own ``` line): its lines monospace, the fence lines dropped;
- a "# heading" line (one to six #, then a space): the line in bold, without the #;
- "- " or "* " at a line's start: "• ";
- *italic*, only with clear edges: the opening * at the line's start or after a space, a dash, an opening bracket or a
  quote, the closing * before a space, the line's end or punctuation, a letter or digit between them, no slash or
  backslash right before the closing one. Never inside a word ("f*ck", "2*3", "*.txt", Japanese without spaces): a
  lone * is too often a footnote, a glob or a product.
Never converted: underscores (_x_ and __x__: snake_case, __init__ and file names use them), a web address (every
character of it stays, markers too; punctuation right after it is the sentence's), a run of three or more markers,
markers across lines, a lone or unbalanced marker, numbered lists, tables, quotes and rules (---). SPOILER is never
made. A line where a dropped marker would sit inside a web or mail address of the result ("**evil**.com",
"bob@**x**.org") goes out as typed: the link guards read the text with its markers, so markup must never join a link
they saw as two pieces (`unmarked`: the reading that joins them all, for the guards).

A text over 2000 UTF-8 bytes goes out from signal-cli as its first 2000 bytes plus the whole text as an attachment,
with the ranges unchanged; how the apps style past that first piece is untested, so only ranges inside it are sent.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import NamedTuple

STYLES = frozenset({"BOLD", "ITALIC", "STRIKETHROUGH", "MONOSPACE", "SPOILER"})  # signal-cli 0.14.9's names
BODY_BYTES = 2000  # signal-cli's ServiceConfig.MAX_MESSAGE_SIZE_BYTES: a longer text's body is its first piece

_Span = tuple[int, int, str]  # start, end (exclusive), style: in code points of the plain text
_Edge = Callable[[str, int, int], bool]  # whether the run of markers at an index may open (or close) a pair

_FENCE_OPEN = re.compile(r"[ \t]*```[\w+#.-]*[ \t]*")
_FENCE_CLOSE = re.compile(r"[ \t]*```[ \t]*")
_HEADING = re.compile(r"#{1,6}[ \t]+(?=\S)")
_HEADING_TAIL = re.compile(r"(?:[ \t]+#+)?[ \t]*$")
_ITEM = re.compile(r"([ \t]*)[-*][ \t]+(?=\S)")
_RULE = re.compile(r"[ \t]*(?:[-*_][ \t]*){3,}")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_URL_TAIL = frozenset("*~`.,;:!?)]}'\"")  # after a link: markers and the sentence's punctuation, never the link's
# in the result: what a phone may make tappable, a web address (a scheme or www., or a name on any ending of two
# letters or more, with or without a path) or a mail address; its trailing punctuation is the sentence's
_LINKISH = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|(?<![\w.-])(?:[^\W_][\w-]*\.)+(?:[a-z]{2,}|xn--[\w-]+)(?![\w-])(?:/\S*)?",
    re.IGNORECASE,
)
MARKERS = frozenset("*~`")  # the inline markers `styled` may drop
_ENTRY = re.compile(r"(\d+):(\d+):([A-Z]+)")
_ASCII_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_NOT_BESIDE = _ASCII_WORD | frozenset("*~`/\\_")  # never right outside a ** or ~~ pair
_ITALIC_AFTER = frozenset("([{\"'“‘«¿¡-–—")  # an italic * may open after these, a space or the line's start
_ITALIC_BEFORE = frozenset(".,;:!?)]}\"'”’»…-–—")  # and close before these, a space or the line's end
_PATH_END = frozenset("/\\")  # never right before a closing marker: "**/node_modules/**" is a glob


class Styled(NamedTuple):
    text: str  # what Signal shows
    styles: tuple[str, ...]  # signal-cli's textStyle entries, "start:length:STYLE" in UTF-16 code units


def styled(markup: str) -> Styled:
    """The text as Signal shows it, and its style ranges (none for plain text)."""
    lines = markup.split("\n")
    out: list[str] = []
    spans: list[_Span] = []
    at = 0  # where the next line starts in the plain text

    def put(line: str, line_spans: Sequence[_Span]) -> None:
        nonlocal at
        out.append(line)
        spans.extend((at + start, at + end, style) for start, end, style in line_spans)
        at += len(line) + 1

    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE_OPEN.fullmatch(line):
            close = next((j for j in range(index + 1, len(lines)) if _FENCE_CLOSE.fullmatch(lines[j])), None)
            if close is not None:
                first = at
                for body in lines[index + 1 : close]:
                    put(body, ())
                if "".join(lines[index + 1 : close]).strip():
                    spans.append((first, at - 1, "MONOSPACE"))
                index = close + 1
                continue
        put(*_line(line))
        index += 1
    text = "\n".join(out)
    return Styled(text, _ranges(text, spans))


def plain(markup: str) -> str:
    """The text as Signal shows it, markers removed."""
    return styled(markup).text


def unmarked(text: str) -> str:
    """The text with every marker (*, ~, `) left out: it joins whatever markup splits, also where `styled` keeps the
    markers, so a check that reads it as well as the text sees every link or word that markup could hide."""
    return "".join(char for char in text if char not in MARKERS)


def valid(text: str, styles: Sequence[str]) -> bool:
    """Whether signal-cli takes every entry (its syntax and style names) and each range lies inside the text; one bad
    entry fails the whole send in signal-cli, so the caller sends the text without them."""
    size = _units(text)[-1]
    for entry in styles:
        match = _ENTRY.fullmatch(entry)
        if match is None or match.group(3) not in STYLES:
            return False
        start, length = int(match.group(1)), int(match.group(2))
        if length < 1 or start + length > size:
            return False
    return True


def spoken(text: str) -> str:
    """Text to be said aloud: the inline markers gone and struck-through words left out (crossed out: not meant).
    Line starts (#, -, *) stay for the caller (voice.spoken_text reads headings and list items as sentences)."""
    lines = []
    for line in text.split("\n"):
        said, spans = _inline(line)
        for start, end in sorted(((s, e) for s, e, style in spans if style == "STRIKETHROUGH"), reverse=True):
            said = said[:start] + said[end:]
        lines.append(said)
    return "\n".join(lines)


def _line(line: str) -> tuple[str, list[_Span]]:
    """One line outside a fence: a rule stays, a heading becomes a bold line, a list start a bullet."""
    if _RULE.fullmatch(line):
        return line, []
    heading = _HEADING.match(line)
    if heading:
        text, spans = _inline(_HEADING_TAIL.sub("", line[heading.end() :]))
        return text, [*spans, (0, len(text), "BOLD")]
    item = _ITEM.match(line)
    if item:
        lead = f"{item.group(1)}• "
        text, spans = _inline(line[item.end() :])
        return lead + text, [(start + len(lead), end + len(lead), style) for start, end, style in spans]
    return _inline(line)


def _inline(line: str) -> tuple[str, list[_Span]]:
    """Code, bold, strike and italics on one line; links and code keep every character."""
    guarded = [False] * len(line)
    for match in _URL.finditer(line):
        end = match.end()
        while end > match.start() and line[end - 1] in _URL_TAIL:  # "**https://x.com**.": not the link's
            end -= 1
        guarded[match.start() : end] = [True] * (end - match.start())
    drop: set[int] = set()
    spans: list[_Span] = []
    for start, end in _pairs(line, "`", 1, guarded, _code_opens, _code_closes):
        guarded[start:end] = [True] * (end - start)  # nothing inside code is markup
        spans.append((start, end, "MONOSPACE"))
        drop.update((start - 1, end))
    for marker, style in (("*", "BOLD"), ("~", "STRIKETHROUGH")):
        for start, end in _pairs(line, marker, 2, guarded, _pair_opens, _pair_closes):
            spans.append((start, end, style))
            drop.update((start - 2, start - 1, end, end + 1))
    for start, end in _pairs(line, "*", 1, guarded, _italic_opens, _italic_closes, word=True):
        spans.append((start, end, "ITALIC"))
        drop.update((start - 1, end))
    if not drop:
        return line, []
    shift = [0]
    for index in range(len(line)):
        shift.append(shift[-1] + (index not in drop))
    text = "".join(char for index, char in enumerate(line) if index not in drop)
    cuts = {shift[index] for index in drop}  # where each dropped marker stood in the result
    if any(start < cut < end for start, end in _links(text) for cut in cuts):
        return line, []  # "**evil**.com" would become a link the guards never saw whole: as typed
    return text, [(shift[start], shift[end], style) for start, end, style in spans]


def _links(text: str) -> list[tuple[int, int]]:
    """Where a phone may see a web or mail address in the text, without the punctuation after it."""
    found = []
    for match in _LINKISH.finditer(text):
        end = match.end()
        while end > match.start() and text[end - 1] in _URL_TAIL:
            end -= 1
        found.append((match.start(), end))
    return found


def _pairs(
    line: str,
    marker: str,
    size: int,
    guarded: list[bool],
    opens: _Edge,
    closes: _Edge,
    *,
    word: bool = False,
) -> list[tuple[int, int]]:
    """The inner text of each pair: runs of exactly `size` markers outside links and code, each opener closed by the
    next run when that run can close (else that run may open the next pair); `word`: a letter or digit inside."""
    runs = [
        match.start()
        for match in re.finditer(re.escape(marker) + "+", line)
        if len(match.group()) == size and not guarded[match.start()]
    ]
    found: list[tuple[int, int]] = []
    index = 0
    while index < len(runs) - 1:
        start, end = runs[index] + size, runs[index + 1]
        inner = line[start:end]
        filled = any(char.isalnum() for char in inner) if word else bool(inner.strip())
        if opens(line, runs[index], size) and closes(line, end, size) and filled:
            found.append((start, end))
            index += 2
        else:
            index += 1
    return found


def _before(line: str, at: int) -> str:
    return line[at - 1] if at > 0 else ""


def _after(line: str, at: int, size: int) -> str:
    return line[at + size] if at + size < len(line) else ""


def _code_opens(line: str, at: int, size: int) -> bool:
    return _before(line, at) not in _ASCII_WORD  # "" (the line's start) is in no set


def _code_closes(line: str, at: int, size: int) -> bool:
    return _after(line, at, size) not in _ASCII_WORD


def _pair_opens(line: str, at: int, size: int) -> bool:
    after = _after(line, at, size)
    return _before(line, at) not in _NOT_BESIDE and after != "" and not after.isspace()


def _pair_closes(line: str, at: int, size: int) -> bool:
    before = _before(line, at)
    return not before.isspace() and before not in _PATH_END and _after(line, at, size) not in _NOT_BESIDE


def _italic_opens(line: str, at: int, size: int) -> bool:
    before, after = _before(line, at), _after(line, at, size)
    return (before == "" or before.isspace() or before in _ITALIC_AFTER) and after != "" and not after.isspace()


def _italic_closes(line: str, at: int, size: int) -> bool:
    before, after = _before(line, at), _after(line, at, size)
    return (
        not before.isspace() and before not in _PATH_END and (after == "" or after.isspace() or after in _ITALIC_BEFORE)
    )


def _units(text: str) -> list[int]:
    """UTF-16 code units before each code point of `text`, and the total last."""
    units = [0]
    for char in text:
        units.append(units[-1] + (2 if ord(char) > 0xFFFF else 1))
    return units


def _body_units(text: str) -> int | None:
    """UTF-16 units in the first piece signal-cli sends of a text over BODY_BYTES (whole characters), else None."""
    if len(text.encode("utf-8", "surrogatepass")) <= BODY_BYTES:
        return None
    used = units = 0
    for char in text:
        used += len(char.encode("utf-8", "surrogatepass"))
        if used > BODY_BYTES:
            break
        units += 2 if ord(char) > 0xFFFF else 1
    return units


def _ranges(text: str, spans: Sequence[_Span]) -> tuple[str, ...]:
    """The spans as signal-cli's entries: one style's overlapping or touching spans merged, sorted by start."""
    units = _units(text)
    limit = _body_units(text)
    merged: list[_Span] = []
    for style in sorted({style for _, _, style in spans}):
        current: list[int] | None = None
        for start, end in sorted((s, e) for s, e, kind in spans if kind == style and e > s):
            if current is not None and start <= current[1]:
                current[1] = max(current[1], end)
                continue
            if current is not None:
                merged.append((current[0], current[1], style))
            current = [start, end]
        if current is not None:
            merged.append((current[0], current[1], style))
    entries = []
    for start, end, style in sorted(merged):
        first, last = units[start], units[end]
        if limit is None or last <= limit:
            entries.append(f"{first}:{last - first}:{style}")
    return tuple(entries)
