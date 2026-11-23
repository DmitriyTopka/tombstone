# Requirements Document

## Introduction

Tombstone is an external circuit breaker for Kiro specs. It prices every spec task in credits from the session files Kiro already writes on the local disk, keeps a hard cap per task and per spec, notices when an agent keeps calling the model while the project files stay untouched, stops that agent in two steps and leaves a receipt behind. The agent itself can ask Tombstone for its budget over MCP.

The tool must never spend a credit of its own. It is built only from shell hooks, a local watcher process and a stdio MCP server. It never changes the model the agent uses.

Facts this spec relies on were measured on Kiro CLI 2.37.1 on 03.11.2026 and re-measured on Kiro CLI 3.0.0 on 05.11.2026 (see MEASURE_0311.md in the planning folder). Since 3.0.0 the V3 engine is the default and V2 is a legacy option (`--v2`). On both engines the exact cost lands in the session file only at the end of a turn, the jsonl file grows during the turn, and `exit 2` from a pre-tool hook blocks the tool and passes stderr to the agent. On 3.0.0 in headless chat the task hooks (`PreTaskExecution`, `PostTaskExecution`) did not fire, while `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse` and `Stop` did, each with `session_id` on STDIN.

## Glossary

- **Turn**: one user prompt and everything the agent does until it hands control back.
- **Request**: one model call inside a turn. Each request has its own metered cost.
- **Task**: one checkbox item in `.kiro/specs/<spec>/tasks.md`.
- **V2 session**: `~/.kiro/sessions/cli/<uuid>.json` plus `<uuid>.jsonl`. Cost per request is in `session_state.conversation_metadata.user_turn_metadatas[].metering_usage`.
- **V3 session**: `~/.kiro/sessions/<project hash>/sess_<uuid>/` with `session.json` and `messages.jsonl`. Cost per turn is in `payload.promptTurnSummaries[].usage` (unit `credit`); the same event lists `requestIds` (one per request) and `usedTools`. Index in `~/.kiro/session-index/<hash>.jsonl`. This is the primary format since CLI 3.0.0.
- **Pulse**: growth of the session jsonl file during a turn.
- **Quiet project**: no project file changed (by mtime and `git status`) inside the configured window.
- **Estimate**: requests seen so far multiplied by the average request price.
- **Tombstone**: one record in `tombstones.json` describing a finished or killed task.
- **STOP file**: `.kiro/tombstone/STOP`, the signal that blocks every tool call in the project.
- **Autopsy**: a Markdown receipt written after a stop.

## Requirements

### Requirement 1: Price a turn and a task from both session formats

**User Story:** As a developer on Kiro, I want to see what each spec task cost in credits, so that I know where my monthly credits went without waiting for `/usage`.

#### Acceptance Criteria

- R1.1 WHEN a V2 session file contains `metering_usage` entries THE SYSTEM SHALL sum them per turn and report the turn cost with at least three decimal places.
- R1.2 WHEN a V3 `messages.jsonl` contains a `promptTurnSummaries[].usage` entry with unit `credit` THE SYSTEM SHALL read it as the turn cost.
- R1.3 THE SYSTEM SHALL report for each turn: engine (V2 or V3), model, number of requests, cost, start time and end time.
- R1.4 WHEN a task spans several turns THE SYSTEM SHALL report the task cost as the sum of its turns.
- R1.5 IF a session file is missing, empty or in an unknown format THEN THE SYSTEM SHALL report the turn as unpriced with the reason and SHALL NOT invent a number.
- R1.6 THE SYSTEM SHALL provide a command `tombstone cost <session>` that prints cost and request count for one session.
- R1.7 THE SYSTEM SHALL tie a session to a project by its working directory (V2 hook STDIN or session metadata, V3 `cwd` and project hash).

Acceptance check: on the V3 session from measurement 5 the command prints 0.390 credits and 5 requests; on the V2 session from measurement 2, if it is still on disk, 0.357 credits and 9 requests.

### Requirement 2: Write tombstones

**User Story:** As a developer, I want a priced record of every task committed next to `tasks.md`, so that the cost history lives in the repo.

#### Acceptance Criteria

- R2.1 WHEN a turn ends (V2 `stop` hook in the agent config, V3 `Stop` hook in `.kiro/hooks/`) THE SYSTEM SHALL append or update one record in `tombstones.json` at the repo root.
- R2.2 THE SYSTEM SHALL store per record: task id, short task name, spec name, engine, model, requests, estimate, actual cost, minutes, outcome (`finished`, `killed`, `cap`), `false_positive` (boolean or null), date.
- R2.3 THE SYSTEM SHALL take the task id from the hook STDIN when present; otherwise from the `tasks.md` line that the agent changed from `- [ ]` to `- [x]` during the turn (seen in `PostToolUse` for `str_replace` or `fs_write` on `.kiro/specs/<spec>/tasks.md`); and SHALL NOT guess from the first unchecked task. A turn that checks no box and carries no task id on STDIN is a work turn outside the task list (a fix, a question, a refactor): it SHALL be recorded with task id `unknown` and a name made of the first 8 words of the user prompt (from the `UserPromptSubmit` hook STDIN `prompt` field, stripped of home paths), so its cost stays visible and never lands on a real task. When several boxes are checked in one turn, the turn cost is split between them equally.
- R2.4 IF the task id cannot be determined THEN THE SYSTEM SHALL write the record with task id `unknown`, the prompt name from R2.3 when known (otherwise the turn time), one record per turn, and SHALL NOT skip the record.
- R2.5 THE SYSTEM SHALL keep `tombstones.json` valid JSON after every write, including when two hooks fire close together.
- R2.6 THE SYSTEM SHALL provide `tombstone graveyard`, which renders a static HTML page from `tombstones.json` with no external resources.
- R2.7 WHEN `tombstone graveyard` renders the page, THE SYSTEM SHALL draw every record as a headstone whose height grows with its cost (actual, else estimate), group finished tasks into plots by the top-level task number with the plot name taken from `tasks.md`, put records with no task id into an "Unmarked graves" row, put every record whose outcome is not `finished` into a "Stopped by the breaker" row drawn apart from the rest, open a detail card with actual, estimate, requests, minutes, model and engine when a headstone is activated, and keep the sortable register table. The page SHALL follow `.kiro/specs/tombstone/graveyard_mockup.html` for look and copy, work without JavaScript for the headstones, respect `prefers-reduced-motion`, show no horizontal scroll at 375 px, and keep R2.6.
- R2.8 WHEN `tombstone graveyard` renders the page, THE SYSTEM SHALL open with a night scene (stars, moon, two hill layers drawn as inline SVG) whose headline is the total cost of all stones ("<total> credits are buried here.", two decimals, the same cost each stone shows), followed by one line on how to read the stones and a strip of computed figures: the number of graves, the most expensive grave with its name, the median grave cost, the total model requests, and, only when stopped records exist, the number stopped by the breaker in the ember color. Unmarked graves SHALL render as low slabs showing the cost only, with the name kept on the detail card. Red SHALL appear only on stopped records and their figure. The page SHALL follow `.kiro/specs/tombstone/graveyard_v3_mockup.html` for look, CSS and copy, keep every R2.7 behavior, show text on headstones at 14 px or more at 375 px width, and load no external resource.
- R2.9 WHEN `tombstone graveyard` is given one or more `--include FILE` options, THE SYSTEM SHALL read each FILE as a tombstones file of the same shape, add its records to the page (a record whose id is already present is skipped, the project record wins), and keep the project's own tombstones.json unchanged. WHEN any stopped record on the page came from an included file, THE SYSTEM SHALL say so in the stopped row note, naming the file, and the kicker SHALL read "Tombstone, the graveyard of its own build and its live tests". THE SYSTEM SHALL show the outcome `killed` as "stopped" in the detail card and the register, because the breaker stops the turn by refusing the next tool call and the process is ended only when the agent keeps going. A missing or unreadable include file SHALL fail with a plain message and exit code 1, without writing the page.
- R2.10 WHEN `tombstone graveyard` renders headstones, THE SYSTEM SHALL give each one of five shapes (arch, slab, cross, gothic, shoulder) so that neighbours in a row never share a shape, use the slab instead of the cross for a headstone under 170 px, give each stopped headstone one of four crack patterns so that neighbours never share one, draw no ghost over stopped headstones, show the same red glow at the base of every stopped headstone whatever its shape, leave about one headstone of space between stopped headstones with at most 8 in a line, and keep height as the price. The page SHALL follow `.kiro/specs/tombstone/graveyard_v4_mockup.html` for look and copy and keep R2.6 to R2.9.
- R2.11 WHEN a stopped headstone is opened on the graveyard page THE SYSTEM SHALL show its stop receipt in the card: the STOP reason, the refusal text the agent saw, the estimate at the stop, whether step two (kill) ran, and the last tool calls (tool and title, at most 10), read from the autopsy file of that stop. Autopsies come from the project's `.tombstone/autopsy/` and from any directory given with `--autopsies DIR`. WHEN no autopsy matches a stopped record THE SYSTEM SHALL say plainly in the card that no receipt was kept for this stop. The page stays one file with no external requests.

### Requirement 3: Caps per task and per spec

**User Story:** As a developer with a fixed credit allowance, I want a hard cap per task and per spec, so that one task cannot eat my month.

#### Acceptance Criteria

- R3.1 THE SYSTEM SHALL read caps from `tombstone.toml` at the repo root: task cap, spec cap, quiet window in minutes, minimum plan balance to start, average request price, kill grace in seconds.
- R3.2 IF `tombstone.toml` is missing THEN THE SYSTEM SHALL use the defaults: task cap 40, spec cap 300, quiet window 10, average request price 0.25, kill grace 30.
- R3.3 WHEN the first tool of a new task is about to run AND the remaining spec budget is lower than the median cost of similar tasks THEN THE SYSTEM SHALL exit the pre-tool hook with code 2 and a message that names the task, the similar cost and the remaining budget.
- R3.4 WHEN the spec total reaches the spec cap THE SYSTEM SHALL block new tasks of that spec the same way and mark the record outcome `cap`.
- R3.5 THE SYSTEM SHALL find similar tasks in `tombstones.json` by task name keywords and task type (test, migration, refactor, docs, other) and return their median cost.
- R3.6 WHILE no similar task exists THE SYSTEM SHALL use the task cap as the reference and say so in the message.

### Requirement 4: Estimate during a turn, reconcile after it

**User Story:** As a developer, I want a running estimate while the agent works, because the exact price only appears after the turn.

#### Acceptance Criteria

- R4.1 WHILE a turn is running THE SYSTEM SHALL count requests from the session jsonl and compute the estimate as requests multiplied by the average request price.
- R4.2 WHEN a turn ends THE SYSTEM SHALL read the exact cost and store both estimate and actual in the tombstone.
- R4.3 WHEN a turn ends THE SYSTEM SHALL update the average request price in project state as the mean of the last 50 priced requests of this project.
- R4.4 THE SYSTEM SHALL label every on-screen number as `est.` or `actual`.

Acceptance check: on a 10-request turn the estimate at the end of the turn is within 30% of the actual cost.

### Requirement 5: Stall detector

**User Story:** As a developer, I want Tombstone to notice an agent that keeps calling the model while nothing in the project changes, so that a stuck loop is caught before it burns hundreds of credits.

#### Acceptance Criteria

- R5.1 THE SYSTEM SHALL run a watcher that polls the active session of the project every 2 seconds.
- R5.2 WHILE the pulse is present AND the project is quiet for longer than the quiet window THE SYSTEM SHALL raise a stall.
- R5.3 WHILE the turn estimate exceeds the task cap THE SYSTEM SHALL raise an over-cap event.
- R5.4 THE SYSTEM SHALL ignore changes inside `.kiro/tombstone/`, `.git/` and `tombstones.json` when deciding whether the project is quiet.
- R5.5 WHEN a stall or over-cap event is raised THE SYSTEM SHALL start the stop sequence in Requirement 6.
- R5.6 THE SYSTEM SHALL keep its own state in `.kiro/tombstone/state.json`: session, requests, estimate, last project change, last event.
- R5.7 IF the pulse grows AND project files change THEN THE SYSTEM SHALL NOT raise a stall.

### Requirement 6: Stop in two steps

**User Story:** As a developer, I want a stuck agent stopped even when I am away from the keyboard, without losing the work already done.

#### Acceptance Criteria

- R6.1 WHEN the stop sequence starts THE SYSTEM SHALL write `.kiro/tombstone/STOP` containing the reason, task id, estimate and time.
- R6.2 WHILE the STOP file exists THE SYSTEM SHALL make every pre-tool hook (V2 `preToolUse`, V3 `PreToolUse`) exit with code 2 and print a one-line reason to stderr, for example `TOMBSTONE: task 11 over budget (est. 58 of 40). Stop.`
- R6.3 WHEN the kill grace has passed after STOP AND the request count is still growing THE SYSTEM SHALL terminate the `kiro-cli` process bound to this project directory.
- R6.4 IF no process can be bound to the project directory THEN THE SYSTEM SHALL skip step two, write a warning to state and the autopsy, and SHALL NOT terminate any other process.
- R6.5 THE SYSTEM SHALL NOT delete or revert any project file during a stop.
- R6.6 WHILE the STOP file exists THE SYSTEM SHALL keep blocking until the user deletes it or runs `tombstone resume`.
- R6.7 WHEN a stop happens THE SYSTEM SHALL show a local notification (macOS `osascript`, Linux `notify-send`) and a line in the terminal, with no network call.

### Requirement 7: Autopsy receipt

**User Story:** As a developer who lost credits to a stuck task, I want a receipt with the evidence, so that I can attach it to a support request.

#### Acceptance Criteria

- R7.1 WHEN a stop happens THE SYSTEM SHALL write `.kiro/tombstone/autopsy/<date>_<task>.md`.
- R7.2 THE SYSTEM SHALL fill in: task id and name, engine, model, number of requests, cost per request where known, estimate at STOP, actual after the turn when it arrives, minutes of quiet project, STOP time and reason, whether step two ran, the last 10 tool calls from the session jsonl.
- R7.3 WHEN the actual cost arrives after the autopsy was written THE SYSTEM SHALL append it to the same file.
- R7.4 THE SYSTEM SHALL strip the home directory path from the autopsy.

### Requirement 8: MCP get_budget

**User Story:** As the agent working on a spec, I want three numbers before I start a task, so that I can stop and ask the human instead of starting work I cannot afford.

#### Acceptance Criteria

- R8.1 THE SYSTEM SHALL expose a stdio MCP server with exactly one tool, `get_budget`, taking an optional task name.
- R8.2 WHEN `get_budget` is called THE SYSTEM SHALL return: median cost of similar tasks, spec cap and remaining spec budget, plan balance with the age of that figure in minutes.
- R8.3 THE SYSTEM SHALL take the plan balance from a cache of `kiro-cli chat --no-interactive "/usage"` refreshed at most once every 5 minutes.
- R8.4 IF the plan balance is unavailable THEN THE SYSTEM SHALL return null for it with a reason and still return the other two numbers.
- R8.5 THE SYSTEM SHALL answer `get_budget` without any model call.

### Requirement 9: Install as a Kiro Power

**User Story:** As a developer, I want to install Tombstone with one command and initialise a project with a second one.

#### Acceptance Criteria

- R9.1 THE SYSTEM SHALL ship as a Power folder: `plugin.json`, `skills/tombstone/SKILL.md`, `mcp.json` with `get_budget`, hook templates for V2 and V3.
- R9.2 WHEN the user runs `tombstone init` in a project THE SYSTEM SHALL create `tombstone.toml`, `.kiro/steering/tombstone.md`, V3 hooks in `.kiro/hooks/tombstone-*.json` and `.kiro/settings/mcp.json` with the `tombstone` MCP server (merged into an existing file), all commands using the absolute interpreter path (`<python> -m tombstone ...`). WHERE the user passes `--v2` THE SYSTEM SHALL also create the V2 agent config `.kiro/agents/tombstone.json` with `preToolUse` and `stop` hooks; without `--v2` it SHALL NOT, because Kiro CLI 3.1.0 loads that file as the active profile and that profile has no MCP tools.
- R9.3 WHEN `tombstone init` runs THE SYSTEM SHALL register the watcher as a launchd agent on macOS or a systemd user unit on Linux.
- R9.4 IF a file to be created already exists THEN THE SYSTEM SHALL leave it unchanged and report it.
- R9.5 THE SYSTEM SHALL print after init which command starts Kiro with the hooks active on V3, and on V2 (`kiro-cli chat --agent tombstone`) when `--v2` was used.
- R9.6 WHEN the user runs `tombstone uninstall` in a project THE SYSTEM SHALL stop and unload the watcher unit of that project (launchd `bootout` or `systemctl --user disable --now`), delete that unit file, delete the files `init` created (`.kiro/hooks/tombstone-*.json`, `.kiro/steering/tombstone.md`, `.kiro/agents/tombstone.json`), remove only the `tombstone` entry from `.kiro/settings/mcp.json`, and print one line per file: removed, not found, or kept.
- R9.7 THE SYSTEM SHALL NOT delete user data on uninstall: `tombstones.json`, `tombstone.toml`, autopsies and `graveyard.html` are kept and listed, unless the user passes `--purge`, which also removes `.kiro/tombstone/` and `tombstone.toml` but still keeps `tombstones.json`.
- R9.8 IF a file `init` would remove was changed by the user since init (content differs from what init wrote) THEN THE SYSTEM SHALL keep it and say so, unless `--force` is given.

### Requirement 10: Work on V3 first, keep V2

**User Story:** As a developer on any current Kiro CLI, I want the same protection on the default V3 engine and on the legacy V2 engine.

#### Acceptance Criteria

- R10.1 THE SYSTEM SHALL use one session parser that detects the format per session.
- R10.2 THE SYSTEM SHALL place V3 hooks in `.kiro/hooks/*.json` with PascalCase triggers and V2 hooks in `.kiro/agents/<name>.json` with camelCase keys.
- R10.3 WHERE task hooks (`PreTaskExecution`, `PostTaskExecution`) are available THE SYSTEM SHALL use them in addition to the tool hooks, and SHALL NOT depend on them.
- R10.4 THE SYSTEM SHALL record the CLI version it was tested with and warn when the installed version differs.

### Requirement 11: Zero credits for the tool, no model switching

**User Story:** As a developer protecting my credits, I want the protection itself to cost nothing and to leave my model choice alone.

#### Acceptance Criteria

- R11.1 THE SYSTEM SHALL use only shell command hooks and SHALL NOT define any agent prompt hook.
- R11.2 THE SYSTEM SHALL NOT call any model, any Kiro agent or any paid API.
- R11.3 THE SYSTEM SHALL NOT change, suggest or force a model.
- R11.4 THE SYSTEM SHALL NOT send any data over the network. The `/usage` probe in R8.3 is the only external command.
- R11.5 WHEN a pre-tool hook runs without a STOP file and without a cap breach THE SYSTEM SHALL exit with code 0 within 200 ms.

Acceptance check: a session with Tombstone installed shows no extra metered request compared to the same task without it.

### Requirement 12: Runtime state outside /.kiro

**User Story:** As a hackathon entrant, I want `/.kiro` to stay fully tracked by git, because the Kiroween rules say "Do NOT add the /.kiro directory or sub-folders to your .gitignore".

#### Acceptance Criteria

- R12.1 THE SYSTEM SHALL keep its machine-local runtime state (STOP file, state.json, usage.json, installed.json, autopsies, graveyard.html, logs and lock files) in `.tombstone/` at the project root, not in `.kiro/tombstone/`. Every rule above that names `.kiro/tombstone/` means `.tombstone/`.
- R12.2 WHEN `.kiro/tombstone/` exists and `.tombstone/` does not THE SYSTEM SHALL move it to `.tombstone/` once, on the first command that touches state, with the same symlink protection as every other write.
- R12.3 THE SYSTEM SHALL NOT write any path under `.kiro/` to a `.gitignore` file, and `tombstone init` SHALL suggest ignoring `.tombstone/` instead.
- R12.4 This repository SHALL run the full V3 hook set that `tombstone init` writes (seven hooks) and the MCP server from `.kiro/`, so what the README says is what a reader finds in `/.kiro`.

### Requirement 13: Continuous integration

**User Story:** As a judge or a new user, I want to see that the tests pass on a clean machine, without installing anything myself.

#### Acceptance Criteria

- R13.1 THE repository SHALL run the full test suite on every push to main and on pull requests, on Linux and macOS, with Python 3.11 and 3.12, using only the standard library plus pytest.
- R13.2 Tests that need a real local resource that a CI runner does not have (a Kiro install, the project virtualenv path, launchd) SHALL skip with a reason instead of failing.
- R13.3 THE README SHALL show the CI status badge near the top.
