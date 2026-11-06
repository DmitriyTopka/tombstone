# Implementation Plan

The package skeleton (pyproject.toml, empty modules, tests/ and tests/fixtures/) already exists. Tasks follow the order: session parser and `tombstone cost`, tombstone writer, caps, graveyard, watcher, stop, autopsy, MCP, init and Power, tests. Behavior and data formats are in design.md.

- [ ] 1. Session parser for V3 and V2
  - One parser, format detected per session, read only, never raises on bad data.
  - _Requirements: R1.1, R1.2, R1.3, R1.5, R1.7, R10.1_

  - [x] 1.1 Fixtures and the V3 parser
    - Copy the V3 session of measurement 5 into `tests/fixtures/` and remove home paths and message text with a small scrub script. Add hand-made edge fixtures: empty file, truncated last line, unknown format, `usage_summary` without a credit entry, an open turn.
    - In `sessions.py` add `Turn`, `Session`, `detect_format`, and `parse_session` for V3: group by `turnId`, cost from `promptTurnSummaries[].usage` with unit `credit`, requests from `requestIds`, model from `session.json`, start from `turn_start`, end from `usage_summary`. Unpriced turns carry a reason.
    - _Requirements: R1.2, R1.3, R1.5, R10.1_

  - [x] 1.2 The V2 parser
    - Add the V2 branch: `user_turn_metadatas[]`, cost as the sum of `metering_usage` values with unit `credit`, requests from `total_request_count`, times from `end_timestamp` and `turn_duration`. Add the V2 fixture if the session is still on disk, otherwise a hand-made one with the same shape.
    - _Requirements: R1.1, R1.3, R1.5, R10.1_

  - [x] 1.3 Session discovery and open-turn progress
    - Add `find_session` (V3 by `session_id` glob, V3 by index tail plus `workspacePaths`, V2 by `cwd`) and cache the V3 project hash. Add `open_turn_progress` returning requests so far, jsonl size and mtime for both engines.
    - _Requirements: R1.7, R4.1, R10.1_

- [x] 2. CLI entry point and `tombstone cost`
  - `cli.main` with argparse, lazy imports per command, project root discovery by walking up from the working directory. `cost` accepts a path, an id or a unique id prefix.
  - Output is one line per turn (engine, model, requests, cost, start, end) and a total. Numbers have at least three decimals and carry `actual` or `est.`. Unpriced turns print the reason. Check by hand: the V3 fixture prints 0.390 credits and 5 requests.
  - _Requirements: R1.3, R1.6, R4.4_

- [ ] 3. Tombstone writer
  - Records in `tombstones.json`, written by the Stop hook with the task id from the three-step rule.
  - _Requirements: R1.4, R2.1, R2.2, R2.3, R2.4, R2.5, R4.2, R4.3_

  - [x] 3.1 Locked JSON store
    - In `tombstones.py` add `update_json` (flock on a lock file, temp file, fsync, `os.replace`), the invalid-JSON path that leaves the file untouched and queues the change in `pending.jsonl`, and load/upsert helpers for `tombstones.json`.
    - _Requirements: R2.5, R2.2_

  - [x] 3.2 tasks.md parsing and task id resolution
    - Add `parse_tasks`, `active_spec`, and `resolve_task`: STDIN keys first, then the checkbox diff against `tasks_snapshot` in `state.json`, then `unknown` (no first unchecked guess, see 3.4). Add `tombstone hook post-tool`, which runs only when the tool input path matches `.kiro/specs/<spec>/tasks.md`, refreshes the snapshot and records newly checked ids for the turn.
    - _Requirements: R2.3, R2.4_

  - [x] 3.3 Record building and the Stop hook
    - Add the record builder (turn entries with `share`, sums, outcome, date), idempotent upsert by record id and turn id, and `tombstone hook stop`: newest closed turn, estimate and actual, `actual = null` when the cost is not there yet, the 50-request average price window written to `state.json`. Hook errors exit 0 and log to `errors.log`.
    - _Requirements: R1.4, R2.1, R2.2, R2.4, R4.2, R4.3_

  - [x] 3.4 Prompt hook and unknown work turns
    - Add `tombstone hook prompt` (`UserPromptSubmit`): store the first 8 words of the prompt with home paths stripped in `state.turn.prompt_name` and reset `state.turn.checked`. Change `resolve_task` to STDIN id, then boxes checked in the turn, then `unknown` named after the prompt; remove the first unchecked fallback. The Stop hook writes one record per checked box with an equal share, and one `unknown` record per turn otherwise.
    - _Requirements: R2.3, R2.4_

- [ ] 4. Caps and budget math
  - `tombstone.toml`, similar-task median, and the gate that blocks a new task before it starts.
  - _Requirements: R3.1, R3.2, R3.3, R3.4, R3.5, R3.6, R11.5_

  - [x] 4.1 Config, task type and similar tasks
    - In `caps.py` add `load_config` (tomllib, defaults from R3.2, per-key fallback with a warning), `task_type`, `keywords`, `similar` and `median_similar` (basis `actual` or `est.`).
    - _Requirements: R3.1, R3.2, R3.5, R3.6_

  - [x] 4.2 Gate decision and the pre-tool hook
    - Add `check_task_start` (new task detection, remaining spec budget, spec cap, task cap as reference when nothing is similar) with the stderr message naming the task, the similar cost and the remaining budget, each labeled `est.` or `actual`. Write a `cap` record once per blocked task. Add `tombstone hook pre-tool` for the cap path with fail-open behavior and lazy imports.
    - _Requirements: R3.3, R3.4, R3.6, R4.4, R11.5_

- [x] 5. Graveyard
  - `render_graveyard` in `tombstones.py` returns one HTML string with inline CSS and JS (sortable table, filter box, outcome badges, `est.` or `actual` on every number) and no external resource of any kind. `tombstone graveyard [--out FILE]` writes it to `.kiro/tombstone/graveyard.html`.
  - _Requirements: R2.6, R4.4_

- [ ] 6. Watcher
  - One process per project, polling every 2 seconds. The decision logic is a pure function.
  - _Requirements: R4.1, R5.1, R5.2, R5.3, R5.4, R5.5, R5.6, R5.7_

  - [x] 6.1 State file and project change detection
    - Add state read/update through `update_json` with the fields from design.md, `~` in stored paths, and the change detector: `git status --porcelain=v1 -z` plus mtime and size of the listed files, a tree walk fallback without git, ignoring `.kiro/tombstone/`, `.git/` and `tombstones.json`.
    - _Requirements: R5.4, R5.6_

  - [x] 6.2 Evaluate and the poll loop
    - Add `evaluate` (pulse in the last 60 s, quiet clock starting at the later of the last change and the turn start, stall, over-cap on the task running estimate, no stall when files change, one event per turn), `poll_once` with the estimate as requests times average price, and `tombstone watch` running the loop every 2 seconds with errors logged and the loop kept alive. Raise events into `stop.begin_stop`.
    - _Requirements: R4.1, R5.1, R5.2, R5.3, R5.5, R5.7_

- [ ] 7. Stop in two steps
  - STOP file, blocking hook, notification, and the kill step.
  - _Requirements: R5.5, R6.1, R6.2, R6.3, R6.4, R6.5, R6.6, R6.7_

  - [x] 7.1 STOP file, blocking and resume
    - In `stop.py` add `write_stop` (reason, message, task id, estimate, requests at stop, time), the STOP check as the first step of `tombstone hook pre-tool` (one-line stderr, exit 2, generic message when the file is unreadable), and `tombstone resume`.
    - _Requirements: R6.1, R6.2, R6.6_

  - [x] 7.2 Notification and terminal line
    - Add `notify` (macOS `osascript` with the text passed as an argument, Linux `notify-send`) and the terminal line, with failures logged and ignored. No network call.
    - _Requirements: R6.7_

  - [x] 7.3 Process lookup and kill
    - First record the real process tree of a running V3 session (`ps -Ao pid,ppid,command`) in LESSONS.md. Then add `find_project_processes` (Linux `/proc`, macOS `ps` and `lsof`, only the `kiro-cli` process and its children whose working directory is the project) and `maybe_kill`: after the grace and with a growing request count send SIGTERM, then SIGKILL after 5 seconds. No match means skip, warn in state and autopsy, kill nothing else. Never touch project files.
    - _Requirements: R6.3, R6.4, R6.5_

  - [x] 7.4 Stop sequence orchestration
    - Add `begin_stop(event)`: write STOP, notify, call the autopsy, store the `stop` entry in state, write the `killed` record. Wire `maybe_kill` into the watcher loop.
    - _Requirements: R5.5, R6.1, R6.3, R6.7_

- [ ] 8. Autopsy
  - Markdown receipt with the evidence, no home path in it.
  - _Requirements: R7.1, R7.2, R7.3, R7.4_

  - [ ] 8.1 Write the autopsy
    - In `autopsy.py` add `write_autopsy` producing `.kiro/tombstone/autopsy/<date>_<task>.md` with all fields from R7.2, the last 10 tool calls from `tool_call` events, and `scrub` replacing the home directory with `~` over the whole document.
    - _Requirements: R7.1, R7.2, R7.4_

  - [ ] 8.2 Append the actual cost
    - Add `append_actual`, guarded by `state.autopsy_pending` so it appends once, called by the watcher and by the Stop hook when the closing turn has a cost.
    - _Requirements: R7.3, R4.2_

- [ ] 9. MCP get_budget
  - One tool, no model call, plan balance from a cached local probe.
  - _Requirements: R8.1, R8.2, R8.3, R8.4, R8.5, R11.4_

  - [ ] 9.1 Usage probe and cache
    - In `caps.py` add `plan_balance`: cache in `.kiro/tombstone/usage.json`, refresh at most every 5 minutes (failures cached too) with `subprocess.run(["kiro-cli", "chat", "--no-interactive", "/usage"], timeout=15)`, parser tested against a recorded sample, `None` with a reason when unavailable. Hooks read the cache only.
    - _Requirements: R8.3, R8.4, R11.4_

  - [ ] 9.2 Budget function and stdio server
    - In `mcp_server.py` add `budget()` (similar median with count and basis, spec cap and remaining, plan balance with age in minutes) and `serve()` registering exactly one tool `get_budget(task_name=None)` on stdio. Add `tombstone mcp`.
    - _Requirements: R8.1, R8.2, R8.4, R8.5_

- [ ] 10. Init and Power
  - One command prepares a project, a Power folder ships the same files.
  - _Requirements: R9.1, R9.2, R9.3, R9.4, R9.5, R10.2, R10.3, R10.4, R11.1, R11.3_

  - [ ] 10.1 Hook and config builders
    - In `cli.py` add builder functions returning the V3 hook files (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, plus the two task triggers calling `tombstone hook task-pre` and `task-post`), the V2 agent config with `preToolUse`, `postToolUse`, `stop` and no `model` key, the steering text and the default `tombstone.toml`. Only `command` actions. Use the absolute `tombstone` path from `shutil.which`.
    - _Requirements: R9.2, R10.2, R10.3, R11.1, R11.3_

  - [ ] 10.2 `tombstone init` and watcher units
    - Add `init`: create each file only when absent and print `created` or `exists, left unchanged`; write the launchd plist or systemd user unit and print the load command (running it needs a steering change, see design.md Open points); print how to start Kiro on V2 (`kiro-cli chat --agent tombstone`) and on V3.
    - _Requirements: R9.2, R9.3, R9.4, R9.5_

  - [ ] 10.3 Power folder and CLI version check
    - Create `power/` with `plugin.json`, `skills/tombstone/SKILL.md`, `mcp.json` for `get_budget`, and hook templates generated from the builders. Add the tested CLI version constant (3.0.0), store the installed version in `state.json` at init and watcher start, and print one warning on mismatch.
    - _Requirements: R9.1, R10.4_

- [ ] 11. Tests
  - Fixtures are already in place from task 1. Tests run with `pytest` and need no Kiro install.
  - _Requirements: R1.1, R1.2, R1.5, R2.4, R2.5, R3.3, R3.4, R3.6, R4.1, R4.2, R5.2, R5.3, R5.4, R5.7, R6.2, R6.3, R6.4, R7.3, R7.4, R8.1, R8.4, R9.4, R11.1, R11.2, R11.3, R11.4, R11.5_

  - [ ] 11.1 Parser, writer and caps tests
    - Cover both formats and every unpriced reason, request counting for open turns against closed turns, task id resolution in all four steps, 20 concurrent writers leaving valid JSON, the invalid-JSON path, and the gate decisions with message text.
    - _Requirements: R1.1, R1.2, R1.5, R2.4, R2.5, R3.3, R3.4, R3.6_

  - [ ] 11.2 Watcher, stop and autopsy tests
    - Table tests for `evaluate`, change detection ignores, STOP block and resume, kill with a fake process list, empty list skips step two, autopsy fields, no home path, actual appended once.
    - _Requirements: R4.1, R5.2, R5.3, R5.4, R5.7, R6.2, R6.3, R6.4, R7.3, R7.4_

  - [ ] 11.3 MCP and init tests
    - `budget()` with and without similar tasks and with the balance missing, tool list equals `["get_budget"]`, `init` run twice leaves files unchanged, `power/` files equal builder output.
    - _Requirements: R8.1, R8.4, R9.4_

  - [ ] 11.4 Latency, zero-credit and accuracy checks
    - Median of five `tombstone hook pre-tool` subprocess runs under 200 ms without a STOP file. Static checks: no prompt-type hook, no `model` key, no model, agent or paid-API call, no network module imports, no em or en dash in any text file. Estimate within 30% of actual on the 10-request fixture. Record the manual "no extra metered request" check in `KIRO_LOG.md`.
    - _Requirements: R4.2, R11.1, R11.2, R11.3, R11.4, R11.5_
