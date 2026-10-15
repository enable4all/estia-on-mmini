#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only (this folder modifies AGPL-3.0 code, see LICENSE and LICENSE-NOTICE.md here)
# Install the JVM build of signal-cli (Homebrew's native build cannot place calls: it lacks reflection metadata for
# TurnServerInfo). The version must match the Homebrew one (same account data). The release signature is checked
# against the maintainer's key from GitHub, and both that key and the signature's signer must have the fingerprint
# pinned below, or nothing is unpacked. Needs: brew install openjdk gnupg; gh (logged in).
# Usage: tools/call-tunnel/install-signal-cli-jvm.sh [version] [target dir]
#   default target: $LOCAL_ASSISTANT_VENDOR/signal-cli-<version>-jvm, with LOCAL_ASSISTANT_VENDOR defaulting to
#   this checkout's vendor/ (gitignored; estia-deploy copies it into her release)
set -eu

# AsamK's signal-cli release key (signal-cli's README and the keyservers name it); a key GitHub serves with any other
# fingerprint, or a signature by any other key, stops the install before anything is unpacked
FINGERPRINT=FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570

VERSION=${1:-0.14.9}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
VENDOR="${LOCAL_ASSISTANT_VENDOR:-$REPO/vendor}"
DEST=${2:-$VENDOR/signal-cli-$VERSION-jvm}
BREW=$(brew --prefix 2>/dev/null || echo /opt/homebrew)  # Apple silicon default when brew is not on PATH
GPG=$(command -v gpg || echo "$BREW/bin/gpg")
[ -e "$DEST" ] && { echo "$DEST exists: installed already (to install it again, delete that folder first)" >&2; exit 1; }
# what it needs, checked before anything is downloaded or made
command -v gh >/dev/null || { echo "gh is missing: brew install gh, then gh auth login" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh is not signed in to GitHub: run gh auth login first (a GitHub account)" >&2; exit 1; }
[ -x "$GPG" ] || { echo "gpg is missing: brew install gnupg" >&2; exit 1; }
JAVA="${JAVA_HOME:-$BREW/opt/openjdk}"
[ -x "$JAVA/bin/java" ] || { echo "Java is missing: brew install openjdk" >&2; exit 1; }
# everything happens in a temporary folder, moved into place only once the signature checks out: a failed run
# leaves nothing behind, so running it again just works
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
GNUPGHOME="$WORK/gnupg"
mkdir -m 700 "$GNUPGHOME" "$WORK/dl"
export GNUPGHOME
cd "$WORK/dl"
gh release download "v$VERSION" -R AsamK/signal-cli -p "signal-cli-$VERSION.tar.gz" -p "signal-cli-$VERSION.tar.gz.asc"
gh api users/AsamK/gpg_keys -q '.[].raw_key' > maintainer.asc
"$GPG" --quiet --import maintainer.asc
# the imported primary keys' fingerprints (pub, then its fpr line): the pinned one must be among them
"$GPG" --with-colons --fingerprint | awk -F: '$1 == "pub" { want = 1; next } $1 == "fpr" && want { print $10; want = 0 }' \
  | grep -qx "$FINGERPRINT" || { echo "the maintainer's key does not have the pinned fingerprint $FINGERPRINT" >&2; exit 1; }
# the signature must be good (exit status) and made by the pinned key: VALIDSIG names the signing key and, last, its
# primary key (a subkey's signature counts by its primary key)
"$GPG" --status-fd 1 --verify "signal-cli-$VERSION.tar.gz.asc" "signal-cli-$VERSION.tar.gz" > verify.status
awk '$2 == "VALIDSIG" { print $3; if (NF >= 12) print $NF }' verify.status | grep -qx "$FINGERPRINT" \
  || { echo "the signature is not by the key with the pinned fingerprint $FINGERPRINT" >&2; exit 1; }
mkdir "$WORK/out"
tar -xzf "signal-cli-$VERSION.tar.gz" -C "$WORK/out"
JAVA_HOME="$JAVA" "$WORK/out/signal-cli-$VERSION/bin/signal-cli" --version
mkdir -p "$(dirname "$DEST")"
mv "$WORK/out" "$DEST"
echo "installed: $DEST/signal-cli-$VERSION/bin/signal-cli"
