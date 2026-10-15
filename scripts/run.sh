#!/usr/bin/env bash
# Phase A: run the bridge in the foreground (a terminal), logging to the terminal too. Ctrl-C stops it.
set -euo pipefail
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved; then  # a second bridge as you would start with your empty data and take her Signal account away
  echo "She runs always on as her own user _estia; this would start a second one with your empty data, so it does not."
  echo "Restart her instead: /usr/bin/sudo /usr/local/estia/bin/estia restart"
  exit 1
fi
cd "$(dirname "$0")/.."
uv sync --frozen --quiet
(cd tools/tapo && uv sync --frozen --quiet)  # the device helper's own environment (python-kasa)
(cd tools/ical && uv sync --frozen --quiet)  # the calendar helper's own environment (icalendar)
(cd tools/youtube && uv sync --frozen --quiet)  # the YouTube helper's own environment (youtube-transcript-api)
# the documents helper (Swift: Apple's PDFKit and Vision): an extra, so a failed build never stops the bridge
(cd tools/documents && swift build -c release --quiet) || echo "! the documents helper was not built: PDFs are not read (it needs Swift 6.0 or later: swift --version)" >&2
if pgrep -f '\.venv/bin/local-assistant' >/dev/null; then  # one signal-cli per account: never two bridges
  echo "a bridge is already running (the daemon? stop it with: /usr/bin/sudo /bin/launchctl bootout system/com.local-assistant.bridge)" >&2
  exit 1
fi
exec .venv/bin/local-assistant --foreground "$@"
