#!/usr/bin/env bash
# Fill in a launchd template: scripts/render-launchd.sh <template.in> <output>
# Placeholders: @RUN_USER@ and @RUN_GROUP@ (her own user, _estia), @RUN_HOME@ (/var/estia), @CODE@ (the root-owned
# release, /usr/local/estia/current), @LOG_DIR@ (her services' output, /var/log/estia, root's). Override with
# LA_RUN_USER, LA_RUN_GROUP, LA_RUN_HOME, LA_CODE or LA_LOG_DIR. A *.plist.in is
# XML-escaped and checked with plutil -lint. An unknown @NAME@ or a bad plist is an error, and then nothing is written
# to <output>. The move and estia-deploy render the same way, from the root-run tools' copy of the templates, and check
# the result (her user, programs under current, no other keys).
set -euo pipefail
[[ $# -eq 2 ]] || { echo "usage: scripts/render-launchd.sh <template.in> <output>" >&2; exit 2; }
repo="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$1" ]] || { echo "no such template: $1" >&2; exit 1; }
exec "$repo/.venv/bin/python" -I -m local_assistant.ownuser render "$1" "$2"
