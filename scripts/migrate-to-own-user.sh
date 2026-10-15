#!/usr/bin/env bash
# Move her into her own hidden user _estia: her data to /var/estia (0700), her code to a root-owned release in
# /usr/local/estia, both services run as _estia from there. Run it as yourself; the real move asks for sudo once.
#   scripts/migrate-to-own-user.sh --dry-run      every step and command, nothing changed (no sudo)
#   ESTIA_ROOT=/tmp/estia-rehearsal scripts/migrate-to-own-user.sh --rehearse
#                                                 the whole move into that folder from a copy of her data: no user,
#                                                 no launchctl, your folders untouched (delete the folder afterwards)
#   scripts/migrate-to-own-user.sh                the move: build her release while she runs, then stop, create
#                                                 _estia, copy her data, install, start, check; on any failure
#                                                 (a Ctrl-C or a dropped SSH session too) her old setup comes back
#   scripts/migrate-to-own-user.sh --rollback     back to your user (within 7 days), with what she saved since:
#                                                 /usr/bin/sudo /usr/local/estia/bin/estia rollback
#   scripts/migrate-to-own-user.sh --remove-old   delete her old folders held for the rollback (no rollback after):
#                                                 /usr/bin/sudo /usr/local/estia/bin/estia remove-old
# At its end, as you and without sudo, your projects folder ([projects] root, default ~/dev) is shared with her
# read-only: scripts/share-projects.sh (the dry run shows its chmod commands too).
# Run the real move inside screen (built into macOS) or tmux when you are on SSH: it keeps going if the connection drops.
# The first move only: once she runs in her own user it stops before any sudo (it would run this checkout as root).
# Updates from then on: /usr/bin/sudo /usr/local/estia/bin/estia-deploy
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
python="$repo/.venv/bin/python"
usage="usage: scripts/migrate-to-own-user.sh [--dry-run | --rehearse | --rollback | --remove-old]"
[[ $EUID -ne 0 ]] || { echo "run it as yourself: it asks for sudo when it needs it" >&2; exit 2; }
[[ $# -le 1 ]] || { echo "$usage" >&2; exit 2; }
source "$(dirname "$0")/own-user.sh"  # $estia: her commands' fixed path
if [[ -z "${1:-}" ]] && moved; then  # never this checkout's Python as root once she is moved
  echo "✓ already in her own user: use /usr/bin/sudo /usr/local/estia/bin/estia-deploy (it shows the commits first)"
  exit 0
fi
case "${1:-}" in --rollback|--remove-old) ;; *) [[ -x "$python" ]] || { echo "no .venv in $repo: run uv sync --frozen first" >&2; exit 1; } ;; esac
share() { "$python" -I -m local_assistant.share --marker "$estia_root/usr/local/estia/migration.json" "$@"; }
case "${1:-}" in
  --rehearse) exec "$python" -I -m local_assistant.ownuser migrate --checkout "$repo" "$1" ;;
  --dry-run)
    "$python" -I -m local_assistant.ownuser migrate --checkout "$repo" --dry-run
    echo "Her projects folder, read-only for her, at the end of the move (as you, no sudo):"
    share --dry-run ;;
  "")
    "$sudo" "$python" -I -m local_assistant.ownuser migrate --checkout "$repo"
    echo "Her projects folder, read-only for her (as you, no sudo):"
    share || { echo "! She runs in her own user; her projects folder is not shared yet: scripts/share-projects.sh" >&2; exit 1; } ;;
  --rollback|--remove-old)  # after the move: the root-owned tools do it, never this checkout's Python as root
    [[ -x "$estia" ]] || { echo "no move into her own user is on record: nothing to go back from" >&2; exit 1; }
    exec "$sudo" "$estia" "${1#--}" ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
