#!/usr/bin/env bash
# Health check: service, signal-cli child, last messages, profile, database, recent errors. Exit 1 if not running.
set -uo pipefail
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
if moved && [[ "$(id -un)" != _estia ]]; then
  echo "She runs as her own user _estia: her data is readable only by that user. Run: /usr/bin/sudo /usr/local/estia/bin/estia check"
  exit 1
fi
db="$HOME/Library/Application Support/local-assistant/assistant.db"
logs="$HOME/Library/Logs/local-assistant"
label=com.local-assistant.bridge
status=0

if launchctl print "system/$label" >/dev/null 2>&1; then
  echo "daemon:     $(launchctl print "system/$label" | awk -F' = ' '/^\tstate =/ {print $2; exit}')"
else
  echo "daemon:     not installed (foreground mode?)"
fi
if pid="$(pgrep -f '\.venv/bin/local-assistant' | head -1)" && [[ -n "$pid" ]]; then
  echo "bridge:     running (pid $pid)"
else
  echo "bridge:     NOT running"; status=1
fi
if pgrep -f 'signal-cli.*jsonRpc' >/dev/null; then echo "signal-cli: running"; else echo "signal-cli: NOT running"; status=1; fi
if voice="$(curl -s --max-time 2 http://127.0.0.1:8150/health)"; then echo "voice:      $voice"; else echo "voice:      not running (optional)"; fi
if [[ -f "$db" ]]; then
  sqlite3 "$db" <<'SQL'  # not -readonly: that fails while the bridge is stopped (no -shm file)
SELECT 'last in:    ' || COALESCE(MAX(created_at), '-') FROM messages WHERE direction = 'in';
SELECT 'last out:   ' || COALESCE(MAX(created_at), '-') FROM messages WHERE direction = 'out';
SELECT 'unanswered: ' || COUNT(*) FROM messages WHERE direction = 'in' AND status = 'received';
SELECT 'failed out: ' || COUNT(*) FROM messages WHERE direction = 'out' AND status = 'failed';
SELECT 'profile:    ' || COUNT(*) || ' facts, ' || COALESCE(SUM(LENGTH(text)), 0) || ' chars' FROM profile_facts;
SELECT 'learned:    ' || COALESCE((SELECT value FROM state WHERE key = 'last_learning_run'), 'not yet');
SQL
  echo "database:   $(du -h "$db" | cut -f1)"
else
  echo "database:   none yet"
fi
echo "recent errors:"
grep -h ' ERROR ' "$logs/bridge.log" 2>/dev/null | tail -5 || true
exit "$status"
