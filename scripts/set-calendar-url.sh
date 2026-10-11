#!/usr/bin/env bash
# Save one Google calendar's iCal address in ~/.config/local-assistant/calendar.env (0600), read only by the calendar
# helper (tools/ical), never by the bridge. Google Calendar → Settings → the calendar → Integrate calendar → "Secret
# address in iCal format" (or "Public address in iCal format" for a calendar that is public anyway).
#   scripts/set-calendar-url.sh          the default calendar, named "calendar" (key CALENDAR_ICS_URL)
#   scripts/set-calendar-url.sh family   a calendar from [[calendar.sources]] (key CALENDAR_ICS_URL_FAMILY)
# Only that one key is replaced (and, next to it, CALENDAR_SAVED_AT[_NAME]: when it was saved, for doctor): the other
# lines stay, in one atomic write. Hidden prompt: the address is never shown, logged or passed on a command line.
set -euo pipefail
name_rule='^[a-z0-9][a-z0-9_-]{0,19}$'
name="${1:-calendar}"
if [[ $# -gt 1 ]] || ! [[ $name =~ $name_rule ]]; then
  echo "usage: scripts/set-calendar-url.sh [calendar name]  (its name in [[calendar.sources]]: lowercase letters, digits, - or _)" >&2
  exit 2
fi
if [[ "$name" == calendar ]]; then
  key=CALENDAR_ICS_URL
else
  key="CALENDAR_ICS_URL_$(printf '%s' "$name" | tr 'a-z-' 'A-Z_')"
fi
saved_key="${key/CALENDAR_ICS_URL/CALENDAR_SAVED_AT}"
dir="$HOME/.config/local-assistant"
file="$dir/calendar.env"
mkdir -p "$dir"
chmod 700 "$dir"
printf 'Paste the iCal address of calendar "%s" (secret or public; nothing shows while you paste), then press Enter:\n' "$name"
IFS= read -rs url || true
printf '\n'
url="${url#"${url%%[![:space:]]*}"}"  # trim spaces
url="${url%"${url##*[![:space:]]}"}"
if [[ "$url" != https://calendar.google.com/calendar/ical/*.ics ]]; then
  echo "That is not a Google iCal address (https://calendar.google.com/calendar/ical/…/basic.ics); nothing saved." >&2
  exit 1
fi
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
printf '%s=%s\n%s=%s\n' "$key" "$url" "$saved_key" "$(date +%s)" >> "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$file"
unset url
echo "saved $key in $file (mode 600); the bridge uses it from the next calendar read on"
