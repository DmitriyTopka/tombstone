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
