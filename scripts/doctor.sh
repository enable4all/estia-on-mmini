#!/usr/bin/env bash
# Is everything in place to start her (Estia, or the name in the config)? Read-only: installs, starts and sends nothing; prints no secrets.
# Each line is ✓ (fine), ! (works, but worth a look) or ✗ (fix this first, with the command to run). Exit 1 on ✗.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --quiet python -m local_assistant.doctor "$@"
