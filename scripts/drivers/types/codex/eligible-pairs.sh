#!/usr/bin/env bash
set -euo pipefail

# Print identities this bridge may read right now. This deliberately resolves
# locks at read time: a role may be claimed by another session after the bridge
# armed its watcher, and inbox.sh would otherwise mark that role's rows read.
PROJECT="${1:?project required}"
TYPE="${2:?type required}"
shift 2
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC2034 # required by sourced subscription/role libraries
SKILL_DIR="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
source "$SCRIPT_DIR/../../../lib/actas-lock.sh"
source "$SCRIPT_DIR/../../../lib/subscription.sh"

requested=""
owner_id="${AGMSG_CODEX_OWNER_ID:-}"
thread=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --pair) requested="${requested:+$requested$'\n'}${2:?pair required}"; shift 2 ;;
    --owner) owner_id="${2:?owner required}"; shift 2 ;;
    --thread) thread="${2:?thread required}"; shift 2 ;;
    *) echo "eligible-pairs: unknown option: $1" >&2; exit 1 ;;
  esac
done

pairs="$(agmsg_subscription_pairs "$PROJECT" "$TYPE" "$owner_id" "" "" "$requested")"
if [ -n "$thread" ]; then
  source "$SCRIPT_DIR/../../../lib/role-session.sh"
  source "$SCRIPT_DIR/../../../lib/resolve-project.sh"
  project_physical="$(agmsg_canonical_path "$PROJECT")"
  current="$(agmsg_role_session_match_unique "$TYPE" "$project_physical" "$thread")" || {
    echo 'eligible-pairs: cannot confirm a unique role for this thread' >&2; exit 75;
  }
  case $'\n'"$pairs"$'\n' in
    *$'\n'"$current"$'\n'*) pairs="$current" ;;
    *) echo 'eligible-pairs: current thread does not own the requested subscription' >&2; exit 75 ;;
  esac
fi
printf '%s\n' "$pairs"
