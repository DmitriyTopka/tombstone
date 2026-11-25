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
