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
