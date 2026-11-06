# Lessons

One entry on the day of the surprise. A cause counts as found only if it was checked; otherwise say so.

Template:

```
## <date> <stage.task> <short name>
Expected: ...
Got: ...
Cause (checked, not a guess; if unchecked, say so): ...
What changes in the plan: ...
Marker: [VERIFIED] / [HYPOTHESIS] / [UNKNOWN]
```

## 2026-11-03 E0.2 V2 ignores .kiro/hooks
Expected: a hook file in `.kiro/hooks/*.json` runs on both engines.
Got: on Kiro CLI 2.37.1 with the default V2 engine the file is never executed. V2 runs hooks only from the agent config `.kiro/agents/<name>.json` (camelCase keys `preToolUse`, `postToolUse`, `stop`, `agentSpawn`) and only when Kiro is started with `kiro-cli chat --agent <name>`. V3 reads `.kiro/hooks/*.json` with PascalCase triggers.
Cause: checked by running the same hook on both engines and capturing STDIN to a file.
What changes in the plan: `tombstone init` writes two sets of hooks, a V3 file in `.kiro/hooks/` and a V2 agent `.kiro/agents/tombstone.json`. README tells V2 users to start with `--agent tombstone`. `agentSpawn` did not fire in `--no-interactive` mode, so nothing in the core depends on it.
Marker: [VERIFIED]

## 2026-11-03 E0 Price only after the turn
Expected: cost per request appears in the session file as each request finishes.
Got: `metering_usage` (V2) and `promptTurnSummaries.usage` (V3) are written once, at the end of the turn (second 36 of a 36 second turn). During the turn only the jsonl grows.
Cause: checked by polling the session files every 2 seconds.
What changes in the plan: the watcher works on an estimate (requests x average request price) during the turn and reconciles with the exact price after it. Both numbers go into every tombstone.
Marker: [VERIFIED]

## 2026-11-03 E0 Task hooks only on V3
Expected: `PreTaskExecution` and `PostTaskExecution` in every CLI version.
Got: in the CLI they exist only on the V3 engine. V2 has tool hooks and `stop`.
Cause: checked against kiro.dev/docs/hooks/types and the V2 run in E0.2. `PreTaskExecution` in V3 spec mode was not run (too costly on free credits).
What changes in the plan: the core runs on `preToolUse` / `PreToolUse` and `stop` / `Stop`. Task hooks are an extra where present.
Marker: [VERIFIED] for V2, [UNKNOWN] for V3 spec mode STDIN

## 2026-11-05 E1 Some turns leave no session file
Expected: every metered turn lands in `~/.kiro/sessions/`.
Got: three headless runs with `/spec`, `/spec help`, `/tasks` (the agent answered from docs) cost about 0.35 credits in `/usage` and left no V3 session directory.
Why it matters: Tombstone cannot price what Kiro does not write down. The README must say the numbers cover sessions on disk, and `/usage` stays the reconciliation source.
Rule: reconcile metered sums with `/usage` once per stage; a gap under 1 credit is expected, a bigger gap is a bug.

## 2026-11-05 E1 CLI 3.0.0 changed the plan
Expected (PLAN_v3): V2 is the core, V3 an add-on, PreTaskExecution gives the task id.
Got: V3 is the default since 03.11, task hooks did not fire headless, `/spec` does not exist in the CLI.
Rule: the task id comes from the `tasks.md` checkbox edit seen in PostToolUse. Task hooks are a bonus.

## 2026-11-05 E1 The kill target is probably not `kiro-cli`
Seen: the V3 agent logs come from a Node process (`node:24362`) started by `kiro-cli`.
Open: record the real process tree before writing the kill step (task 7.3 now says so).

## 2026-11-06 E3 Real process tree of a V3 headless session (measured for task 7.3)
`ps -Ao pid,ppid,pgid,command` during `kiro-cli chat --no-interactive` running `sleep 20`:
```
PID   PPID  PGID  COMMAND
4529  1     4526  ~/.local/bin/kiro-cli chat --no-interactive -a ...
4539  4529  4539  ~/Library/Application Support/kiro-cli/node --experimental-wasm-modules .../kiro-cli/kas/3.1.0-.../...
4556  4539  4556  sleep 20            (the tool the agent runs)
```
`lsof -a -p <pid> -d cwd -Fn`: both 4529 and 4539 have the project directory as cwd.
Facts: the agent is a Node process (KAS 3.1.0) under `kiro-cli`, in its own process group; each tool command gets its own group too. Killing only `kiro-cli` or only its group leaves the Node agent and the running tool alive.
Rule for the kill step: match `kiro-cli` whose cwd is the project, then walk its descendants by PPID (Node agent, tools), SIGTERM the leaves first and the root last, SIGKILL whatever is left after 5 s.
Side find: pytest spawn tests leave `tombstone hook reconcile` children polling for up to 30 s after the suite ends (they exit by themselves). Harmless, but tests should pass a short timeout.
