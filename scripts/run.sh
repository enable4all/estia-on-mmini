#!/usr/bin/env bash
# Phase A: run the bridge in the foreground (a terminal), logging to the terminal too. Ctrl-C stops it.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen --quiet
(cd tools/tapo && uv sync --frozen --quiet)  # the device helper's own environment (python-kasa)
(cd tools/ical && uv sync --frozen --quiet)  # the calendar helper's own environment (icalendar)
if pgrep -f '\.venv/bin/local-assistant' >/dev/null; then  # one signal-cli per account: never two bridges
  echo "a bridge is already running (the daemon? stop it with: sudo launchctl bootout system/com.local-assistant.bridge)" >&2
  exit 1
fi
exec .venv/bin/local-assistant --foreground "$@"
