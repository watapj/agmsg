#!/usr/bin/env bash
# Project/type opt-in for direct terminal delivery. Runtime state belongs to
# this installation; no private setting keys are added to either CLI's config.
[ -n "${_AGMSG_POKE_MODE_SH:-}" ] && return 0
_AGMSG_POKE_MODE_SH=1
_agmsg_poke_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hash.sh
. "$_agmsg_poke_lib/hash.sh"
# shellcheck source=resolve-project.sh
. "$_agmsg_poke_lib/resolve-project.sh"

agmsg_poke_mode_path() {
  local type="$1" project="$2" key
  case "$type" in codex|claude-code) ;; *) return 1 ;; esac
  project="$(agmsg_canonical_path "$project")" || return 1
  project="$(agmsg_normalize_project_path "$project")" || return 1
  case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) project="$(printf '%s' "$project" | tr '[:upper:]' '[:lower:]')" ;; esac
  key="$(printf '%s' "$project" | agmsg_sha1)" || return 1
  printf '%s/run/delivery-poke.%s.%s\n' "$SKILL_DIR" "$type" "$key"
}

# Returns 0 for a poke target, 1 for ordinary delivery, 2 for ambiguous or
# incomplete poke setup. Never choose a project using the sender's cwd.
agmsg_poke_target() {
  local team="$1" name="$2" cfg q="'" rows type project marker="" found=0 count=0
  AGMSG_POKE_PROJECT=""
  # Keep ordinary sends on their existing path when nobody selected poke.
  for marker in "$SKILL_DIR"/run/delivery-poke.*; do [ -f "$marker" ] && break; done
  [ -f "$marker" ] || return 1
  cfg="$(agmsg_sql_readfile_path "$SKILL_DIR/teams/$team/config.json")"
  name=${name//$q/$q$q}
  rows="$(agmsg_sqlite_mem "
    WITH agent(a) AS (
      SELECT value FROM json_each(json_extract(CAST(readfile('$cfg') AS TEXT), '\$.agents'))
      WHERE key='$name'
    ), registrations(r) AS (
      SELECT value FROM agent, json_each(json_extract(a, '\$.registrations'))
      UNION SELECT a FROM agent WHERE json_extract(a, '\$.type') IS NOT NULL
    )
    SELECT DISTINCT json_extract(r, '\$.type') || char(9) || json_extract(r, '\$.project')
    FROM registrations WHERE json_extract(r, '\$.project') IS NOT NULL;
  ")" || return 2
  while IFS=$'\t' read -r type project; do
    [ -n "$type" ] && [ -n "$project" ] || continue
    count=$((count + 1))
    marker="$(agmsg_poke_mode_path "$type" "$project" 2>/dev/null)" || continue
    [ -f "$marker" ] || continue
    found=$((found + 1))
    # shellcheck disable=SC2034 # consumed by send.sh after this plain call
    AGMSG_POKE_PROJECT="$project"
    [ "$(cat "$marker")" = ready ] || { echo 'send: poke setup is incomplete; rerun mode poke in the receiving project.' >&2; return 2; }
  done <<< "$rows"
  [ "$found" -gt 0 ] || return 1
  [ "$count" -eq 1 ] || { echo 'send: poke target has multiple project/type registrations; resolve the ambiguity before sending.' >&2; return 2; }
}
