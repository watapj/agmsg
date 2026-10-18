<!-- Codex overlay for the shared SKILL.md. -->

<!-- agmsg:slot shell-extra -->
On Windows PowerShell, invoke Git Bash explicitly and keep the full `bash -lc` payload inside one single-quoted string:

`& 'C:\Program Files\Git\bin\bash.exe' -lc '~/.agents/skills/__SKILL_NAME__/scripts/whoami.sh "$(pwd)" codex'`

Do not use POSIX `'"'"'` quote splicing in PowerShell, and do not use escaped double quotes inside the wrapper.

If Git Bash has already failed in this session's Windows tool sandbox with `CreateFileMapping` / Win32 error 5, use supported outside-sandbox command execution with a scoped justification for subsequent agmsg Bash commands, including read-only commands. Do not repeat the known failing sandbox probe for each script. Keep the sandbox enabled and leave ACLs unchanged; if execution is refused, report the refusal instead of trying another shell.
<!-- /agmsg:slot shell-extra -->

<!-- agmsg:slot delivery -->
<!-- agmsg:render-overlay __AGENT_TYPE__ -->
  5. **REQUIRED — Do NOT skip this step.** Ask the user to pick `monitor`, `turn`, `poke`, or `off` delivery. Empty input means `monitor`.
     Run `~/.agents/skills/__SKILL_NAME__/scripts/delivery.sh set <mode> __AGENT_TYPE__ "$(pwd)"`.
     On Windows, run this command outside the tool sandbox (Git Bash cannot start inside it). For `monitor`, it starts the native lifecycle dispatcher and attaches the bridge to this session's existing app-server; it reports receiving only after its watcher is armed. Select a role with `actas` first if none belongs to this thread. An embedded/`--no-daemon` session has no attachment endpoint; report the error and consult `docs/codex-monitor-beta.md`. Linux/macOS use the existing launch wrapper.
<!-- /agmsg:slot delivery -->

<!-- agmsg:slot actas -->
If argument starts with "actas" followed by an agent name:
1. Run `~/.agents/skills/__SKILL_NAME__/scripts/identities.sh "$(pwd)" __AGENT_TYPE__`. If `<name>` is not listed, join with `~/.agents/skills/__SKILL_NAME__/scripts/join.sh <team> <name> __AGENT_TYPE__ "$(pwd)"`.
2. Record this Codex thread so a later spawn can resume it, together with optional profile metadata: `~/.agents/skills/__SKILL_NAME__/scripts/drivers/types/codex/codex-record-session.sh <team> <name>`. Both arguments are required; `<team>` is the team `<name>` belongs to (from step 1). Without them nothing is recorded and the monitor cannot deliver to this thread.
3. Use the role as the active FROM; monitor delivery is routed only to its recorded thread.
<!-- /agmsg:slot actas -->

<!-- agmsg:slot execute-extra -->
When the Codex bridge supplies unread message text inline, process that text directly. If the only remaining action is waiting for another agent and monitor receiving has been confirmed, finish the current turn so the bridge can deliver the next message. Do not keep the turn alive with repeated inbox/peek polling. Check runtime status and recover delivery when there is evidence of a failure.
<!-- /agmsg:slot execute-extra -->

<!-- agmsg:slot drop -->
If argument starts with "drop", run `reset.sh "$(pwd)" __AGENT_TYPE__ <name>` and clear the role's recorded Codex seat.
<!-- /agmsg:slot drop -->

<!-- agmsg:slot spawn -->
If argument starts with "spawn" (e.g. "spawn claude-code alice", "spawn codex reviewer --window"):
1. Parse `<type>` (a spawnable agent type), `<name>`, and any options (`--boot-prompt <text>`, `--project <path>`, `--team <team>`, `--window`, `--split h|v`, `--terminal <template>`, `--no-wait`, `--ready-timeout <secs>`, `--model <id>`, `--fresh`).
2. Run: `~/.agents/skills/__SKILL_NAME__/scripts/spawn.sh <type> <name> --project "$(pwd)" [options]`
   - `spawn.sh` pre-joins `<name>`, then opens a new pane or window through the terminal driver and launches the target CLI with `$__SKILL_NAME__ actas <name>` as its initial prompt. `--boot-prompt` appends a first task to that prompt. Codex spawn uses the bundled shim and bridge. Which terminal that is is the driver's decision, never something to name here.
   - By default it blocks until a spawned Claude Code agent's watcher attaches and prints `status=ready`; `--no-wait` returns immediately. A spawned Codex agent has no Monitor and skips the readiness wait.
   - It refuses early when `<name>` is already held by another live session, the target CLI is missing, the project path is invalid, or the terminal driver has nowhere to place it.
3. Show the script's output.

If argument starts with "despawn" (e.g. "despawn reviewer", "despawn alice --force"):
1. Parse `<name>` and any options (`--force`, `--timeout <secs>`). `despawn` tears down a member previously spawned by this session.
2. Determine which team `<name>` belongs to, then run:
   `~/.agents/skills/__SKILL_NAME__/scripts/despawn.sh <team> $AGENT <name> [--force] [--timeout <secs>]`
   - The default graceful path sends a `ctrl:despawn` message so a Claude Code member's watcher drops its role and closes its spawned pane, then waits for the lock to release.
   - A Codex member has no watcher, so use `--force` for it. `--force` skips the message, tears down the recorded pane/window, and drops the registration directly.
3. Show the script's output.
<!-- /agmsg:slot spawn -->

<!-- agmsg:slot mode -->
If argument is "mode", run `delivery.sh status __AGENT_TYPE__ "$(pwd)"`.

For `mode monitor|turn|off|poke`, run `delivery.sh set <mode> __AGENT_TYPE__ "$(pwd)"`; `both` is unsupported. Legacy `hook on` maps to `turn`; `hook off` maps to `off`.

`mode poke` disables monitor and turn hooks for this project/type, and stops bridge subscriptions while keeping the Codex TUI/app-server alive. It requires an addressable terminal pane. On Windows, use the supported command escalation if writing project settings is refused. Do not launch a bridge or poll inbox after selecting poke.

On Windows, execute `delivery.sh set monitor` outside the tool sandbox, using the supported command escalation when required. Choose the active FROM role with `actas` first if this thread has no unique recorded role. This command starts receiving in the current thread; do not launch a second `codex-monitor.sh` from inside Codex. If the execution tool returns a running session ID, retain it and collect the final output and exit code; forwarding only stdout loses completion metadata. Partial output is not an activation failure.

Confirm receiving from `Codex monitor receiving:` for this role/thread, or from one `delivery.sh status codex "$(pwd)"` check showing this role as `receiving` with this exact current thread ID. Status can list other roles: another thread's receiving state, configured hooks, dispatcher liveness or a running PID does not confirm this session. Do not restart a confirmed monitor just to obtain the startup banner. Report command errors separately even if status confirms an existing receiver. If neither check confirms this thread, report receiving as unconfirmed with the observed error. An embedded thread, an unloaded thread or a project mismatch can prevent attachment; ordinary non-administrator shared-server sessions support it.
<!-- /agmsg:slot mode -->
