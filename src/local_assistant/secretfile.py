"""`/usr/bin/sudo /usr/local/estia/bin/estia secret set <file> <KEY>`: save one key in one of her key files.

The value comes only from stdin (the wrapper asks with a hidden prompt when stdin is a terminal); a value on the
command line is refused. Only these files and keys:

    web.env       BRAVE_API_KEY, OLLAMA_API_KEY, GOOGLE_MAPS_API_KEY
    calendar.env  CALENDAR_ICS_URL[_NAME] (a Google iCal address; CALENDAR_SAVED_AT[_NAME] is set next to it),
                  CALENDAR_SAVED_AT[_NAME]
    tapo.env      TAPO_USERNAME, TAPO_PASSWORD

Only that key is replaced; the other lines stay, in one atomic write in her own folder
(<ESTIA_ROOT>/var/estia/.config/local-assistant, which the move into her own user made). The file is 0600 and
hers; links are never followed (the folder is opened step by step without following them). Only lengths are printed.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import re
import sys
import time
from pathlib import Path

from local_assistant.hostsec import estia_root
from local_assistant.ownuser import HOME, ROLE_GROUP, ROLE_USER

_NAME = r"(_[A-Z0-9][A-Z0-9_]{0,19})?"
ALLOWED: dict[str, re.Pattern[str]] = {
    "web.env": re.compile(r"^(BRAVE_API_KEY|OLLAMA_API_KEY|GOOGLE_MAPS_API_KEY)$"),
    "calendar.env": re.compile(rf"^CALENDAR_(ICS_URL|SAVED_AT){_NAME}$"),
    "tapo.env": re.compile(r"^(TAPO_USERNAME|TAPO_PASSWORD)$"),
}
ICAL = re.compile(r"^https://calendar\.google\.com/calendar/ical/\S+\.ics$")
FOLDER = (".config", "local-assistant")  # under her home, opened one step at a time


class SecretError(Exception):
    """Nothing was saved."""


def target_folder() -> Path:
    return estia_root() / HOME.relative_to("/") / Path(*FOLDER)


def check_key(file: str, key: str) -> None:
    pattern = ALLOWED.get(file)
    if pattern is None or not pattern.match(key):
        raise ValueError(f"not allowed: {file} {key} (see: estia secret --help)")


def check(file: str, key: str, value: str) -> None:
    check_key(file, key)
    if not value:
        raise SecretError("no value given; nothing saved")
    if any(ch in value for ch in "\n\r\0"):
        raise SecretError("the value must be one line; nothing saved")
    if key.startswith("CALENDAR_ICS_URL") and not ICAL.match(value):
        raise SecretError("that is not a Google iCal address (https://calendar.google.com/calendar/ical/…/basic.ics)")
    if key.startswith("CALENDAR_SAVED_AT") and not value.isdigit():
        raise SecretError("a saved time is a number of seconds")


def _open_folder(root_folder: Path) -> int:
    """Her key folder as a directory descriptor: her home as it is (the system's /var link included), then each step
    below it opened without following a link."""
    home = root_folder.parents[len(FOLDER) - 1]
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in FOLDER:
            step = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = step
    except OSError:
        os.close(fd)
        raise
    return fd


def _read(dir_fd: int, name: str) -> str:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        return ""
    with os.fdopen(fd, encoding="utf-8") as handle:
        return handle.read()


def merged(text: str, values: dict[str, str]) -> str:
    """The file with these keys replaced (their old lines left out, the new ones at the end); every other line kept."""
    kept = []
    for line in text.splitlines():
        found = line.partition("=")[0].strip()
        if "=" in line and found in values:
            continue
        kept.append(line)
    return "".join(f"{line}\n" for line in kept) + "".join(f"{key}={value}\n" for key, value in values.items())


def save(folder: Path, file: str, key: str, value: str, *, now: float | None = None) -> None:
    """Replace `key` (and a calendar address's saved time) in her `file`, 0600, in one rename."""
    check(file, key, value)
    values = {key: value}
    if key.startswith("CALENDAR_ICS_URL"):
        values[key.replace("CALENDAR_ICS_URL", "CALENDAR_SAVED_AT", 1)] = str(int(now or time.time()))
    try:
        dir_fd = _open_folder(folder)
    except OSError as exc:
        raise SecretError(f"her folder {folder} is missing or a link ({type(exc).__name__}); nothing saved") from exc
    tmp = f".{file}.{os.getpid()}.tmp"
    try:
        try:
            text = merged(_read(dir_fd, file), values)
        except OSError as exc:
            raise SecretError(f"{file} cannot be read or is a link; nothing saved") from exc
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
                if os.geteuid() == 0:
                    import grp
                    import pwd

                    os.fchown(handle.fileno(), pwd.getpwnam(ROLE_USER).pw_uid, grp.getgrnam(ROLE_GROUP).gr_gid)
                os.fchmod(handle.fileno(), 0o600)
            os.replace(tmp, file, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="estia secret",
        description="Save one key in her key files. The value comes from stdin or a hidden prompt, never the "
        "command line. Files and keys: " + "; ".join(f"{file} {rule.pattern}" for file, rule in ALLOWED.items()),
    )
    parser.add_argument("action", choices=["set"])
    parser.add_argument("file")
    parser.add_argument("key")
    parser.add_argument("extra", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.extra:
        print(
            "the value goes through stdin or the hidden prompt, never the command line; nothing saved", file=sys.stderr
        )
        return 2
    try:
        check_key(args.file, args.key)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    value = (getpass.getpass(f"{args.key} (hidden): ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
    length = len(value)
    folder = target_folder()
    try:
        save(folder, args.file, args.key, value)
    except (SecretError, OSError) as exc:
        print(str(exc) if isinstance(exc, SecretError) else f"not saved: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        value = ""
    print(f"saved {args.key} in {folder / args.file} ({length} characters, mode 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
