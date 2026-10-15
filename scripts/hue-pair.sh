#!/usr/bin/env bash
# Pair the Philips Hue Bridge on the home network: press its round button when asked. Without an address the bridge is
# found by mDNS (macOS's own dns-sd); Philips' cloud is never asked. Saves hue.env (mode 600): the bridge's address,
# the key it gives and its certificate's SHA-256 fingerprint, shown here and checked on every later connection (trust
# on first use). The key is never shown. Then it lists the bridge's lights for [[tapo.devices]].
# Once she runs as her own user _estia, it runs as her: /usr/bin/sudo /usr/local/estia/bin/estia hue-pair [address].
set -euo pipefail
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
[[ $# -le 1 ]] || { echo "usage: scripts/hue-pair.sh [bridge address]" >&2; exit 2; }
if moved && [[ "$(id -un)" != _estia ]]; then exec "$sudo" "$estia" hue-pair "$@"; fi  # her Hue key is hers
tapo="$(cd "$(dirname "$0")/../tools/tapo" && pwd)"
cd "$tapo"
[[ -x .venv/bin/python ]] || uv sync --frozen -q
exec .venv/bin/python -I hue.py pair "$@"
