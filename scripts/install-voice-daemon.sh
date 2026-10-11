#!/usr/bin/env bash
# One-time, run by the user (prompts for sudo): build the voice service (tools/voice) and install it as an
# always-on LaunchDaemon (runs as your user, 127.0.0.1:8150). The first start downloads the models (~1.6 GB) and
# the Neural Engine compiles them (up to ~15 minutes; again after a macOS update): /health says "loading" until then.
# Then turn voice on in ~/.config/local-assistant/config.toml (take the # off [voice] and its lines)
# and restart the bridge.
# Undo: sudo launchctl bootout system/com.local-assistant.voice
#       sudo rm /Library/LaunchDaemons/com.local-assistant.voice.plist
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
[[ "$(id -u)" != 0 ]] || { echo "run as your normal user, not root (the script calls sudo itself)" >&2; exit 1; }
(cd "$repo/tools/voice" && swift build -c release --force-resolved-versions)  # exactly the versions in Package.resolved
mkdir -p "$HOME/Library/Logs/local-assistant" && chmod 700 "$HOME/Library/Logs/local-assistant"
label=com.local-assistant.voice
# fill in user, home and repo path (checked with plutil -lint), then install the result
rendered="$(mktemp -d)"
trap 'rm -rf "$rendered"' EXIT
"$repo/scripts/render-launchd.sh" "$repo/launchd/$label.plist.in" "$rendered/$label.plist"
sudo install -m 644 -o root -g wheel "$rendered/$label.plist" /Library/LaunchDaemons/
sudo launchctl bootout "system/$label" 2>/dev/null || true
# bootout is asynchronous; bootstrapping before it finishes fails with error 5
for _ in $(seq 1 50); do sudo launchctl print "system/$label" >/dev/null 2>&1 || break; sleep 0.2; done
sudo launchctl bootstrap system "/Library/LaunchDaemons/$label.plist"
echo "loaded $label; waiting for the models (first start: download and Neural Engine compile, up to ~15 minutes)"
state=""
for _ in $(seq 1 240); do
  state="$(curl -s --max-time 2 http://127.0.0.1:8150/health || true)"
  if [[ "$state" == *'"stt":"ready"'* && "$state" == *'"tts":"ready"'* ]]; then echo "voice: ready"; exit 0; fi
  [[ "$state" == *failed* ]] && break
  sleep 5
done
if [[ "$state" == *failed* ]]; then
  echo "voice: failed (${state}); see ~/Library/Logs/local-assistant/voice.log" >&2
else
  echo "voice: still loading after 20 minutes (${state:-no answer}). On a slow line the first download takes longer:" >&2
  echo "the service keeps going. Check later: curl -s http://127.0.0.1:8150/health (log: ~/Library/Logs/local-assistant/voice.log)" >&2
fi
exit 1
