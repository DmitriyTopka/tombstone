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
