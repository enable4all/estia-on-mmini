#!/usr/bin/env bash
# Lists the Kasa and Tapo devices on the home network (local broadcast; nothing is switched), to fill
# [[tapo.devices]] in ~/.config/local-assistant/config.toml. Older Kasa devices need no login; newer Kasa firmware
# and Tapo devices need the TP-Link login (Tapo is untested). The listing never sends that login: any device on the
# network may answer a broadcast. A device that needs it is listed by its address, model and login type; its name is
# the one in the Kasa or Tapo app. The login goes only to the devices in [[tapo.devices]]: save it with
# scripts/set-tapo-credentials.sh. A fixed address per device (DHCP reservation in the router) is recommended.
# Once the Hue Bridge is paired (scripts/hue-pair.sh), its lights follow: read from the bridge, with its certificate
# checked as saved at pairing; also when the TP-Link listing fails.
set -euo pipefail
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved && [[ "$(id -un)" != _estia ]]; then exec "$sudo" "$estia" tapo-discover; fi  # her TP-Link login is hers
tapo="$(cd "$(dirname "$0")/../tools/tapo" && pwd)"
cd "$tapo"
[[ -x .venv/bin/python ]] || uv sync --frozen -q
listed=0
.venv/bin/python helper.py discover 2>/dev/null | .venv/bin/python -c '
import json, sys
found = json.load(sys.stdin)
if not found.get("ok"):
    sys.exit(f"discovery failed: {found.get("error")} {found.get("detail", "")}")
print(f"{"name (app)":28} {"model":8} {"kind":6} address")
for d in found["devices"]:
    if "error" in d:
        print(f"{"?":28} {"?":8} {"?":6} {d["host"]}  ({d["error"]})")
    elif "login" in d:
        print(f"{"(as in the app)":28} {d["model"]:8} {"?":6} {d["host"]}  (TP-Link login: {d["login"]})")
    else:
        print(f"{d["alias"]:28} {d["model"]:8} {d["kind"]:6} {d["host"]}")
for u in found["unsupported"]:
    print(f"not controllable yet: {u["type"]} at {u["host"]} (TP-Link login: {u["encryption"]})")
' || listed=$?
if [[ -f "$HOME/.config/local-assistant/hue.env" ]]; then
  .venv/bin/python -I hue.py lights || true
fi
exit "$listed"
