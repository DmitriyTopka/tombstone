# Kiro build log

One entry per Kiro run. Credits come from `metering_usage` (V2) or `promptTurnSummaries.usage` (V3) until `tombstones.json` exists, then from `tombstones.json`. `/usage` is noted separately because it lags by 2 to 5 minutes.

Template:

```
## <YYYY-MM-DD> <stage.task> <short name>
Engine: V2 | V3      CLI: 2.37.1      Model: auto | <name>
Requests: <n>        Credits (metered): <x.xxx>      /usage before -> after: <a> -> <b>
What Kiro did on its own: ...
Where Kiro missed: ...
Gate criteria: <quote> ✅ / ❌
```

## 2026-11-03 E0.1-E0.2 Measurements (free plan, scratchpad, not this repo)

Engine: V2 and V3      CLI: 2.37.1      Model: auto

| Run | Engine | Requests | Credits (metered) |
|---|---|---|---|
| Two files with pytest tests | V2 | 2 | 0.087 |
| Seven actions, six files and README | V2 | 24 | 1.156 |
| Four files with re-reading, session polled every 2 s | V2 | 9 | 0.357 |
| Two files (E0.1) | V3 | not counted | 0.1455 |
| Hook STDIN capture, exit 2 test (E0.2) | V2 and V3 | not counted | about 0.3 |

/usage for the day: about 1.15 credits spent of 50.
Metered sum from the session files: about 2.05. The gap is not explained yet: either `/usage` had not caught up or some runs were counted differently. Status: open, recheck with `/usage` after a 5 minute wait in E1.5.

What Kiro did on its own: created the files and tests from one-line prompts, stopped by itself after a pre-tool hook returned exit 2 and did not retry.
Where Kiro missed: nothing in the tasks themselves. Surprises are in LESSONS.md (price only after the turn, V2 ignores `.kiro/hooks`).

## 2026-11-03 Gate K0 status (pre-check)
- [x] Measurement 3 (V3 session format) recorded in MEASURE_0311.md
- [x] Hook STDIN known for PreToolUse/PostToolUse/Stop on V2 and V3 (PreTaskExecution moved to stage 1)
- [x] requirements.md approved by Dima on 2026-11-03
- [x] Repo exists with MIT: github.com/DmitriyTopka/tombstone (private)
- [ ] 4,000 hackathon credits on balance: not yet, no Didit email as of 2026-11-03

## 2026-11-05 Gate K0 closed
- [x] 4,000 hackathon credits on balance: `/usage` shows `kiroween-2026: 0.00/4000 credits used, expires in 56 days` (checked 2026-11-05 by Claude in this repo, and by the control session on app.kiro.dev).
- [x] Kiro CLI updated to 3.0.0; V3 is the default engine. Measurement 5 (MEASURE_0311.md in the planning folder): V3 session path unchanged, turn cost in `promptTurnSummaries[].usage`, `.kiro/hooks/*.json` fire, `exit 2` in PreToolUse blocks. `PreTaskExecution` did not fire in headless chat.
- requirements.md updated to V3-first (intro, glossary, R1 acceptance check, R2.3 task id from the `tasks.md` checkbox edit, R10 title). No requirement added or removed.

## 2026-11-05 E1.1 + E1.2 Skeleton, design.md, tasks.md
Engine: V3      CLI: 3.0.0      Model: claude-sonnet-5.5 (spec generation only)
Requests: 13        Credits (metered): 9.655      Time: 6 min 41 s
What Kiro did on its own: package skeleton, design.md (505 lines, every module, data formats, error table, open points), tasks.md with 36 tasks, every requirement id cited; found one uncited id (R11.2) itself and added it.
Where Kiro missed: ran one read-only `git status` against the prompt rule and said so itself. Asked a real question: the steering's subprocess list was too narrow for kill and init. Resolved by Claude in steering (adds `kiro-cli --version`, `ps`, `lsof`, `launchctl`/`systemctl` for init only).
Gate criteria: "Three files in .kiro/specs/tombstone/, each task cites a requirement" ✅

## 2026-11-05 Spec tasks 1.1, 1.2, 1.3, 2
Engine: V3      CLI: 3.0.0      Model: auto

| Task | What | Requests | Credits (metered) | Tests after |
|---|---|---|---|---|
| 1.1 | V3 parser, scrubbed fixture, 5 edge fixtures | 28 | 5.240 | 10 |
| 1.2 | V2 parser, real V2 fixture from measurement 2 | 26 | 4.697 | 18 |
| 1.3 | Session discovery, open-turn progress | 18 | 4.287 | 37 |
| 2 | `tombstone cost` CLI | 19 | 4.093 | 56 |

`tombstone cost` prints `0.390 credits (actual), 5 requests` on the measurement 5 session and 1.156 / 24 on the measurement 2 V2 session, matching the hand counts.
What Kiro did on its own: found the old V2 session still on disk and used it instead of a hand-made fixture; scrubbed home paths and message text and wrote a test that checks the scrub.
Where Kiro missed: nothing found in review.

Cross-check (K1 criterion 1): metered sum of all V3 sessions since the update is 28.537 credits; `/usage` shows 29.04. The gap of about 0.5 is the three headless `/spec` and `/tasks` probes, which wrote no session file (see LESSONS). Within 1 credit ✅

## 2026-11-05 Spec tasks 3.1, 3.2, 3.3, 4.1 and the first live bug fix
Engine: V3      CLI: 3.0.0      Model: auto

| Run | What | Requests | Credits (metered) | Tests after |
|---|---|---|---|---|
| 3.1 | Locked JSON store | 15 | 3.041 | 69 |
| 3.2 | tasks.md parsing, task id resolution, post-tool hook | 26 | 5.751 | 91 |
| 3.3 | Record builder, Stop hook | 27 | 6.613 | 112 |
| 4.1 | Config, task type, similar tasks (first run with Tombstone hooks live in this repo) | 13 | 3.209 | 131 |
| fix | Two bugs found on the 4.1 live run | 52 | 15.150 | 139 |

Dogfood: `.kiro/hooks/tombstone-post-tool.json` and `tombstone-stop.json` run `.venv/bin/tombstone hook ...` in this repo from task 4.1 on.
Bugs the live run found (Claude read the session file, Kiro fixed):
1. The V3 `Stop` hook fires one event before `usage_summary`, so the turn cost is not on disk when the hook runs. No record was written. Fix: record now with `actual = null`, a detached `tombstone hook reconcile` child fills the cost within 30 s. Verified on the fix run itself: the record got `actual 15.1502`, 52 requests.
2. With no earlier snapshot, every task already marked done counted as checked in this turn. Fix: baseline from the edit's `oldStr`/`newStr`, `turn.checked` reset on a new turn.
Known wrong record: the fix run touched no checkbox, so R2.3 fell back to "first unchecked task" and filed it under task `1` (a parent whose subtasks are all done). Proposed spec change, waiting for Dima: a turn that checks no box and has no task id on STDIN goes to `unknown` with the first words of the prompt as its name.
Estimate vs actual: est. 4.545, actual 15.150. The default average request price (0.045, measured on 2.37.1) is far too low on 3.0.0: this stage averaged 0.23 credits per request on auto. Calibration (E1.6) is next.

Stage E1 spend so far: 61.7 credits of the 120 budget (all metered sums above).

## 2026-11-05 R2.3 change, three live bugs, spec tasks 4.2 and 5
Engine: V3      CLI: 3.0.0      Model: auto. Credits below are the hook-written `actual` values from tombstones.json.

| Run | Tombstone record | Requests | Credits (actual) |
|---|---|---|---|
| R2.3 change (approved by Dima): `unknown` + prompt name, no "first unchecked" guess, task 3.4 | unknown "hi there" (see below) | 12 | 3.863 |
| Fix: reconcile child died with kiro-cli; added a safety net in every hook | unknown "Bug fix: the detached reconcile child does not" | 12 | 4.336 |
| Task 4.2 gate and pre-tool hook | 4.2 and 3.4, split 50/50 (bug A) | 14 | 2.429 + 2.429 |
| Fix: bug A (new ids counted as checked), bug B (reconcile filled one share only) | unknown "Bug fix, two more bugs from the live" | 12 | 2.711 |
| Task 5 graveyard | 5 "Graveyard", share 1.0 | 15 | 5.055 |

Live bugs found by running Tombstone on its own build (each one read from the session file by Claude, fixed by Kiro, with a test):
1. The detached reconcile child did not survive `kiro-cli` exit: actual stayed null. Fix: full detach plus `reconcile.log`, and a safety net that fills null records at every hook. Seen working: "filled turn ... actual 4.3359 requests 12" 5 ms after Stop.
2. Bug A: task 3.4 was added already checked, so the next turn counted it as done and split the 4.2 turn in half.
3. Bug B: reconcile filled only one of the two split records.
Known noise in the data: the record named "hi there" got its name from Kiro running `tombstone hook prompt` by hand against the real state. Steering now forbids that (Dogfood rules). Records stay as written; none edited by hand.

Gate K1 (2026-11-05):
- ✅ Price of one spec task matches in two sources: task 5 record actual 5.0545 = `tombstone cost` 5.054 for the same session; all session sums vs `/usage` within 1 credit.
- ✅ tombstones.json has 7 records, all written by hooks.
- ⚠️ Task id: task 5 correct with full share; three work turns correctly `unknown` with prompt names; task 4.2 got the right id but a wrong 50/50 split (bug A, fixed after). Re-check on the next three tasks in E2.
- ✅ Commits after Kiro code, this log filled per run.
- Budget: E1 spent about 83 of 120 credits (`/usage` 83.69 including measurement 5), under budget.
Graveyard v1 renders from the real file: 7 tombstones, 35.971 credits actual.

## 2026-11-06 Watcher, stop, kill (spec tasks 6.1-7.4) and the first live stop tests
Decisions by Dima on 06.11: watcher and stop before README; 10 to 12 hours of graveyard design after the watcher; repo stays private until about 01.12; no second Kiroween entry.

| Run | Tombstone record | Requests | Credits (actual) |
|---|---|---|---|
| 6.1 State and change detection | 6.1 | 17 | 4.214 |
| 6.2 Evaluate and poll loop | unknown (box flipped by `sed`, bug) | 18 | 7.372 |
| 7.1 STOP file, blocking, resume | 6.2 + 7.1 split (same bug) | 11 | 2.524 |
| 7.2 Notification | 7.2 | 9 | 1.427 |
| Fix: Stop hook diffs tasks.md (catches shell edits) | unknown | 12 | 2.482 |
| 7.3 Process lookup and kill (built to the measured process tree in LESSONS) | 7.3 | 17 | 4.653 |
| 7.4 Stop sequence | 7.4 | 18 | 5.444 |
| Fix: watcher never found the session (index paths are relative to ~/.kiro/sessions), unknown toml keys now warn | - | - | about 4 |
Task id attribution after the shell-edit fix: 7.2, 7.3, 7.4 each correct with share 1.0 (closes the K1 caveat).

Live stop tests in scratch projects (hooks + `tombstone watch`, logs outside the project):

| Test | Cap | What happened | Credits |
|---|---|---|---|
| burn1, loop prompt | 3 | Agent looped 420 s, 439 tool calls, 102 model events. Tombstone never fired: the watcher could not find the session (bug above). External guard killed it. | about 22 (from `/usage`; the killed turn has no cost in the session file) |
| burn2, loop prompt | 3 | Watcher raised over-cap at est. 3.50 after 41 s, STOP written at 43 s. The agent had read tombstone.toml and the test logs in the project and stopped on its own at 48 s, before hitting the block. Detector proven, block not exercised. | 1.775 |
| burn3, loop prompt, logs outside, `.kiroignore` | 3 | Agent ignored `.kiroignore`, decided the audit was done and answered in 34 s. No stop needed. | 0.971 |
| burn4, "80 iterations" prompt | 3 | Agent read each file once and answered in 19 s. | 0.423 |
| burn5, normal 12-step task | 0.3 | STOP at 9 s (est. 0.50 of 0.30). The next tool (`wc -w`) was refused by the pre-tool hook; the agent stopped and told the user: "A PreToolUse hook blocked the `wc -w` command and said this turn was over budget". Record outcome `killed`. Kill step not needed. | 0.334 |

What this proves: detector, STOP file, pre-tool block and the agent's reaction work end to end on CLI 3.0.0 (burn5). Real numbers for the video: one unguarded loop burned about 22 credits in 7 minutes (burn1); the guarded run stopped at 0.33.
Not proven yet: the kill step on a live process (the agent always stopped itself after the block), the stall path (quiet project) on a live run, the autopsy (task 8).
Spend: `/usage` 142.80 of 4000 on 2026-11-06.

## 2026-11-06 Autopsy, MCP, init and Power (spec tasks 8.1-10.3) and the first end-to-end live run
Engine: V3      CLI: 3.0.0, auto-updated to 3.1.0 during the day      Model: auto
All task records below were written by the hooks with share 1.0 and the right task id.

| Run | Requests | Credits (actual) |
|---|---|---|
| 8.1 Autopsy | 14 | 3.547 |
| 8.2 Append actual | 21 | 4.289 |
| 9.1 Usage probe and cache | 11 | 2.460 |
| 9.2 get_budget and stdio server | 13 | 3.005 |
| 10.1 Hook and config builders | 11 | 2.211 |
| 10.2 init and watcher units (`--no-watcher-unit` added) | 9 | 1.974 |
| 10.3 Power folder and CLI version check | 18 | 4.668 |
| Fix: hooks used a bare `tombstone`, MCP not registered, V2 agent profile hijacked V3 | 15 | 5.326 |
| Fix: stdlib MCP server, zero runtime dependencies | 14 | 2.859 |

Live runs of `tombstone init --no-watcher-unit` in fresh scratch projects, cap lowered for the test:
1. live1: every hook failed with `tombstone: command not found` (bare name, venv not on PATH). The watcher still wrote STOP and an autopsy, but nothing blocked the agent. Kiro 3.1.0 also loaded `.kiro/agents/tombstone.json` as the active profile (`profile.loaded {"agentId":"tombstone"}` in kiro.log), which hid the MCP tool.
2. live2: hooks fixed. The agent could not see get_budget: the first prompt left at 09:28:30.968, the MCP server finished connecting at 09:28:32.708 (1.8 s, mostly importing the `mcp` SDK). The agent read tombstone.toml itself, saw the 0.3 cap and paused to ask the user. Steering working as intended.
3. live3: stdlib MCP server answers `initialize` in about 45 ms. Full chain worked: the agent called `@tombstone/get_budget`, the watcher raised over-cap (est. 1.00 of 0.60), the next `Write File` was refused, the agent quoted `TOMBSTONE: this turn over budget (est. 1.00 of 0.60). Stop.` and stopped without retrying. Autopsy written with refusal text, plan balance at stop 3874.15 (live `/usage` parse), actual 0.536 appended after the turn.
Open: estimate on a fresh project is high (1.00 vs 0.536 actual) until the per-unit price calibrates; kill step still not exercised live (the agent always stops after the block); stall path not run live; launchd unit not loaded live (needs Dima's OK, it changes his login items).
Spend: `/usage` 174.70 of 4000 on 2026-11-06.

## 2026-11-06 Live check of the launchd watcher (Dima's OK: "install and remove")
- `tombstone init` in a fresh scratch project wrote `~/Library/LaunchAgents/com.tombstone.live4-2a89b377.plist` and loaded it: `launchctl list` showed `25562 0 com.tombstone.live4-2a89b377`.
- Same test task with cap 0.6: the launchd watcher wrote STOP (`est. 1.00 of 0.60`), the next write was refused, the agent stopped and wrote an autopsy. No manual watcher process was running.
- Removed right after: `launchctl bootout gui/501/com.tombstone.live4-2a89b377` (exit 0), plist deleted, `launchctl list` and `~/Library/LaunchAgents` show no tombstone entry, no `tombstone watch` process left.
- Gap found: there is no `tombstone uninstall`; removal took two manual commands. Candidate task for E4.

## 2026-11-06 tombstone uninstall (R9.6-R9.8, task 10.4), approved by Dima
Kiro: 9 requests, 2.984 credits (actual). The record went to `unknown`: task 10.4 was added to tasks.md already checked, and by the bug A rule a new id is never "checked now". Expected, not a bug.
Live check in a fresh scratch project with an existing `.kiro/settings/mcp.json` holding another server: `init` loaded the launchd unit (1 unit, 1 plist); the user edited `tombstone.toml`; `uninstall` booted out and removed the unit, removed the 7 hook files and the steering file, removed only the `tombstone` entry from mcp.json (the `other` server kept), kept `tombstone.toml` and `tombstones.json` as user data. Second `uninstall` reported "not found" for each file and changed nothing. After: 0 units in `launchctl list`, 0 plists, 0 watcher processes. 420 tests.

## 2026-11-06 Task 11 (tests), 11.1-11.4
Kiro: 11.1 15 requests 3.192 credits, 11.2 8 requests 1.290, 11.3 5 requests 0.631 (tests already there, only the tick), 11.4 three runs: 1.056 (cut off silently), 1.251 (ModelThrottleError "Auto is experiencing high traffic"), 3.427 (done, 500 tests). Then the calibration fix below, 506 tests.
Bug found by 11.4: in hooks-only mode (no watcher service) the V3 open-turn estimate was never calibrated, `avg_unit_price` stayed null and the estimate fell back to units x 0.25, while V3 units are about 2x requests. Recorded estimates were 1.4x to 3.7x the actual (11.1: 7.98 against 3.19). Fix: `fold_priced_turn` in tombstones.py calibrates on the Stop hook, the reconcile child and the safety net too, deduped with the watcher on `unit_turns`.
Check by hand on 33 real V3 turns from tombstones.json, each estimated from the turns before it: median error 26 percent (was about 65), 18 of 33 within 30 percent. Spread remains because the price of one unit grows with context size.
Manual "no extra metered request" check (R11.5): `/usage` read 222.86/4000 before; then 5 more `kiro-cli chat --no-interactive /usage` probes, `tombstone hook pre-tool`, `tombstone cost`, and an MCP `get_budget` call; `/usage` read 222.86/4000 right after and again one minute later. No new session file appeared. The `/usage` probe and Tombstone itself spend nothing.

## 2026-11-06 Task 12, graveyard v2 (design approved by Dima)
Claude drew the mockup on real data (`.kiro/specs/tombstone/graveyard_mockup.html`), added R2.7 and task 12. Kiro: 12.1 10 requests 4.018 credits (port into `render_graveyard`), 12.2 8 requests 1.132 (tests), copy fix for the empty states 5 requests 0.787. 519 tests. Checked in a browser at 1280 px on this repo and on a stopped-only project (live3).
Bug found while reading the result: the 12.1 turn was split over 11 task ids, 0.365 each. Cause: the tasks.md snapshot was refreshed only on post-tool and Stop, so parent boxes the human ticked by hand between turns were counted for the next agent turn. Fix (8 requests, 1.488): the prompt and session-start hooks re-baseline every spec's snapshot without counting. The three new tests fail on the old hook code and pass on the new one. The 11 wrong shares stay in tombstones.json until Dima decides.
`/usage` after: 229.51/4000.
Hand correction of tombstones.json, approved by Dima on 2026-11-06: turn 354d44ec (the 12.1 run, 4.0176 credits, 10 requests) is given back to task 12.1 with share 1.0. The nine records that held only this turn (3, 4, 6, 7, 8, 9, 10, 11, 11.4) are removed, and the share is dropped from task 1, which is back to 15.1502 credits and 52 requests. Done through `update_json` under the file lock, with a backup kept outside the repo. This is the only hand edit of tombstones.json in the build.

## 2026-11-06 Live stall and kill checks
Stall (scratch project, cap 25, quiet_window_minutes 0.4, watcher run by hand): the agent ran read-only shell steps. At 17 units the watcher wrote STOP with reason `stall`, the next `wc -w` was refused, the agent quoted the message and stopped at step 9. 33 s, actual 0.927 credits. Autopsy written, actual appended after the turn.
Two fixes from that run (Kiro, 526 tests): the uncalibrated V3 price per unit is now 0.16, measured on 35 turns of this repo (855 units, 135.91 credits); before it fell back to 0.25 and the live estimate was 4.25 against 0.927. The stall message showed "0 min" for a window under a minute; minutes now keep one decimal under 10.
Kill (cap 0.30, kill_grace_seconds 10), prompt told the agent to keep going after any refusal: with the steering file the agent refused to work around the block and cited the steering rule. With the steering file removed it still stopped at the first refusal (est. 0.33 of 0.30, actual 0.498). The kill step never had to fire. Across burn5, live3, live6 and live7 the agent on `auto` always stopped at the first refused tool. The kill step is covered by tests with a fake process list only.
`/usage` after: 234.98/4000.

## 2026-11-06 Stage 1 checklist and changelog re-read (plan items 5.4, 5.5)
Checklist, checked by Claude on the local repo, no Kiro run, 0 credits:
- License: MIT in `LICENSE`, GitHub detects it as MIT. Pass.
- `.kiro/` at the repo root and tracked: specs (requirements, design, tasks), steering, three hooks. Only `.kiro/tombstone/` runtime state is ignored. Pass.
- English: no Cyrillic in tracked files except one test path (`tests/test_cli_builders.py`), which tests a project folder with a non-ASCII name on purpose. No en or em dashes, no user paths, no keys. Pass.
- Demo link alive: not yet. The repo is private, so the URL in the README and the video works only after it is opened. Open.
Changelog re-read (kiro.dev/changelog, entries 28 Oct to 5 Nov 2026): no per-task or per-spec credit cap, no budget warning, no automatic stop of a runaway turn. Closest are credit pooling (4 Nov, limits per user, group and profile) and review loops pausing after two rejected passes (28 Oct). Issues #10999, #11477, #7485 and #10848 are still open. The pitch stays as it is.
The demo project used for the video recording was uninstalled with `tombstone uninstall`; its launchd watcher is gone.

## 2026-11-07 Task 13, graveyard v3 (design approved by Dima)
Claude restyled the real `tombstone graveyard` output into a mockup (`.kiro/specs/tombstone/graveyard_v3_mockup.html`): a night scene with inline SVG hills, the total as the headline, a strip of computed figures, moonlit granite, unmarked graves as low slabs, red only on stopped records. Added R2.8 and task 13. Kiro: 13.1 16 requests 4.221 credits (stylesheet and header ported, figures computed from the records), 13.2 7 requests 0.924 (one missing test, the rest came with 13.1). 530 tests.
Checked by hand: the rendered `<style>` equals the mockup's byte for byte; on this repo the headline is the sum of the stones (159.15 before 13.2 landed); with six stopped records from the test projects the strip shows "6 stopped by the breaker" in red; an empty project reads "Nothing is buried here yet." with no strip. Mockup checked at 1920, 1440 and 375 px (no horizontal scroll, headstone text 14 px or more).
`/usage` after: 244.17/4000.

## 2026-11-07 Task 14, live stops on the graveyard (approved by Dima)
The repo's own build never tripped the breaker, so its graveyard had no stopped stones. Claude collected every stopped record from the live test projects (burn2, burn5, live1 to live7) and the video recording into `live_stops.json`, unchanged except for a `source` field: 15 turns, 10.555 credits. Added R2.9 and task 14. Kiro: 14.1 `--include FILE` for `tombstone graveyard` (merge by id, project wins, plain error and exit 1 on a missing or bad file), a note and kicker when included stops are shown, and "stopped" instead of "killed" in the card and the register, since the process kill never fired. 14.2 one more test. 536 tests.
Checked by hand: with `--include live_stops.json` the page shows 61 stones, "15 stopped by the breaker" in red, the note names the file; a missing include exits 1 and writes nothing; no visible "killed" left (only a CSS class name).
`/usage` after: 248.55/4000.

## 2026-11-07 Task 15, graveyard v4 (Dima's review of v3)
Dima: the stones stood too close, all looked alike, and the glow over stopped stones read as candles. Claude made a v4 mockup on the real page (`.kiro/specs/tombstone/graveyard_v4_mockup.html`, changes in `graveyard_v4_changes.py`): five headstone shapes (arch, slab, cross, gothic, shoulder), four crack patterns on stopped stones, no ghost, stopped stones in lines of at most 8 with about one stone of space between them. Added R2.10 and task 15. Kiro: 15.1 port, 15.2 tests, 15.3 a short cross no longer falls back to the same shape as a neighbour, and the note reads right for one included stop. Kiro also fixed the mockup's flaw where the clip-path cut the red base glow on non-arch shapes. 550 tests.
Checked by hand on this repo with `live_stops.json`: 64 stones, no two neighbours share a shape, 15 stopped in lines of 8 and 7, the glow is the same on every shape.
`/usage` after: 254.99/4000.

## 2026-11-09 Tasks 16 and 17, /.kiro fully tracked (win-protocol review)
A review of the submission against the Kiroween rules found `.kiro/tombstone/` in `.gitignore`. The rules say "Do NOT add the /.kiro directory or sub-folders to your .gitignore". Added Requirement 12 and tasks 16 and 17.
Task 16 (Kiro): runtime state moved to `.tombstone/` behind one constant in `sidecar.py`, a one-time guarded migration of an old `.kiro/tombstone/`, `init` advises ignoring `.tombstone/`. 10 new tests, 560 in all. The tests moved this repo's own sidecar through the migration code.
Task 17 (Kiro): this repo now runs all seven V3 hooks from `.kiro/hooks/` (the brake included) and the MCP server from `.kiro/settings/mcp.json`, with `spec = 600` in `tombstone.toml`. Kiro was denied writing `.kiro/settings/`, so a human created `mcp.json` with the line Kiro proposed. A test checks the hook set, the MCP entry and that `git check-ignore` matches nothing under `.kiro/`. 563 tests.
The 17.1 test turn itself ran with the `PreToolUse` brake armed and was not blocked. `get_budget` over stdio from this repo: spec spent 181.01 of 600.
`/usage` after: 266.08/4000.

## 2026-11-20 Task 18, the crack opens the receipt (approved by Dima)
A cracked stone now opens the stop receipt in its card: STOP reason, the refusal text the agent read, the estimate at the stop, whether the kill ran, and the last tool calls. Added R2.11 and task 18. Kiro: 18.1 autopsy parser and matching by turn id or by task and date, `--autopsies DIR` on `tombstone graveyard`; 18.2 the receipt block in the card; 18.3 tests on a real autopsy. 572 tests.
Checked by hand in Chrome: no card opened at all. The new code wrote `"\n"` into the page script as a real line break, so the whole inline script failed to parse, and the tests only looked at the HTML. Added 18.4: Kiro fixed the escaping and added a test that parses the inline script (with node when it is on PATH). 574 tests. The card then showed the receipt at 1440 and 500 px.
Only one autopsy from the live tests survives (the video recording run); the scratch projects of the earlier runs are gone, so 14 of the 15 cracked stones say "No receipt was kept for this stop."
`/usage` after: 271.59/4000.

## 2026-11-23 Tasks 19 and 20, CI and the first live kill (approved by Dima)
Task 19 (Kiro): `.github/workflows/tests.yml` runs the suite on Ubuntu and macOS with Python 3.11 and 3.12; badge under the README title. Kiro checked the suite in a fresh venv with an empty HOME first; no test needed changes. The first run on GitHub passed on all four jobs.
Live kill (run by Claude in a scratch project, Kiro CLI 3.5.0, cap 0.30, `kill_grace_seconds` 10 then 5, the `PreToolUse` hook removed on purpose so only the watcher could act, the looping audit prompt from the burn tests). Run 1: STOP at 08:54:18Z, the watcher ended the kiro-cli tree 12 s later (3 processes, SIGTERM, exit 143). The receipt still said "kill: no", because the autopsy was written at STOP and never updated. Run 2: the agent ended its own turn before the grace ran out, no kill. Task 20 (Kiro) fixed the receipt: after `maybe_kill` the step two line is rewritten with the outcome. Run 3: STOP at 08:58:39Z, kill at 08:58:46Z, receipt "Step two (kill): yes (ran at 2026-11-23T08:58:46Z; 3 processes terminated, 0 killed)". Kiro writes no price for a killed turn, so the record keeps the estimate (0.32). Run 3 is now in `live_stops.json` (source `kill-live-2026-11-23`) with its autopsy in `live_autopsies/`; a test that pinned 15 stopped records now counts them from the file (Kiro). 579 tests.
`/usage` after: 274.28/4000 (CI task, task 20, three kill runs and the test fix together: 2.69).

## 2026-11-24 Ten more live stops and a calibrated one (approved by Dima)
Run by Claude with `draft/run_stops.sh` in scratch projects outside the repo (Kiro CLI 3.5.0, hooks on, watcher on). Eight runs with task caps 0.20 to 0.40: all eight were refused by the pre-tool hook after 4 requests and stopped, 11 to 18 s each, actual 0.160 to 0.187. In every one the actual stayed under the cap: a fresh project has no price data, so the estimate uses the default 0.16 per unit and runs 2 to 5 times high. Two looping-audit runs (cap 5, quiet window 0.4 min) did not stall: the agent did one pass and then refused to loop on its own. Then one calibrated project: a first turn with no tight cap (0.343 actual, unit price learned 0.038), then a turn with cap 0.10: STOP at est. 0.114, actual 0.126, the agent quoted the refusal and said it would not work around a hook from the user's own `.kiro/hooks`. Nine records with their receipts are in `live_stops.json` and `live_autopsies/` (25 stopped in all). Cost of all eleven turns: 2.18 credits actual.

## 2026-11-24 Task 21, a first screen a newcomer understands (approved by Dima)
From the judges' note (simplicity, clean UI, clear UX). Added R2.12 and task 21. Kiro: 21.1 tagline, three figures instead of five, legend, click hint, `--video` and `--repo` links, "Live stop N" with the stop reason instead of "no task" and the cut-off prompt on stopped stones, "task" in visible copy; 21.2 tests in `tests/test_graveyard_first_screen.py`. 588 tests. 4.166 credits actual for the task.
Checked by hand in the browser: the first screen reads tagline, total, three figures, legend and hint, two links; Live stop 1 to 25 with "over its cap" on the stops that have a receipt; the card of Live stop 25 shows actual 0.126, estimate 0.114, "9 percent below", the refusal text. Unmarked finished tasks of this repo still read "no task" (outside R2.12, they are not stopped stones).

## 2026-11-24 Task 22, `tombstone demo` (approved by Dima)
For a judge with no paid Kiro plan. Added Requirement 14 and task 22. Kiro: 22.1 the scrubbed V3 session from `tests/fixtures/v3_measurement5/` packaged under `src/tombstone/data/demo_session/`, `src/tombstone/demo.py` and the `demo` subcommand, reusing `watcher.evaluate`, `stop.begin_stop` and the real pre-tool hook function; 22.2 `tests/test_demo.py`. 594 tests, green on the first run.
Checked by hand: `tombstone demo --dir <tmp>` trips the 0.30 cap at request 2 (est. 0.32), writes STOP and a receipt, the hook answers exit code 2 with the refusal text, 0.12 s. A clean `pip install` of the repo into a fresh Python 3.12 venv with an empty `HOME` runs the same demo and leaves `HOME` empty.
Cost: 2.533 credits actual (14 requests), filled by the watcher after the turn.

## 2026-11-24 Task 23, a lede that matches the page (approved by Dima)
A cold reader saw "Each stone is a spec task Kiro finished" next to 22 stones filed as unknown. Added R2.13 and task 23. Kiro: 23.1 new lede text and the two tests that quoted the old one. 594 tests.
Cost: 0.197 credits actual (8 requests); the turn ticked 23 and 23.1, so it is filed under both, as with tasks 20 to 22. The build graveyard is now 75 stones, 195.83 credits; the page 100 stones, 208.20; `/usage` 283.83.
