#!/usr/bin/env bash
# claude-code delivery plug.
#
# Uses the default JSON event-hooks apply (agmsg_delivery_apply). On enable
# (monitor/both) it emits the in-session Monitor directive so a running Claude
# Code session starts streaming immediately. Sourced into delivery.sh's context,
# so emit_monitor_directive is in scope. Args: on_enable <mode> <type> <project>.
agmsg_delivery_on_enable() {
  echo "Future sessions: SessionStart hook will auto-launch the watcher."
  emit_monitor_directive "$2" "$3"
}

agmsg_delivery_prepare_settings() {
  local settings="$1" mode="$2" temp sql_path
  [ "$mode" = poke ] || return 0
  # Orca's draft API also reports Claude's ghost suggestions as typed text.
  # Disable suggestions for this project instead of bypassing draft safety.
  temp="$(mktemp "${TMPDIR:-/tmp}/agmsg-poke-settings.XXXXXX")" || return 1
  sql_path="$(agmsg_sql_readfile_path "$settings")"
  if ! agmsg_sqlite_mem "SELECT json_set(CAST(readfile('$sql_path') AS TEXT), '\$.promptSuggestionEnabled', json('false'));" > "$temp"; then
    rm -f "$temp"
    return 1
  fi
  mv "$temp" "$settings"
  echo 'Poke settings: promptSuggestionEnabled=false for this project; Orca may report unaccepted suggestions as drafts.'
  echo 'A running session may need /config: Prompt suggestions off, or a restart, to clear an existing suggestion. Real drafts are still protected.'
  echo 'This preference remains off when leaving poke mode; re-enable it explicitly in project settings when wanted.'
}
