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
import unicodedata
from collections.abc import Callable, Sequence
from typing import NamedTuple

from local_assistant.web_endings import ENDINGS

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


# what Signal Desktop's link reading (linkify-it) also takes into a name though Python's \w does not: combining marks
# ("evil" with an accent written apart, "नमस्ते.com"), symbols ("C++.NET", "a=b.com", "a°.com"), private-use and
# unassigned characters; never those no host holds ("<", ">", "|", "^"). `_reading` writes each one outside ASCII as
# `_SIGN`, so the patterns stay small and fast; the ASCII ones are listed as they are.
_SIGN = "\ue000"
_PSEUDO = f"$+=`~{_SIGN}"
_LABEL = rf"(?:[^\W_]|[{_PSEUDO}])[\w\-{_PSEUDO}]*"  # a name's part: a letter, digit or one of _PSEUDO first


def _names(ending: str) -> re.Pattern[str]:
    """A name's host on `ending` (`_named` checks the ending afterwards), from its first part's start only, so the
    reading stays linear on one long word."""
    return re.compile(rf"(?<![\w.\-{_PSEUDO}])(?:{_LABEL}\.)+(?:{ending})(?![^\W_])", re.IGNORECASE)


# `unlinked`'s reading of a link, wider than `_LINKISH` (which keeps `styled`'s guard, and so her answers, as they are),
# in four patterns, each run on its own so a candidate one refuses never hides another's link:
# - always a link: any scheme with "//" wherever it starts ("1-https://", "²https://", "https%3A%2F%2F": Android and
#   Signal Desktop start one after any punctuation, iOS nearly anywhere), a mail address as Android reads one (its
#   parts may hold any character outside ASCII, the ending any letters), an address by its four numbers
_LOCAL = r"[^\s!\"#$&()*,/:;<=>?@\[\\\]^`{|}~\x00-\x1f\x7f]"
_DOMAIN = r"[^\s!\"#$%&'()*+,./:;<=>?@\[\\\]^`{|}~\x00-\x1f\x7f]"
_TOP = r"[^\s\x00-\x40\x5b-\x60\x7b-\x7f]"
_ALWAYS = re.compile(
    r"(?<![a-zA-Z0-9+.-])[a-zA-Z0-9+.-]*(?:://|%3[aA]%2[fF]%2[fF])\S*"
    rf"|(?<!{_LOCAL}){_LOCAL}+@{_DOMAIN}+(?:\.{_DOMAIN}+)*\.(?:[xX][nN]--[\w-]+|{_TOP}{{2,}})(?![a-zA-Z0-9])"
    r"|(?<![\w.-])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)(?::\d{1,5})?(?:/\S*)?"
)
# - www. (no ending checked, as Android and iOS link "www.evil.notatld") and the schemes iOS links without "//"
#   (macOS's NSDataDetector, 21 Oct: tel:, sms:, maps:, itms-services: and the others), case-insensitive as iOS reads
#   them ("Maps:q", "NEWS:x"), each when no ASCII letter or digit is right before it (`_free`); the structural net
#   (`netted`) is the guarantee past this list, so a scheme it misses still loses its colon between two letters
_WWW = re.compile(r"www\.(?=[^\s.])", re.IGNORECASE)
_NO_SLASHES = re.compile(
    r"(?:mailto|tel|sms|mms|callto|facetime|facetime-audio|maps|skype|sip|sips|xmpp|im|aim|jabber|radar|itms-services"
    r"|itms-apps|itms-appss|itms|itmss|webcal|news|feed|feeds|rdar|cid):(?=\S)",
    re.IGNORECASE,
)
# - a name on a real ending (`ENDINGS`, any letters' ending before a path, a query or a port: `_named`): any letters
#   after a real dot ("пример.рф", "例子.中国"); with other scripts' full stops read as dots (IDNA reads them so), only
#   a Latin ending: 。 ends a Japanese or Chinese sentence, and the words after it are no name
_NAMES = _names(r"xn--[\w-]+|[^\W\d_]{2,}")  # xn-- first: "xn" alone is a letters' ending too
_NAMES_LATIN = _names(r"xn--[\w-]+|[a-z]{2,}")
_REST = re.compile(r"[-_:/?#]\S*")  # a name's port, path, query or the rest of its word ("paypal.com-verify")
_RUN = re.compile(r"\S*")
_ROUNDS = 4  # `unlinked`'s rounds of tidy removal before it leaves out whole words
# `unlinked`'s second look, as initiative.has_link reads around a name: a run of dots ("Wait...evil.ch") and dots,
# underscores or dashes at a word's edge ("_evil.com_", "-evil.com") or after a dot ("a.-evil.com") hold no name, so
# they are read as spaces; a whole run only, so the dashes inside "xn--p1ai" stay
_EDGES = re.compile(r"\.{2,}|(?<![\w.-])[._-]+|(?<=\.)[_-]+|[_-]+(?![\w-])")
_FULL_STOPS = str.maketrans({"。": ".", "．": ".", "｡": "."})
# characters no app shows, read as nothing: they can sit inside a name the phone joins ("evil\u034f.com")
_UNSEEN = frozenset("\u034f\u115f\u1160\u17b4\u17b5\u3164\uffa0") | frozenset(map(chr, range(0x180B, 0x1810)))
_BEFORE_LINK = frozenset("._-")  # touching a removed link on its left: never a word's, so they go with it
_AFTER_LINK = frozenset("_-")  # and on its right (a dot there is the sentence's)
_JOINS = frozenset(" \t,;/|·&+")  # between two removed links: they go too ("evil.com, www.evil.example")
_PAIRS = {"(": ")", "[": "]", "{": "}", "<": ">", '"': '"', "'": "'", "“": "”", "‘": "’", "«": "»"}
_ENDS = ".!?)]}>…"  # after a removed link: never a space before them, nor a separator
_STRAY = ":;,-–—|/·"  # a separator a removed link leaves at an end
_ENTRY = re.compile(r"(\d+):(\d+):([A-Z]+)")
_ASCII_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_NOT_BESIDE = _ASCII_WORD | frozenset("*~`/\\_")  # never right outside a ** or ~~ pair
_ITALIC_AFTER = frozenset("([{\"'“‘«¿¡-–—")  # an italic * may open after these, a space or the line's start
_ITALIC_BEFORE = frozenset(".,;:!?)]}\"'”’»…-–—")  # and close before these, a space or the line's end
_PATH_END = frozenset("/\\")  # never right before a closing marker: "**/node_modules/**" is a glob

# `netted`'s structural net (owner, 21 Oct), run after `unlinked`'s removal in the note paths: a guarantee that does
# not read a link as a phone does. The characters that glue a link's parts, between two letters or digits in one word,
# become a space, so no name, mail address or scheme-without-"//" can stay tappable. The dot-like set is built from the
# Unicode data (every Po character whose name holds FULL STOP, DOT or POINT, so "evil。com", "evilｰcom", "evil․com"
# all break), checked in `tests/test_signal_text.py` against a re-derivation; NFKC in `netted` first folds the forms it
# can ("．", "｡", "：", fullwidth) onto ASCII, so only the look-alikes NFKC leaves need listing here.
_DOT_LIKE = frozenset(
    map(
        chr,
        (
            0x2E,
            0xB7,
            0x589,
            0x61E,
            0x66D,
            0x6D4,
            0x701,
            0x702,
            0x1362,
            0x166E,
            0x1803,
            0x1805,
            0x1809,
            0x2024,
            0x2025,
            0x2027,
            0x2041,
            0x2056,
            0x2058,
            0x2059,
            0x205A,
            0x205B,
            0x205C,
            0x205E,
            0x2CF9,
            0x2CFE,
            0x2E01,
            0x2E07,
            0x2E08,
            0x2E13,
            0x2E16,
            0x2E1E,
            0x2E1F,
            0x2E2A,
            0x2E2B,
            0x2E2C,
            0x2E2D,
            0x2E30,
            0x2E31,
            0x2E33,
            0x2E3C,
            0x2E3D,
            0x2E48,
            0x2E4A,
            0x3002,
            0x30FB,
            0xA4FF,
            0xA60E,
            0xA6F3,
            0xFE12,
            0xFE30,
            0xFE45,
            0xFE46,
            0xFE52,
            0xFF0E,
            0xFF61,
            0xFF65,
            0x10101,
            0x10A50,
            0x10AF2,
            0x10AF3,
            0x10AF4,
            0x10AF5,
            0x10B3A,
            0x10B3B,
            0x10B3C,
            0x10B3D,
            0x10B9B,
            0x10B9C,
            0x10F56,
            0x10F57,
            0x10F58,
            0x10F59,
            0x10F88,
            0x10F89,
            0x11049,
            0x1104A,
            0x115C4,
            0x115CB,
            0x115CC,
            0x115CD,
            0x115CE,
            0x11F49,
            0x11F4A,
            0x11F4B,
            0x16AF5,
            0x16E98,
            0x1BC9F,
            0x1DA88,
        ),
    )
)
_COLON_LIKE = frozenset(
    map(
        chr,
        (
            0x3A,
            0x2F8,
            0x703,
            0x704,
            0x705,
            0x706,
            0x707,
            0x708,
            0x709,
            0x1365,
            0x1366,
            0x1804,
            0x205D,
            0x2236,
            0xA6F4,
            0xA789,
            0xFE13,
            0xFE55,
            0xFF1A,
            0x12471,
            0x12472,
            0x12473,
            0x12474,
            0x1DA8A,
        ),
    )
)
_SLASH_LIKE = frozenset("/\\⁄∕⧸")  # "／" and "＼" fold to ASCII under NFKC; these do not
_GLUE = _DOT_LIKE | _COLON_LIKE | _SLASH_LIKE | frozenset("@%")  # what joins a link's parts, besides underscore runs
_DOTCOLON = _DOT_LIKE | _COLON_LIKE  # a dot or colon between two digits stays (4.99, 09:30, 1.2.3.4)
_UNDERSCORES = re.compile(r"(?<=[^\W_])_+(?=[^\W_])")  # a run of underscores between two word characters


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


def unlinked(text: str) -> str:
    """Outside text that code quotes in a note, with every link left out (owner, 21 Oct: never shown, not defanged):
    any scheme, the schemes iOS links without "//", www., a mail address, an address by its numbers, a name on a real
    ending, as a phone may make them tappable (`_ALWAYS`, `_WWW`, `_NO_SLASHES`, `_NAMES`), in every reading the phone
    may see: as typed and without markers ("**evil**.com": the phone joins it), in the letters NFKC gives
    ("ｅｖｉｌ.ｃｏｍ"), without characters no app shows, and once more with `_EDGES` as spaces. The markers inside a link
    go with it, and so do markers that only wrapped it; every other marker stays (her styling). The spaces and
    punctuation a removed link leaves are tidied (`_joined`, which never glues two words into one), and the text is
    read again until no reading finds a link; after `_ROUNDS` rounds, every word that still holds one goes whole
    (`_words_left`), so the time stays linear in the text's length. A text without a link comes back as it was."""
    for _ in range(_ROUNDS):
        found = _found(text)
        if not found:
            return text
        text = _left_out(text, found)
    while found := _found(text):
        text = _words_left(text, found)
    return text


def netted(text: str) -> str:
    """The structural safety net for a code-written note, after `unlinked` has removed what it recognises (owner,
    21 Oct): a guarantee that no outside text stays a tappable link, without reading links as a phone does. The text is
    first what the user will receive: NFKC, and every character no app shows (`_unseen`: format characters, variation
    selectors, fillers, U+034F) a space, as an app that joins or splits across them would read. Then, within each
    space-delimited word, a character that glues a link's parts becomes a space when a letter or digit stands on each
    side: a dot or dot-like character, a colon or colon-like one, @, a slash or backslash (and their look-alikes), %,
    or a run of underscores. So no name ("evil.com"), mail address ("bob@evil.example") or scheme without "//"
    ("maps:q") can stay joined. Kept for reading, never a link: a dot or colon between two digits (a price 4.99, a time
    09:30, a version 1.2.3.4), a dot then one letter then a dot or the word's end (an abbreviation e.g., z.B., i.e.,
    p.m.: no one-letter ending is a registrable name), and punctuation at a word's end. One left-to-right pass, so the
    time grows only with the length; a phone number with no scheme stays tappable (`docs/agents/signal-text.md`)."""
    text = unicodedata.normalize("NFKC", "".join(" " if _unseen(char) else char for char in text))
    text = _UNDERSCORES.sub(" ", text)
    out = list(text)
    size = len(out)
    word_alnum = False  # a letter or digit has stood in this word before here: a mail address has a local part
    for index, char in enumerate(out):
        if char == " ":
            word_alnum = False
        elif char in _GLUE:
            right = out[index + 1] if index + 1 < size else ""
            if char == "@":
                if word_alnum and right and _alnum(right):
                    out[index] = " "  # a local part before and a domain after: a mail address, whatever joins them
            else:
                left = out[index - 1] if index else ""
                if left and right and _alnum(left) and _alnum(right):
                    after = out[index + 2] if index + 2 < size else " "  # " " stands for the word's end
                    if char in _DOTCOLON and left.isdigit() and right.isdigit():
                        pass  # a price, a time or a version: no letters, so no ending
                    elif char in _DOT_LIKE and right.isalpha() and (after == " " or after in _DOT_LIKE):
                        pass  # a dot then one letter then a dot or the word's end: an abbreviation, no one-letter ending
                    else:
                        out[index] = " "
        if _alnum(char):
            word_alnum = True
    return "".join(out)


def _alnum(char: str) -> bool:
    """A letter, a digit or a combining mark: the net breaks a link's glue only with one on each side. A mark counts
    because a phone reads it as part of the label it sits on, so a name that ends in one ("உதாரணம்.pe", a virama before
    the dot) is still a link; a mark is neither a digit nor a letter, so it never makes a price or an abbreviation."""
    return char.isalpha() or char.isdigit() or unicodedata.category(char)[0] == "M"


def _words_left(text: str, found: Sequence[tuple[int, int]]) -> str:
    """The text without each whole word (a run without spaces) that holds a link: no word left can make one with
    another, as a space still stands between them and no reading joins words across a space."""
    out: list[str] = []
    gap = ""  # the spaces since the last word kept: a line break stays one
    at = 0
    for match in re.finditer(r"\S+", text):
        start, end = match.span()
        gap += text[at:start]
        at = end
        if not any(first < end and start < last for first, last in found):
            out.append(("\n" if "\n" in gap else " ") * bool(out) + match.group())
            gap = ""
    return "".join(out)


def _left_out(text: str, found: Sequence[tuple[int, int]]) -> str:
    """One round of `unlinked`: every link found left out, the gaps tidied."""
    cuts: list[tuple[int, int]] = []
    for start, end in sorted(_wrapped(text, first, last) for first, last in found):
        if cuts and (start <= cuts[-1][1] or set(text[cuts[-1][1] : start]) <= _JOINS):
            cuts[-1] = (cuts[-1][0], max(end, cuts[-1][1]))
        else:
            cuts.append((start, end))
    edges = [0, *(edge for cut in cuts for edge in cut), len(text)]
    pieces = [text[edges[index] : edges[index + 1]] for index in range(0, len(edges), 2)]
    out = pieces[0]
    for index, piece in enumerate(pieces[1:], 1):
        out = _joined(out, piece, last=index == len(pieces) - 1)
    return out


def _found(text: str) -> list[tuple[int, int]]:
    """Every link's span in `text` (its own indices), as any reading shows one: as typed and without markers, with the
    characters no app shows left out and read as spaces, each as it is and with `_EDGES` as spaces, and with other
    scripts' full stops as dots before a Latin ending. Overlapping spans are joined."""
    spans: list[tuple[int, int]] = []
    hidden = any(map(_unseen, text))
    for markers, unseen in ((True, ""), (False, ""), (True, " "), (False, " ")):
        if unseen and not hidden:
            continue  # nothing to read as a space: the same readings as before
        reading, at = _reading(text, markers=markers, unseen=unseen)
        stops = reading.translate(_FULL_STOPS)
        for names, view in ((_NAMES, reading), (_NAMES_LATIN, stops)):
            edged = _EDGES.sub(lambda match: " " * len(match.group()), view)
            for seen in (view, edged):
                spans += [(at[start], at[end - 1] + 1) for start, end in _unlink_spans(seen, text, at, names)]
    joined: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if joined and start < joined[-1][1]:
            joined[-1] = (joined[-1][0], max(end, joined[-1][1]))
        else:
            joined.append((start, end))
    return joined


def _unlink_spans(seen: str, text: str, at: Sequence[int], names: re.Pattern[str]) -> list[tuple[int, int]]:
    """Each link's span in `seen` (a reading of `text`, `at` its map), without the punctuation after it: each pattern
    on its own, so a candidate refused (a name on no real ending, "hotel:") never hides another link."""
    spans = [match.span() for match in _ALWAYS.finditer(seen)]
    for pattern in (_WWW, _NO_SLASHES):
        spans += [
            (match.start(), _word_end(seen, match.end()))
            for match in pattern.finditer(seen)
            if _free(seen, text, at, match.start())
        ]
    for match in names.finditer(seen):
        if _named(seen, text, at, match.start(), match.end()):
            rest = _REST.match(seen, match.end())
            spans.append((match.start(), rest.end() if rest else match.end()))
    found = []
    for start, end in spans:
        while end > start and seen[end - 1] in _URL_TAIL:
            end -= 1
        if end > start:
            found.append((start, end))
    return found


def _word_end(text: str, start: int) -> int:
    """Where the word that runs on at `start` ends: the next space, or the text's end."""
    run = _RUN.match(text, start)
    return run.end() if run else start


def _free(seen: str, text: str, at: Sequence[int], start: int) -> bool:
    """Whether a link that iOS reads from its first letters ("www.", "tel:") may start at `start`: not right after an
    ASCII letter or digit ("hotel:" is no link). One that only the reading made ASCII ("™tel:" reads "TMtel:") or a
    character the reading left out (a hidden one, a marker) frees it, as the phone sees another character there."""
    if start == 0 or not (seen[start - 1].isascii() and seen[start - 1].isalnum()):
        return True
    before = at[start] - 1
    return at[start - 1] != before or not (text[before].isascii() and text[before].isalnum())


def _named(seen: str, text: str, at: Sequence[int], start: int, end: int) -> bool:
    """Whether the name at `seen[start:end]` is a link as a phone reads one: a part after its first on a real ending
    (`ENDINGS`, or any xn-- one; Signal Android and Desktop check the ending so), or any ending before a path, a query
    or a port. A part is also read up to the first letter that NFKC made of a sign ("evil.com™" reads "evil.comTM",
    but the phone's ending is "com"), and up to where wide letters start or stop (iOS ends "evil.com中" after "com":
    Chinese, Japanese and Korean need no space between words)."""
    at_part = start
    for index, part in enumerate(seen[start:end].split(".")):
        own = next((n for n, _ in enumerate(part) if not _word(text[at[at_part + n]])), len(part))
        wide = next(
            (n for n in range(1, len(part)) if _wide(text[at[at_part + n]]) != _wide(text[at[at_part]])), len(part)
        )
        forms = {part.lower(), part[:own].lower(), part[:wide].lower()}
        if index and any(form in ENDINGS or form.startswith("xn--") for form in forms):
            return True
        at_part += len(part) + 1
    after = seen[end : end + 2]
    return after[:1] in ("/", "?", "#") or (after[:1] == ":" and after[1:].isdigit())


def _wide(char: str) -> bool:
    """A letter of a script written without spaces between words (Chinese, Japanese, Korean): a wide character."""
    return unicodedata.east_asian_width(char) in ("W", "F")


def _word(char: str) -> bool:
    """A letter, a digit or an underscore, as the phone reads a name's part."""
    return char.isalnum() or char == "_"


def _reading(text: str, *, markers: bool, unseen: str) -> tuple[str, list[int]]:
    """The text as a phone may read it, and the index in `text` of each of its characters: each character in its
    NFKC form (fullwidth letters as letters, "…" as dots; a mark or a symbol outside ASCII as `_SIGN`), the characters
    no app shows (format characters, variation selectors, `_UNSEEN`) as `unseen` ("" leaves them out, as an app that
    joins across them; " " splits there, as iOS does at a zero-width space), and without markers unless `markers`."""
    chars: list[str] = []
    at: list[int] = []
    for index, char in enumerate(text):
        if not markers and char in MARKERS:
            continue
        for part in unseen if _unseen(char) else unicodedata.normalize("NFKC", char):
            chars.append(_SIGN if not part.isascii() and _sign(part) else part)
            at.append(index)
    return "".join(chars), at


def _sign(char: str) -> bool:
    """A mark, a symbol, a private-use or an unassigned character: part of a name to Signal Desktop (`_PSEUDO`)."""
    kind = unicodedata.category(char)
    return kind[0] in "MS" or kind in ("Co", "Cn")


def _unseen(char: str) -> bool:
    """A character no app shows: a format character (zero-width, bidi, soft hyphen), a variation selector or one of
    `_UNSEEN`."""
    point = ord(char)
    return (
        unicodedata.category(char) == "Cf"
        or char in _UNSEEN
        or 0xFE00 <= point <= 0xFE0F
        or 0xE0100 <= point <= 0xE01EF
    )


def _wrapped(text: str, first: int, last: int) -> tuple[int, int]:
    """A link's span widened over what touches it and goes with it: dots, underscores and dashes, and markers that
    only wrapped it (a pair around it, or the other half of a pair inside it: "**evil**.com"); markers that style more
    stay ("**Pay at evil.com**" keeps its closing pair)."""
    start, end = first, last
    while start and (text[start - 1] in MARKERS or text[start - 1] in _BEFORE_LINK):
        start -= 1
    while end < len(text) and (text[end] in MARKERS or text[end] in _AFTER_LINK):
        end += 1
    inner = {match.group() for match in re.finditer(r"[*~`]+", text[first:last])}
    before = "".join(char for char in text[start:first] if char in MARKERS)
    after = "".join(char for char in text[last:end] if char in MARKERS)
    mirrored = bool(before) and before == after[::-1]
    return (
        start if mirrored or not before or before in inner else first,
        end if mirrored or not after or after in inner else last,
    )


def _joined(before: str, after: str, *, last: bool) -> str:
    """The text on either side of a removed link, tidied: an empty pair of brackets, quotes or markers that held only
    the link left out, one space (a line break when one was there), none before a full stop or a comma that ends a
    sentence, and a separator the link leaves at an end; markers that open or close what stays keep their place beside
    it. Two words are never glued into one: "evil(ab.cd).com" leaves "evil .com", never "evil.com", so a round of
    `unlinked` cannot build a link that only the next round would find."""
    left, right = before, after
    while left and right:
        tail, head = _run(left, -1), _run(right, 1)
        if _PAIRS.get(left[-1]) == right[0]:
            left, right = left[:-1], right[1:]
        elif tail and tail == head[::-1]:
            left, right = left[: -len(tail)], right[len(head) :]
        else:
            break
    tail, head = _run(left, -1), _run(right, 1)
    opening = tail if tail and (len(tail) == len(left) or left[-len(tail) - 1].isspace()) else ""
    closing = (
        head
        if head and (len(head) == len(right) or right[len(head)].isspace() or right[len(head)] in _ENDS + ",;:")
        else ""
    )
    left, right = left[: len(left) - len(opening)], right[len(closing) :]
    gap = left[len(left.rstrip()) :] + right[: len(right) - len(right.lstrip())]
    left, right = left.rstrip(), right.lstrip()
    if left and left[-1] in _STRAY and right[:1] in (",", ";", ":"):
        right = right[1:].lstrip()
    if (last and not right) or (right and right[0] in _ENDS):
        left = left.rstrip(_STRAY + " \t")
    if not left:
        right = right.lstrip(_STRAY + _ENDS + " \t")
    joiner = "" if not left or not right or _ends_sentence(right) else "\n" if "\n" in gap else " "
    return left + closing + joiner + opening + right


def _ends_sentence(text: str) -> bool:
    """Whether `text` starts with punctuation that ends a sentence or a clause (".", ",", ")", "…"), then a space or
    the end: only that may stand right after the word before a removed link."""
    marks = len(text) - len(text.lstrip(_ENDS + ",;:"))
    return marks > 0 and (marks == len(text) or text[marks].isspace())


def _run(text: str, side: int) -> str:
    """The markers at the end (`side` -1) or the start (1) of `text`."""
    count = 0
    while count < len(text) and text[len(text) - 1 - count if side < 0 else count] in MARKERS:
        count += 1
    return text[len(text) - count :] if side < 0 else text[:count]


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


def _links(text: str, pattern: re.Pattern[str] = _LINKISH) -> list[tuple[int, int]]:
    """Where a phone may see a web or mail address in the text (`pattern`'s reading), without the punctuation after
    it."""
    found = []
    for match in pattern.finditer(text):
        end = match.end()
        while end > match.start() and text[end - 1] in _URL_TAIL:
            end -= 1
        if end > match.start():
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
