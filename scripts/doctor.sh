#!/usr/bin/env bash
# Is everything in place to start her (Estia, or the name in the config)? Read-only: installs, starts and sends nothing; prints no secrets.
# Each line is ✓ (fine), ! (works, but worth a look) or ✗ (fix this first, with the command to run). Exit 1 on ✗.
set -euo pipefail
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved && [[ "$(id -un)" != _estia ]]; then
  echo "She runs as her own user _estia: her data is readable only by that user. Run: /usr/bin/sudo /usr/local/estia/bin/estia doctor"
  exit 1
fi
cd "$(dirname "$0")/.."
exec uv run --quiet python -m local_assistant.doctor "$@"
