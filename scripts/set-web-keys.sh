#!/usr/bin/env bash
# Copy the web search and maps keys from the login keychain (services "brave-search", "ollama", "google-maps") into
# ~/.config/local-assistant/web.env (0600). The bridge reads that file per search: the always-on daemon cannot read
# the login keychain. Keys are never shown, logged or passed on a command line; only their lengths are printed.
# Run again after changing a key in the keychain (no restart needed).
# Add a key to the keychain with a hidden prompt: security add-generic-password -U -a "$USER" -s brave-search -w
# (same with -s ollama or -s google-maps). Without a keychain, write web.env yourself (mode 0600, one per line):
# BRAVE_API_KEY=..., OLLAMA_API_KEY=..., GOOGLE_MAPS_API_KEY=...
# A key the keychain does not hold keeps its line from the old web.env; to remove a key, delete its line there.
set +x  # never trace: the keys pass through variables
set -euo pipefail
kc="$HOME/Library/Keychains/login.keychain-db"
dir="$HOME/.config/local-assistant"
file="$dir/web.env"

read_key() { security find-generic-password -s "$1" -w "$kc" 2>/dev/null | tr -d '\r\n' || true; }

brave="$(read_key brave-search)"
ollama="$(read_key ollama)"
maps="$(read_key google-maps)"
if [[ -z "$brave" && -z "$ollama" && -z "$maps" ]]; then
  echo "The login keychain is locked (or holds no key): unlocking it."
  security unlock-keychain "$kc"
  brave="$(read_key brave-search)"
  ollama="$(read_key ollama)"
  maps="$(read_key google-maps)"
fi
if [[ -z "$brave" && -z "$ollama" && -z "$maps" ]]; then
  echo "No key is in the login keychain (services brave-search, ollama, google-maps); nothing saved." >&2
  exit 1
fi
keep() { [[ -f "$file" ]] && grep -E "^$1=" "$file" | tail -1 || true; }  # a hand-written line, never printed
kept=""
for name in BRAVE_API_KEY OLLAMA_API_KEY GOOGLE_MAPS_API_KEY; do
  case "$name" in BRAVE_API_KEY) have="$brave" ;; OLLAMA_API_KEY) have="$ollama" ;; *) have="$maps" ;; esac
  if [[ -z "$have" ]] && [[ -n "$(keep "$name")" ]]; then kept="$kept $name"; fi
done
mkdir -p "$dir"
chmod 700 "$dir"
umask 077
tmp="$(mktemp "$dir/.web.XXXXXX")"
trap 'rm -f "$tmp"' EXIT  # no copy of the keys left behind if this stops halfway
{
  if [[ -n "$brave" ]]; then printf 'BRAVE_API_KEY=%s\n' "$brave"; else keep BRAVE_API_KEY; fi
  if [[ -n "$ollama" ]]; then printf 'OLLAMA_API_KEY=%s\n' "$ollama"; else keep OLLAMA_API_KEY; fi
  if [[ -n "$maps" ]]; then printf 'GOOGLE_MAPS_API_KEY=%s\n' "$maps"; else keep GOOGLE_MAPS_API_KEY; fi
  true
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$file"
echo "saved to $file (mode 600): Brave key ${#brave}, Ollama key ${#ollama}, Google Maps key ${#maps} characters (0 = not in the keychain)"
if [[ -n "$kept" ]]; then echo "kept from the old web.env:$kept"; fi
unset brave ollama maps
