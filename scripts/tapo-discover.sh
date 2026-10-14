#!/usr/bin/env bash
# Lists the Kasa and Tapo devices on the home network (local broadcast; nothing is switched), to fill
# [[tapo.devices]] in ~/.config/local-assistant/config.toml. Older Kasa devices need no login; newer Kasa firmware
# and Tapo devices need the TP-Link login (Tapo is untested). Save it first with scripts/set-tapo-credentials.sh.
# A fixed address per device (DHCP reservation in the router) is recommended.
set -euo pipefail
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved && [[ "$(id -un)" != _estia ]]; then exec "$sudo" "$estia" tapo-discover; fi  # her TP-Link login is hers
tapo="$(cd "$(dirname "$0")/../tools/tapo" && pwd)"
cd "$tapo"
[[ -x .venv/bin/python ]] || uv sync --frozen -q
.venv/bin/python helper.py discover 2>/dev/null | .venv/bin/python -c '
import json, sys
found = json.load(sys.stdin)
if not found.get("ok"):
    sys.exit(f"discovery failed: {found.get("error")} {found.get("detail", "")}")
print(f"{"name (app)":28} {"model":8} {"kind":6} address")
for d in found["devices"]:
    if "error" in d:
        print(f"{"?":28} {"?":8} {"?":6} {d["host"]}  ({d["error"]})")
    else:
        print(f"{d["alias"]:28} {d["model"]:8} {d["kind"]:6} {d["host"]}")
for u in found["unsupported"]:
    print(f"not controllable yet: {u["type"]} at {u["host"]} (login {u["encryption"]})")
'
