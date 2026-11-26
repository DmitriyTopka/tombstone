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
- R2.12 WHEN `tombstone graveyard` renders the page THE SYSTEM SHALL open with a first screen a newcomer reads in five seconds: the line "Tombstone stops a Kiro agent when a task goes over its budget." above the headline total; exactly three figures in the strip (tasks buried, stopped by the breaker, the most expensive task) instead of five; a one-line legend "Height is the price. A crack means the breaker stopped it."; WHEN at least one stopped headstone is shown, the hint "Click a cracked stone to read its receipt."; and links to the demo video and the repository WHEN they are given with `--video URL` and `--repo URL` (no link is drawn for an option that is not given, and both URLs are HTML-escaped). A stopped headstone whose task id is unknown SHALL show a plain name, "Live stop N" numbered by stop time from the oldest, and under it the reason in plain words from its receipt ("over its cap", "stalled", "blocked before it started", or "stopped" when no receipt matches), never "no task" and never the cut-off prompt; the prompt stays in the card. Visible copy SHALL call every stone a task, never a grave or a turn, except the page title "The Graveyard". The page stays one file with no external requests and keeps R2.6 to R2.11.
- R2.13 WHEN `tombstone graveyard` renders the page THE SYSTEM SHALL use this lede under the headline, word for word: "Each stone is a task: a spec task Kiro finished, work that ticked no checkbox (filed as unknown), or a task the breaker stopped. It is as tall as its price. Open a stone for the estimate, the requests and the time." so the lede does not claim every stone is a finished spec task.
- R2.14 WHEN `tombstone graveyard` renders the stopped row THE SYSTEM SHALL put the stopped headstones that have a receipt first, newest stop first, and the stopped headstones without a receipt after them under the small label "Early test stops, no receipt was kept", keeping the "Live stop N" numbers by stop time from the oldest. WHEN a stopped headstone with an unknown task id is opened THE SYSTEM SHALL title the card "Live stop N, <reason in plain words>" (the same words as under the stone) and show the cut-off prompt only inside the card as a quoted line starting "Prompt:" and ending with an ellipsis when it was cut. Under the headline total THE SYSTEM SHALL show the line "What Kiro charged to build this tool, and the live test runs the breaker stopped." The page stays one file with no external requests and keeps R2.6 to R2.13.

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
- R4.5 WHILE a V3 turn is running AND its session `messages.jsonl` carries `session_metadata` events with key `creditSplit` THE SYSTEM SHALL take the live cost of the open turn as `main + delegated` of the latest `creditSplit` after the turn's `turn_start`, minus the same sum of the last `creditSplit` before that `turn_start` (0 when there is none), label it `live`, and use it in place of the estimate for the cap check whenever it is higher than the estimate; WHEN no `creditSplit` is present THE SYSTEM SHALL keep the R4.1 estimate. Kiro CLI 3.5.0 writes this metadata after every request (measured 24 Nov 2026: the last value equals the turn's `usage_summary` usage).

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

### Requirement 14: A demo that costs nothing

**User Story:** As a judge without a paid Kiro plan, I want to see the breaker trip on my own machine in under a minute, without Kiro, without credits and without a network.

#### Acceptance Criteria

- R14.1 THE SYSTEM SHALL provide `tombstone demo`, which creates a scratch project in a new temporary directory (or in `--dir DIR` when given, which must be empty or not exist), replays a recorded and scrubbed Kiro V3 session that ships inside the package, request by request, against a task cap of 0.30, and prints one line per request with the running estimate against the cap.
- R14.2 WHEN the estimate goes over the cap THE SYSTEM SHALL write the STOP file and the autopsy through the same code paths the watcher uses, then run the real pre-tool hook entry point on a sample tool call and print its exit code (2) and the refusal text the agent would read.
- R14.3 THE demo SHALL NOT start Kiro, call any model, use the network or read the user's `~/.kiro`; it SHALL finish in under 10 seconds, print where the receipt was written and the command to render the graveyard for the demo project, and exit 0. The temporary directory is kept so the receipt can be opened.
- R14.4 THE recorded session SHALL be packaged as data of the `tombstone` package, so the demo works after `pip install` from a clean checkout, and SHALL contain no home path, user name or account id.

### Requirement 15: Run the agent on the page

**User Story:** As a judge with five minutes and no Kiro plan, I want to set a cap on the graveyard page and watch a real recorded Kiro session spend until the breaker cracks its stone, so that I see the breaker work instead of reading about it.

#### Acceptance Criteria

- R15.1 WHEN `tombstone graveyard` renders the page THE SYSTEM SHALL show, right after the first screen and before the stopped row, a block titled "Run the agent" with a cap slider, a "Run" button, a running credit counter, one headstone that grows with the spend, and the caption "A recorded real Kiro CLI session, scrubbed. For every cap on the slider, the stop point was computed by Tombstone's own watcher code when this page was built."
- R15.2 WHEN the page is built THE SYSTEM SHALL replay the session packaged for `tombstone demo` request by request through `watcher.evaluate` (the same estimate and over-cap rule as the watcher and the demo), once for each slider position (at least 20 caps from 0.05 up to just under the session total), and embed the result in the page as inline JSON: the credits after each request, and for each cap the request at which the breaker stops (or none), the figure at the stop and the refusal text built by the same code the pre-tool hook uses for that cap. The browser SHALL only look results up and animate them, never compute the stop itself.
- R15.3 WHEN the visitor presses "Run" THE SYSTEM SHALL step through the requests in 4 to 10 seconds, raise the counter and the headstone with each request, and at the stop request crack the headstone, show "STOP", "hook exit code 2" and the refusal text the agent read, and the line "Without the breaker this session went on to X credits." WHEN the chosen cap is above the session total THE SYSTEM SHALL let the session finish and say that the cap was never reached. Pressing "Run" again restarts it; moving the slider resets it.
- R15.4 THE block SHALL work at 375 px wide, with keyboard (slider and button reachable and labelled), and with `prefers-reduced-motion` (no growth animation, the final state is shown at once). THE slider SHALL start at the cap 0.75, so a run shows several requests before the stop; WITHOUT JavaScript the block SHALL show the 0.75 result as static text and a cracked headstone. The page stays one file with no external requests and keeps R2.6 to R2.14.
- R15.5 THE block SHALL grow with its content: the STOP line, the exit code, the refusal text and the "Without the breaker" line SHALL be fully visible below the headstone and never covered by the next section, at 1280 px and at 375 px wide.

### Requirement 16: A frightening first screen built around the breaker

**User Story:** As a judge of the Costume Contest ("a haunting user interface that's polished and unforgettable ... spooky design elements that enhance your app's function"), I want the first screen to be a night graveyard scene where I set a cap and release the agent myself, so that the scare is the product working and not decoration around a report.

#### Acceptance Criteria

- R16.1 WHEN `tombstone graveyard` renders the page THE SYSTEM SHALL open with a full-height hero scene (at least the viewport height, at most 900 px tall on desktop) drawn with inline SVG and CSS only: a cold moon with a halo, two or three drifting fog layers, bare black trees, a wrought-iron fence, a row of dark headstone silhouettes on a hill, and in the centre the R15 headstone that grows with the spend. Palette: near-black blue night, bone-white text, one blood-red accent used only for the cap, the crack, STOP and the refusal. No cartoon faces, no emoji, no external fonts or images.
- R16.2 THE hero SHALL carry, above the scene's ground line, the line "Tombstone stops a Kiro agent when a task goes over its budget.", the headline "Set a cap. Release the agent." with "Release the agent." in red italic, the cap slider and a "Release the agent" button as the main control; the R15 counter, STOP, "hook exit code 2", the refusal text and the "Without the breaker" line SHALL appear in the hero next to or under the central headstone. The R15 caption about the recorded session and the watcher code SHALL stay visible in the hero in small text. The R15 block below the first screen is removed (the hero replaces it); the trace data and its behaviour (R15.2, R15.3) stay the same.
- R16.3 WHEN the run reaches the stop THE SYSTEM SHALL play a short scare in the scene: the crack runs down the central headstone, the scene flashes a dim red once, the fog thickens around the stone and the background silhouettes darken, all within 1.5 seconds. WHEN the visitor turns sound on with a "Sound off" / "Sound on" toggle (off by default) THE SYSTEM SHALL play a stone-crack and a low thud made with the Web Audio API at the stop, no audio files.
- R16.4 THE former first-screen content (headline total "N credits are buried here.", the line under it, the lede, the three figures, the legend, the hint and the video and repository links) SHALL follow directly after the hero under the heading "The graveyard of this build", unchanged in wording and keeping R2.12 to R2.14. The video and repository links SHALL also appear as small links at the top right of the hero.
- R16.5 THE hero SHALL work at 375 px (no horizontal scroll, the controls above the fold, the scene simplified: fewer trees and silhouettes), with keyboard, and with `prefers-reduced-motion` (no fog drift, no flash, the final state shown at once). WITHOUT JavaScript the hero SHALL show the scene with the 0.75 result as static text and a cracked central headstone. The page stays one file with no external requests and keeps R2.6 to R2.14 and R15.2 to R15.5.

### Requirement 17: A photographic scene and a headstone that rises from the grave

**User Story:** As a Costume Contest judge, I want the first screen to look like a real night cemetery and the breaker's headstone to rise out of the ground as the agent spends, so that the scene is memorable and the scare itself shows the price.

#### Acceptance Criteria

- R17.1 THE hero of R16 SHALL use the photograph `src/tombstone/data/scene/scene_desktop.webp` as its background at 768 px wide and above and `scene_mobile.webp` below, covering the hero, with a dark gradient over the top third so the white text stays readable (contrast at least 4.5:1 on the headline area). The drawn SVG moon, trees, fence and silhouettes of R16.1 are removed; the drifting fog layers stay over the photo, and on desktop the photo moves by at most 12 px with the pointer (parallax), never on touch or with reduced motion.
- R17.2 THE central headstone SHALL be the cut-out photograph `headstone.webp`, standing on the empty path in the middle of the scene and toned to the scene's moonlight (darker and bluer than the raw image). Before a run the headstone is fully below the ground and nothing of it shows. With each request it rises out of the ground, with a small shake and a little falling earth, so that the visible height is proportional to the credits spent on the same scale as R15 (fully risen at the session total). The part still underground is hidden by clipping at the ground line, not by a drawn band, so at rest the photo's path shows untouched.
- R17.3 THE credits spent SHALL be carved into the blank face of the headstone (dark letters with a thin light lower edge, centred in the upper half), updated with each request. AT the stop the R15/R16 crack runs down the photographed face with a dim red glow inside it, together with the R16.3 scare.
- R17.4 THE three images SHALL ship as package data and be embedded in the page as base64 data URIs, so the page stays one file with no external requests; the whole page SHALL stay under 700 KB. A small line at the very bottom of the page SHALL say "Scene and headstone images generated with Nano Banana Pro (Google Gemini)."
- R17.5 AT 375 px the mobile photo and a smaller headstone SHALL keep the controls and the result above the fold as in R16.5; with `prefers-reduced-motion` the headstone appears at its final height without rising or shaking; WITHOUT JavaScript the hero shows the photo and the headstone fully risen to the 0.75 result with the crack. R16.2 to R16.5 and R15.2 to R15.5 still hold.

### Requirement 18: The headstone breaks through the earth

**User Story:** As a Costume Contest judge, I want to see the headstone break up through the ground, shed earth as it climbs and end standing in a heap of soil before it splits, so that the rise reads as a grave opening and the stop as the final blow.

#### Acceptance Criteria

- R18.1 AT rest the path SHALL show no mound, heap or soil cracks. WHEN a run starts THE SYSTEM SHALL first heave the ground where the headstone will come up: the earth swells into a low mound and a few dark cracks open in the soil around it, before the stone's top appears.
- R18.2 WHILE the headstone rises (one step per request, height still the price per R17.2) THE SYSTEM SHALL break the ground line visibly at the stone (torn edges of soil and grass pushed outward), shed clumps and crumbs of earth from the stone's top and shoulders that fall and roll down to the ground with each step, and leave a dusting of soil on the stone's upper edges.
- R18.3 WHEN the headstone has reached its final height THE SYSTEM SHALL show it standing in a visible heap of loose earth around its base (bigger the higher it rose), with a few clumps lying on the grass nearby.
- R18.4 ONLY after the stone has stopped rising and the earth has settled (about 0.4 s) THE SYSTEM SHALL show STOP and, as the last beat, crack the headstone in two: the crack runs from the top of the arch to the base, the two halves part by a few pixels and tilt slightly apart with nothing drawn in the gap (no line, no stroke, no glow; the scene shows through), with the R16.3 flash and the optional sound at that moment. The carved figure stays readable on the halves.
- R18.5 THE effect SHALL be drawn with inline SVG, CSS and the page script only (no new images, no external requests), the page SHALL stay under 750 KB, and with `prefers-reduced-motion` the final state (heap of earth, split stone) is shown at once; without JavaScript the noscript scene shows the split stone in its heap. R15.2 to R15.5, R16 and R17 still hold.

### Requirement 19: Photographed earth, a skeletal hand and old bones

**User Story:** As a Costume Contest judge, I want the earth, the falling clods, a skeletal hand clawing out of the grave and the unearthed bones to be real photographs lit like the scene, so that the whole first screen reads as one frightening photograph and the hand shows the agent trying to climb out until the breaker buries it.

#### Acceptance Criteria

- R19.1 THE heap of R18.3 SHALL be the cut-out photograph `src/tombstone/data/scene/heap.webp` (its hollow middle behind the headstone's foot, the rest in front of it), scaled from about 20 percent to full size with the rise so that at the final height it is about 1.6 times the stone's width; the falling and scattered clods of R18.2 and R18.3 SHALL be the photographs in `src/tombstone/data/scene/clods/`, in varied sizes and rotations. The drawn SVG heap and clumps are removed; the drawn soil fissures stay, behind the heap.
- R19.2 FROM the second rise step THE SYSTEM SHALL raise the photographed skeletal hand `hand_reach.webp` out of the ground to the left of the headstone (its foot hidden by the ground line and the heap, about 28 percent of the stone's final height, see R19.6), climbing in small jerks with each step with a little falling earth. AT the split of R18.4 the hand SHALL go limp and sink back into the earth within about 0.8 s and disappear.
- R19.3 WHEN the heap has reached its full size THE SYSTEM SHALL let the photographed bones `bones.webp` appear on the grass to the right of the heap (fade in with a puff of soil dust), sized per R19.6. At rest neither the hand nor the bones are visible (R18.1 still holds).
- R19.4 THE hand, bones, heap and clods SHALL be toned to the scene's cold moonlight (darker, less saturated, slightly blue, highlights from the upper right) so they do not look pasted in. The headstone image `headstone.webp` has been replaced by a new photograph; its blank face is the sunken panel, and the carved figure of R17.3 SHALL sit centred in that panel.
- R19.5 EACH image SHALL be embedded once as a data URI and reused (for example as a CSS custom property or a shared class), including the two halves of the split headstone; the page SHALL stay one file with no external requests and under 1.1 MB. Reduced motion shows the final state at once (hand already sunk, bones and heap shown); noscript shows the final scene. R15 to R18 still hold.
- R19.6 ALL elements SHALL keep real-world scale against the headstone, taken as 100 cm tall and 60 cm wide including its plinth: the visible part of the skeletal hand (fingertips to where it enters the earth, about 25 to 28 cm) SHALL be 25 to 30 percent of the stone's final visible height and at most 45 percent of its width; the bones group (skull about 20 cm long, femur about 45 cm) SHALL be 65 to 80 percent of the stone's width, the skull itself 15 to 20 percent of the stone's height; the heap SHALL be 160 to 180 percent of the stone's width and 20 to 25 percent of its height; each clod SHALL be 4 to 14 percent of the stone's width (3 to 8 cm), the falling ones the smaller; all scaled together with the stone at every screen size, with elements a little further back (the bones, the hand) at most 10 percent smaller for depth.
- R19.7 THE photographed skull and bones SHALL lie in the lower left foreground of the hero, on the grass between the old graves of the photo near the left edge, away from the central headstone and the path (no overlap with the stone, the heap, the hand, the counter or the caption), visible at rest as part of the cemetery, about 1.1 times their R19.6 size because they are closer to the camera. At 390 px they may be smaller or hidden if they would overlap a control or text.
- R19.8 THE credits figure on the headstone's panel SHALL glow with a cold blue-white light like moonlight on wet stone (near-white core, soft pale blue halo of a few pixels, a faint light spill on the stone around it), as if lit from inside the carving; it SHALL be clearly readable from the first step the panel is above the heap, on both halves after the split, and at 390 px. It SHALL flicker slightly while the stone rises and hold steady after the split.

### Requirement 20: A night ambience behind the sound toggle

**User Story:** As a judge who turns the sound on, I want to hear the cemetery at night, so that the scene feels like a horror film and the stone's crack lands in that silence.

#### Acceptance Criteria

- R20.1 WHEN the visitor turns "Sound on" THE SYSTEM SHALL play the looping night ambience `src/tombstone/data/scene/ambience.m4a` (generated by Dima in Suno: a quiet mournful music box and cello far in the background, a low drone, wind, distant wolves, an owl, a cracked church bell, whispers), fading in over about 1.5 s at a moderate level and looping without a gap; WHEN the visitor turns "Sound off" it fades out over about 0.5 s and stops. Sound stays off by default and nothing plays before the visitor turns it on.
- R20.2 AT the stop THE SYSTEM SHALL duck the ambience for about 1 s under the R16.3 stone-crack and thud, then bring it back. The ambience keeps playing across runs until the visitor turns sound off.
- R20.3 THE audio SHALL be embedded once as a data URI and played through the Web Audio API or a single audio element; the page SHALL stay one file with no external requests and under 1.2 MB. With `prefers-reduced-motion` sound behaves the same (motion and sound are separate). A small line at the bottom of the page SHALL credit the sound: "Night ambience generated with Suno."

### Requirement 21: Sound for every movement

**User Story:** As a judge with the sound on, I want to hear the stone grind up through the earth, the soil fall, the hand claw out and the stone split, so that every movement on screen has its sound.

#### Acceptance Criteria

- R21.1 WITH sound on THE SYSTEM SHALL play the effects in `src/tombstone/data/scene/sfx/` (generated with ElevenLabs) on their events: `ground_break.m4a` when the ground heaves at the start of a run; `stone_grind.m4a` on each rise step (restarted or overlapped with a short crossfade so consecutive steps sound like one continuous grinding, slight random pitch and level variation per step); `earth_crumble.m4a` with the clods falling (at most once per step, quieter than the grind); `hand_emerge.m4a` when the skeletal hand first breaks through; `stone_split.m4a` at the split instead of the synthesized crack and thud of R16.3; `hand_sink.m4a` when the hand sinks back.
- R21.2 THE ambience of R20 SHALL duck under these effects and come back after them; effects never play with sound off; with reduced motion only `stone_split.m4a` plays when the final state is shown.
- R21.3 EACH file SHALL be embedded once as a data URI and decoded once through the existing Web Audio context; the page stays one file with no external requests and under 1.35 MB. The credit line of R20.3 SHALL read "Night ambience generated with Suno, sound effects with ElevenLabs."

### Requirement 22: Music from the start, a violent break-through, no milky haze

**User Story:** As a judge, I want the music to be there from the moment I start, the headstone to tear out of the ground like something forcing its way up, and a clear dark night without a milky veil, so that the first screen feels like a horror film.

#### Acceptance Criteria

- R22.1 THE sound SHALL be on by default: the ambience of R20 SHALL start at the visitor's first interaction with the page (any pointer, touch or key event, including the "Release the agent" press), because browsers do not allow sound before one; the toggle then reads "Sound on" and turns everything off and on. Before the first interaction nothing plays.
- R22.2 THE break-through SHALL be violent and physical: before the stone shows, the ground swells two or three times with a small tremor of the whole scene (2 to 3 px) and the soil fissures spread outward in real time; at the moment the top breaks the surface, 15 to 25 photographed clods (R19.1) are thrown up and out in arcs with gravity and spin and fall back onto the grass, torn grass tufts fly, and a cloud of dark soil dust (brown-grey, never white) bursts and settles; the stone then pushes up in jerks, one per step, each with a short scene tremor, a slight tilt wobble of at most 1.5 degrees that settles, and clods tumbling off its shoulders and rolling down the heap; the hand bursts out of the soil with its own small spray of earth. Animation runs on requestAnimationFrame with at most 40 particles at a time; reduced motion shows the final state.
- R22.3 THE fog SHALL not veil the scene: the drifting fog layers sit only in the lowest third of the hero, at most 0.18 opacity, cool blue-grey and textured (wisps, not a flat sheet), so the photo keeps its contrast and blacks; nothing produces a white or milky wash over the scene at any moment (the dust of R22.2 and the stop flash of R16.3 included; the flash stays dim red). The moon and its halo stay as they are.
