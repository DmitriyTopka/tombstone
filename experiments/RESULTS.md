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
