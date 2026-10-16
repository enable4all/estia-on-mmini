#!/usr/bin/env bash
# Ollama's official build: setup's Ollama step on its own. An official Ollama that answers is kept; else the pinned
# release is downloaded from GitHub, checked before anything is installed (its sha256, every program's signature by
# Ollama's developer ID, Apple's notarization, both JSON-schema libraries), put in ~/opt/ollama, and run as a background
# service from the Mac's start (sudo asks for your Mac password once). Homebrew's Ollama and the Ollama app are named
# with their fix, never used.
#   scripts/install-ollama.sh            check, then install what is missing
#   scripts/install-ollama.sh --dry-run  read and show every step; install and start nothing
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
python="$repo/.venv/bin/python"
[[ $EUID -ne 0 ]] || { echo "run it as yourself: it asks for sudo itself, only for the background service" >&2; exit 2; }
[[ -x "$python" ]] || { echo "no .venv in $repo: run uv sync --frozen first" >&2; exit 1; }
exec "$python" -I -m local_assistant.ollama_build "$@"
