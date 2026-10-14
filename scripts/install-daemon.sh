#!/usr/bin/env bash
# Always on, run by the user (asks for sudo): she runs as her own hidden user _estia, from a root-owned release in
# /usr/local/estia, and starts at boot without a login.
#   First time, or from an install that runs as you: the move into her own user (scripts/migrate-to-own-user.sh).
#   Already in her own user: a new release from this checkout (/usr/bin/sudo /usr/local/estia/bin/estia-deploy).
# Undo, within 7 days of the move: scripts/migrate-to-own-user.sh --rollback
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
[[ "$(id -u)" != 0 ]] || { echo "run as your normal user, not root (the script calls sudo itself)" >&2; exit 1; }
if pgrep -f '\.venv/bin/local-assistant --foreground' >/dev/null; then
  echo "stop the foreground bridge first (Ctrl-C in its terminal)" >&2; exit 1
fi
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved; then
  exec "$sudo" "$estia_deploy" "$repo"
fi
uv sync --frozen  # the move runs from this checkout's environment
exec "$repo/scripts/migrate-to-own-user.sh"
