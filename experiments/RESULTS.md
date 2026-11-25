# Results

Every run, in order. Credits are Kiro's own numbers from the session files (`tombstone cost`). Run 24 November 2026, Kiro CLI 3.5.0, model `auto`.

| Run | Group | Cap | Stopped | Credits actual (task turn) | Estimate at the end | Requests | Seconds | Tests at the end |
|---|---|---|---|---|---|---|---|---|
| pilot_c0 | control | none | - | 0.987 | - | 5 | 49 | 57 passed |
| c1 | control | none | - | 0.868 | - | 5 | 49 | 53 passed |
| b1 | breaker | 0.33 | **no** | 0.776 | 0.217 | 5 | 40 | 45 passed |
| f1 | fit | 0.33 | no (correct) | 0.122 | - | 3 | 12 | 3 passed |
| c2 | control | none | - | 0.968 | - | 6 | 52 | 54 passed |
| b2 | breaker | 0.33 | **no** | 0.877 | 0.244 | 5 | 43 | 51 passed |
| f2 | fit | 0.33 | no (correct) | 0.129 | - | 3 | 12 | 3 passed |
| c3 | control | none | - | 0.765 | - | 5 | 37 | 47 passed |
| b3 | breaker | 0.33 | **no** | 0.827 | 0.217 | 5 | 43 | 52 passed |
| f3 | fit | 0.33 | no (correct) | 0.123 | - | 3 | 13 | 3 passed |

Each breaker and fit run also had a calibration turn first (2 requests, 0.054 credits each). The series used 5.83 credits by `/usage` (284.82 to 290.65).

## What it shows
- **The breaker did not trip in 3 of 3 runs.** The task cost 0.78 to 0.88 credits against a 0.33 cap, and the live estimate stayed at 0.22 to 0.24, about 72 percent low.
- **Why:** the estimate counts requests and prices them at the average of the closed turns in the project. The only closed turn was the calibration turn, a 2-request "write hello.txt" at 0.018 credits per request. The coding task cost about 0.16 per request, nine times more. One cheap, unlike turn taught the breaker a price that was far too low.
- **No false stops:** the three small tasks under the cap ran to the end.
- This is the opposite failure of the one in Known limitations: an uncalibrated project trips early (the default price runs high); a project calibrated on a much cheaper kind of turn trips late or not at all.

## Round 2, after task 24 (live credits from `creditSplit`)

| Run | Group | Cap | STOP written | Live at the STOP | Credits actual (task turn) | Requests | Tests at the end |
|---|---|---|---|---|---|---|---|
| r2b1 | breaker | 0.33 | yes | 0.729 | 0.958 | 6 | 50 passed, 3 failed |
| r2f1 | fit | 0.33 | no (correct) | - | 0.126 | 3 | 3 passed |
| r2b2 | breaker | 0.33 | yes | 0.401 | 0.945 | 5 | no tests written |
| r2f2 | fit | 0.33 | no (correct) | - | 0.125 | 3 | 3 passed |
| r2b3 | breaker | 0.33 | yes | 0.623 | 0.825 | 5 | 48 passed |
| r2f3 | fit | 0.33 | not run (the series was cut off when the session restarted) | | | | |

The estimate at each stop was 0.09 to 0.11; the live figure was what crossed the cap.

### What round 2 shows
- **The breaker now catches the overrun in 3 of 3 runs** (round 1: 0 of 3), and still leaves the small tasks alone (2 of 2).
- **The stop lands one request late.** Kiro writes the running total after a request is finished, and on this task one request writes the whole package and costs about 0.35 to 0.55 credits. The total jumps from under the cap to 0.40 to 0.73 in that one step.
- **On a task this short, stopping saved almost nothing.** After the STOP the agent made one or two more requests to explain why it stopped, and those are expensive because the context is already large. Task turns cost 0.83 to 0.96 with the breaker against 0.77 to 0.99 without it. In r2b2 the agent stopped before writing tests, in r2b1 and r2b3 the last test run had already started.
- So this bench shows that the breaker sees the real cost and stops the next tool call. It does not show savings; that needs a task with many requests after the cap is crossed.

## Round 3, a long task (Task C, Kiro CLI 3.6.0, V3 engine)

| Run | Group | Cap | STOP written | Live at the STOP | Credits actual (task turn) | Requests | Seconds | Tests at the end |
|---|---|---|---|---|---|---|---|---|
| r3c2 | control3 | none | - | - | 4.130 | 21 | 148 | 67 passed |
| r3c3 | control3 | none | - | - | 5.834 | 42 | 167 | 55 passed |
| r3c4 | control3 | none | - | - | 5.503 | 42 | 161 | 54 passed |
| r3b1 | breaker3 | 1.00 | yes | 1.05 | 1.173 | 13 | 47 | 17 passed |
| r3b2 | breaker3 | 1.00 | yes | 1.03 | 1.286 | 14 | 53 | 9 passed |
| r3b3 | breaker3 | 1.00 | yes | 1.03 | 1.365 | 11 | 58 | 20 passed |

Each breaker3 run also had the calibration turn first (2 requests, 0.055 credits). r3c1 (V2 engine, 6.355 credits) is left out, see the amendment in PROTOCOL.md. The five runs after the pilot used 15.37 credits by `/usage` (308.80 to 324.17); round 3 as a whole, with both pilots, about 25.9 of the 40 allowed.

### What round 3 shows
- **The breaker tripped in 3 of 3 runs, and this time it saved money.** Without Tombstone the task cost 4.13 to 5.83 credits (mean 5.16). With a 1.00 cap it cost 1.17 to 1.37 (mean 1.27, or 1.33 with the calibration turn). That is about 3.8 credits, or 74 percent, less per run.
- **The live figure crossed the cap by 3 to 5 percent** (1.03 to 1.05): on this task each request is small, so the one-request lag from round 2 costs little.
- **The agent still spends after the STOP.** It made one or two more requests to explain why it stopped, and the final bill landed 17 to 37 percent over the cap. A cap is a point where the work stops, not a hard ceiling on the bill; set it with that margin in mind.
- **Stopping means the work is not finished.** The breaker runs ended with 9 to 20 passing tests against 54 to 67 for the full runs. Tombstone does not make a task cheaper, it stops a task that is going over the budget someone chose, and writes the autopsy so a person can decide whether to resume.
- Small sample: three runs per group, one task, one model setting (`auto`). The control cost varied by 41 percent between runs of the same prompt.
