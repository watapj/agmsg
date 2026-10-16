#!/usr/bin/env bats
load test_helper

setup() {
  setup_test_env
  export SKILL_DIR="$TEST_SKILL_DIR"
  export PROJECT="$AGMSG_TEST_TMPDIR/project o'brien \$x & [日本語]"
  mkdir -p "$PROJECT"
  export AGMSG_SELF_RENAME=off
  export CAPTURE="$TEST_SKILL_DIR/submitted"
  source "$SCRIPTS/lib/storage.sh"
  agmsg_storage_load
}
teardown() { teardown_test_env; }

configure_pair() {
  bash "$SCRIPTS/delivery.sh" set poke "$1" "$PROJECT"
  bash "$SCRIPTS/join.sh" poketeam sender claude-code "$PROJECT" >/dev/null
  bash "$SCRIPTS/join.sh" poketeam receiver "$1" "$PROJECT" >/dev/null
}

fake_poke() {
  cat > "$SCRIPTS/poke.sh" <<'SH'
#!/usr/bin/env bash
set -eu
[ "$1" = poketeam ] && [ "$2" = receiver ]
[ "$3" = --no-fallback ] && [ "$4" = --expect-project ] && [ "$5" = "$PROJECT" ] && [ "$6" = --body-file ]
cat "$7" > "$CAPTURE"
exit "${POKE_RESULT:-0}"
SH
}

@test "poke mode: Codex and Claude remove delivery hooks, preserve unrelated hooks, and report poke" {
  for type in codex claude-code; do
    local relative=.codex/hooks.json
    [ "$type" != claude-code ] || relative=.claude/settings.local.json
    mkdir -p "$(dirname "$PROJECT/$relative")"
    printf '%s' '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo unrelated"}]}]}}' > "$PROJECT/$relative"
    bash "$SCRIPTS/delivery.sh" set turn "$type" "$PROJECT" >/dev/null
    run bash "$SCRIPTS/delivery.sh" set poke "$type" "$PROJECT"
    [ "$status" -eq 0 ]
    grep -q 'echo unrelated' "$PROJECT/$relative"
    run grep -E 'session-start.sh|check-inbox.sh' "$PROJECT/$relative"
    [ "$status" -ne 0 ]
    run bash "$SCRIPTS/delivery.sh" status "$type" "$PROJECT"
    [ "$status" -eq 0 ]
    [ "${lines[0]}" = 'mode: poke' ]
    run bash "$SCRIPTS/session-start.sh" "$type" "$PROJECT" </dev/null
    [ "$status" -eq 0 ] && [ -z "$output" ]
    run bash "$SCRIPTS/check-inbox.sh" "$type" "$PROJECT" </dev/null
    [ "$status" -eq 0 ] && [ -z "$output" ]
    run bash "$SCRIPTS/watch.sh" fixture "$PROJECT" "$type" --max-seconds=1
    [ "$status" -eq 0 ] && [ -z "$output" ]
  done
}

assert_raw_send() {
  fake_poke
  local body="$TEST_SKILL_DIR/body"
  printf '%s' $'日本語 "quoted" `literal` $(touch INJECTED) & [x]\nsecond line' > "$body"
    configure_pair "$1"
    local old
    old="$(storage_send poketeam sender receiver backlog)"
    run bash "$SCRIPTS/send.sh" poketeam sender receiver --body-file "$body"
    [ "$status" -eq 0 ]
    cmp "$body" "$CAPTURE"
    local unread
    unread="$(storage_list_unread poketeam receiver)"
    [[ "$unread" == *"$old"* ]]
    [[ "$unread" != *'second line'* ]]
    run storage_history poketeam receiver
    [[ "$output" == *'second line'* ]]
    [ ! -f INJECTED ]
}

@test "poke mode: send raw body to Codex and mark only that message read" { assert_raw_send codex; }
@test "poke mode: send raw body to Claude Code and mark only that message read" { assert_raw_send claude-code; }

@test "poke mode: failed submission stays unread, returns failure, and is not retried" {
  configure_pair codex
  fake_poke
  export POKE_RESULT=14
  run bash "$SCRIPTS/send.sh" poketeam sender receiver 'raw failure'
  [ "$status" -eq 14 ]
  [[ "$output" == *'saved unread'* ]]
  run storage_list_unread poketeam receiver
  [[ "$output" == *'raw failure'* ]]
  run storage_history poketeam receiver
  [ "$(printf '%s\n' "$output" | wc -l | tr -d '[:space:]')" -eq 1 ]
}

@test "poke mode: plain terminal refusal never falls back into recursive send" {
  configure_pair claude-code
  source "$SCRIPTS/lib/actas-lock.sh"
  printf 'plain:-\t%s\tclaude-code' "$PROJECT" > "$(agmsg_spawn_path poketeam receiver)"
  run bash "$SCRIPTS/send.sh" poketeam sender receiver 'plain failure'
  [ "$status" -ne 0 ]
  [[ "$output" == *'saved unread'* ]]
  run storage_history poketeam receiver
  [ "$(printf '%s\n' "$output" | wc -l | tr -d '[:space:]')" -eq 1 ]
}

@test "poke mode: pending setup and ambiguous target fail before storing a message" {
  configure_pair codex
  source "$SCRIPTS/lib/poke-mode.sh"
  local marker
  marker="$(agmsg_poke_mode_path codex "$PROJECT")"
  printf 'pending\n' > "$marker"
  run bash "$SCRIPTS/send.sh" poketeam sender receiver blocked
  [ "$status" -ne 0 ]
  [[ "$output" == *'incomplete'* ]]
  printf 'ready\n' > "$marker"
  bash "$SCRIPTS/join.sh" poketeam receiver codex "$AGMSG_TEST_TMPDIR" >/dev/null
  run bash "$SCRIPTS/send.sh" poketeam sender receiver blocked
  [ "$status" -ne 0 ]
  [[ "$output" == *'multiple project/type registrations'* ]]
  [ -z "$(storage_list_unread poketeam receiver)" ]
}

@test "poke mode: off removes only this type's opt-in; normal send remains queued" {
  configure_pair claude-code
  bash "$SCRIPTS/delivery.sh" set poke codex "$PROJECT" >/dev/null
  bash "$SCRIPTS/delivery.sh" set off claude-code "$PROJECT" >/dev/null
  fake_poke
  run bash "$SCRIPTS/send.sh" poketeam sender receiver queued
  [ "$status" -eq 0 ]
  [ ! -f "$CAPTURE" ]
  [[ "$(storage_list_unread poketeam receiver)" == *'queued'* ]]
  run bash "$SCRIPTS/delivery.sh" status codex "$PROJECT"
  [ "${lines[0]}" = 'mode: poke' ]
}

fake_orca() {
  mkdir -p "$TEST_SKILL_DIR/bin"
  cat > "$TEST_SKILL_DIR/bin/orca" <<'SH'
#!/usr/bin/env bash
case "$2" in
  show) echo '{"ok":true,"result":{"terminal":{"agentIdentity":"fixture"}}}' ;;
  read) jq -cn --arg draft "${DRAFT:-}" '{ok:true,result:{terminal:{draft:$draft}}}' ;;
  send)
    while [ "$#" -gt 0 ]; do
      case "$1" in --text) printf '%s' "$2" > "$CAPTURE"; shift ;; esac
      shift
    done
    echo '{"ok":true,"result":{"send":{"accepted":true}}}' ;;
esac
SH
  chmod +x "$TEST_SKILL_DIR/bin/orca"
  export PATH="$TEST_SKILL_DIR/bin:$PATH"
  source "$SCRIPTS/lib/actas-lock.sh"
  printf 'orca:term_11111111-1111-4111-8111-111111111111\t%s\t%s' "$PROJECT" "$1" > "$(agmsg_spawn_path poketeam receiver)"
}

@test "poke mode: real poke and Orca driver submit raw Claude body once" {
  configure_pair claude-code
  fake_orca claude-code
  printf '%s' $'本文 `literal` $(touch INJECTED)\n次行' > "$TEST_SKILL_DIR/body"
  run bash "$SCRIPTS/send.sh" poketeam sender receiver --body-file "$TEST_SKILL_DIR/body"
  [ "$status" -eq 0 ] || { printf '%s\n' "$output"; return 1; }
  cmp "$TEST_SKILL_DIR/body" "$CAPTURE"
  [ -z "$(storage_list_unread poketeam receiver)" ]
}

@test "poke mode: real Codex draft protection refuses without typing or consuming" {
  configure_pair codex
  fake_orca codex
  export DRAFT='human draft'
  run bash "$SCRIPTS/send.sh" poketeam sender receiver refused
  [ "$status" -eq 14 ]
  [ ! -f "$CAPTURE" ]
  [[ "$(storage_list_unread poketeam receiver)" == *'refused'* ]]
}

@test "poke mode: concurrent or interrupted send lock refuses before storing another copy" {
  configure_pair claude-code
  source "$SCRIPTS/lib/poke-mode.sh"
  local lock="$TEST_SKILL_DIR/run/poke-send.$(printf '%s\n%s' poketeam receiver | agmsg_sha1).lock"
  mkdir "$lock"
  run bash "$SCRIPTS/send.sh" poketeam sender receiver duplicate
  [ "$status" -ne 0 ]
  [[ "$output" == *'another poke send is active'* ]]
  [ -d "$lock" ]
  [ -z "$(storage_list_unread poketeam receiver)" ]
}

@test "poke mode: stale placement in another project is refused without typing" {
  configure_pair claude-code
  fake_orca claude-code
  printf 'orca:term_11111111-1111-4111-8111-111111111111\t%s\tclaude-code' "$AGMSG_TEST_TMPDIR" > "$(agmsg_spawn_path poketeam receiver)"
  run bash "$SCRIPTS/send.sh" poketeam sender receiver misplaced
  [ "$status" -ne 0 ]
  [[ "$output" == *'placement project differs'* ]]
  [ ! -f "$CAPTURE" ]
  [[ "$(storage_list_unread poketeam receiver)" == *'misplaced'* ]]
}

@test "poke mode: existing Codex launch wrapper starts normally without enabling monitor" {
  bash "$SCRIPTS/delivery.sh" set poke codex "$PROJECT" >/dev/null
  local cli="$TEST_SKILL_DIR/fake-codex"
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$@" > "$CAPTURE"\n' > "$cli"
  chmod +x "$cli"
  export AGMSG_REAL_CODEX="$cli"
  run bash "$SCRIPTS/drivers/types/codex/codex-monitor.sh" --project "$PROJECT" --codex-command codex -- --model literal
  [ "$status" -eq 0 ]
  [ "$(cat "$CAPTURE")" = $'--model\nliteral' ]
  run bash "$SCRIPTS/delivery.sh" status codex "$PROJECT"
  [ "${lines[0]}" = 'mode: poke' ]
  run find "$TEST_SKILL_DIR/run" -name 'codex-app-server.*.record'
  [ -z "$output" ]
}
