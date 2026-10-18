#!/usr/bin/env bats
load test_helper
setup() {
  setup_test_env
  export SKILL_DIR="$TEST_SKILL_DIR"
  source "$SCRIPTS/lib/storage.sh"
  agmsg_storage_load
}
teardown() { teardown_test_env; }

@test "inbox: snapshot stays unread and ack consumes only the accepted IDs" {
  local one two
  one="$(storage_send snapshot bob alice '日本語 payload')"
  run bash "$SCRIPTS/inbox.sh" snapshot alice --snapshot
  [ "$status" -eq 0 ]
  [[ "$output" == *"$one"* ]]
  [[ "$(storage_list_unread snapshot alice)" == *"$one"* ]]
  two="$(storage_send snapshot bob alice later)"
  run bash "$SCRIPTS/inbox.sh" snapshot alice --ack "$one"
  [ "$status" -eq 0 ]
  local unread
  unread="$(storage_list_unread snapshot alice)"
  [[ "$unread" != *"$one"* ]]
  [[ "$unread" == *"$two"* ]]
  run bash "$SCRIPTS/inbox.sh" snapshot alice --ack
  [ "$status" -eq 2 ]
}

@test "placement: only byte-identical legacy records can be archived" {
  bash "$SCRIPTS/join.sh" team alice codex "$TEST_SKILL_DIR" >/dev/null
  source "$SCRIPTS/lib/actas-lock.sh"
  local key id legacy
  key="$(_agmsg_id_key_for team alice)"
  id="$TEST_SKILL_DIR/run/spawn.$key"
  legacy="$TEST_SKILL_DIR/run/spawn.team__alice"
  mkdir -p "$TEST_SKILL_DIR/run"
  printf 'orca:one\t/project\tcodex\n' > "$id"
  printf 'orca:two\t/project\tcodex\n' > "$legacy"
  run bash "$SCRIPTS/placement-dedup.sh" team alice
  [ "$status" -ne 0 ]
  [ -f "$legacy" ] && [ -f "$id" ]
  cp "$id" "$legacy"
  run bash "$SCRIPTS/placement-dedup.sh" team alice
  [ "$status" -eq 0 ]
  [ ! -e "$legacy" ]
  [ -f "$id" ]
  run agmsg_spawn_path team alice
  [ "$status" -eq 0 ]
  [ "$output" = "$id" ]
}
