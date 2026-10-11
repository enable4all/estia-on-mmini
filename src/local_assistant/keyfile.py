"""The private key file (`web.env`, 0600): KEY=value lines, written by scripts/set-web-keys.sh from the login keychain.

Read for every request, so a key added later works without a restart; a file that others can read is not used.
"""

from __future__ import annotations

import logging
import stat
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)


def read_keys(path: Path, names: Iterable[str]) -> dict[str, str]:
    """The asked keys that have a value (quotes around a hand-edited value are dropped)."""
    wanted = set(names)
    try:
        if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            log.warning("the key file is readable by others; not used (chmod 600 %s)", path.name)
            return {}
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    keys: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].strip()
        if sep and name.strip() in wanted and value:
            keys[name.strip()] = value
    return keys
