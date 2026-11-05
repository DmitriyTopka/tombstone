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
