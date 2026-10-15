#!/usr/bin/env bash
# Save one CalDAV calendar's app-specific password (iCloud, Fastmail, Nextcloud) in ~/.config/local-assistant/calendar.env
# (0600), read only by the calendar helper (tools/ical), never by the bridge. iCloud needs an app-specific password:
# account.apple.com → Sign-In and Security → App-Specific Passwords; Fastmail: Settings → Privacy & Security →
# App passwords; Nextcloud: Personal settings → Security → Devices & sessions.
#   scripts/set-calendar-password.sh          the calendar named "calendar" (key CALDAV_PASSWORD)
#   scripts/set-calendar-password.sh family   a calendar from [[calendar.sources]] (key CALDAV_PASSWORD_FAMILY)
# Only that one key is replaced (and, next to it, CALENDAR_SAVED_AT[_NAME]: when it was saved, for doctor): the other
# lines stay, in one atomic write. Hidden prompt: the password is never shown, logged or passed on a command line.
# Once she runs as her own user _estia, the password goes through a pipe into
# /usr/bin/sudo /usr/local/estia/bin/estia secret set calendar.env <KEY>, which writes her calendar.env the same way.
set -euo pipefail
name_rule='^[a-z0-9][a-z0-9_-]{0,19}$'
name="${1:-calendar}"
if [[ $# -gt 1 ]] || ! [[ $name =~ $name_rule ]]; then
  echo "usage: scripts/set-calendar-password.sh [calendar name]  (its name in [[calendar.sources]]: lowercase letters, digits, - or _)" >&2
  exit 2
fi
if [[ "$name" == calendar ]]; then
  key=CALDAV_PASSWORD
else
  key="CALDAV_PASSWORD_$(printf '%s' "$name" | tr 'a-z-' 'A-Z_')"
fi
saved_key="${key/CALDAV_PASSWORD/CALENDAR_SAVED_AT}"
dir="$HOME/.config/local-assistant"
file="$dir/calendar.env"
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
printf 'Paste the app-specific password of calendar "%s" (nothing shows while you paste), then press Enter:\n' "$name"
IFS= read -rs password || true
printf '\n'
password="${password#"${password%%[![:space:]]*}"}"  # trim spaces
password="${password%"${password##*[![:space:]]}"}"
if [[ -z "$password" ]]; then
  echo "No password given; nothing saved." >&2
  exit 1
fi
if moved; then
  printf '%s\n' "$password" | "$sudo" "$estia" secret set calendar.env "$key" >/dev/null
  unset password
  echo "saved $key in her calendar.env (mode 600, readable only by _estia); the bridge uses it from the next calendar read on"
  exit 0
fi
mkdir -p "$dir"
chmod 700 "$dir"
umask 077
tmp="$(mktemp "$dir/.calendar.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
if [[ -f "$file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    found="${line%%=*}"
    found="${found//[[:space:]]/}"
    [[ "$line" == *=* && ( "$found" == "$key" || "$found" == "$saved_key" ) ]] && continue  # this calendar's: below
    printf '%s\n' "$line"
  done < "$file" > "$tmp"
fi
printf '%s=%s\n%s=%s\n' "$key" "$password" "$saved_key" "$(date +%s)" >> "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$file"
unset password
echo "saved $key in $file (mode 600); the bridge uses it from the next calendar read on"
