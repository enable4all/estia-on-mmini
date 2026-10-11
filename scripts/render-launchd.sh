#!/usr/bin/env bash
# Fill in a launchd template: scripts/render-launchd.sh <template.in> <output>
# Placeholders: @USER@ (login name), @HOME@ (home folder), @REPO@ (this checkout), @BREW_PREFIX@ (Homebrew prefix).
# The values come from the person running it; override with LA_USER, LA_HOME, LA_REPO or LA_BREW_PREFIX.
# Output of a *.plist.in is XML-escaped and checked with plutil -lint. An unknown @NAME@ or a bad plist is an
# error, and then nothing is written to <output>. Used by install-daemon.sh and install-voice-daemon.sh.
set -euo pipefail
[[ $# -eq 2 ]] || { echo "usage: scripts/render-launchd.sh <template.in> <output>" >&2; exit 2; }
template="$1"
out="$2"
[[ -f "$template" ]] || { echo "no such template: $template" >&2; exit 1; }

user="${LA_USER:-$(id -un)}"
home="${LA_HOME:-$HOME}"
repo="${LA_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
if [[ -n "${LA_BREW_PREFIX:-}" ]]; then
  brew_prefix="$LA_BREW_PREFIX"
elif command -v brew >/dev/null 2>&1; then
  brew_prefix="$(brew --prefix)"
else
  brew_prefix=/opt/homebrew  # Apple silicon default
fi

is_plist=0
[[ "$template" == *.plist.in ]] && is_plist=1

# a value goes into a sed replacement (delimiter |): escape \ & and |; a plist also needs XML escaping
escape() {
  local value="$1"
  if (( is_plist )); then
    value="$(printf '%s' "$value" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')"
  fi
  printf '%s' "$value" | sed -e 's/[\\&|]/\\&/g'
}

sed_args=()
for placeholder in $(grep -oE '@[A-Z_]+@' "$template" | sort -u || true); do
  case "$placeholder" in
    @USER@) value="$user" ;;
    @HOME@) value="$home" ;;
    @REPO@) value="$repo" ;;
    @BREW_PREFIX@) value="$brew_prefix" ;;
    *) echo "$template: unknown placeholder $placeholder" >&2; exit 1 ;;
  esac
  [[ -n "$value" && "$value" != *$'\n'* ]] || { echo "empty or multi-line value for $placeholder" >&2; exit 1; }
  # newsyslog.conf fields are separated by white space
  if (( ! is_plist )) && [[ "$value" == *[[:space:]]* ]]; then
    echo "$placeholder is \"$value\": white space is not allowed in $template" >&2; exit 1
  fi
  sed_args+=(-e "s|$placeholder|$(escape "$value")|g")
done

tmp="$(mktemp "$out.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
sed ${sed_args[@]+"${sed_args[@]}"} "$template" > "$tmp"
if (( is_plist )); then
  plutil -lint "$tmp" >/dev/null || { echo "$template: the rendered plist is not valid (plutil -lint)" >&2; exit 1; }
fi
chmod 644 "$tmp"
mv "$tmp" "$out"
