#!/usr/bin/env bash
# Sign in to Spotify for the music feature (PKCE, no secret): run on the Mac, open the shown address on a computer,
# sign in with the Premium account, then paste back the address the browser could not open (127.0.0.1:8888).
# Works while the bridge is running; the sign-in lasts 6 months (she reminds you before it ends).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"  # before the cd: $0 may be relative to where it was started
cd "$here/.."
source "$here/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved; then exec "$sudo" "$estia" spotify-login; fi  # the sign-in is saved in her data, as her
exec uv run --quiet local-assistant --spotify-login
