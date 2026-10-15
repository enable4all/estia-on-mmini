#!/usr/bin/env bash
# Is everything in place to start her (Estia, or the name in the config)? Read-only: installs, starts and sends nothing; prints no secrets.
# Each line is ✓ (fine), ! (works, but worth a look) or ✗ (fix this first, with the command to run). Exit 1 on ✗.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved && [[ "$(id -un)" != _estia ]]; then
  echo "She runs as her own user _estia: her data is readable only by that user. Run: /usr/bin/sudo /usr/local/estia/bin/estia doctor"
  exit 1
fi
cd "$here/.."
# Homebrew installed but not on PATH (a Terminal window opened before its line was in ~/.zprofile): its tools count
homebrew="${ESTIA_TEST_ROOT:-}/opt/homebrew"
if ! command -v brew >/dev/null && [[ -x "$homebrew/bin/brew" ]]; then eval "$("$homebrew/bin/brew" shellenv)"; fi
# the environment setup made, run as it is: `uv run` could install or download packages
if [[ ! -x .venv/bin/python ]]; then
  echo "No environment yet in $(pwd): run scripts/setup.sh first (it installs the tools and makes it)." >&2
  exit 1
fi
exec .venv/bin/python -I -m local_assistant.doctor "$@"
