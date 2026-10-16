#!/usr/bin/env bash
set -euo pipefail

# Fixed out-of-sandbox entrypoint. Data arrives via bounded environment fields
# and raw stdin; never source/eval any queue content or project-owned script.
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
case "${AGMSG_HOOK_EVENT:-}" in
  SessionStart) hook=session-start.sh ;;
  SessionEnd) hook=session-end.sh ;;
  *) echo 'agmsg hook replay: unsupported event' >&2; exit 1 ;;
esac
# Let MSYS resolve its mount aliases (notably /tmp) instead of manufacturing
# /c/... spellings that would disagree with existing role/seat project keys.
PROJECT="$(cygpath -u "${AGMSG_HOOK_PROJECT:?Missing project}")"
# A seat from another allowed project must never be republished for this one.
# Reuse the current seat record parser and project hash contract.
if [ -n "${AGMSG_CODEX_SEAT_KEY:-}" ]; then
  source "$SCRIPT_DIR/drivers/types/codex/_seat-key.sh"
  source "$SCRIPT_DIR/lib/hash.sh"
  SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  _agmsg_codex_seat_record_read "$SKILL_DIR/run/codex-app-server.$AGMSG_CODEX_SEAT_KEY.record" || {
    echo 'agmsg hook replay: seat record missing or malformed' >&2; exit 1;
  }
  [ "$SEAT_REC_PROJECT" = "$(printf '%s' "$PROJECT" | agmsg_sha1)" ] || {
    echo 'agmsg hook replay: seat project mismatch' >&2; exit 1;
  }
fi
exec bash "$SCRIPT_DIR/$hook" codex "$PROJECT"
