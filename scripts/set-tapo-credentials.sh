#!/usr/bin/env bash
# Store the TP-Link account login used for local Kasa/Tapo control (read only by the device helper, never by
# the bridge). Hidden prompt: the password is never echoed, logged or passed on a command line.
set -euo pipefail
dir="$HOME/.config/local-assistant"
file="$dir/tapo.env"
mkdir -p "$dir"
chmod 700 "$dir"
read -r -p "TP-Link account email: " email
read -r -s -p "TP-Link account password (hidden): " password
echo
[[ -n "$email" && -n "$password" ]] || { echo "email and password are both needed" >&2; exit 1; }
umask 077
printf 'TAPO_USERNAME=%s\nTAPO_PASSWORD=%s\n' "$email" "$password" > "$file.tmp"
mv "$file.tmp" "$file"
chmod 600 "$file"
unset password
echo "saved to $file (mode 600)"
