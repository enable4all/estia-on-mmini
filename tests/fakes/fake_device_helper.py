"""Stand-in for tools/tapo/helper.py in tests: the same arguments and JSON, devices kept in a JSON file.

Run as `fake_device_helper.py <state.json> <action> <host> [options]` (the state file comes first, as part of the
command the tests configure; the real helper gets a clean environment, so no environment variable is used).
The state file holds {"<host>": device}; a device is {"alias", "kind": "light" | "plug", "on", "brightness",
"temp", "hsv"} or {"error": "unreachable" | "login" | "unsupported", "detail": ...}; an optional "sleep" (seconds)
delays the answer. Unknown hosts are unreachable. Every call's arguments are appended to <state>.calls (one JSON
list per line) and the names of its environment variables written to <state>.env.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def answer(device: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "alias": device["alias"], "kind": device["kind"], "on": device["on"]}
    if device["kind"] == "light":
        out |= {"brightness": device["brightness"], "temp": device["temp"], "temp_range": [2500, 9000]}
        out["hsv"] = device["hsv"]
    return out


def main() -> None:
    path = Path(sys.argv[1])
    args = sys.argv[2:]
    with Path(f"{path}.calls").open("a") as calls:
        calls.write(json.dumps(args) + "\n")
    Path(f"{path}.env").write_text(json.dumps(sorted(os.environ)))
    devices: dict[str, dict[str, Any]] = json.loads(path.read_text())
    if args[0] == "discover":
        found = [{"host": host, "alias": d["alias"], "kind": d["kind"]} for host, d in devices.items() if "alias" in d]
        print(json.dumps({"ok": True, "devices": found, "unsupported": []}))
        return
    device = devices.get(args[1])
    if device is None:
        device = {"error": "unreachable"}
    time.sleep(float(device.get("sleep", 0)))
    if "error" in device:
        print(json.dumps({"ok": False, "error": device["error"], "detail": device.get("detail", "")}))
        raise SystemExit(1)
    if args[0] == "set":
        options = dict(zip(args[2::2], args[3::2], strict=False))
        if "--power" in options:
            device["on"] = options["--power"] == "on"
        if "--brightness" in options:
            device["brightness"] = int(options["--brightness"])
        if "--temp" in options:
            device["temp"], device["hsv"] = int(options["--temp"]), [0, 0, device["brightness"]]
        if "--hue" in options:
            hue, saturation = int(options["--hue"]), int(options.get("--saturation", "100"))
            device["temp"], device["hsv"] = 0, [hue, saturation, device["brightness"]]
        path.write_text(json.dumps(devices))
    print(json.dumps(answer(device)))


if __name__ == "__main__":
    main()
