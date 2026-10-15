"""Stand-in for tools/documents' documents-helper in tests: the same arguments and JSON line, the answers in a file.

Run as `fake_documents_helper.py <state.json> read <absolute path> --max-pages N --max-seconds S`, or `… warm
[--max-seconds S]` ({"ok": true}; "warm" in the state file answers instead) (the state file comes first, as part of the
command the tests configure). The arguments are read as strictly as the real helper reads them
(the path right after `read`, absolute; each limit once, a whole number from 1): anything else is
{"ok": false, "error": "unreadable"}. The state file maps a file's name to its answer ({"ok": true, "pages": …} or
{"ok": false, "error": …}, or {"crash": true}: no answer, the process killed by SIGSEGV, as PDFKit crashing on a bad
file); "*" answers for any other file; "sleep" (seconds) delays every answer, "raw" replaces it with that text (every
call's). Without an answer for the file, a regular file (never a symlink) starting with "%PDF-" gives one page
per line after its first ("%PDF-1.7"), else not_pdf. Every call's arguments are appended to <state>.calls (one JSON
list per line), and the names of its environment variables written to <state>.env.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
import time
from pathlib import Path
from typing import Any


def parse(argv: list[str]) -> tuple[str, int, int] | None:
    """The real helper's reading of its arguments (tools/documents' Arguments.parse): None when invalid."""
    if len(argv) < 2 or argv[0] != "read" or not argv[1].startswith("/"):
        return None
    rest, limits = argv[2:], {"--max-pages": 300, "--max-seconds": 60}
    if len(rest) % 2:
        return None
    seen: set[str] = set()
    for name, value in zip(rest[::2], rest[1::2], strict=True):
        if name not in limits or name in seen or not value.isdecimal() or int(value) < 1:
            return None
        seen.add(name)
        limits[name] = int(value)
    return argv[1], limits["--max-pages"], limits["--max-seconds"]


def answer(path: str, state: dict[str, Any]) -> dict[str, Any]:
    name = Path(path).name
    if name in state:
        found: dict[str, Any] = state[name]
        return found
    if "*" in state:
        default: dict[str, Any] = state["*"]
        return default
    try:
        info = os.lstat(path)
    except OSError:
        return {"ok": False, "error": "unreadable"}
    if not stat.S_ISREG(info.st_mode):  # a symlink or anything but a regular file is refused
        return {"ok": False, "error": "unreadable"}
    data = Path(path).read_bytes()
    if not data.startswith(b"%PDF-"):
        return {"ok": False, "error": "not_pdf"}
    lines = data.decode("utf-8", "replace").splitlines()[1:]
    pages = [{"n": number, "text": line, "ocr": False} for number, line in enumerate(lines, 1)]
    return {"ok": True, "pages": pages, "skipped": [], "page_count": len(pages)}


def main() -> None:
    state_path = Path(sys.argv[1])
    args = sys.argv[2:]
    with open(f"{state_path}.calls", "a") as calls:
        calls.write(json.dumps(args) + "\n")
    Path(f"{state_path}.env").write_text(json.dumps(sorted(os.environ)))
    state: dict[str, Any] = json.loads(state_path.read_text()) if state_path.exists() else {}
    time.sleep(float(state.pop("sleep", 0)))
    if "raw" in state:
        print(state["raw"])
        raise SystemExit(1)
    if args[:1] == ["warm"] and (args[1:] == [] or (args[1] == "--max-seconds" and args[2:3] and args[2].isdecimal())):
        print(json.dumps(state.get("warm", {"ok": True})))
        return
    parsed = parse(args)
    if parsed is None:
        print(json.dumps({"ok": False, "error": "unreadable"}))
        raise SystemExit(1)
    found = answer(parsed[0], state)
    if found.get("crash"):
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGSEGV)
    print(json.dumps(found))


if __name__ == "__main__":
    main()
