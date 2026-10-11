#!/usr/bin/env bash
# Set up Google for her places and routes with the gcloud CLI: a project, its billing link, Places API (New)
# and Routes API switched on, and an API key that may call only those two. The key goes into the login keychain
# (service google-maps) and her key file (scripts/set-web-keys.sh); it is never printed or logged.
# Before: `gcloud auth login --no-launch-browser` (once). Usage: scripts/setup-google-maps.sh ["Project name"] [project-id] [key-id]
# Daily caps: she stops at [maps] daily_cap itself (default 30); daily quotas of 30 in the Cloud console are a
# second guard in case the key ever leaks (recommended; the console page is printed at the end).
set +x  # never trace: the key passes through a variable
set -euo pipefail
name="${1:-Estia AI}"
project="${2:-estia-ai-$(openssl rand -hex 3)}"
key_id="${3:-estia-maps}"  # an existing key by its id: the third argument
cd "$(dirname "$0")/.."

account="$(gcloud config get-value account 2>/dev/null || true)"
[[ -n "$account" ]] || { echo "Not signed in: run gcloud auth login --no-launch-browser first." >&2; exit 1; }

if gcloud projects describe "$project" >/dev/null 2>&1; then
  echo "project $project exists: using it"
else
  echo "creating project \"$name\" ($project)"
  gcloud projects create "$project" --name="$name" --quiet >/dev/null
fi

billing="$(gcloud billing accounts list --filter=open=true --format='value(name)' | head -1)"
[[ -n "$billing" ]] || { echo "No open billing account on this Google account." >&2; exit 1; }
billing_name="$(gcloud billing accounts describe "$billing" --format='value(displayName)' 2>/dev/null || true)"
read -r -p "Link billing account \"${billing_name:-$billing}\" ($billing) to $project? [y/N] " answer
[[ "$answer" == [yY]* ]] || { echo "Stopped: nothing was linked." >&2; exit 1; }
gcloud billing projects link "$project" --billing-account="$billing" --quiet >/dev/null

echo "switching on Places API (New) and Routes API"
gcloud services enable places.googleapis.com routes.googleapis.com apikeys.googleapis.com --project="$project" --quiet

if ! gcloud services api-keys describe "$key_id" --project="$project" >/dev/null 2>&1; then
  echo "creating an API key allowed for those two APIs only"
  gcloud services api-keys create --project="$project" --key-id="$key_id" --display-name="Estia places and routes" \
    --api-target=service=places.googleapis.com --api-target=service=routes.googleapis.com --quiet >/dev/null 2>&1
fi
key="$(gcloud services api-keys get-key-string "$key_id" --project="$project" --format='value(keyString)')"
[[ -n "$key" ]] || { echo "Could not read the new key." >&2; exit 1; }

kc="$HOME/Library/Keychains/login.keychain-db"
save_key() {  # through security's own input (printf is a shell builtin): the key never shows up in `ps`
  printf 'add-generic-password -U -a %s -s google-maps -w %s %s\n' "$USER" "$key" "$kc" | security -i >/dev/null 2>&1
  [[ "$(security find-generic-password -a "$USER" -s google-maps -w "$kc" 2>/dev/null)" == "$key" ]]  # compared, never shown
}
save_key || {
  echo "The login keychain is locked: unlocking it."
  security unlock-keychain "$kc"
  save_key || { echo "Could not save the key in the login keychain." >&2; exit 1; }
}
echo "key saved in the login keychain (service google-maps, ${#key} characters)"
unset key
scripts/set-web-keys.sh
echo "done: project $project. Add [maps] to the config (home = \"...\") and restart the bridge."
echo "Recommended: set the daily quotas of both APIs to 30 at https://console.cloud.google.com/apis/dashboard?project=$project"
