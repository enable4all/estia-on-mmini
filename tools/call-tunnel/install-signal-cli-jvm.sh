#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only (this folder modifies AGPL-3.0 code, see LICENSE and LICENSE-NOTICE.md here)
# Install the JVM build of signal-cli (Homebrew's native build cannot place calls: it lacks reflection metadata for
# TurnServerInfo). The version must match the Homebrew one (same account data). The release signature is checked
# against the maintainer's key from GitHub. Needs: brew install openjdk gnupg; gh (logged in).
# Usage: tools/call-tunnel/install-signal-cli-jvm.sh [version] [target dir]
#   default target: $LOCAL_ASSISTANT_VENDOR/signal-cli-<version>-jvm, with LOCAL_ASSISTANT_VENDOR defaulting to
#   ~/Library/Application Support/local-assistant/vendor
set -eu

VERSION=${1:-0.14.9}
VENDOR="${LOCAL_ASSISTANT_VENDOR:-$HOME/Library/Application Support/local-assistant/vendor}"
DEST=${2:-$VENDOR/signal-cli-$VERSION-jvm}
BREW=$(brew --prefix 2>/dev/null || echo /opt/homebrew)  # Apple silicon default when brew is not on PATH
GPG=$(command -v gpg || echo "$BREW/bin/gpg")
[ -e "$DEST" ] && { echo "$DEST exists: remove it first" >&2; exit 1; }
mkdir -p "$DEST/dl"
cd "$DEST/dl"
gh release download "v$VERSION" -R AsamK/signal-cli -p "signal-cli-$VERSION.tar.gz" -p "signal-cli-$VERSION.tar.gz.asc"
GNUPGHOME=$(mktemp -d)
export GNUPGHOME
trap 'rm -rf "$GNUPGHOME"' EXIT
gh api users/AsamK/gpg_keys -q '.[].raw_key' > maintainer.asc
"$GPG" --quiet --import maintainer.asc
"$GPG" --verify "signal-cli-$VERSION.tar.gz.asc" "signal-cli-$VERSION.tar.gz"
cd "$DEST"
tar -xzf "dl/signal-cli-$VERSION.tar.gz"
JAVA_HOME="${JAVA_HOME:-$BREW/opt/openjdk}" "$DEST/signal-cli-$VERSION/bin/signal-cli" --version
echo "installed: $DEST/signal-cli-$VERSION/bin/signal-cli"
