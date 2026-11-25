# Experiment: the same Kiro task with and without Tombstone

Written and committed before the first run (24 November 2026). Results go in `experiments/RESULTS.md`, every run included, failed ones too.

## Question
Does Tombstone stop a Kiro CLI task near its cap, where the same task without it runs to its natural cost? And does it leave tasks that fit under the cap alone?

## Stand
- Fresh scratch git repo outside this repo for every run, Kiro CLI 3.5.0, model `auto`, `kiro-cli chat --no-interactive --trust-all-tools`, no steering file.
- Task A (the bench, a normal coding task, not a loop): build a small Markdown-to-HTML package `minimd` with at least 30 pytest tests and iterate until they pass, then a CLI and a README. Prompt text is in `experiments/run.sh` and does not change after the pilot.
- Task B (fits under the cap): add one function with three tests to a tiny repo.
- Hard ceiling in every run: 10 minutes, then `kiro-cli` is ended from outside. Same ceiling in both groups.

## Groups
- Control: Task A, Tombstone not installed in the project. Cost read afterwards from Kiro's own session file with `tombstone cost`.
- Breaker: Task A, `tombstone init`, hooks on, watcher on, task cap set before the run. One short calibration turn first in the same project, because an uncalibrated project trips early (documented in Known limitations).
- Fit: Task B with the breaker and the same cap. Counts false stops.

## Pilot
One control run first. If Task A naturally costs less than 1 credit, the cap is set to about a third of the pilot's cost, and that is written here before the series. If the agent stops on its own early, that is reported as a result.

## Series
3 control, 3 breaker, 3 fit. Reported per run: actual credits (Kiro's number), minutes, requests, stopped or not, estimate at the stop, tests passing at the end.

## Budget
Stop the series if the total passes 80 credits.

## Pilot result and the cap (written before the series)
Pilot control, 24 Nov: Task A finished on its own in 49 s, 5 requests, 0.987 credits actual, 57 tests passing. It is a normal task, not a runaway. Per the rule above the cap for the breaker and fit groups is 0.33 credits, a third of the pilot.

## Round 2 (written before it ran)
After round 1 the breaker was changed by Kiro (task 24, R4.5): it now also reads the running credit total Kiro CLI 3.5.0 writes into the session (`creditSplit`) and compares the higher of that and the estimate with the cap. Round 2 repeats the breaker and fit groups exactly as in round 1 (same script, same prompts, same cap 0.33). The round 1 control runs stand as the control.

## Round 3, a long task (written before it ran, 25 November 2026)
Round 2 showed the stop but no saving, because Task A spends most of its money in one request. Round 3 uses Task C: a todo app built one feature at a time with tests after each, so it makes many requests and there is money left to save after the cap is crossed. Groups `control3` (no Tombstone) and `breaker3` (Tombstone, same calibration turn). Ceiling 20 minutes in both. From round 3 on, Kiro runs with `--trust-tools=fs_read,fs_write,execute_bash` instead of `--trust-all-tools`. One pilot control3 run first; the breaker3 cap is set to about a quarter of its cost and written here before the breaker runs. Then 3 control3 (the pilot counts as the first) and 3 breaker3. Stop if round 3 passes 40 credits.

### Round 3 amendment (before any breaker run)
The first pilot (r3c1, 25 Nov) ran on Kiro's older V2 engine: with `--trust-tools=...` Kiro CLI starts a V2 session (`~/.kiro/sessions/cli/`), with `--trust-all-tools` a V3 one (checked with one-line prompts the same morning). Kiro CLI also updated itself to 3.6.0 overnight; its V3 sessions still carry `creditSplit`. r3c1 is kept and reported (V2, 35 requests, 6.355 credits, 171 s, 60 tests passing) but not used for the comparison. Round 3 goes back to `--trust-all-tools` so every run is V3, like rounds 1 and 2, and the pilot is repeated as r3c2.
