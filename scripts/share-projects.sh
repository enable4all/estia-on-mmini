#!/usr/bin/env bash
# Her read-only access to your projects folder, set as you: no sudo. In her own user (_estia) your home is closed to
# her; this adds ACL entries for _estia:
#   - your home (and each folder on the way to the projects folder): passing through by name only, never listing or
#     reading anything there;
#   - the projects folder and every folder and file in it, also what is made there later: read-only.
# Only folders and files you own are changed; a symbolic link is never followed. Run it again any time: an entry
# already there is never added twice.
# At its end it names what in your home every account may open, and in a terminal asks once whether to close it
# (chmod -h o-rwx, as you; Enter closes them, a no keeps everything as it is).
#   scripts/share-projects.sh [folder]             share (default: the folder in her config, recorded by the move)
#   scripts/share-projects.sh --dry-run [folder]   every chmod command, nothing changed
#   scripts/share-projects.sh --revoke [folder]    take it back: your home closes first
# The move into her own user (scripts/migrate-to-own-user.sh) runs it at its end.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
python="$repo/.venv/bin/python"
[[ $EUID -ne 0 ]] || { echo "run it as yourself, without sudo: the files are yours" >&2; exit 2; }
[[ -x "$python" ]] || { echo "no .venv in $repo: run uv sync --frozen first" >&2; exit 1; }
source "$(dirname "$0")/own-user.sh"  # $estia_root: a test's fake tree only, never ESTIA_ROOT
exec "$python" -I -m local_assistant.share --marker "$estia_root/usr/local/estia/migration.json" "$@"
