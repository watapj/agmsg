#!/usr/bin/env bash
# Archive only a byte-identical legacy placement. Never choose between two
# different pane records, and never modify a pane or its role ownership.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/validate.sh"
source "$SCRIPT_DIR/lib/actas-lock.sh"
TEAM="${1:?Usage: placement-dedup.sh <team> <name>}"
NAME="${2:?agent name required}"
agmsg_validate_team_name "$TEAM"
agmsg_validate_agent_name "$NAME"
key="$(_agmsg_id_key_for "$TEAM" "$NAME")"
id="$SKILL_DIR/run/spawn.$key"
legacy="$SKILL_DIR/run/spawn.$(_actas_lock_encode "$TEAM")__$(_actas_lock_encode "$NAME")"
if [ ! -e "$id" ] || [ ! -e "$legacy" ]; then echo 'No duplicate placement.'; exit 0; fi
cmp -s "$id" "$legacy" || { echo 'placement-dedup: records differ; the owning session must repair its placement with agmsg fix.' >&2; exit 1; }
archive="$(mktemp -d "$SKILL_DIR/run/placement-duplicate.XXXXXX")"
mv "$legacy" "$archive/legacy"
if ! cmp -s "$id" "$archive/legacy" || [ -e "$legacy" ]; then
  # A concurrent writer changed a record. Restore without overwriting it and
  # preserve the archived bytes even when restoration is no longer possible.
  ln "$archive/legacy" "$legacy" 2>/dev/null || true
  echo "placement-dedup: concurrent change; inspect $archive/legacy; no identity was selected." >&2
  exit 1
fi
echo "Identical legacy placement archived at $archive/legacy"
