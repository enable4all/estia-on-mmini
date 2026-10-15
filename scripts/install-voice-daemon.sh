#!/usr/bin/env bash
# Voice, run by the user (asks for sudo): /usr/bin/sudo /usr/local/estia/bin/estia-deploy --voice builds the voice service
# (tools/voice) as you from the commit's own sources, puts it into her release and installs and starts it as an
# always-on LaunchDaemon (runs as her own user _estia, 127.0.0.1:8150). Her own user comes first: scripts/install-daemon.sh.
# Later updates need no extra step: each deploy builds it again when the commit's tools/voice changed.
# The first start downloads the models (~1.7 GB; the move into her own user copies them when they are there) and the
# Neural Engine compiles them (up to ~15 minutes; again after a macOS update): /health says "loading" until then.
# Then turn voice on in her config (take the # off [voice] and its lines):
#   /usr/bin/sudo /usr/local/estia/bin/estia config   (nano as her; Ctrl-O, Enter saves; Ctrl-X quits)
# and restart the bridge: /usr/bin/sudo /usr/local/estia/bin/estia restart
# Undo: /usr/bin/sudo /usr/local/estia/bin/estia stop voice
#       /usr/bin/sudo /bin/rm /Library/LaunchDaemons/com.local-assistant.voice.plist
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
[[ "$(id -u)" != 0 ]] || { echo "run as your normal user, not root (the script calls sudo itself)" >&2; exit 1; }
case "$(sw_vers -productVersion 2>/dev/null)" in  # FluidAudio 0.15.8's own advisory (its logger only)
  26.4|26.4.*|26.5|26.5.*)
    echo "warning: macOS 26.4 and 26.5 have an Apple bug that can crash English speech (Kokoro) and the voice" >&2
    echo "service with it, in the middle of a call; update to macOS 26.6 or later" >&2 ;;
esac
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
moved || { echo "her own user comes first: scripts/install-daemon.sh" >&2; exit 1; }
"$sudo" "$estia_deploy" --voice "$repo"
echo "waiting for the models (first start: download and Neural Engine compile, up to ~15 minutes)"
state=""
for _ in $(seq 1 240); do
  state="$(curl -s --max-time 2 http://127.0.0.1:8150/health || true)"
  if [[ "$state" == *'"stt":"ready"'* && "$state" == *'"tts":"ready"'* ]]; then echo "voice: ready"; exit 0; fi
  [[ "$state" == *failed* ]] && break
  sleep 5
done
if [[ "$state" == *failed* ]]; then
  echo "voice: failed (${state}); see its log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice" >&2
else
  echo "voice: still loading after 20 minutes (${state:-no answer}). On a slow line the first download takes longer:" >&2
  echo "the service keeps going. Check later: curl -s http://127.0.0.1:8150/health (log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice)" >&2
fi
exit 1
