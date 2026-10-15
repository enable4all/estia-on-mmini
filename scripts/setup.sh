#!/usr/bin/env bash
# Set Estia up step by step: tools, the model for this Mac, her Signal number, who may talk to her, the config,
# always on, then the extras you pick. Safe to run again: done steps are skipped. --dry-run prints without doing.
# No key or password passes through this script; Signal's one-time captcha link and SMS code are typed hidden.
# Run it in your own Terminal window: it asks questions and one-time codes (an AI's command tool has no terminal).
set -euo pipefail
export HOMEBREW_NO_ANALYTICS=1  # Homebrew sends usage data unless told not to: never from this setup
cd "$(dirname "$0")/.."
[[ "$(uname -s)/$(uname -m)" == "Darwin/arm64" ]] || { echo "Estia needs a Mac with Apple silicon (M1 or later)." >&2; exit 1; }
if [[ ! -e .git ]]; then  # her releases and every update are built from git: a ZIP download can never be installed
  echo "This folder is not a git clone, and her install and every update are built from git. Clone it, then run setup:" >&2
  echo "  git clone https://github.com/enable4all/estia-on-mmini.git ~/estia && cd ~/estia && scripts/setup.sh" >&2
  exit 1
fi
dry=""; for a in "$@"; do [[ "$a" == "--dry-run" ]] && dry=1; done
if [[ -z "$dry" && ! -t 0 ]]; then
  echo "Setup asks questions and Signal's one-time codes: run scripts/setup.sh in your own Terminal window." >&2
  exit 1
fi
run() { echo "  \$ $*"; [[ -n "$dry" ]] || "$@"; }

homebrew="${ESTIA_TEST_ROOT:-}/opt/homebrew"  # Homebrew's folder on Apple silicon (a test: its fake tree)
profile="$HOME/.zprofile"; [[ "${SHELL:-}" == */bash ]] && profile="$HOME/.bash_profile"
line='eval "$(/opt/homebrew/bin/brew shellenv)"'
on_path() {  # Homebrew on PATH for this run, and its line in the login profile for every new Terminal window
  eval "$("$homebrew/bin/brew" shellenv)"
  grep -qs "brew shellenv" "$profile" && return 0  # a line of its own or of Homebrew's: kept as it is
  if [[ -n "$dry" ]]; then
    echo "Homebrew: would add $line to $profile, so new Terminal windows find brew, uv and ollama"
  else
    printf '\n%s\n' "$line" >> "$profile"
    echo "✓ Homebrew: added to new Terminal windows ($line in $profile)"
  fi
}
if ! command -v brew >/dev/null && [[ -x "$homebrew/bin/brew" ]]; then on_path; fi  # installed, not on PATH yet
if ! command -v brew >/dev/null && [[ -n "$dry" ]]; then
  echo "Homebrew: would offer to install it (its installer asks for your Mac password once)"
elif ! command -v brew >/dev/null; then
  echo "Homebrew installs the tools Estia needs. Its installer asks for your Mac password once (it writes to /opt/homebrew)."
  read -r -p "Install Homebrew now? [y/N] " answer || answer=""
  [[ "$answer" == [yY]* ]] || { echo "Install Homebrew from https://brew.sh, then run this again." >&2; exit 1; }
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  url=https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh
  echo "  \$ /bin/bash -c \"\$(curl -fsSL $url)\""  # the one command, never the installer's own text
  curl -fsSL "$url" -o "$installer" \
    || { echo "Could not download Homebrew's installer: check the internet connection, then run setup again." >&2; exit 1; }
  /bin/bash "$installer"
  rm -f "$installer"
  [[ -x "$homebrew/bin/brew" ]] || { echo "Homebrew's installer did not finish: run setup again." >&2; exit 1; }
  on_path
fi
missing=()
for tool in signal-cli ollama uv; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if ((${#missing[@]})); then run brew install "${missing[@]}"; fi
if [[ -n "$dry" ]]; then
  echo "  (signal-cli must be 0.14.x; Ollama, when it does not answer: brew services start ollama, or its app)"
  run uv python install 3.13
  run uv sync --frozen
  if [[ -x .venv/bin/python ]]; then exec .venv/bin/python -m local_assistant.setup "$@"; fi
  echo "Then: the model for this Mac's memory, her Signal number (captcha link and SMS code typed hidden),"
  echo "your account (a one-time word you send her), the config, always on in her own user (asks for your password),"
  echo "the security step (each change on your yes), and the extras."
  exit 0
fi
# registration and the bridge use signal-cli 0.14's requests: another version fails later, with an obscure error
version="$(signal-cli --version 2>/dev/null || true)"
if [[ "$version" != "signal-cli 0.14."* ]]; then
  echo "Estia is written for signal-cli 0.14.x, and this Mac has ${version:-a signal-cli that gives no version}." >&2
  echo "Install signal-cli 0.14.x (brew install signal-cli, or brew upgrade signal-cli), then run setup again." >&2
  exit 1
fi
echo "✓ Tools: signal-cli, ollama, uv"
ollama_up() { curl -s --max-time 2 http://127.0.0.1:11434/api/version >/dev/null; }
if ! ollama_up; then
  if brew list --formula ollama >/dev/null 2>&1; then  # Homebrew's Ollama: its service
    run brew services start ollama
  elif [[ -d "${ESTIA_TEST_ROOT:-}/Applications/Ollama.app" ]]; then  # the app from ollama.com
    run open -a Ollama
  fi
  for _ in $(seq 1 30); do ollama_up && break; sleep 1; done
  if ! ollama_up; then
    echo "Ollama does not answer yet. Start it (open the Ollama app, or brew services start ollama when Homebrew" >&2
    echo "installed it), then run setup again." >&2
    exit 1
  fi
fi
run uv python install 3.13
run uv sync --frozen --quiet
exec uv run --quiet python -m local_assistant.setup "$@"
