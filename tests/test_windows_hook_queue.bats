#!/usr/bin/env bats

load test_helper

setup() { setup_test_env; }
teardown() { teardown_test_env; }

@test "windows-hook-queue: lifecycle uses native enqueue and turn hooks keep their wrapper" {
  local project="$TEST_SKILL_DIR/project o'brien & [x]"
  mkdir -p "$project"
  run bash "$SCRIPTS/delivery.sh" set monitor codex "$project"
  [ "$status" -eq 0 ]
  local file="$project/.codex/hooks.json" cw cmd event
  for event in SessionStart SessionEnd; do
    cw=$(sqlite_mem "SELECT json_extract(readfile('$(rf "$file")'), '\$.hooks.$event[0].hooks[0].commandWindows');")
    printf '%s' "$cw" | grep -Fq 'hook-enqueue.ps1'
    printf '%s' "$cw" | grep -Fq -- "-Event '$event'"
    refute bash -c 'printf %s "$1" | grep -E "bash.exe|GIT_BASH|AGMSG_BASH"' _ "$cw"
    cmd=$(sqlite_mem "SELECT json_extract(readfile('$(rf "$file")'), '\$.hooks.$event[0].hooks[0].command');")
    printf '%s' "$cmd" | grep -Fq 'session-'
  done
  run bash "$SCRIPTS/delivery.sh" set turn codex "$project"
  [ "$status" -eq 0 ]
  cw=$(sqlite_mem "SELECT json_extract(readfile('$(rf "$file")'), '\$.hooks.Stop[0].hooks[0].commandWindows');")
  printf '%s' "$cw" | grep -Fq GIT_BASH
  refute grep -q hook-enqueue "$file"
  run bash "$SCRIPTS/delivery.sh" set off codex "$project"
  [ "$status" -eq 0 ]
  refute grep -q hook-enqueue "$file"
}

@test "windows-hook-queue: Windows CLI accepts Codex monitor; Claude stays synchronous" {
  local project="$TEST_SKILL_DIR/project"
  mkdir -p "$project"
  run bash "$SCRIPTS/windows/dispatch.sh" --type codex --project "$project" -- mode monitor
  [ "$status" -eq 0 ]
  grep -q hook-enqueue "$project/.codex/hooks.json"
  run bash "$SCRIPTS/delivery.sh" set monitor claude-code "$project"
  [ "$status" -eq 0 ]
  refute grep -q commandWindows "$project/.claude/settings.local.json"
}

@test "windows-hook-queue: native PowerShell transport integration" {
  skip_unless_windows "requires native Windows PowerShell and Git Bash"
  run python "$BATS_TEST_DIRNAME/test_windows_hook_queue.py"
  [ "$status" -eq 0 ]
}

@test "windows-hook-queue: current-session monitor activation" {
  skip_unless_windows "requires native Windows named pipes and Git Bash"
  run python "$BATS_TEST_DIRNAME/test_codex_monitor_activate.py"
  [ "$status" -eq 0 ]
}
