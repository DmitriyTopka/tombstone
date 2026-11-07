# Tombstone

Tombstone is a circuit breaker for Kiro specs. You give every spec task a credit cap. When a running task goes past it, Tombstone blocks the agent's next tool call with a plain message, and if the agent keeps going, it ends the `kiro-cli` process for that project. Every finished task gets a tombstone with its real price, and every stopped one leaves a receipt. The breaker itself spends zero credits and never changes your model.

> A user put "HARD STOP: 40 credits OR 40 minutes" in the prompt. The agent ran for 2 h 03 min. ([kirodotdev/Kiro#10999](https://github.com/kirodotdev/Kiro/issues/10999))
>
> A prompt is not a breaker.

Built for Kiroween 2026, Costume Contest. MIT. Tested on Kiro CLI 3.0.0 and 3.1.0 (V3 engine), macOS, Python 3.11+. No dependencies outside the standard library.

## Try it in 60 seconds

You don't need Kiro or credits for this part. You need Python 3.11 or newer; the `python3` that ships with macOS is often older, so the commands use [uv](https://docs.astral.sh/uv/). The repo carries the 42 real tombstones from its own build and a real V3 session.

```bash
git clone https://github.com/DmitriyTopka/tombstone && cd tombstone
uv venv --python 3.12 && uv pip install -e .
# no uv? any Python 3.11+ works: python3.12 -m venv .venv && .venv/bin/pip install -e .

# price a real Kiro V3 session from the fixtures
.venv/bin/tombstone cost tests/fixtures/v3_measurement5/sess_8b5bdb0f-3662-4dca-be63-268a0365f3d9
#   v3  auto  5 requests (actual)  0.390 credits (actual)  ...

# draw the graveyard of this repo's own build
.venv/bin/tombstone graveyard --out graveyard.html && open graveyard.html
```

## What it does

**Prices every task.** Kiro CLI already writes session files to your disk. Tombstone reads them and turns them into a cost per turn, per spec task and per spec. Nothing is sent anywhere.

**Caps a task and a spec.** `tombstone.toml` holds the limits, 40 credits per task and 300 per spec by default. If the spec has less left than similar tasks usually cost, the next task does not start.

**Stops a runaway turn in two steps.** First a `STOP` file makes every tool call fail, and the agent sees why:

```
TOMBSTONE: this turn over budget (est. 1.00 of 0.60). Stop.
```

In our live runs the agent quoted that line and stopped on its own. If it doesn't, the watcher ends the `kiro-cli` process for this project after a grace period. Your files are never touched.

**Catches a stall.** If the agent keeps calling the model while no project file changes for 10 minutes, that trips the breaker too. In a live test with a short window, the stall stop came 9 steps into a read-only run, and the agent stopped there.

**Leaves a receipt.** `.kiro/tombstone/autopsy/*.md` lists the task, the model, the estimate at the stop, the refusal text, the last 10 tool calls and, once Kiro reports it, the actual cost. A real one from our test run is shown below in "How it works".

**Tells the agent its budget.** One MCP tool, `get_budget`, returns what a similar task cost, what is left in the spec and what is left in your plan.

## Install

```bash
uv tool install git+https://github.com/DmitriyTopka/tombstone   # or pip install with Python 3.11+
cd your-project
tombstone init
kiro-cli chat
```

`tombstone init` writes `tombstone.toml`, seven command hooks in `.kiro/hooks/`, `.kiro/steering/tombstone.md`, the `tombstone` entry in `.kiro/settings/mcp.json`, and a launchd or systemd unit for the watcher. Every hook calls the absolute path of the Python you installed into, so a venv that is not on `PATH` still works. Files that already exist are left alone. No tokens, servers or API keys.

`tombstone uninstall` removes all of that and keeps your `tombstone.toml` and `tombstones.json`. `tombstone resume` removes a `STOP` file when you decide to let the agent go on.

The `power/` folder carries the same pieces as a Kiro Power (`plugin.json`, `skills/tombstone/SKILL.md`, `mcp.json`, hook templates). Hooks are not a documented Power component yet, so the Power alone does not enable the cap. Run `tombstone init` in the project.

## How it works

1. **Source.** Kiro CLI V3 writes `~/.kiro/sessions/<hash>/sess_<id>/messages.jsonl`. The exact price of a turn arrives in one `usage_summary` event when the turn ends. Reading it is free.
2. **Estimate while the turn runs.** Kiro gives no price during a turn, so the watcher counts assistant events and multiplies by a price per event calibrated from this project's own closed turns. On our 33 real turns, each estimated only from the turns before it, the median error was 26 percent.
3. **Reconcile.** After the turn the actual price lands in the tombstone next to the estimate. The `Stop` hook fires before Kiro writes the price, so a small background step fills it in a few seconds later.
4. **Which task.** A turn belongs to the task whose checkbox it ticked in `tasks.md`. A turn that ticks nothing goes to `unknown`, named by the first eight words of its prompt. Tombstone does not guess.
5. **Breaker.** A cap breach or a stall writes `.kiro/tombstone/STOP`. The `PreToolUse` hook sees it and exits with code 2, so Kiro refuses the tool and passes the message to the agent.

A receipt from a live run (cap 0.60 credits, the agent was asked to work past it):

```
- Estimate at stop (est.): 1.000 credits
- STOP reason: over_cap
- Refusal text: TOMBSTONE: this turn over budget (est. 1.00 of 0.60). Stop.
- Plan balance at stop (est.): 3874.150 credits
- Actual (actual): 0.536 credits
```

## Known limitations

- The exact price is known only after the turn. The breaker works on an estimate, and both numbers are kept so you can see the gap.
- A turn that Kiro aborts has no price in the session file at all. It shows only in `/usage`.
- The process kill has only been tested with a fake process list. In every live run the agent stopped at the first refused tool, even when the prompt told it to keep going and the steering file was removed, so the kill never had to fire.
- The Kiro IDE stores its data elsewhere. It is not a source yet.
- Installing from GitHub (`uv tool install git+...`) and installing the Power by URL can be checked only once the repo is public.

## How we built it

Every line of product code was written by Kiro CLI from the spec in `.kiro/specs/tombstone/`: `requirements.md` in EARS notation, `design.md` and `tasks.md` generated by Kiro from it. A human picked the next task, read the diff and ran the checks. Each task was run with `kiro-cli chat --no-interactive` and priced by Tombstone itself, with its own hooks installed in this repo.

The build log with the credits per task is in [KIRO_LOG.md](KIRO_LOG.md). The 42 tombstones in [tombstones.json](tombstones.json) add up to 154.93 credits of priced turns: 22 spec tasks and 20 turns that ticked no checkbox, filed as unknown. The whole build, measurements, test runs and the video recording included, used 239.94 credits by `/usage`. 526 tests run in about nine seconds and need no Kiro install.

## How we used Kiro

- **CLI.** Kiro CLI 3.0.0, later 3.1.0, V3 engine, model `auto`.
- **Spec-driven development.** Twelve top-level tasks with subtasks, all done. When the CLI moved to 3.0.0 in the middle of the build, we rewrote the requirements for V3 and Kiro regenerated the design and tasks.
- **Hooks.** Shell command hooks only, no agent hooks, so the breaker costs nothing: `PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit`, `SessionStart` and the two task hooks.
- **Steering.** `project.md` holds the stack and the dogfood rules. The `tombstone.md` file that `init` writes asks the agent to call `get_budget` before a task and to stop and ask a human when the budget is short.
- **MCP.** `get_budget`, a stdio server on the standard library. It answers `initialize` in about 45 ms.
- **Powers.** `power/` packages the skill, the MCP server and the hook templates.

## Inspiration

Kiro bills in credits, and the team has said there is no external circuit breaker ([#11477](https://github.com/kirodotdev/Kiro/issues/11477)). People report spec tasks costing from 2.6 to 45 credits, and one stuck task passing 820 ([#7485](https://github.com/kirodotdev/Kiro/issues/7485)). We had a fixed allowance for this hackathon and no way to buy more. In our own first test, before the watcher could find V3 sessions, one unguarded loop burned about 22 credits in 7 minutes.

## Challenges

Kiro CLI 3.0.0 shipped two days into the build and moved session files, hooks and the price format. Most of the plan had been written for 2.37.1.

The price comes after the turn, and the `Stop` hook fires before the price is written. We had to reconcile in the background and keep the estimate honest.

Pinning a turn to a task without guessing took three rounds. The rule that survived: a checkbox the turn ticked, or `unknown`.

Three rounds of security review found ways a planted symlink in `.kiro/tombstone/` could make Tombstone overwrite a file elsewhere. Every file it writes now opens with `O_NOFOLLOW` and is replaced atomically.

## Accomplishments

On a normal 12-step task with a cap of 0.30 credits, the breaker wrote `STOP` 9 seconds in. The next tool call was refused and the agent stopped. The run cost 0.33 credits.

The final test pass found that, with the watcher service off, the estimate was never calibrated and came out about twice the real price. The fix moved the median error from about 65 to 26 percent.

We checked by hand that the breaker is free. `/usage` read 222.86 before and after five balance probes, a hook call, a cost report and a `get_budget` call.

## What we learned

A feature that only the background service feeds is off when the service is off. Test everything with the watcher stopped too.

A Kiro run that ends without a summary has failed, even if a checkbox moved. Read the tail of the log before trusting the tick.

The agent reads whatever is in the project, including the cap in `tombstone.toml`. Smart models often stop themselves before the breaker has to. That is a feature, so the cap is public.

More in [LESSONS.md](LESSONS.md).

## What's next

The Kiro IDE as a second source, a watcher for agents running on a server, and a published false positive rate once the stall detector has fired on real work.

## Honest comparison

Most neighbors answer "how much is left" or "did the agent really do it". Kiro itself now shows what a session has spent (CLI 3.1.0). Tombstone turns spend into a price per spec task, stops the task when it passes its cap and leaves a receipt. Tombstone answers "what did this task cost, and stop it when it costs too much". Checked on GitHub on 6 November 2026.

| | What it answers | Cost per spec task | Cap per task | Stops the agent | Receipt |
|---|---|---|---|---|---|
| [Kiro credit pooling](https://kiro.dev/changelog/general/credit-pooling/) (Enterprise, 4 Nov 2026) | Shared credits with usage limits per user, group and profile | no | no, the limit is per person or group | not per task (what happens at the limit is not described) | no |
| [Kiro CLI 3.1.0 session credits](https://kiro.dev/changelog/cli/3-1/) (5 Nov 2026) | What this session has spent so far, in `/usage` and an optional status line segment | no, per session (per step in the workflow monitor) | no | no | no |
| [StefanSuess/Bobert](https://github.com/StefanSuess/Bobert) | Will my credits last until the reset | no | no, daily budget and warnings | no | no |
| [vokako/kirocrew-usage-lens](https://github.com/vokako/kirocrew-usage-lens) | Where credits go, by model, agent, job and session | no, per session | no | no | no |
| [ChowRex/kiro-credit-usage-web](https://github.com/ChowRex/kiro-credit-usage-web) | Monthly credits per account for an admin | no | no | no | no |
| [yeeyon/kiro-usage-widget](https://github.com/yeeyon/kiro-usage-widget), [raffaelps/kiro-usage](https://github.com/raffaelps/kiro-usage), [HolgerRiecke/kiro-credits-bar](https://github.com/HolgerRiecke/kiro-credits-bar) | Balance in a tray, menu bar or Obsidian status bar | no | no | no | no |
| [MSAbhishek22/spec-ghost](https://github.com/MSAbhishek22/spec-ghost) | Which specs have no code and which code has no spec | no | no | no | no |
| [ShenJun93/specter-audit](https://github.com/ShenJun93/specter-audit) | The agent said "done", what does git say | no | no | no | a verdict per claim |
| [ifelse-codes/praman](https://github.com/ifelse-codes/praman) | Evidence that the agent's work runs, for several agents | no | no | no | proof records |
| [SanvioLabs/chrono-lab](https://github.com/SanvioLabs/chrono-lab) | Rewind an agent run and compare two futures with their cost | planned | no | no | planned (starter kit, nothing built yet per its README) |
| **Tombstone** | What did this task cost, stop it past the cap | yes | yes, and per spec | yes, two steps | yes |

They do their jobs well. If you only want a balance gauge, Bobert or one of the widgets is the better pick. Credit pooling is the right tool for a company limit. It sets limits on people and groups, and Tombstone stops one task while the spec goes on.

## License

MIT, see [LICENSE](LICENSE).
