#!/usr/bin/env bats

setup() {
  export SKILL_DIR="$BATS_TEST_TMPDIR/skill"
  export READ_LOG="$BATS_TEST_TMPDIR/locks-read"
  mkdir -p "$SKILL_DIR/scripts"
  cat > "$SKILL_DIR/scripts/identities.sh" <<'EOF'
#!/usr/bin/env bash
printf 'team\talice\nteam\tbob\nother\talice\nteam-extra\talice\nteam\theld\nteam\tunknown\n'
EOF
  chmod +x "$SKILL_DIR/scripts/identities.sh"
  : > "$READ_LOG"
  source "$BATS_TEST_DIRNAME/../scripts/lib/subscription.sh"
  actas_lock_state() {
    printf '%s/%s\n' "$1" "$2" >> "$READ_LOG"
    case "$2" in held) echo other:peer ;; unknown) echo unknown:unreadable ;; *) echo free ;; esac
  }
  actas_lock_path() { echo /fixture/lock; }
  agmsg_instance_is_composite() { return 1; }
}

@test "subscription pair filter checks only the exact registered role" {
  run agmsg_subscription_pairs /project codex '' '' '' $'team\talice'
  [ "$status" -eq 0 ]
  [ "$output" = $'team\talice' ]
  [ "$(cat "$READ_LOG")" = team/alice ]
}

@test "subscription pair filter cannot subscribe an unregistered role" {
  run agmsg_subscription_pairs /project codex '' '' '' $'missing\talice'
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -s "$READ_LOG" ]
}

@test "subscription pair filter supports multiple exact team and role pairs" {
  run agmsg_subscription_pairs /project codex '' '' '' $'team\tbob\nother\talice'
  [ "$status" -eq 0 ]
  [ "$output" = $'team\tbob\nother\talice' ]
  [ "$(cat "$READ_LOG")" = $'team/bob\nother/alice' ]
}

@test "subscription pair filter preserves held and unknown ownership refusals" {
  run agmsg_subscription_pairs /project codex '' '' '' $'team\theld\nteam\tunknown'
  [ "$status" -eq 0 ]
  [[ "$output" == *'not serving these pairs'* ]]
  [ "$(cat "$READ_LOG")" = $'team/held\nteam/unknown' ]
}

@test "subscription without a pair filter retains the existing subscription set" {
  run agmsg_subscription_pairs /project claude-code ''
  [ "$status" -eq 0 ]
  [[ "$output" == *$'team\talice\nteam\tbob\nother\talice\nteam-extra\talice'* ]]
  [ "$(wc -l < "$READ_LOG" | tr -d '[:space:]')" -eq 6 ]
}

eligible_fixture() {
  mkdir -p "$SKILL_DIR/scripts/drivers/types/codex" "$SKILL_DIR/scripts/lib"
  cp "$BATS_TEST_DIRNAME/../scripts/drivers/types/codex/eligible-pairs.sh" "$SKILL_DIR/scripts/drivers/types/codex/"
  : > "$SKILL_DIR/scripts/lib/actas-lock.sh"
  cat > "$SKILL_DIR/scripts/lib/subscription.sh" <<'EOF'
agmsg_subscription_pairs() { printf '%s\n' "$6"; }
EOF
  cat > "$SKILL_DIR/scripts/lib/role-session.sh" <<'EOF'
agmsg_role_session_match_unique() {
  [ "$3" = current-thread ] || return 1
  printf 'team\talice\n'
}
EOF
  cat > "$SKILL_DIR/scripts/lib/resolve-project.sh" <<'EOF'
agmsg_canonical_path() { printf '%s\n' "$1"; }
EOF
  cat > "$SKILL_DIR/scripts/lib/poke-mode.sh" <<'EOF'
agmsg_poke_mode_path() { printf '%s/poke-mode' "$SKILL_DIR"; }
EOF
}

@test "eligible pairs checks the exact current thread after ownership resolution" {
  eligible_fixture
  run bash "$SKILL_DIR/scripts/drivers/types/codex/eligible-pairs.sh" /project codex --pair $'team\talice' --thread current-thread
  [ "$status" -eq 0 ]
  [ "$output" = $'team\talice' ]
}

@test "eligible pairs refuses a replaced or ambiguous current thread" {
  eligible_fixture
  run bash "$SKILL_DIR/scripts/drivers/types/codex/eligible-pairs.sh" /project codex --pair $'team\talice' --thread old-thread
  [ "$status" -eq 75 ]
  [[ "$output" == *'cannot confirm a unique role'* ]]
}

@test "eligible pairs refuses another role bound to the same thread" {
  eligible_fixture
  run bash "$SKILL_DIR/scripts/drivers/types/codex/eligible-pairs.sh" /project codex --pair $'team\tbob' --thread current-thread
  [ "$status" -eq 75 ]
  [[ "$output" == *'does not own the requested subscription'* ]]
}

@test "eligible pairs refuses delivery after switching to poke mode" {
  eligible_fixture
  touch "$SKILL_DIR/poke-mode"
  run bash "$SKILL_DIR/scripts/drivers/types/codex/eligible-pairs.sh" /project codex --pair $'team\talice' --thread current-thread
  [ "$status" -eq 75 ]
  [[ "$output" == *'monitor is disabled'* ]]
}

role_fixture() {
  mkdir -p "$SKILL_DIR/run"
  _actas_lock_encode() { printf '%s' "$1"; }
  _actas_lock_dir() { printf '%s/run' "$SKILL_DIR"; }
  source "$BATS_TEST_DIRNAME/../scripts/lib/role-session.sh"
  printf 'type=codex\nsession=current\nproject=/project\nteam=team\nagent=alice' > "$SKILL_DIR/run/role-session.target"
  # A field getter must not be spawned on this hot path.
  _agmsg_role_session_field() { return 99; }
}

@test "role lookup reads one exact record without field subprocesses" {
  role_fixture
  printf 'type=claude-code\nsession=current\nproject=/project\nteam=team\nagent=bob\n' > "$SKILL_DIR/run/role-session.other"
  run agmsg_role_session_match_unique codex /project current
  [ "$status" -eq 0 ]
  [ "$output" = $'team\talice' ]
  run agmsg_role_session_match_unique codex /other current
  [ "$status" -eq 1 ]
  run agmsg_role_session_match_unique codex /project replaced
  [ "$status" -eq 1 ]
}

@test "role lookup refuses ambiguous matching records" {
  role_fixture
  cp "$SKILL_DIR/run/role-session.target" "$SKILL_DIR/run/role-session.duplicate"
  run agmsg_role_session_match_unique codex /project current
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}

@test "role lookup preserves first fields including empty values" {
  role_fixture
  printf '\ntype=claude-code\nsession=other\nproject=/other\nteam=wrong\nagent=wrong\n' >> "$SKILL_DIR/run/role-session.target"
  run agmsg_role_session_match_unique codex /project current
  [ "$status" -eq 0 ]
  [ "$output" = $'team\talice' ]
  printf 'type=\ntype=codex\nsession=current\nproject=/project\nteam=team\nagent=alice\n' > "$SKILL_DIR/run/role-session.target"
  run agmsg_role_session_match_unique codex /project current
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}
