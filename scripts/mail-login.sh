#!/usr/bin/env bash
# Sign in one mailbox for the mail feature (device code): run on the Mac, open the shown link on any
# device and sign in as that mailbox's user. Works while the bridge is running. When the mailbox's calendar is
# in [[calendar.sources]], the sign-in asks for Calendars.Read too; --mail-only signs in mail alone (for an
# organisation where an administrator must approve Calendars.Read first).
set -euo pipefail
cd "$(dirname "$0")/.."
usage="usage: scripts/mail-login.sh <mailbox address> [--mail-only]"
case $# in
  1) exec uv run --quiet local-assistant --mail-login "$1" ;;
  2) [[ "$2" == --mail-only ]] || { echo "$usage" >&2; exit 2; }
     exec uv run --quiet local-assistant --mail-login "$1" --mail-only ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
