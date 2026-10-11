#!/usr/bin/env bash
# Sign in to Spotify for the music feature (PKCE, no secret): run on the Mac, open the shown address on a computer,
# sign in with the Premium account, then paste back the address the browser could not open (127.0.0.1:8888).
# Works while the bridge is running; the sign-in lasts 6 months (she reminds you before it ends).
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --quiet local-assistant --spotify-login
