"""Signal Android's own link reading, ported for the tests: an oracle that knows nothing of `signal_text.unlinked`.

From Signal Android's core/util-jvm `Linkifier.kt` (2026: web addresses with http or https, www., a bare name on a real
ending, each only at a leading boundary, trailing punctuation trimmed) and AndroidX's `AUTOLINK_EMAIL_ADDRESS` (mail
addresses, any characters outside ASCII in their parts). The real endings are `web_endings.ENDINGS`, a superset of
Signal Android's own list, so this oracle finds at least what the app links. Signal Desktop (linkify-it) and iOS
(NSDataDetector) are checked only by hand, with probes on a Mac (signal-text.md)."""

import re
import unicodedata

from local_assistant.web_endings import ENDINGS

_STOP = '\\s\u0085\u00a0\u1680\u2000-\u200d\u2028\u2029\u202a-\u202f\u205f\u2066-\u2069\u3000\ufeff<>"`|\\\\'
_URL = f"(?:[^{_STOP},;]|[,;](?!https?://|www\\.))"
_AUTHORITY = f"(?:[^{_STOP},;'/?#]|[,;](?!https?://|www\\.))"
_LABEL = r"[^\W_](?:[^\W_]|-)*"  # a letter or digit first, then letters, digits and dashes
_TOP = r"[^\W_](?:(?:[^\W_]|-)*[^\W_])?"  # the same without a dash at its end
_WEB = re.compile(
    f"https?://{_AUTHORITY}+(?:[/?#]{_URL}*)?"
    f"|www\\.{_LABEL}(?:\\.{_LABEL})+(?:[/?#]{_URL}*)?"
    f"|{_LABEL}(?:\\.{_LABEL})*\\.{_TOP}(?:[/?#]{_URL}*)?",
    re.IGNORECASE,
)
_HIDDEN = (
    frozenset("\u200b\u200c\u200d\ufeff")
    | frozenset(map(chr, range(0x202A, 0x202F)))
    | frozenset("\u2066\u2067\u2068\u2069")
)
_TRAIL = frozenset(".,!?;:'\"`|<>")
_CLOSE = {")": "(", "]": "[", "}": "{"}
_MAIL_CHAR = r"[a-zA-Z0-9+\-_%'\u00a1-\U0010ffff]"
_MAIL_LABEL = r"[a-zA-Z0-9\u00a1-\U0010ffff](?:[a-zA-Z0-9_\-\u00a1-\U0010ffff]{0,61}[a-zA-Z0-9\u00a1-\U0010ffff])?"
_MAIL = re.compile(
    rf"(?<!\w){_MAIL_CHAR}(?:(?:{_MAIL_CHAR}|\.){{0,62}}{_MAIL_CHAR})?@(?:{_MAIL_LABEL}\.)+"
    r"(?:xn--[\w-]{0,58}\w|[a-zA-Z\u00a1-\U0010ffff]{2,63})(?!\w)"
)


def _letter_or_digit(char: str) -> bool:
    """Java's Character.isLetterOrDigit on one UTF-16 unit: a letter or a decimal digit, never a character above
    U+FFFF (Java reads it as two halves)."""
    kind = unicodedata.category(char)
    return ord(char) <= 0xFFFF and (kind[0] == "L" or kind == "Nd")


def android_links(text: str) -> list[str]:
    """Every web or mail address Signal Android makes tappable in `text`."""
    found = []
    for match in _WEB.finditer(text):
        start, end = match.start(), match.end()
        before = text[start - 1] if start else ""
        if before and (_letter_or_digit(before) or before in "_@" or before in _HIDDEN):
            continue
        while end > start:
            last = text[end - 1]
            if last in _TRAIL or (
                last in _CLOSE and text.count(last, start, end - 1) >= text.count(_CLOSE[last], start, end - 1)
            ):
                end -= 1
                continue
            break
        if end <= start or (end < len(text) and text[end] in _HIDDEN):
            continue
        link = text[start:end]
        if "://" in link:
            host = re.split(r"[/?#]", link.split("://", 1)[1])[0]
            found += [link] if host else []
        elif link.lower().startswith("www."):
            found.append(link)
        else:
            ending = re.split(r"[/?#]", link)[0].rsplit(".", 1)[-1].lower()
            if ending in ENDINGS or ending.startswith("xn--"):  # the app adds each ending's xn-- form
                found.append(link)
    return found + [match.group() for match in _MAIL.finditer(text)]
