# Sourced by the owner's scripts: whether she runs as her own user _estia, and her commands' and sudo's full paths.
# The paths are fixed. A test points them at a fake tree only with ESTIA_TEST_ROOT: an ESTIA_ROOT left from a
# rehearsal never changes what sudo runs.
estia_root="${ESTIA_TEST_ROOT:-}"
estia_root="${estia_root%/}"
estia="$estia_root/usr/local/estia/bin/estia"
estia_deploy="$estia_root/usr/local/estia/bin/estia-deploy"
# sudo by its full path: a planted `sudo` earlier on your PATH would catch your password, and any key piped into it
sudo="$estia_root/usr/bin/sudo"
her_plist="$estia_root/Library/LaunchDaemons/com.local-assistant.bridge.plist"
# she runs as her own user when the bridge's service file says so (after a rollback it names you again)
moved() { [[ -x "$estia" && "$(/usr/libexec/PlistBuddy -c 'Print :UserName' "$her_plist" 2>/dev/null)" == _estia ]]; }
