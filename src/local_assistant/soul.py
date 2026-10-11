"""Her personality: the user's own soul.md, style only (the rules, the bridge's checks and tool results win).

The user writes it and she never changes it. It is read for every answer, so an edit counts from the next
message. Lines starting with # are notes for the user and are left out. A file that others can read, that is too
long or that hides characters or chat tokens is not used at all (never cut in half).
"""

from __future__ import annotations

import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from local_assistant.fetcher import strip_control_tokens

SOUL_MAX_CHARS = 2_000
_MAX_FILE_BYTES = 64 * 1024  # comments included; a bigger file is not read at all
_HIDDEN = frozenset({"Cc", "Cf", "Co", "Cs"})  # control, format (bidi, zero-width), private use, surrogates


@dataclass(frozen=True)
class Soul:
    text: str  # "" when there is none or it is not used
    problem: str = ""  # why not, for /soul and the log


def load_soul(path: Path) -> Soul:
    try:
        info = path.stat()
    except FileNotFoundError:
        return Soul("", "no soul.md yet")
    except OSError as exc:
        return Soul("", f"soul.md cannot be read ({type(exc).__name__})")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return Soul("", "soul.md is readable by others (chmod 600 it)")
    if info.st_size > _MAX_FILE_BYTES:
        return Soul("", f"soul.md is too long ({info.st_size:,} bytes; at most {SOUL_MAX_CHARS:,} characters)")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Soul("", f"soul.md cannot be read ({type(exc).__name__})")
    text = "\n".join(line.rstrip() for line in raw.splitlines() if not line.lstrip().startswith("#")).strip()
    if not text:
        return Soul("", "soul.md is empty")
    if len(text) > SOUL_MAX_CHARS:
        return Soul("", f"soul.md is too long ({len(text):,} characters; at most {SOUL_MAX_CHARS:,})")
    hidden = any(ch not in "\n\t" and unicodedata.category(ch) in _HIDDEN for ch in text)
    if hidden or strip_control_tokens(text) != text:
        return Soul("", "soul.md has hidden characters or chat tokens")
    return Soul(text)
