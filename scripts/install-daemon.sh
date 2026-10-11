#!/usr/bin/env bash
# One-time, run by the user (prompts for sudo): install the bridge as an always-on LaunchDaemon (runs as your user).
# Undo: sudo launchctl bootout system/com.local-assistant.bridge
#       sudo rm /Library/LaunchDaemons/com.local-assistant.bridge.plist /etc/newsyslog.d/local-assistant.conf
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
[[ "$(id -u)" != 0 ]] || { echo "run as your normal user, not root (the script calls sudo itself)" >&2; exit 1; }
if pgrep -f '\.venv/bin/local-assistant' >/dev/null; then
  echo "stop the foreground bridge first (Ctrl-C in its terminal)" >&2; exit 1
fi
uv sync --frozen
(cd tools/tapo && uv sync --frozen)  # the device helper's own environment (python-kasa)
(cd tools/ical && uv sync --frozen)  # the calendar helper's own environment (icalendar)
mkdir -p "$HOME/Library/Logs/local-assistant" && chmod 700 "$HOME/Library/Logs/local-assistant"
label=com.local-assistant.bridge
# fill in user, home, repo path and Homebrew prefix (checked with plutil -lint), then install the results
rendered="$(mktemp -d)"
trap 'rm -rf "$rendered"' EXIT
scripts/render-launchd.sh "launchd/$label.plist.in" "$rendered/$label.plist"
scripts/render-launchd.sh launchd/local-assistant.newsyslog.conf.in "$rendered/local-assistant.conf"
sudo install -m 644 -o root -g wheel "$rendered/$label.plist" /Library/LaunchDaemons/
sudo install -m 644 -o root -g wheel "$rendered/local-assistant.conf" /etc/newsyslog.d/local-assistant.conf
sudo launchctl bootout "system/$label" 2>/dev/null || true
# bootout is asynchronous; bootstrapping before it finishes fails with error 5
for _ in $(seq 1 50); do sudo launchctl print "system/$label" >/dev/null 2>&1 || break; sleep 0.2; done
sudo launchctl bootstrap system "/Library/LaunchDaemons/$label.plist"
echo "loaded $label; waiting for signal-cli"
for _ in $(seq 1 60); do pgrep -f 'signal-cli.*jsonRpc' >/dev/null && break; sleep 1; done
scripts/check.sh
