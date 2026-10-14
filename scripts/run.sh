#!/usr/bin/env bash
# Phase A: run the bridge in the foreground (a terminal), logging to the terminal too. Ctrl-C stops it.
set -euo pipefail
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved; then  # a second bridge as you would start with your empty data and take her Signal account away
  echo "She runs always on as her own user _estia; this would start a second one with your empty data, so it does not."
  echo "Restart her instead: /usr/bin/sudo /usr/local/estia/bin/estia restart (it is /usr/bin/sudo launchctl kickstart -k system/com.local-assistant.bridge)"
  exit 1
fi
cd "$(dirname "$0")/.."
uv sync --frozen --quiet
(cd tools/tapo && uv sync --frozen --quiet)  # the device helper's own environment (python-kasa)
(cd tools/ical && uv sync --frozen --quiet)  # the calendar helper's own environment (icalendar)
if pgrep -f '\.venv/bin/local-assistant' >/dev/null; then  # one signal-cli per account: never two bridges
  echo "a bridge is already running (the daemon? stop it with: /usr/bin/sudo launchctl bootout system/com.local-assistant.bridge)" >&2
  exit 1
fi
exec .venv/bin/local-assistant --foreground "$@"
