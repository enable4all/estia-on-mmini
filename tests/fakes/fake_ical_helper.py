"""Stand-in for tools/ical/helper.py in tests: the same arguments and JSON, the calendar kept in a JSON file.

Run as `fake_ical_helper.py <state.json> events --from <ISO> --to <ISO> --zone <zone> [--calendar NAME] [--words W]
[--limit N]` (the state file comes first, as part of the command the tests configure). The state file holds
{"calendar": name, "events": [helper-form events]} or {"error": "gone" | "not_set" | "unreachable" | "invalid"};
"sleep" (seconds) delays the answer, "raw" replaces the answer with that text. With --calendar NAME the state is
"calendars"[NAME] instead (none there: not set). Events overlapping the range are
returned in start order; --words keeps those whose title or place has every word. Every call's arguments are
appended to <state>.calls (one JSON list per line), the names of its environment variables written to
<state>.env and its process ID to <state>.pid.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def moment(value: str, zone: ZoneInfo) -> datetime:
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), clock_time(), zone)
    return datetime.fromisoformat(value)


def main() -> None:
    path = Path(sys.argv[1])
    args = sys.argv[2:]
    with open(f"{path}.calls", "a") as calls:
        calls.write(json.dumps(args) + "\n")
    Path(f"{path}.env").write_text(json.dumps(sorted(os.environ)))
    Path(f"{path}.pid").write_text(str(os.getpid()))
    state: dict[str, Any] = json.loads(path.read_text())
    if "--calendar" in args:
        state = state.get("calendars", {}).get(args[args.index("--calendar") + 1], {"error": "not_set"})
    time.sleep(float(state.get("sleep", 0)))
    if "raw" in state:
        print(state["raw"])
        raise SystemExit(1)
    if "error" in state:
        print(json.dumps({"ok": False, "error": state["error"], "detail": "fake"}))
        raise SystemExit(1)
    options: dict[str, str] = {}
    rest = iter(args[1:])
    for arg in rest:
        key, sep, value = arg.partition("=")
        options[key] = value if sep else next(rest)
    zone = ZoneInfo(options["--zone"])
    low, high = datetime.fromisoformat(options["--from"]), datetime.fromisoformat(options["--to"])
    words = re.findall(r"\w+", options.get("--words", "").casefold())
    limit = int(options.get("--limit", "1000"))
    found = []
    for item in sorted(state.get("events", []), key=lambda e: moment(e["start"], zone)):
        start, end = moment(item["start"], zone), moment(item["end"], zone)
        if not (start < high and (end > low or start >= low)):
            continue
        text = set(re.findall(r"\w+", f"{item['title']} {item.get('place', '')}".casefold()))
        if words and not set(words) <= text:
            continue
        found.append(item)
    print(
        json.dumps(
            {
                "ok": True,
                "calendar": state.get("calendar", ""),
                "events": found[:limit],
                "truncated": len(found) > limit,
                "partial": bool(state.get("partial")),
            }
        )
    )


if __name__ == "__main__":
    main()
