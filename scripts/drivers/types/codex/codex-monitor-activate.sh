#!/usr/bin/env bash
set -euo pipefail

# Called outside the Windows tool sandbox. Never start another Codex session or
# another app-server: the exact current thread must already be reachable.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
# shellcheck source=../../../lib/resolve-project.sh
source "$SKILL_DIR/scripts/lib/resolve-project.sh"
# shellcheck source=../../../lib/role-session.sh
source "$SKILL_DIR/scripts/lib/role-session.sh"

check=0
if [ "${1:-}" = --check ]; then check=1; shift; fi
PROJECT="$(agmsg_canonical_path "${1:?project required}")"
# shellcheck source=../../../lib/poke-mode.sh
source "$SKILL_DIR/scripts/lib/poke-mode.sh"
if [ -f "$(agmsg_poke_mode_path codex "$PROJECT")" ]; then
  echo 'Codex monitor is disabled while poke mode is selected.' >&2
  exit 1
fi
thread="${2:-${CODEX_THREAD_ID:-}}"
case "$thread" in ''|*[!a-zA-Z0-9._-]*) echo 'Codex monitor requires CODEX_THREAD_ID.' >&2; exit 1 ;; esac
pair="$(agmsg_role_session_match_unique codex "$PROJECT" "$thread" || true)"
if [ -z "$pair" ]; then
  # shellcheck disable=SC2016 # Literal skill invocation, not a shell variable.
  echo 'No unique role belongs to this thread. Run $agmsg actas <name>, then $agmsg mode monitor.' >&2
  exit 1
fi
registered="$("$SKILL_DIR/scripts/identities.sh" "$PROJECT" codex)"
printf '%s\n' "$registered" | cut -f1,2 | grep -Fxq "$pair" || exit 1
if [ "$check" = 1 ]; then printf '%s\n' "$pair"; exit 0; fi

# shellcheck source=./_app-server.sh
source "$SCRIPT_DIR/_app-server.sh"
# shellcheck source=../../../lib/node.sh
source "$SKILL_DIR/scripts/lib/node.sh"
# shellcheck source=../../../lib/storage.sh
source "$SKILL_DIR/scripts/lib/storage.sh"
server="$(_agmsg_codex_app_server_url "$PROJECT")"
[ -n "$server" ] || server=daemon://
export GIT_BASH
GIT_BASH="$(cygpath -m "$(command -v bash)")"
IFS=$'\t' read -r team name <<< "$pair"
agmsg_role_session_load "$team" "$name"
exec "$(agmsg_resolve_node)" "$(cygpath -m "$SCRIPT_DIR/codex-bridge.js")" \
  --project "$(cygpath -m "$PROJECT")" --pair "$pair" --thread "$thread" --app-server "$server" \
  --owner "${AGMSG_ROLE_SESSION_OWNER:-}" --activate-current --inline-inbox \
  --workspace-root "$(cygpath -m "$(agmsg_storage_dir)")" \
  --workspace-root "$(cygpath -m "$SKILL_DIR/run")" --workspace-root "$(cygpath -m "$SKILL_DIR/teams")"
