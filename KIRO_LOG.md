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
