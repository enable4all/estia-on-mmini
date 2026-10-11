#!/usr/bin/env bash
# Set Estia up step by step: tools, the model for this Mac, her Signal number, who may talk to her, the config,
# always on, then the extras you pick. Safe to run again: done steps are skipped. --dry-run prints without doing.
# No key or password passes through this script; Signal's one-time captcha link and SMS code are typed hidden.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "$(uname -s)/$(uname -m)" == "Darwin/arm64" ]] || { echo "Estia needs a Mac with Apple silicon (M1 or later)." >&2; exit 1; }
dry=""; for a in "$@"; do [[ "$a" == "--dry-run" ]] && dry=1; done
run() { echo "  \$ $*"; [[ -n "$dry" ]] || "$@"; }

if ! command -v brew >/dev/null && [[ -n "$dry" ]]; then
  echo "Homebrew: would offer to install it (its installer asks for your Mac password once)"
elif ! command -v brew >/dev/null; then
  echo "Homebrew installs the tools Estia needs. Its installer asks for your Mac password once (it writes to /opt/homebrew)."
  read -r -p "Install Homebrew now? [y/N] " answer
  [[ "$answer" == [yY]* ]] || { echo "Install Homebrew from https://brew.sh, then run this again." >&2; exit 1; }
  run /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
missing=()
for tool in signal-cli ollama uv; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if ((${#missing[@]})); then run brew install "${missing[@]}"; fi
if [[ -n "$dry" ]]; then
  run brew services start ollama
  run uv python install 3.13
  run uv sync --frozen
  if [[ -x .venv/bin/python ]]; then exec .venv/bin/python -m local_assistant.setup "$@"; fi
  echo "Then: the model for this Mac's memory, her Signal number (captcha link and SMS code typed hidden),"
  echo "your account (a one-time word you send her), the config, always on (asks for your password), and the extras."
  exit 0
fi
echo "✓ Tools: signal-cli, ollama, uv"
if ! curl -s --max-time 2 http://127.0.0.1:11434/api/version >/dev/null; then
  run brew services start ollama
  for _ in $(seq 1 30); do curl -s --max-time 1 http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
fi
run uv python install 3.13
run uv sync --frozen --quiet
exec uv run --quiet python -m local_assistant.setup "$@"
