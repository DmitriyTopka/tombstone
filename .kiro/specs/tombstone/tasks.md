# Implementation Plan

The package skeleton (pyproject.toml, empty modules, tests/ and tests/fixtures/) already exists. Tasks follow the order: session parser and `tombstone cost`, tombstone writer, caps, graveyard, watcher, stop, autopsy, MCP, init and Power, tests. Behavior and data formats are in design.md.

- [x] 1. Session parser for V3 and V2
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

- [x] 3. Tombstone writer
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

- [x] 4. Caps and budget math
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

- [x] 6. Watcher
  - One process per project, polling every 2 seconds. The decision logic is a pure function.
  - _Requirements: R4.1, R5.1, R5.2, R5.3, R5.4, R5.5, R5.6, R5.7_

  - [x] 6.1 State file and project change detection
    - Add state read/update through `update_json` with the fields from design.md, `~` in stored paths, and the change detector: `git status --porcelain=v1 -z` plus mtime and size of the listed files, a tree walk fallback without git, ignoring `.kiro/tombstone/`, `.git/` and `tombstones.json`.
    - _Requirements: R5.4, R5.6_

  - [x] 6.2 Evaluate and the poll loop
    - Add `evaluate` (pulse in the last 60 s, quiet clock starting at the later of the last change and the turn start, stall, over-cap on the task running estimate, no stall when files change, one event per turn), `poll_once` with the estimate as requests times average price, and `tombstone watch` running the loop every 2 seconds with errors logged and the loop kept alive. Raise events into `stop.begin_stop`.
    - _Requirements: R4.1, R5.1, R5.2, R5.3, R5.5, R5.7_

- [x] 7. Stop in two steps
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

- [x] 8. Autopsy
  - Markdown receipt with the evidence, no home path in it.
  - _Requirements: R7.1, R7.2, R7.3, R7.4_

  - [x] 8.1 Write the autopsy
    - In `autopsy.py` add `write_autopsy` producing `.kiro/tombstone/autopsy/<date>_<task>.md` with all fields from R7.2, the last 10 tool calls from `tool_call` events, and `scrub` replacing the home directory with `~` over the whole document.
    - _Requirements: R7.1, R7.2, R7.4_

  - [x] 8.2 Append the actual cost
    - Add `append_actual`, guarded by `state.autopsy_pending` so it appends once, called by the watcher and by the Stop hook when the closing turn has a cost.
    - _Requirements: R7.3, R4.2_

- [x] 9. MCP get_budget
  - One tool, no model call, plan balance from a cached local probe.
  - _Requirements: R8.1, R8.2, R8.3, R8.4, R8.5, R11.4_

  - [x] 9.1 Usage probe and cache
    - In `caps.py` add `plan_balance`: cache in `.kiro/tombstone/usage.json`, refresh at most every 5 minutes (failures cached too) with `subprocess.run(["kiro-cli", "chat", "--no-interactive", "/usage"], timeout=15)`, parser tested against a recorded sample, `None` with a reason when unavailable. Hooks read the cache only.
    - _Requirements: R8.3, R8.4, R11.4_

  - [x] 9.2 Budget function and stdio server
    - In `mcp_server.py` add `budget()` (similar median with count and basis, spec cap and remaining, plan balance with age in minutes) and `serve()` registering exactly one tool `get_budget(task_name=None)` on stdio. Add `tombstone mcp`.
    - _Requirements: R8.1, R8.2, R8.4, R8.5_

- [x] 10. Init and Power
  - One command prepares a project, a Power folder ships the same files.
  - _Requirements: R9.1, R9.2, R9.3, R9.4, R9.5, R9.6, R9.7, R9.8, R10.2, R10.3, R10.4, R11.1, R11.3_

  - [x] 10.1 Hook and config builders
    - In `cli.py` add builder functions returning the V3 hook files (`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, plus the two task triggers calling `tombstone hook task-pre` and `task-post`), the V2 agent config with `preToolUse`, `postToolUse`, `stop` and no `model` key, the steering text and the default `tombstone.toml`. Only `command` actions. Use the absolute `tombstone` path from `shutil.which`.
    - _Requirements: R9.2, R10.2, R10.3, R11.1, R11.3_

  - [x] 10.2 `tombstone init` and watcher units
    - Add `init`: create each file only when absent and print `created` or `exists, left unchanged`; write the launchd plist or systemd user unit and print the load command (running it needs a steering change, see design.md Open points); print how to start Kiro on V2 (`kiro-cli chat --agent tombstone`) and on V3.
    - _Requirements: R9.2, R9.3, R9.4, R9.5_

  - [x] 10.3 Power folder and CLI version check
    - Create `power/` with `plugin.json`, `skills/tombstone/SKILL.md`, `mcp.json` for `get_budget`, and hook templates generated from the builders. Add the tested CLI version constant (3.0.0), store the installed version in `state.json` at init and watcher start, and print one warning on mismatch.
    - _Requirements: R9.1, R10.4_

  - [x] 10.4 Uninstall
    - Record the sha256 of each file `init` creates in `.kiro/tombstone/installed.json`. Add `tombstone uninstall [--purge] [--force] [--project DIR]`: unload and delete this project's watcher unit, delete the init files, remove only the `tombstone` entry from `.kiro/settings/mcp.json`, keep changed files unless `--force`, keep user data unless `--purge` (never `tombstones.json`), print one line per file. Safe to run twice and works without `installed.json`. Tests use `tmp_path`, a fake HOME and a mocked unit loader.
    - _Requirements: R9.6, R9.7, R9.8_

- [x] 11. Tests
  - Fixtures are already in place from task 1. Tests run with `pytest` and need no Kiro install.
  - _Requirements: R1.1, R1.2, R1.5, R2.4, R2.5, R3.3, R3.4, R3.6, R4.1, R4.2, R5.2, R5.3, R5.4, R5.7, R6.2, R6.3, R6.4, R7.3, R7.4, R8.1, R8.4, R9.4, R11.1, R11.2, R11.3, R11.4, R11.5_

  - [x] 11.1 Parser, writer and caps tests
    - Cover both formats and every unpriced reason, request counting for open turns against closed turns, task id resolution in all four steps, 20 concurrent writers leaving valid JSON, the invalid-JSON path, and the gate decisions with message text.
    - _Requirements: R1.1, R1.2, R1.5, R2.4, R2.5, R3.3, R3.4, R3.6_

  - [x] 11.2 Watcher, stop and autopsy tests
    - Table tests for `evaluate`, change detection ignores, STOP block and resume, kill with a fake process list, empty list skips step two, autopsy fields, no home path, actual appended once.
    - _Requirements: R4.1, R5.2, R5.3, R5.4, R5.7, R6.2, R6.3, R6.4, R7.3, R7.4_

  - [x] 11.3 MCP and init tests
    - `budget()` with and without similar tasks and with the balance missing, tool list equals `["get_budget"]`, `init` run twice leaves files unchanged, `power/` files equal builder output.
    - _Requirements: R8.1, R8.4, R9.4_

  - [x] 11.4 Latency, zero-credit and accuracy checks
    - Median of five `tombstone hook pre-tool` subprocess runs under 200 ms without a STOP file. Static checks: no prompt-type hook, no `model` key, no model, agent or paid-API call, no network module imports, no em or en dash in any text file. Estimate within 30% of actual on the 10-request fixture. Record the manual "no extra metered request" check in `KIRO_LOG.md`.
    - _Requirements: R4.2, R11.1, R11.2, R11.3, R11.4, R11.5_

- [x] 12. Graveyard v2
  - Design approved by Dima on 2026-11-06.
  - Reference design: `.kiro/specs/tombstone/graveyard_mockup.html` (a JS mockup with `__DATA__` and `__PLOTS__` placeholders). Port it into `render_graveyard`; do not ship the mockup's client-side rendering of headstones.
  - _Requirements: R2.6, R2.7_

  - [x] 12.1 Server-rendered headstones and plots
    - `render_graveyard(records, plots=None)` emits the sky, the stopped row, plots, unmarked graves and the register as HTML. `write_graveyard` reads plot names from the top-level `- [ ] N. Name` lines of `.kiro/specs/*/tasks.md` (checked or not, backticks removed), falling back to "Task N". Height uses the mockup formula. Inline JS only for the detail card, register sort and filter. All text escaped. No external resource; an SVG data URI may keep its xmlns.
    - _Requirements: R2.6, R2.7_

  - [x] 12.2 Graveyard v2 tests
    - Update `tests/test_graveyard.py` for the new structure and add: plot grouping and names from a tasks.md fixture, unknown records in Unmarked graves, killed and blocked records in the stopped row, height grows with cost, headstones present with JS disabled (in the HTML source), reduced-motion rule present, dialog markup, no external resource, no em or en dash.
    - _Requirements: R2.6, R2.7_

- [x] 13. Graveyard v3 (scene and proof strip)
  - Design approved by Dima on 2026-11-07.
  - Reference design: `.kiro/specs/tombstone/graveyard_v3_mockup.html`. It is the real output of `render_graveyard` on this repo with two changes: the whole `<style>` block and the `<header class="sky">` block. Stones, plots, register, card and script are unchanged. Port those two blocks into `render_graveyard`; the figures in the header must be computed from the records, not copied.
  - _Requirements: R2.6, R2.7, R2.8_

  - [x] 13.1 New stylesheet and header in render_graveyard
    - Replace the page CSS with the mockup CSS (the grain SVG data URI keeps its xmlns). Header: kicker "Tombstone, the graveyard of its own build", headline "<total> credits are buried here." where total is the sum of the cost each stone shows, the lede line from the mockup, and a `<dl class="proof">` strip: graves; most expensive cost with "the most expensive: <name in lower case>"; median cost "credits for a median grave"; total requests "model requests behind them"; and only when stopped records exist "<n> stopped by the breaker" with class `red`. Two decimals for costs. The `<title>` becomes "The Graveyard, <total> credits". With no records, the headline reads "Nothing is buried here yet." and there is no strip. Keep the existing empty-state copy below.
    - _Requirements: R2.7, R2.8_

  - [x] 13.2 Graveyard v3 tests
    - In `tests/test_graveyard.py`: headline total equals the sum of stone costs; strip figures for a fixture (graves, most expensive, median, requests); the stopped figure is present only when a stopped record exists and carries class `red`; empty records give "Nothing is buried here yet." and no strip; the hills SVG is inline; the 375 px rule keeps headstone text at 14 px or more (check the CSS source); no external resource; no em or en dash.
    - _Requirements: R2.7, R2.8_

- [x] 14. Live stops on the graveyard
  - Approved by Dima on 2026-11-07.
  - `live_stops.json` at the repo root holds the 15 real stopped turns from the live tests (burn2, burn5, live1 to live7) and the video recording, copied unchanged from those scratch projects with one extra field, `source`. Do not edit it.
  - _Requirements: R2.6, R2.7, R2.8, R2.9_

  - [x] 14.1 `--include` for `tombstone graveyard`
    - Repeatable `--include FILE`. Load with the same reader as tombstones.json, merge by id (project records win), pass to `render_graveyard` with which records came from which include. Stopped row note adds one sentence when included stopped records exist: "<n> of them are live test runs on scratch projects, kept in <file name>." Kicker as R2.9 when any included record is on the page. Outcome `killed` reads "stopped" in the card meta and the register cell (the `data-outcome` attribute and sort keep the raw value). Missing or bad include file: plain message, exit 1, no page written.
    - _Requirements: R2.9_

  - [x] 14.2 Tests for live stops
    - CLI: include merges records, duplicate id skipped and project record wins, missing file exits 1 without writing, bad JSON exits 1. Render: note sentence and kicker appear only with included stopped records; "killed" never shows as visible text in card meta or register cells, "stopped" does; project tombstones.json is unchanged after a run with includes. A test that renders this repo with `live_stops.json` shows 15 stopped records.
    - _Requirements: R2.9_

- [x] 15. Graveyard v4 (headstone shapes, cracks, spacing)
  - Design approved by Dima on 2026-11-07.
  - Reference: `.kiro/specs/tombstone/graveyard_v4_mockup.html`, made from the real v3 output with `--include live_stops.json` by `.kiro/specs/tombstone/graveyard_v4_changes.py`. Read the script: its docstring lists the five changes, and its CSS block and shape and crack rules are what to port. Port them into `render_graveyard` and `_g_stone`; do not post-process HTML with regular expressions in the product.
  - _Requirements: R2.6, R2.7, R2.8, R2.9, R2.10_

  - [x] 15.1 Shapes, cracks, spacing and copy in render_graveyard
    - Shape per headstone: `SHAPES[(index in row + zlib.crc32(row key)) % 5]` with row key the plot number, "unmarked" or "stopped"; a cross under 170 px becomes a slab. Class `stone shape-<name>`, and for stopped headstones also `crack-<(index * 3 + 1) % 4>`. Remove the ghost span and its CSS. Add the v4 CSS. One fix over the mockup: in the mockup the red base glow of stopped slab, gothic, shoulder and cross stones is cut by their clip-path; make the glow show the same on every shape, for example by widening the plinth band in the clip-path and drawing the glow inside it. Copy: the lede and the "All N are live test runs" note as in the mockup ("N of them" stays when only some stopped stones are included).
    - _Requirements: R2.10_

  - [x] 15.2 Tests for v4
    - Neighbours in every row have different shape classes and stopped neighbours different crack classes; a headstone under 170 px is never a cross; no ghost markup or CSS; the shape and crack classes are stable for the same records; the lede and note copy; the stopped row CSS caps a line at 8 headstones and keeps a gap of at least 80 px; no external resource; no em or en dash.
    - _Requirements: R2.10_

  - [x] 15.3 Neighbours never match, one-stop copy
    - When a cross under 170 px falls back, pick the first of slab, arch, gothic, shoulder that differs from both neighbours in the row (the row is known in render_graveyard, pass it in). Make the 15.2 neighbour test cover stones under 170 px too. Note copy for one included stop: "It is a live test run on a scratch project, kept in <file>." Keep "All N are" for N of 2 or more when all are included, "N of them are" otherwise.
    - _Requirements: R2.10_

- [x] 16. Runtime state outside /.kiro
  - Approved by Dima on 2026-11-09. Reason: the Kiroween rules disqualify a submission that puts /.kiro or its sub-folders in .gitignore, and this repo has `.kiro/tombstone/` there.
  - _Requirements: R12.1, R12.2, R12.3_

  - [x] 16.1 Move the sidecar to `.tombstone/`
    - One constant for the sidecar path, used by stop, autopsy, tombstones, caps, watcher, cli (installed.json, uninstall, graveyard default out). The watcher ignores `.tombstone/` instead of `.kiro/tombstone/`. One-time migration as R12.2, using the existing O_NOFOLLOW and atomic helpers; if both directories exist, use `.tombstone/` and leave the old one alone. Steering text, docstrings, `--purge` and init output name `.tombstone/`. If init touches .gitignore or prints advice about it, it names `.tombstone/`.
    - _Requirements: R12.1, R12.2, R12.3_

  - [x] 16.2 Tests
    - Update the existing tests to the new path. New tests: migration moves an old `.kiro/tombstone/` with its files; both present leaves the old one; a symlinked old sidecar is not followed; no code path writes under `.kiro/tombstone/`; uninstall --purge removes `.tombstone/`.
    - _Requirements: R12.1, R12.2_

- [x] 17. Full dogfood hook set in this repo
  - Approved by Dima on 2026-11-09.
  - _Requirements: R12.4_

  - [x] 17.1 Seven hooks and MCP in `.kiro/`
    - Add to `.kiro/hooks/` the four missing V3 hooks from `power/hooks/v3/` (pre-tool, session-start, task-pre, task-post), in the same style as the three already there: command `.venv/bin/tombstone hook <name>`, timeout 10. Add `.kiro/settings/mcp.json` that starts `.venv/bin/tombstone mcp` for this project. Add `tombstone.toml` at the repo root with `spec_cap = 600` so the brake never blocks normal build tasks, keep the other keys at their defaults. A test checks that `.kiro/hooks/` holds one hook per trigger that init writes for V3 and that no file under `.kiro/` is matched by the repo `.gitignore`.
    - _Requirements: R12.4_

- [x] 18. Stop receipt in the card of a cracked stone
  - Approved by Dima on 2026-11-20. Reason: the Costume Contest asks for spooky elements that serve the function; the crack should open the receipt that explains it.
  - _Requirements: R2.11_

  - [x] 18.1 Read autopsies and match them to stopped records
    - A small parser for the autopsy markdown that `write_autopsy` writes (src/tombstone/autopsy.py): STOP reason, refusal text, estimate at stop, step two line, and the rows of the "Last 10 tool calls" table (tool, title). Match an autopsy to a stopped record by the turn id when the file name or the content holds it, otherwise by task id and date. Sources: the project's `.tombstone/autopsy/` and every `--autopsies DIR` (repeatable) on `tombstone graveyard`. A missing or unreadable directory is skipped with one stderr line; a malformed autopsy is skipped, never crashes the render. Open files with the existing safe helpers (no symlink following).
    - _Requirements: R2.11_

  - [x] 18.2 Receipt block in the card
    - For stopped headstones only, carry the receipt in data attributes (escaped) and render a "Receipt" section in the card: reason, the refusal text in the monospace red style already used for stops, "kill: ran / did not run", and the tool calls as a short list. Without a receipt the section reads "No receipt was kept for this stop." Same look as the existing card (fonts, colours, spacing), works with the keyboard and on a 375 px screen, no external requests, no em or en dash.
    - _Requirements: R2.11_

  - [x] 18.3 Tests
    - Parser on a real autopsy fixture (copy `live_autopsies/` files into tests/fixtures), matching by turn id and by task and date, a stopped record without autopsy shows the "No receipt" line, a malformed file is skipped, `--autopsies` with a missing dir exits 0 and writes the page, HTML escaping of a refusal text with `<` and `"`, no receipt section on finished stones.
    - _Requirements: R2.11_

  - [x] 18.4 Fix: the page script must parse
    - Found by a human after 18.2: the generated page's inline script contains `.split("` followed by a real line break, because the Python source writes `"\n"` inside a normal (not raw) string, so the browser gets a syntax error and no card opens at all. Fix the escaping (for example `\\n` in the Python source, or split on a character that needs no escape). Add a test that extracts the inline script from a rendered page and checks it has no raw line break inside a JS string literal; if `node` is on PATH, also check `new Function(script)` does not throw (skip that part when node is missing).
    - _Requirements: R2.11_

- [x] 19. Continuous integration
  - Approved by Dima on 2026-11-23.
  - _Requirements: R13.1, R13.2, R13.3_

  - [x] 19.1 GitHub Actions workflow
    - `.github/workflows/tests.yml`: on push to main and pull_request; matrix ubuntu-latest and macos-latest, Python 3.11 and 3.12; checkout, setup-python, `pip install -e ".[dev]"`, `python -m pytest -q`. No secrets, no network beyond installing pytest. Pin actions to a major version.
    - _Requirements: R13.1_

  - [x] 19.2 Make the suite runner-safe
    - Run the suite the way CI will: in a fresh venv outside this repo's `.venv` (for example `python3 -m venv /tmp/ci-venv && /tmp/ci-venv/bin/pip install -e ".[dev]"`) and with `HOME` pointed at an empty temp dir. Any test that fails only because of this machine's setup (the project `.venv` path, a home-directory file, macOS-only tools such as launchctl or ps flags) gets a skip with a clear reason or a portable fix. Tests that check product behaviour must not be skipped. Report which tests were changed and why.
    - _Requirements: R13.2_

  - [x] 19.3 Badge
    - Add the badge `[![tests](https://github.com/DmitriyTopka/tombstone/actions/workflows/tests.yml/badge.svg)](https://github.com/DmitriyTopka/tombstone/actions/workflows/tests.yml)` as the first line under the README title. Change nothing else in README.md.
    - _Requirements: R13.3_

- [x] 20. The receipt records a kill that ran
  - Found on 2026-11-23 in the first live kill: the watcher ended the kiro-cli tree 12 s after STOP (state.json `stop.kill.status = killed`, three PIDs terminated), but the autopsy still says "Step two (kill): no (not yet; waits for the grace period and more requests after the stop)", because it is written at STOP time and never updated.
  - _Requirements: R6.3, R7.1_

  - [x] 20.1 Update the step two line after `maybe_kill`
    - When `maybe_kill` finishes with `killed` or `no_process`, rewrite the "Step two (kill)" line of that stop's autopsy (path in `state.stop.autopsy`) with the outcome, the time and the number of processes terminated and killed, using the existing safe atomic write helpers and `safe_autopsy_path`. Leave the rest of the file unchanged. If the autopsy is missing or unreadable, do nothing and keep the kill result in state.json.
    - Tests: after a fake kill the autopsy line says the kill ran with the PID counts; `no_process` writes its warning; a missing autopsy does not raise; a symlinked autopsy is not followed. The graveyard receipt parser reads the new line and the card shows "ran".
    - _Requirements: R6.3, R7.1_

- [x] 21. A first screen a newcomer understands
  - Approved by Dima on 2026-11-24 (council plan, item 3). Judges asked for simplicity, a clean UI and a clear UX.
  - _Requirements: R2.12_

  - [x] 21.1 First screen, plain names, one word
    - In `render_graveyard` (tombstones.py) add the tagline line, cut the figure strip to three (tasks buried, stopped by the breaker, the most expensive task), add the legend and the click hint, and `--video URL` / `--repo URL` options on `tombstone graveyard` that draw two plain links in the header (escaped, omitted when not given). For a stopped record with task id `unknown`, show "Live stop N" (N by stop time from the oldest, stable for the same input) and under it the reason from its matched autopsy in plain words (over_cap -> "over its cap", stall -> "stalled", a block before the turn started -> "blocked before it started", no receipt -> "stopped"); keep the prompt only inside the card. Replace "grave" and "turn" in visible copy with "task"; keep CSS class names and data attributes as they are. Keep the existing look; no new colours, no new sections.
    - _Requirements: R2.12_

  - [x] 21.2 Tests
    - The tagline, the legend and exactly three figures are on the page; the hint shows only when a stopped stone exists; `--video`/`--repo` links appear only when given and are escaped; an unknown-task stopped record reads "Live stop N" with the right reason and never "no task"; no visible "grave" or "turn" outside the title and CSS; the inline script still parses (existing test).
    - _Requirements: R2.12_

- [x] 22. `tombstone demo`
  - Approved by Dima on 2026-11-24 (council plan, item 4). A judge can see the breaker trip with no Kiro plan and no credits. If the suite is not green after one run, the task is dropped.
  - _Requirements: R14.1, R14.2, R14.3, R14.4_

  - [x] 22.1 Packaged session and the demo command
    - Copy the scrubbed V3 fixture from `tests/fixtures/v3_measurement5/` (or another existing scrubbed V3 fixture with enough requests to pass 0.30 at the uncalibrated price) into package data under `src/tombstone/`, declared in `pyproject.toml` so it installs. Add the `demo` subcommand in `cli.py`: scratch project with `tombstone.toml` (task cap 0.30), replay the session one request at a time through the existing estimate and over-cap logic, print a line per request, write STOP and the autopsy through the existing functions, then call the existing pre-tool hook function with a sample `PreToolUse` payload and print its exit code and refusal text; end with the receipt path and the `tombstone graveyard --project <dir>` command. Reuse existing code; no new estimate logic.
    - _Requirements: R14.1, R14.2, R14.3, R14.4_

  - [x] 22.2 Tests
    - `tombstone demo --dir <tmp>` exits 0, prints a STOP line, a hook exit code 2 and the refusal text, writes the STOP file and one autopsy in `<tmp>/.tombstone/`; it does not touch `HOME` (run it with `HOME` set to an empty temp dir and check nothing appeared there) and runs with `PATH` stripped of `kiro-cli`; a non-empty `--dir` is refused with exit 1; the packaged session contains no `/Users/` or `/home/` path.
    - _Requirements: R14.1, R14.2, R14.3, R14.4_

- [x] 23. Lede that matches what the page shows
  - Approved by Dima on 2026-11-24 (item 6 of the final check). A cold reader saw "Each stone is a spec task Kiro finished" next to 22 stones filed as unknown.
  - _Requirements: R2.13_

  - [x] 23.1 Lede text and its test
    - Change `_GRAVEYARD_LEDE` in `tombstones.py` to the exact R2.13 text and update the expected lede in `tests/test_graveyard.py` (`V3_LEDE`) to match. No other change to the page.
    - _Requirements: R2.13_

- [x] 24. Live credits from Kiro instead of a guess
  - Approved by Dima on 2026-11-24. The experiment in `experiments/RESULTS.md` showed the breaker missing a 0.33 cap in 3 of 3 runs because the calibrated estimate was 72 percent low. Kiro CLI 3.5.0 already writes the running credit total of a turn (`session_metadata` key `creditSplit`).
  - _Requirements: R4.5_

  - [x] 24.1 Read `creditSplit` and use it for the cap
    - In `sessions.py` add a function that returns the live credits of the open V3 turn per R4.5 (None when the session has no `creditSplit`). In the watcher's evaluate path and in the pre-tool hook's cap check, use `max(estimate, live)` as the figure compared with the cap, store `live` in the turn state next to the estimate with basis `live`, and write it into the autopsy and the STOP message ("live 0.60 of 0.33" when live is the deciding figure). Keep everything else as it is; no new config keys.
    - _Requirements: R4.5_

  - [x] 24.2 Fixture and tests
    - Teach `tests/fixtures/scrub_session.py` to keep the numeric `value` of `session_metadata` events whose key is `creditSplit` or `contextUsage` (still blank every other text). Scrub the raw session in `~/tombstone-demo/exp_2411/fixture_src/` into `tests/fixtures/v3_live_credits/` and check it holds no `/Users/` or `/home/` path. Tests: the live credits of the fixture's open-turn prefix (cut the file before `usage_summary`) match the last `creditSplit` main; a session without `creditSplit` gives None and the old estimate path is unchanged; with a 0.33 cap the watcher raises over_cap on the prefix that reaches main 0.598 although the calibrated estimate is lower; the hook refuses with the live figure in the message.
    - _Requirements: R4.5_

- [x] 25. A first click that shows a receipt
  - Approved by Dima on 2026-11-26 (council of 26.11, item 0). A cold reader opened the first cracked stone and got a card titled with a cut-off prompt ("You are auditing this project for consistency. Do") and "No receipt was kept for this stop": 14 of the 25 live stops are early test stops from 6 Nov with no autopsy.
  - _Requirements: R2.14_

  - [x] 25.1 Order, card title, headline line
    - In `render_graveyard` and `_g_stone` (tombstones.py): order the stopped row per R2.14 (with receipt first, newest first; without receipt after the label), keep the "Live stop N" numbers by stop time; for a live stop set the card title to "Live stop N, <reason>" and move the prompt into a "Prompt: ..." line inside the card (ellipsis when cut); add the line under the headline. Keep the page one file, everything escaped.
    - _Requirements: R2.14_

  - [x] 25.2 Tests
    - In `tests/test_graveyard.py`: the first stopped stone on the page has `data-receipt="yes"` and is the newest such stop; stones without a receipt come after the label and only then; numbering by time is unchanged; the card title of a live stop never contains the prompt and the prompt line ends with an ellipsis when cut; the headline line is present; no external requests. Update any existing expectation that these changes break, nothing else.
    - _Requirements: R2.14_

- [x] 26. Run the agent on the page
  - Approved by Dima on 2026-11-26 (council of 26.11, item 1). The Costume rubric asks for spooky design that enhances the app's function; today the crack only reports a stop that happened elsewhere. Gate: if the suite is not green or the block breaks the page on a phone by 2026-11-29 evening, the block is not published.
  - _Requirements: R15.1, R15.2, R15.3, R15.4_

  - [x] 26.1 Trace at build time
    - New function in `src/tombstone/demo.py` (reuse `packaged_session`, the replay loop of `run` and `watcher.evaluate`; no STOP file, no autopsy, no hook process, nothing written to disk) that returns the per-request credits and, for each cap of the slider, the stop request, the figure and the refusal text from the hook's own refusal builder. Deterministic, under 2 seconds.
    - _Requirements: R15.2_

  - [x] 26.2 The block on the page
    - In `render_graveyard` (tombstones.py) draw the "Run the agent" block per R15.1, R15.3 and R15.4 from the trace of 26.1, in the page's existing style (stone shapes, crack, red glow), inline JSON escaped for a script tag, no external requests.
    - _Requirements: R15.1, R15.3, R15.4_

  - [x] 26.3 Tests
    - The trace is deterministic; for every cap the stop request equals what `tombstone demo` logic gives for that cap (check the default 0.30 against `demo.run` output); a cap above the total has no stop; the block, caption, slider labels and noscript text are on the page; the inline JSON cannot close the script tag; the page makes no external requests; R2 tests still pass.
    - _Requirements: R15.1, R15.2, R15.3, R15.4_

  - [x] 26.4 Layout and starting cap
    - Found in a browser check on 2026-11-26: after a run the refusal text and the "Without the breaker" line sit under the "Stopped by the breaker" section and cannot be seen; at the 0.30 start the run stops on request 2 of 9, so the stone barely grows. Make the block grow with its content per R15.5 (no fixed height, no overlap at 1280 and 375 px) and start the slider at 0.75 per R15.4 (noscript text too). `tombstone demo` keeps its own 0.30 cap. Update the tests that check the start cap and add one that the result lines are inside the block's flow (no absolute positioning or fixed height on the block).
    - _Requirements: R15.4, R15.5_

- [x] 27. A frightening first screen built around the breaker
  - Approved by Dima on 2026-11-26 ("Да, делай страшную"). A side-by-side of first screens (contests_2026-10/new_3110_kiroween/visual_2611/) showed the strongest Costume entries drawing a full illustrated scene with the main action on the first screen, while ours opened with text and figures and the Run block below the fold. Rollback tag `rollback-before-1127` exists.
  - _Requirements: R16.1, R16.2, R16.3, R16.4, R16.5_

  - [x] 27.1 The hero scene and controls
    - In `render_graveyard` (tombstones.py) replace the `header.sky` first screen with the R16.1/R16.2 hero: inline SVG layers (moon and halo, fog, trees, fence, silhouettes, ground) and the R15 runner moved into it as the centre; move the old first-screen content under "The graveyard of this build" per R16.4. Reuse `_g_runner` and its trace, the existing stone, crack and glow CSS and the page script; keep everything escaped and in one file.
    - _Requirements: R16.1, R16.2, R16.4_

  - [x] 27.2 The scare, sound, small screens and fallbacks
    - The stop sequence of R16.3 (crack, one dim red flash, fog thickening, silhouettes darkening, under 1.5 s), the opt-in Web Audio crack and thud behind a "Sound off"/"Sound on" toggle, the 375 px layout, keyboard labels, `prefers-reduced-motion` and the noscript scene per R16.5.
    - _Requirements: R16.3, R16.5_

  - [x] 27.3 Tests
    - Hero present with the line, the headline with the red italic part, the slider, the "Release the agent" button, the R15 caption and the sound toggle off by default; no second Run block below; the old first-screen texts sit after the hero under "The graveyard of this build" with R2.12 to R2.14 wording intact; no `<audio>`, no `src=`/`href=` to external hosts except the two links; a reduced-motion rule and a noscript scene exist; the R15 trace tests still pass. Update existing tests that the move breaks, nothing else.
    - _Requirements: R16.1, R16.2, R16.3, R16.4, R16.5_

- [x] 28. A photographic scene and a headstone that rises from the grave
  - Approved by Dima on 2026-11-26. Dima generated the background (16:9 and 9:16) and a blank headstone with Nano Banana Pro; Claude chroma-keyed the headstone and compressed the three images into `src/tombstone/data/scene/` (137, 107 and 72 KB). Rollback tag `rollback-before-1127` exists.
  - _Requirements: R17.1, R17.2, R17.3, R17.4, R17.5_

  - [x] 28.1 Photo background and rising headstone
    - In `render_graveyard` and `_g_runner` (tombstones.py) and the page script: read the three files from package data (declare them in `pyproject.toml` like the demo session), embed them as data URIs, replace the SVG scenery with the photo and fog per R17.1, and replace the drawn central stone with the rising photographed headstone, carved figure and crack per R17.2 and R17.3; add the credit line per R17.4.
    - _Requirements: R17.1, R17.2, R17.3, R17.4_

  - [x] 28.2 Small screens, motion and fallbacks
    - 375 px, reduced motion, parallax only on fine pointers, and the noscript scene per R17.5.
    - _Requirements: R17.5_

  - [x] 28.3 Tests
    - The three data URIs are in the page and no `src=`/`url(` points outside it; the page is under 700 KB; the credit line is present; the drawn SVG trees and fence are gone while the fog stays; the headstone's visible height at the stop matches the R15 scale for the default cap; reduced-motion and noscript rules exist; the package data is installed (the files resolve through the package). Update existing tests the change breaks, nothing else.
    - _Requirements: R17.1, R17.2, R17.3, R17.4, R17.5_

  - [x] 28.4 Fit, crack and moon
    - Found in a browser check on 2026-11-26 at 800 px: the carved "0.80" and "CREDITS EST." are wider than the headstone's blank face and are cut at both edges; the crack is a thin dark line pinned to the right edge of the stone and reads as a scratch; the dark gradient over the top third dims the moon so the photo loses its brightest point. Make the carved figure and its unit always fit inside the blank face (size from the stone's width, unit on its own smaller line, at most 70 percent of the face width) at 1440, 800 and 375 px; run the crack from the top of the arch down through the middle of the face, wider, with a red glow inside it that pulses once at the stop; shape the top gradient so the moon and its halo keep their brightness (for example a radial clear area around the moon's position in each photo) while the headline area keeps 4.5:1 contrast. Add tests for the size rule of the carved text and for the crack path starting at the top of the stone.
    - _Requirements: R17.1, R17.3_

- [x] 29. The headstone breaks through the earth
  - Approved by Dima on 2026-11-26 (his idea: the slab should break the ground, shed earth, stand in a heap of soil, and split in half only at the very end). Rollback tag `rollback-before-1127` exists.
  - _Requirements: R18.1, R18.2, R18.3, R18.4, R18.5_

  - [x] 29.1 Ground heave, breaking ground, falling earth and the heap
    - In the hero of `render_graveyard` and the page script (tombstones.py): the mound and soil cracks before the first request (R18.1); torn ground edge, falling clumps and crumbs per step, soil dust on the stone (R18.2); the heap of earth at the base sized by the final height and loose clumps nearby (R18.3). Keep the rise on the R15/R17 scale.
    - _Requirements: R18.1, R18.2, R18.3_

  - [x] 29.2 The split at the end
    - Move the crack and STOP to after the stone has settled (R18.4): crack from arch to base, two halves part and tilt slightly, red glow in the gap, flash and optional sound at that moment; reduced motion and noscript per R18.5.
    - _Requirements: R18.4, R18.5_

  - [x] 29.3 Tests
    - The mound, soil cracks, falling-earth and heap elements exist in the hero; the split uses two halves of the same headstone image; STOP and the split are scheduled after the last rise step, not during it; reduced-motion and noscript show the split stone in its heap; page under 750 KB with no external requests; existing R15 to R17 tests still pass (update only those the change breaks).
    - _Requirements: R18.1, R18.2, R18.3, R18.4, R18.5_

  - [x] 29.4 A clean split and no earth before the break
    - Dima's review of the recorded run on 2026-11-26: (1) after the split a dark line with a red glow stays in the gap between the halves; remove it: when the stone splits there is no line, no stroke and no glow in the gap, the two halves simply part and the scene shows through between them (the R16.3 flash stays); (2) the heap of earth is already visible before the run; at rest the path must show no mound, no heap and no soil cracks: the ground heave starts only when the run starts, and the heap builds up only as the stone breaks through and rises, reaching its full size at the final height. Same in reduced motion (final state) and noscript (split stone in its heap, no line in the gap). Update the R18 tests accordingly: no crack stroke or glow element is visible after the split; at rest the mound, heap and cracks are hidden.
    - _Requirements: R18.1, R18.3, R18.4_

  - [x] 29.5 Nothing on the path at rest
    - Browser check by Claude on 2026-11-26 after 29.4: at rest the top of the headstone still shows and a straight brown band of soil (the strip that hides the underground part of the stone) lies across the path, so the earth is visible before the break, against Dima's review. Per the updated R17.2: hide the underground part of the headstone by clipping it at the ground line (clip-path or an overflow box whose bottom is the ground line), remove the drawn band, and keep the stone fully underground at rest, so the path in the photo is untouched until the run starts. The torn soil edge and the heap appear only from the first rise step (R18.1 to R18.3). Noscript and reduced motion keep the final state. Add a test that at rest no soil band, heap or stone is visible and that the stone is clipped, not covered.
    - _Requirements: R17.2, R18.1_

  - [x] 29.6 The stone ends at the ground, the heap covers its foot
    - Browser check by Claude on 2026-11-26 after 29.5 (frames of a recorded run at 1280 px): the rest state is now clean, but while rising and after the split the stone's lower part shows BELOW the heap, about 60 px of stone hang under the ground, and the zigzag soil cracks are drawn on top of that stone part; a thin light line runs along the bottom edge of the heap. Fix the geometry: the clip box bottom must be exactly the ground line where the heap sits, so no part of the stone is ever visible below the ground; the heap is drawn in front of the stone's foot and overlaps it by a few pixels so the stone looks planted in the soil; the soil cracks and torn edges sit on the ground around the stone, behind it in depth, never on its face; no light outline on the heap. Same at 375 px. Add a test that the stone's clip box bottom equals the ground line and that the cracks are layered behind the stone.
    - _Requirements: R17.2, R18.2, R18.3_

  - [x] 29.7 A real heap and natural soil cracks
    - Browser check by Claude on 2026-11-26 after 29.6 (frames at 1280 px): the stone now ends at the ground, good. Two things still read as fake: (1) the heap is a thin strip, a few pixels tall, so the stone does not look like it stands in loose earth as Dima asked; (2) the soil cracks are a row of identical small zigzags lined up under the ground line, like stitches. Make the heap a real mound of loose earth in front of the stone's foot: at the final height about 22 to 30 percent of the visible stone height tall and about 1.6 times the stone's width, irregular silhouette with lumps and a few small stones, darker at the bottom, lit from the upper right, growing with each rise step; a few loose clumps scattered further out on the grass. Replace the zigzags with 3 to 5 irregular dark fissures of different length and angle radiating from the stone across the ground, fading at their ends, drawn on the ground behind the heap. Same at 375 px, reduced motion and noscript. Update the tests that check heap size and the cracks.
    - _Requirements: R18.1, R18.3_

- [x] 30. Photographed earth, a skeletal hand and old bones
  - Approved by Dima on 2026-11-26 (more frightening, realistic, skeletal hands, no blood or flesh because the rules let the sponsor remove "inappropriate" material). Dima generated the images with Nano Banana Pro; Claude chroma-keyed them into `src/tombstone/data/scene/` (headstone.webp replaced, heap.webp, hand_reach.webp, bones.webp, clods/clod_0..7.webp). Rollback tag `rollback-before-1127` exists.
  - _Requirements: R19.1, R19.2, R19.3, R19.4, R19.5_

  - [x] 30.1 Photo heap and clods, single embedding
    - Replace the SVG heap and clumps with the photographs per R19.1, embed every image once per R19.5 (also fix the headstone being embedded twice for the halves), keep the clip at the ground line and the layering of 29.6.
    - _Requirements: R19.1, R19.5_

  - [x] 30.2 The hand and the bones
    - The rising hand and its sinking at the split (R19.2), the bones appearing with the full heap (R19.3), moonlight toning (R19.4), the carved figure centred in the new headstone's sunken panel, reduced motion and noscript.
    - _Requirements: R19.2, R19.3, R19.4_

  - [x] 30.3 Tests
    - Each image file is embedded exactly once; no SVG heap or clump elements remain; the hand and bones are hidden at rest, the hand appears from the second step and is scheduled to sink at the split, the bones appear with the full heap; the page is under 1.1 MB with no external requests; existing R15 to R18 tests still pass (update only those the change breaks).
    - _Requirements: R19.1, R19.2, R19.3, R19.4, R19.5_

  - [x] 30.4 Real-world scale and a readable stone
    - Browser check by Claude on 2026-11-26 after task 30, at 1280x800 after a run to the 0.75 stop (stone image box 191x286 px): the front heap rises over half of the visible stone so the split headstone looks buried; the skull and bones sit on top of the stone's right half, the skull about a third of the stone's height; the carved figure is not visible at all; the stone is toned so dark it reads as a black shape. Apply R19.6 (added during task 30) with the stone image box as the unit (W = its width, H = its full height): the front heap's visible height above the ground line 0.20 to 0.25 H and its width 1.6 to 1.8 W, covering only the plinth and the lowest part of the slab; the bones group 0.65 to 0.8 W wide with the skull 0.15 to 0.2 H tall, placed on the grass to the right of the heap with no overlap of the stone; the hand's visible part 0.25 to 0.3 H tall and at most 0.45 W wide, to the left of the stone with no overlap; clods 0.04 to 0.14 W. The carved figure (R17.3) SHALL be visible on the stone's panel from the first step the panel is above the heap, and on both halves after the split. Tone the stone like the moonlit graves on the left of the photo (mid grey with a light right edge), not black. Same proportions at 390 px. Add a test that computes these ratios from the CSS values for both widths.
    - _Requirements: R19.6, R17.3, R19.4_

  - [x] 30.5 Bones to the side, a glowing figure
    - Dima's review and a browser check by Claude on 2026-11-26 after 30.4 (1280x800, after a run to the 0.75 stop): the stone is grey and the heap is low now, good; but (1) the carved figure is still not visible anywhere on the stone, at any step; (2) Dima wants the skull and bones away from the stone, in the foreground at the side among the old graves; (3) Dima wants the figure to glow, white or blue-white, not dark carved letters. Implement R19.7 and R19.8 and find why the figure is invisible (layering under the halves, clipping, opacity, colour or the 60 percent rule) and fix it. Add tests: the figure element is above the stone halves in the stacking order, visible (opacity 1, non-zero size) after the first step and after the split, and has the glow styles; the bones box does not intersect the stone, heap or hand boxes and sits in the left 30 percent of the hero at 1280 px.
    - _Requirements: R19.7, R19.8_

- [ ] 31. A night ambience behind the sound toggle
  - Approved by Dima on 2026-11-26 ("Новый мрачный"). Dima generated the ambience in Suno; Claude downloaded it from his account and re-encoded it to mono AAC 64 kbps, 13.8 s loop, 114 KB, in `src/tombstone/data/scene/ambience.m4a`.
  - _Requirements: R20.1, R20.2, R20.3_

  - [ ] 31.1 Play, loop, fade and duck
    - Wire the existing "Sound off"/"Sound on" toggle to the ambience per R20.1 and R20.2, reusing the Web Audio context of the R16.3 crack sound; embed the file once per R20.3; add the credit line.
    - _Requirements: R20.1, R20.2, R20.3_

  - [ ] 31.2 Tests
    - The audio data URI is present exactly once and is audio/mp4; no `<audio autoplay>` and no playback before the toggle; the toggle starts and stops the loop with fades and the stop ducks it; the credit line is present; the page is under 1.2 MB with no external requests; existing tests still pass (update only the size limit and those the change breaks).
    - _Requirements: R20.1, R20.2, R20.3_
