"""Stand-in for tools/youtube/helper.py in tests: the same arguments and JSON, the answer kept in a JSON file.

Run as `fake_youtube_helper.py <state.json> captions [--until S] [--] <video ID>` (the state file comes first, as part
of the command the tests configure). Its arguments are read with the real helper's parser shape, so arguments the real
helper would refuse (a video ID starting with "-" before "--") get its "invalid" here too. The state file holds the
helper's answer: {"ok": true, "segments": …, …} or {"ok": false, "error": "<code>"}; "sleep" (seconds) delays it,
"raw" replaces it with that text. Every call's arguments are appended to <state>.calls (one JSON list per line), the
names of its environment variables written to <state>.env and its process ID to <state>.pid.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # as the real helper: a usage error is "invalid", not text on stderr
        raise ValueError(message)


def _video_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise argparse.ArgumentTypeError("not a video ID")
    return value


def parse(argv: list[str]) -> argparse.Namespace:
    """The real helper's parser shape (tools/youtube/helper.py `parse`)."""
    parser = _Parser(prog="helper.py", add_help=False)
    actions = parser.add_subparsers(dest="action", required=True, parser_class=_Parser)
    read = actions.add_parser("captions", add_help=False)
    read.add_argument("video_id", type=_video_id)
    read.add_argument("--until", type=float, default=None)
    return parser.parse_args(argv)


def main() -> None:
    path = Path(sys.argv[1])
    args = sys.argv[2:]
    with open(f"{path}.calls", "a") as calls:
        calls.write(json.dumps(args) + "\n")
    Path(f"{path}.env").write_text(json.dumps(sorted(os.environ)))
    Path(f"{path}.pid").write_text(str(os.getpid()))
    try:
        parse(args)
    except (ValueError, argparse.ArgumentTypeError):
        print(json.dumps({"ok": False, "error": "invalid"}))
        raise SystemExit(1) from None
    state: dict[str, Any] = json.loads(path.read_text())
    time.sleep(float(state.pop("sleep", 0)))
    if "raw" in state:
        print(state["raw"])
        raise SystemExit(1)
    print(json.dumps(state))
    raise SystemExit(0 if state.get("ok") else 1)


if __name__ == "__main__":
    main()
