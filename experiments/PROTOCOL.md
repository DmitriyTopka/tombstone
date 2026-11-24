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
