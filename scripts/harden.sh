#!/usr/bin/env bash
# The Mac's security step around her: each setting with its state and one line on why; on your yes it is applied
# (your Mac password when first needed): SSH with keys only, no passwordless sudo, her files private, her
# Registration Lock. What only you can change (FileVault, automatic login, the screen lock, security responses, other
# admins, Tailscale) is shown with its fix, never applied. Ends with the phone checklist.
#   scripts/harden.sh            ask, then apply what you said yes to
#   scripts/harden.sh --dry-run  show every check and the commands it would run; change nothing
#   scripts/harden.sh --nuclear  also the firewall, stealth mode, python3 and ruby off the firewall's list and the
#                                keychain lock: it may stop other services on this Mac (it says how, then asks each)
#   scripts/harden.sh --new-pin  also a new Registration Lock PIN while the lock is on (after a compromise)
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
python="$repo/.venv/bin/python"
[[ $EUID -ne 0 ]] || { echo "run it as yourself: it asks for sudo once, before it changes anything" >&2; exit 2; }
[[ -x "$python" ]] || { echo "no .venv in $repo: run uv sync --frozen first" >&2; exit 1; }
exec "$python" -I -m local_assistant.harden "$@"
