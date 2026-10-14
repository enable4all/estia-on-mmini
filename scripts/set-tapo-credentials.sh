#!/usr/bin/env bash
# Store the TP-Link account login used for local Kasa/Tapo control (read only by the device helper, never by
# the bridge). Hidden prompt: the password is never echoed, logged or passed on a command line. Once she runs as her
# own user _estia, both go through pipes into /usr/bin/sudo /usr/local/estia/bin/estia secret set tapo.env <KEY>.
set -euo pipefail
dir="$HOME/.config/local-assistant"
file="$dir/tapo.env"
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
read -r -p "TP-Link account email: " email
read -r -s -p "TP-Link account password (hidden): " password
echo
[[ -n "$email" && -n "$password" ]] || { echo "email and password are both needed" >&2; exit 1; }
if moved; then
  printf '%s\n' "$email" | "$sudo" "$estia" secret set tapo.env TAPO_USERNAME >/dev/null
  printf '%s\n' "$password" | "$sudo" "$estia" secret set tapo.env TAPO_PASSWORD >/dev/null
  unset password
  echo "saved in her tapo.env (mode 600, readable only by _estia)"
  exit 0
fi
mkdir -p "$dir"
chmod 700 "$dir"
umask 077
printf 'TAPO_USERNAME=%s\nTAPO_PASSWORD=%s\n' "$email" "$password" > "$file.tmp"
mv "$file.tmp" "$file"
chmod 600 "$file"
unset password
echo "saved to $file (mode 600)"
