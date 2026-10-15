#!/usr/bin/env bash
# Her access to your documents folder, always ~/Estia-KB, set as you: no sudo. In her own user (_estia) your home is
# closed to her; this adds ACL entries:
#   - your home: passing through by name only, never listing or reading anything there;
#   - ~/Estia-KB (made, private to you, when missing) and every folder and file in it: she lists and reads them, adds
#     files to the folder itself (never a folder, never anything below it), and may never delete or change yours;
#   - what she adds is yours to open, move or delete in Finder (an entry for you, inherited by every new file).
# The folder is directly in your home: macOS guards Documents, Desktop and Downloads; a folder in your home needs no
# extra permission. Only folders and files you own are changed; a symbolic link is never followed, and a file with a
# second name (a hard link: it may be a file of yours from elsewhere) is never shared, and a folder that holds a git
# checkout (.git) is refused. Run it again any time: an entry already there is never added twice.
#   scripts/share-documents.sh             share
#   scripts/share-documents.sh --dry-run   every command, nothing changed
#   scripts/share-documents.sh --revoke    take it back: the way closes first; her index of the documents goes at her
#                                          next look, and a new share starts a fresh one
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
python="$repo/.venv/bin/python"
[[ $EUID -ne 0 ]] || { echo "run it as yourself, without sudo: the files are yours" >&2; exit 2; }
[[ -x "$python" ]] || { echo "no .venv in $repo: run uv sync --frozen first" >&2; exit 1; }
source "$(dirname "$0")/own-user.sh"  # $estia_root: a test's fake tree only, never ESTIA_ROOT
exec "$python" -I -m local_assistant.share --documents --marker "$estia_root/usr/local/estia/migration.json" "$@"
