# Design Document

## Overview

Tombstone is a Python 3.11+ package with no runtime dependency except `mcp`. It has three kinds of running code, all of them local and none of them calling a model:

- **Hook commands.** Short-lived `tombstone hook <event>` processes started by Kiro shell hooks. They read the hook JSON from STDIN and either exit 0 quickly or exit 2 with a one-line reason on stderr.
- **The watcher.** One long-lived process per project (`tombstone watch`). It polls the active session every 2 seconds, keeps `.kiro/tombstone/state.json` current, and runs the stop sequence.
- **The MCP server.** A stdio process (`tombstone mcp`) with one tool, `get_budget`.

Everything else (`cost`, `graveyard`, `init`, `resume`) is a one-shot CLI command.

V3 is the primary engine (Kiro CLI 3.0.0, default). V2 is legacy and reached with `--v2`. Both are read through one parser (R10.1). Tested CLI versions: 2.37.1 and 3.0.0.

### Architecture

```
 Kiro CLI (V3 or V2)
   |  hook STDIN (JSON)                         writes session files
   v                                                     |
 tombstone hook pre-tool | post-tool | stop              v
   |        |                  |            ~/.kiro/sessions/... (read only)
   |        |                  |                         ^
   |        v                  v                         |
   |   .kiro/tombstone/state.json <------ tombstone watch (poll 2 s)
   |        |                  |                |        |
   |        |                  v                |        v
   |        |            tombstones.json        |   stall / over-cap
   |        |            (repo root)            |        |
   |        v                                   v        v
   +--> STOP file  <----------------------- stop sequence ---> autopsy/*.md
                                                 |
                                                 +--> notification, kill (step two)

 Kiro agent --MCP stdio--> tombstone mcp --> get_budget (reads tombstones.json,
                                             tombstone.toml, usage cache)
```

Data flow in one sentence: hooks and the watcher read session files and `tasks.md`, write `state.json`, `tombstones.json` and the STOP file inside the project, and never write anywhere else except the launchd or systemd unit created by `tombstone init`.

### Design decisions

| Decision | Reason |
|---|---|
| Hooks are separate short processes, not a daemon call | A hook must work when the watcher is down, and R11.5 asks for 200 ms. A cold `python` start with lazy imports is about 30 to 60 ms. |
| Hooks fail open | An internal error exits 0 and appends a line to `.kiro/tombstone/errors.log`. A bug in Tombstone must not freeze the user's agent. The only exit 2 causes are the STOP file and a cap decision. |
| Estimate during a turn, actual after it | Measured: cost lands in the session file only at the end of the turn (R4). |
| `tombstones.json` is the cost history, `state.json` is the scratchpad | Records are committed to the repo. State is machine-local and noisy. |
| One watcher process per project | No project registry, no cross-project process search. The unit name carries a short hash of the project path. |
| Plain functions, `dataclass` only for passive data | Matches the steering. State that is real (the watcher loop) is a dict persisted to `state.json`, not an object. |
| Lazy imports everywhere on the hook path | `mcp`, `subprocess`, `tomllib` and HTML code are imported only by the commands that need them. |

## Components and Interfaces

All modules live in `src/tombstone/`. The module list is fixed by the steering. Where one module carries a sub-area (tasks.md parsing, HTML rendering), that area is a group of functions inside the module and is split out only if the file passes about 400 lines.

All functions that touch Kiro's files take a `kiro_home: Path` argument (default `~/.kiro`) and all time-dependent functions take `now: datetime` so tests need no clock or real home.

### `sessions` (read Kiro session files, price turns)

One job: turn a session on disk into a list of `Turn` values. Read only. Never writes.

```python
@dataclass(frozen=True)
class Turn:
    engine: Literal["v2", "v3"]
    session_id: str
    turn_id: str
    model: str | None
    requests: int | None          # None when unknown
    cost: float | None            # credits, None when unpriced
    start: datetime | None
    end: datetime | None          # None while the turn is open
    state: Literal["closed", "open"]
    unpriced_reason: str | None   # set whenever cost is None on a closed turn

@dataclass(frozen=True)
class Session:
    engine: Literal["v2", "v3"]
    session_id: str
    path: Path
    cwd: str | None
    model: str | None
    turns: list[Turn]

def detect_format(path: Path) -> Literal["v2", "v3"] | None
def parse_session(path: Path) -> Session            # R10.1, R1.5
def find_session(project: Path, session_id: str | None = None,
                 kiro_home: Path = ...) -> Path | None   # R1.7
def open_turn_progress(path: Path) -> Progress | None    # R4.1, R5.1
```

`Progress` is `(requests_so_far, size_bytes, mtime)` of the running turn. `parse_session` never raises on bad data: it returns a `Session` whose turns carry `unpriced_reason` (R1.5).

**Format detection (R10.1).** A path is V3 if it is a `sess_*` directory (or a `messages.jsonl` inside one) that has `session.json` and `messages.jsonl`. A path is V2 if it is `<uuid>.json` (or its `.jsonl` sibling) and the JSON has `session_state.conversation_metadata`. A `<session>` argument to `tombstone cost` may be a path, a full id, or a unique id prefix. Anything else gives `None` and the reason `unknown format`.

**V3 parsing (primary).** Layout: `~/.kiro/sessions/<project hash>/sess_<uuid>/session.json` and `messages.jsonl`. Index: `~/.kiro/session-index/<hash>.jsonl`, lines `{"op", "sessionPath", "at"}`.

`messages.jsonl` is one JSON object per line: `{"id", "timestamp", "payload": {"type": ...}}`. Event types seen on 3.0.0 that matter:

| `payload.type` | Used for |
|---|---|
| `turn_start` | opens a turn; carries `turnId`, `executionId`; its `timestamp` is the turn start |
| `usage_summary` | closes a turn; `promptTurnSummaries[]` with `unit`, `usage`, `usedTools`; `requestIds[]`; `elapsedTime`; `status`; its `timestamp` is the turn end |
| `assistant` | one model reply; used to count requests while the turn is open |
| `tool_call` | `toolName`, `args`, `status`, `title`; feeds the last-10-tool-calls list in the autopsy |
| `tool_result` | `success`, `content` (truncated in the autopsy) |
| `session_metadata`, `user`, `agent_note`, `ContextualHookInvoked` | ignored by the parser |

Rules:

1. Group events by `turnId`. A turn is `closed` if a `usage_summary` with that `turnId` exists, otherwise `open`.
2. Cost (R1.2): sum `usage` over `promptTurnSummaries` entries whose `unit` is `credit`. If the event exists but no entry has unit `credit`, the turn is unpriced with reason `no credit entry in usage_summary`.
3. Requests: `len(requestIds)` on a closed turn. For an open turn, the number of `assistant` events since `turn_start`. This is a [HYPOTHESIS]: it is checked against closed turns in the fixture test, and the counting rule changes if the numbers disagree.
4. Model: `modelId` from `session.json` (per-turn model is not in the summary). Project: `workspacePaths` and `rootPaths` from `session.json` (R1.7).
5. Lines that are not valid JSON (for example a half-written last line) are skipped. Unknown event types are skipped.

**V2 parsing (legacy).** Layout: `~/.kiro/sessions/cli/<uuid>.json` plus `<uuid>.jsonl`. The JSON has `cwd`, `session_id`, `created_at`, `updated_at` and `session_state.conversation_metadata.user_turn_metadatas[]`. Each entry is one turn with `total_request_count`, `metering_usage[]` (`value`, `unit`, `unitPlural`), `model`, `end_timestamp`, `turn_duration {secs, nanos}`, `end_reason`.

Rules:

1. Cost (R1.1): sum `value` over `metering_usage` entries whose `unit` is `credit`. Empty `metering_usage` gives an unpriced turn.
2. Requests: `total_request_count`. End: `end_timestamp`. Start: end minus `turn_duration`.
3. Open turn: the `.jsonl` grows while the turn runs and the `.json` is only rewritten at turn end. Requests so far are the `assistant`-role lines in the `.jsonl` after the line count recorded at the last closed turn. The exact line shape is not yet measured, so this is a [HYPOTHESIS] to settle with a fixture.
4. Pulse for both engines is growth in bytes of `messages.jsonl` (V3) or the `.jsonl` (V2).

**Session discovery (R1.7).**

- V3 with `session_id` from hook STDIN: glob `<kiro_home>/sessions/*/sess_<id>`. The project hash is cached in `state.json` (`v3_project_hash`) after the first hit, so later hooks avoid the glob.
- V3 without an id: read the tail of the index files, newest `at` first, open `session.json`, accept the session if `workspacePaths` or `rootPaths` contains the project directory.
- V2: list `sessions/cli/*.json`, keep files whose `cwd` equals the project directory, take the newest by `updated_at`.
- When both engines have a candidate, the one with the newest activity wins. The watcher re-resolves every poll.

### `tombstones` (project-local data: records, tasks.md, task id, graveyard)

One job: everything that is stored in or derived from files of the project. It holds four function groups.

**Locked JSON I/O.** Shared by `tombstones.json`, `state.json` and the usage cache.

```python
def update_json(path: Path, fn: Callable[[dict], dict], default: dict) -> dict
```

Takes an exclusive `fcntl.flock` on `<path>.lock` (inside `.kiro/tombstone/`), reads, applies `fn`, writes a temp file in the same directory, `fsync`, then `os.replace`. Readers never see a partial file. If the existing file is not valid JSON, `update_json` leaves it untouched, appends the pending change as one line to `.kiro/tombstone/pending.jsonl`, logs a warning, and the next successful write merges the pending lines first (R2.5, and no user file is ever overwritten or deleted).

**tasks.md parsing.**

```python
@dataclass(frozen=True)
class Task:
    spec: str
    task_id: str       # "11", "4.2"
    name: str
    checked: bool
    line: int

def parse_tasks(path: Path) -> list[Task]
def active_spec(project: Path, state: dict) -> str | None
```

A task line matches `^\s*- \[( |x|X|-)\]\*?\s+(\d+(?:\.\d+)*)\.?\s+(.+)$`. Lines without a number get the id `L<line>`. The active spec is, in order: the spec of the last `tasks.md` the agent touched (recorded by the post-tool hook), the spec with the newest `tasks.md` that still has an unchecked task, the only spec if there is one.

**Task id resolution (R2.3, R2.4).** `resolve_tasks(project, stdin, state) -> list[TaskRef]` (and `resolve_task`, which returns the first), first match wins:

1. *Hook STDIN.* Look for `task_id`, `taskId`, `task.id` in the JSON (task hooks on V3 may carry it; the exact STDIN of `PreTaskExecution` in spec mode is [UNKNOWN] and the lookup is deliberately tolerant).
2. *Checkbox diff.* `state.json` keeps `tasks_snapshot[spec] = {task_id: checked}`. The post-tool hook runs only when the tool input path matches `.kiro/specs/<spec>/tasks.md` (a cheap string check, so other tools cost nothing). It re-parses the file, compares with the snapshot and records newly checked ids in `state.turn.checked`, then refreshes the snapshot.

   On the **first** edit of a run there is no snapshot yet, and the pre-edit file state is already gone, so comparing the whole re-parsed file against an empty snapshot would count every already checked task as checked this turn. The real `sess_29996a7d` run hit this: tasks 1.1 through 3.3 and 4.1 were all `[x]`, so the record was attributed to task 1.1 with `share = 0.125` instead of the task 4.1 that the turn actually completed. When no snapshot exists, the baseline is therefore the edit itself: for a `str_replace` the `oldStr`/`newStr` in the hook STDIN show which checkbox line changed from `- [ ]` to `- [x]`, and only that id counts; for a whole-file `fs_write` with no snapshot nothing is treated as newly checked. Once a snapshot exists, later edits diff against it as before.

   A task id counts as checked in a turn only if the previous snapshot held it as unchecked, or, with no snapshot, its line flipped from `[ ]` to `[x]` inside one edit (the id must be present and unchecked in `oldStr`). An id that is new in the file is never "checked now": it just enters the snapshot. The live run of task 4.2 hit this: task 3.4 had been added as `- [x] 3.4` in an earlier turn, the snapshot did not know it, so the 4.2 turn was split 50/50 between 3.4 and 4.2.

   `state.turn.checked` is reset when a new turn begins (a change in `state.turn.turn_id`, set by the post-tool, UserPromptSubmit and session-start hooks) so a task completed in one turn never leaks into the next.

   **Stop diff is the authority.** The post-tool path only sees tools whose input names `tasks.md`, so a box flipped by a shell command (the live run of task 6.2 used `sed -i` through `execute_bash`) is invisible to it. The 6.2 turn was recorded as `unknown` and the next turn saw 6.2 and 7.1 as newly checked and split 50/50. Before building the record, the Stop hook now calls `sync_checked_at_stop`: under the state lock it re-parses the `tasks.md` of every spec, adds each id that went from unchecked to checked since `tasks_snapshot` to `state.turn.checked` (an id already listed is never added twice), and refreshes the snapshot. A spec with no snapshot only gets a baseline. The post-tool path stays as it is for earlier information. The cost is a few small file parses, well under the 200 ms budget.
3. *Unknown.* `TaskRef(task_id="unknown", ...)`. There is no "first unchecked task" guess: a turn that checks no box and carries no task id is a work turn outside the task list (a fix, a question, a refactor), and a guess would put its cost on a real task. The record id is `unknown/<session turn id>`, so there is one record per turn and it is always written (R2.4). The name is `state.turn.prompt_name`, or the turn start time when no prompt was seen.

*Prompt hook.* `tombstone hook prompt` (V3 `UserPromptSubmit`, STDIN `{"session_id", "hook_event_name", "cwd", "prompt"}`) runs `record_prompt` through the locked store. It resets `state.turn.checked` and stores `state.turn.prompt_name`: the first 8 words of `prompt` after the home directory and any `/Users/<name>` or `/home/<name>` prefix are replaced by `~`. It fails open and does no work beyond one locked state write, so it stays under 200 ms.

If several tasks were checked in one turn, the turn is split equally between them: the Stop hook writes one record per checked task, each turn entry with `share = 1/n`, so the sums stay correct.

**Records (R2.1, R2.2).** `tombstones.json` at the repo root:

```json
{
  "version": 1,
  "tombstones": [
    {
      "id": "tombstone/4.2",
      "task_id": "4.2",
      "name": "Add stall evaluation",
      "spec": "tombstone",
      "task_type": "other",
      "engine": "v3",
      "model": "auto",
      "requests": 14,
      "estimate": 0.63,
      "actual": 0.71,
      "minutes": 6.4,
      "outcome": "finished",
      "false_positive": null,
      "date": "2026-11-05",
      "turns": [
        {"session_id": "sess_...", "turn_id": "...", "requests": 14,
         "estimate": 0.63, "actual": 0.71, "start": "2026-11-05T09:12:03Z",
         "end": "2026-11-05T09:18:27Z", "share": 1.0, "unpriced_reason": null}
      ]
    }
  ]
}
```

- `id` is `<spec>/<task_id>`, or `unknown/<turn_id>` when the task is unknown. The writer upserts by `id` and, inside the record, by `turn_id`, so re-running the same hook is idempotent.
- Top-level `requests`, `estimate`, `actual`, `minutes` are sums over `turns` weighted by `share` (R1.4). `actual` is `null` while any turn is unpriced; the human view then shows `est.` only (R4.4).
- `outcome`: `finished` (turn ended normally), `killed` (the stop sequence ran, with or without step two), `cap` (blocked by the spec cap or by the remaining-budget check before work started). A `cap` record has no turns and zero cost.
- `false_positive` stays `null` unless the user edits it. The graveyard shows it.
- `date` is the local date of the last turn end.

**Turn-end handler (R2.1, R4.2, R4.3).** `on_turn_end(project, stdin)` is the body of `tombstone hook stop`. It resolves the session, takes the newest unrecorded turn, resolves the task, builds the record, upserts it, and updates the average request price.

The `Stop` event fires before the cost of the finishing turn is on disk: in the real V3 session `sess_29996a7d` the `ContextualHookInvoked` event for `tombstone-stop` is line 101 and the `usage_summary` with `promptTurnSummaries` is line 102, so when the hook runs the turn still parses as open. The handler must therefore record the newest unrecorded turn even when it is still open, not just the newest closed turn. It writes the record immediately with `actual = null` and the estimate filled from the request count, then starts a detached reconcile child. The write plus spawn stays well under the 200 ms hook budget (measured around 10 ms).

The reconcile child is `tombstone hook reconcile --session <id> --turn <turn or latest> --project <path>`, started with `subprocess.Popen(start_new_session=True, ...)` and stdout/stderr to devnull so it outlives the hook. It polls the session every 0.5 s for up to 30 s; once the target turn is closed with a cost it fills `actual` and `requests` (from `requestIds`) on every stored turn entry with that session and turn id, across all records, through the same locked `update_json`. A turn split between several tasks has one entry per record, and each keeps its own `share`. The record totals of every touched record are recomputed, and folds the turn into the average price window. If it times out, `actual` is left null and one line is written to `errors.log`. The watcher's `reconcile_pending` still covers the same gap on its next poll, so a turn is reconciled by whichever path lands first.

Average request price (R4.3): `state.price_window` is a list of `[requests, cost]` per priced turn, newest last. Oldest turns are dropped while the remaining requests still total at least 50. `avg_request_price = sum(cost) / sum(requests)` over the window, stored in `state.json`. Until a window exists, the value from `tombstone.toml` is used.

**Graveyard (R2.6).** `render_graveyard(records) -> str` returns one HTML string with inline CSS and inline JS. No `<link>`, no `<script src>`, no `@import`, no web fonts, no `http(s)://` strings. A table sorted by cost with a filter box, outcome badges, and an `est.` or `actual` suffix on every number. The command writes it to `.kiro/tombstone/graveyard.html` (or `--out`).

### `caps` (config and budget math)

One job: decide whether a task may start and answer budget questions. Pure functions over data plus one cached external probe.

**`tombstone.toml` (R3.1, R3.2).**

```toml
[caps]
task = 40            # credits per task
spec = 300           # credits per spec

[stall]
quiet_window_minutes = 10

[budget]
avg_request_price = 0.045   # credits, used until state.json has 50 requests
min_plan_balance = 0        # 0 disables the check

[stop]
kill_grace_seconds = 30
```

`load_config(project) -> Config` uses `tomllib`. A missing file gives the defaults from R3.2 (`min_plan_balance` default 0). A parse error or a wrong type falls back to the default for that key and records a warning in `state.json`; it never blocks the agent.

**Task type and similar tasks (R3.5, R3.6).**

```python
def task_type(name: str) -> Literal["test", "migration", "refactor", "docs", "other"]
def keywords(name: str) -> set[str]
def similar(records, name, spec=None) -> list[dict]
def median_similar(records, name) -> Similar    # value, count, basis
```

`task_type` is first-match keyword lookup on the lowercased name (`test`, `pytest`, `spec` -> test; `migrat`, `schema` -> migration; `refactor`, `rename`, `cleanup` -> refactor; `doc`, `readme` -> docs; else other). `keywords` is the set of words of length 3 or more minus a small stop list. A record is similar if its `task_type` matches and the keyword sets share at least one word, and its outcome is `finished`. The cost used is `actual` if present, else `estimate`, and `basis` says which. `median_similar` returns the median and the count. With no similar record the result has `value = None` and the caller uses the task cap as reference (R3.6).

**Gate decision (R3.3, R3.4).**

```python
def check_task_start(config, records, state, task: TaskRef) -> Decision
```

A task is *new* when `task.task_id` differs from `state.current_task`. For a new task:

1. `spent = sum of the spec's record costs (actual, else estimate) + the open turn estimate`. `remaining = config.spec_cap - spent`.
2. If `spent >= spec_cap`: block, outcome `cap` (R3.4).
3. Else `reference = median_similar` or, without similar tasks, `task_cap` (R3.6). If `remaining < reference`: block, outcome `cap` (R3.3).
4. Else if `min_plan_balance > 0` and the cached plan balance is known and lower: block. The hook only reads the cache and never starts the probe.
5. Else allow, and set `state.current_task`.

A blocked decision writes a `cap` record (once per task id) and returns the stderr line, for example:

`TOMBSTONE: task 4.2 "Add stall evaluation" blocked. Similar tasks cost 12.4 (actual, median of 3). Spec "tombstone" has 8.1 of 300 left (actual). Raise caps.spec in tombstone.toml to continue.`

The same line shape, with `no similar task, using task cap 40` for R3.6. Raising the cap in `tombstone.toml` is the override; the next tool call re-evaluates and passes. The block repeats on every tool call until then, because the task never becomes current.

**Plan balance and usage probe (R8.3, R8.4, R11.4).**

```python
def plan_balance(project, now, refresh: bool) -> Balance   # value|None, age_minutes, reason
```

The cache is `.kiro/tombstone/usage.json`: `{"fetched_at", "balance", "reason"}`. Failures are cached too, so a failing probe is not retried within 5 minutes. A refresh runs `kiro-cli chat --no-interactive "/usage"` with a 15 second timeout through `subprocess.run` with an argument list, never a shell. The output is parsed with a regex for the remaining-credits figure. The text format of `/usage` is not captured yet, so the parser is written against a fixture recorded in the parser task, and an unparseable output gives `balance = None, reason = "unparseable /usage output"`. Only the MCP server and the watcher may refresh; hooks read the cache only.

### `watcher` (poll, state.json, stall and over-cap)

One job: observe the active session and project and decide when to raise an event. The decision is a pure function so it is testable without a clock, disk or process.

```python
def evaluate(state: dict, obs: Observation, cfg: Config, now: datetime) -> Event | None
def poll_once(project: Path, cfg: Config, now: datetime, kiro_home: Path) -> Event | None
def run(project: Path) -> None            # the 2 second loop
```

`Observation` holds: project, session path, engine and id, the open turn id (None when no turn is open), the open-turn **units**, the jsonl size and `prior_cost` (earlier turns of the same task). The project fingerprint is compared in `poll_once`, which stores `last_project_change` in state before `evaluate` runs. `Event` carries kind, message, detail, task, estimate, units, session and turn ids, and the project path, so `stop.begin_stop(event)` needs nothing else.

**The unit of the open-turn estimate (R4.1).** While a turn is open the watcher counts `assistant` events of that turn. That count is called a *unit*. It overcounts real requests: in session `sess_b2bf8b22` 21 assistant events were counted while the turn was open and 12 `requestIds` were on the closed turn. Pricing units with the per-request average would therefore overestimate. The estimate is:

```
open-turn estimate (est.) = units * avg_unit_price
avg_unit_price            = sum(actual cost) / sum(units)   over closed turns of this project
```

Calibration:

- The unit count of a closed turn is the number of `assistant` events with its `turnId` (`sessions.assistant_counts`), the same rule as for the open turn. It is never `len(requestIds)`.
- Each closed turn with an `actual` cost and at least one unit is folded once into `state.unit_window` as `[units, actual]`, newest last. The turn id goes into `state.unit_turns` (last 200) so no turn is folded twice. Old entries are dropped while the rest still total at least 50 units, like the request price window.
- `state.avg_unit_price` is stored next to `state.avg_request_price`. Both are kept: the request average prices closed turns and records (R4.3), the unit average prices only the open turn.
- Until a V3 calibration exists, the open turn is priced with the per-request average and labeled `est., uncalibrated`. This overcounts and is the only case where the unit and its price disagree. Once one closed turn is folded the label becomes `est., calibrated`. The basis is stored in `state.turn.estimate_basis`.
- V2 counts real requests (`assistant` lines past the closed total), so the per-request average is the right price there (`est., per request`).

**state.json (R5.6).**

```json
{
  "version": 1,
  "session": {"id": "sess_...", "engine": "v3", "path": "~/.kiro/sessions/..."},
  "turn": {"turn_id": "...", "started_at": "...", "requests": 12, "estimate": 0.54,
           "jsonl_size": 48213, "last_growth_at": "...", "checked": ["4.2"]},
  "current_task": {"spec": "tombstone", "task_id": "4.2"},
  "tasks_snapshot": {"tombstone": {"4.1": true, "4.2": false}},
  "last_project_change": "2026-11-05T09:14:40Z",
  "project_fingerprint": "...",
  "last_event": {"kind": "stall", "at": "...", "detail": "..."},
  "avg_request_price": 0.047,
  "price_window": [[14, 0.71], [9, 0.357]],
  "avg_unit_price": 0.0286,
  "unit_window": [[21, 0.6], [9, 0.25]],
  "unit_turns": ["<closed turn id>", "..."],
  "v3_project_hash": "14e984af5081f39b",
  "cli_version": {"installed": "3.0.0", "tested": "3.0.0"},
  "warnings": []
}
```

All paths stored in state use `~` for the home directory. The file is written only through `update_json`.

**Project change detection (R5.4).** The fingerprint is a hash of `(path, mtime_ns, size)` for the files that git reports as changed (`git status --porcelain=v1 -z --untracked-files=all`, run with `subprocess.run([...], cwd=project, timeout=5)`), minus paths under `.kiro/tombstone/`, `.git/` and the file `tombstones.json`. Outside a git repo the fingerprint covers a walk of the tree that skips `.git`, `.kiro/tombstone`, `tombstones.json`, `node_modules`, `.venv`, `__pycache__`. When the fingerprint differs from the previous poll, `last_project_change = now`. Edits to git-ignored files are not seen; this is accepted and documented.

**Rules (R5.2, R5.3, R5.7).**

- `pulse`: the jsonl grew at least once in the last 60 seconds (`last_growth_at`).
- `quiet_since = max(last_project_change, turn.started_at)`. Starting the clock at the turn start keeps a turn that begins after a long idle period from stalling at once.
- **Stall** (R5.2): an open turn exists, `pulse` is true, and `now - quiet_since > quiet_window`.
- **Over-cap** (R5.3): `estimate > task_cap`, where `estimate = open-turn units * avg_unit_price` (see the unit above) plus the actual or estimated cost of earlier turns of the same task. For a task on its first turn this is exactly the turn estimate of R5.3; for a continued task it keeps "cap per task" meaningful.
- If the pulse grows and project files change, `quiet_since` moves forward and no stall can be raised (R5.7).
- An event is raised once per turn. After the event, `evaluate` returns nothing for that turn.

**`state.turn` fields written by the watcher.** `watch_turn_id` (the open turn the watcher tracks, None when none is open), `started_at`, `jsonl_size`, `last_growth_at` (set to the turn start when the turn is first seen, then to `now` whenever the jsonl grows), `requests` (the units), `estimate`, `estimate_basis` and `event_raised`. The hooks own `turn_id`, `checked` and `prompt_name` in the same dict, and the watcher leaves them alone.

**`poll_once`.** Reads the session and the project outside the lock, then does one locked state write: session, project change, calibration of newly closed turns, open-turn tracking, estimate, then `evaluate`. An event sets `turn.event_raised` and `last_event` in the same write, so it is raised once per turn.

**Loop.** Every 2 seconds: `poll_once`, then if an event comes back, call `stop.begin_stop`. Until task 7.4 adds `begin_stop`, the event is only logged to `errors.log`. The loop also calls `reconcile_pending` and, when a turn closes, appends the actual cost to a waiting autopsy. A crash in one poll is caught, logged to `errors.log`, and the loop continues. The watcher warns on start if the installed CLI version differs from the tested one (R10.4).

### `stop` (STOP file, hook gate, notification, kill)

One job: the two-step stop and the pre-tool gate.

**STOP file (R6.1).** `.kiro/tombstone/STOP` is a JSON object:

```json
{
  "reason": "over_cap",
  "message": "TOMBSTONE: task 11 over budget (est. 58 of 40). Stop.",
  "task_id": "11",
  "spec": "tombstone",
  "estimate": 58.0,
  "requests_at_stop": 1290,
  "time": "2026-11-05T09:30:12Z",
  "session_id": "sess_..."
}
```

`reason` is `over_cap`, `stall` or `manual`. `message` is the one line the hook prints.

**Pre-tool gate (R6.2, R11.5).** `tombstone hook pre-tool` does this, in this order, and imports nothing heavy:

1. Find the project root from `cwd` in STDIN (walk up to the first directory containing `.kiro` or `tombstone.toml`).
2. If `STOP` exists: print `message` (or `TOMBSTONE: stopped. Run tombstone resume.` if the file is unreadable) to stderr, exit 2.
3. Resolve the current task (first unchecked task of the active spec, unless `state.current_task` is already set for the same turn). If it equals `state.current_task`, exit 0. This is the common path and costs one `stat`, one small read and one JSON load.
4. Otherwise call `caps.check_task_start`. Exit 2 with its message, or exit 0.

**Resume (R6.6).** `tombstone resume` deletes the STOP file and clears the `stop` entry in state. Deleting the file by hand has the same effect. Nothing else unblocks the agent.

**Notification (R6.7).** `notify(title, text)` runs `osascript -e 'display notification ...'` on macOS and `notify-send` on Linux, with the text passed as an argument-list element (macOS: through `on run argv` so the text is never interpolated into AppleScript). It also prints one line to the watcher's terminal. A missing tool or a non-zero exit is logged and ignored.

**Step two: kill (R6.3, R6.4, R6.5).** `begin_stop(event)` writes the STOP file, notifies, writes the autopsy, and stores a `stop` entry in state. On every later poll, `maybe_kill` checks: `now >= stop.time + kill_grace` and the open-turn request count is above `requests_at_stop`. If so:

1. `find_project_processes(project)` returns pids whose executable name starts with `kiro-cli` and whose current directory equals the project directory exactly. On Linux this reads `/proc/<pid>/exe` and `/proc/<pid>/cwd`. On macOS it runs `ps -Ao pid=,ppid=,command=` and `lsof -a -p <pid> -d cwd -Fn` (allowed by the steering since 05.11). Note: on CLI 3.0.0 the V3 agent runs as a Node process started by `kiro-cli`, so the match must cover the `kiro-cli` process and its child processes; the exact process tree is measured in task 7.3 before the kill logic is written.
2. Send `SIGTERM` to each, wait up to 5 seconds, then `SIGKILL` for any survivor. Never the watcher's own pid or its parents.
3. If the list is empty, skip step two, add a warning to state and to the autopsy, and kill nothing else (R6.4).

The stop sequence reads and writes only inside `.kiro/tombstone/` and `tombstones.json`. It never touches a project file (R6.5). The record for the stopped task gets outcome `killed` and `actual = null` until the turn summary arrives, if it ever does (a killed process may not write one).

### `autopsy` (Markdown receipt)

One job: write `.kiro/tombstone/autopsy/<YYYY-MM-DD>_<task>.md` (task id with `.` and `/` replaced by `-`; `unknown` plus the turn id for unknown tasks).

```python
def write_autopsy(project, stop: dict, session: Session, state: dict) -> Path
def append_actual(path: Path, turn: Turn) -> None
```

Sections (R7.2): header with task id and name; engine and model; requests; cost per request where known (actual divided by requests, labeled `actual`, or `unknown`); estimate at STOP (`est.`); actual after the turn (`pending` until it arrives); minutes of quiet project; STOP time and reason; step two ran yes, no, or skipped with the reason; a table of the last 10 tool calls (`toolName`, `title` or first arg, `status`, time) read from `tool_call` events, each cell cut to 200 characters.

`append_actual` adds a section `## Actual cost (actual)` once, guarded by `state.autopsy_pending` (R7.3). The watcher calls it when the turn closes; the Stop hook calls it too, whichever comes first.

**Home path stripping (R7.4).** One function, `scrub(text, home)`, replaces the home directory string and its `Path.resolve()` form with `~`. It is applied to the whole document just before writing, including tool arguments.

### `mcp_server` (get_budget)

One job: a stdio MCP server built with `mcp.server.fastmcp.FastMCP`, exactly one tool.

```python
def budget(project: Path, task_name: str | None, now: datetime) -> dict
def serve() -> None
```

`budget` is a plain function so it is tested without MCP. `serve` registers `get_budget(task_name: str | None = None)` and runs on stdio. The project root comes from the server's working directory (walk up as in the hooks), or `--project` in `mcp.json`.

Return value (R8.2):

```json
{
  "similar": {"median": 12.4, "count": 3, "basis": "actual", "reference": "similar tasks"},
  "spec": {"name": "tombstone", "cap": 300, "spent": 291.9, "remaining": 8.1, "basis": "est. + actual"},
  "plan": {"balance": 35.2, "age_minutes": 3.0, "basis": "actual (from /usage)", "reason": null}
}
```

Without similar tasks `median` is `null` and `reference` says `task cap`. If the balance is unavailable, `plan.balance` is `null` with `reason`, and `similar` and `spec` are still filled (R8.4). The function does no model call and no network call; the only subprocess is the cached `/usage` probe (R8.3, R8.5).

### `cli` (entry point, init, units)

One job: argument parsing and the one-shot commands. Subcommands:

| Command | Purpose |
|---|---|
| `tombstone cost <session>` | price one session (R1.6) |
| `tombstone hook <pre-tool\|post-tool\|stop\|session-start\|task-pre\|task-post>` | hook entry points, read STDIN |
| `tombstone watch [--project DIR]` | run the watcher |
| `tombstone mcp [--project DIR]` | run the MCP server |
| `tombstone graveyard [--out FILE]` | write the HTML page |
| `tombstone resume` | remove the STOP file |
| `tombstone init` | create files and the watcher unit |

`main()` imports a command's module only after parsing the arguments, so `hook pre-tool` never imports `mcp`, `subprocess` or `tomllib` unless it needs them.

**`tombstone cost` output (R1.3, R1.6).** One line per turn: engine, model, requests, cost, start, end; then a total. Every number carries a label: `0.390 credits (actual)`, `5 requests (actual)`. An open turn prints `est.`. An unpriced turn prints `unpriced: <reason>`. Acceptance: the V3 session of measurement 5 prints `0.390` and `5`.

**Hook files written by init (R9.2, R10.2, R10.3, R11.1, R11.3).**

- V3, `.kiro/hooks/tombstone-*.json`, schema `{"version": "v1", "hooks": [{"name", "trigger", "matcher"?, "action": {"type": "command", "command"}}]}` with PascalCase triggers: `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, and `PreTaskExecution` / `PostTaskExecution` where available. The task hook files only call `tombstone hook task-pre|task-post`, which store the task id from STDIN in `state.current_task`. The core never waits for them (R10.3).
- V2, `.kiro/agents/tombstone.json`, camelCase keys `preToolUse`, `postToolUse`, `stop`, `agentSpawn`. No `model` key is written, ever (R11.3). Started with `kiro-cli chat --agent tombstone`.
- Every action has type `command`. No action of type `agent` is generated (R11.1).
- The command is the absolute path of the `tombstone` executable found at init time (`shutil.which`), so hooks work from a bare environment.

**Existing files (R9.4).** `init` never overwrites. For each target it prints `created <path>` or `exists, left unchanged <path>`.

**Watcher unit (R9.3).** macOS: `~/Library/LaunchAgents/com.tombstone.<name>-<hash8>.plist` with `ProgramArguments = [tombstone, watch, --project, <dir>]`, `RunAtLoad`, `KeepAlive`. Linux: `~/.config/systemd/user/tombstone-<name>-<hash8>.service`. These are the only files written outside the project. Init loads the unit with `launchctl bootstrap gui/<uid> <plist>` or `systemctl --user enable --now <unit>` (allowed for init only) and prints the same command so the user can repeat it.

**After init (R9.5).** Prints:

```
V3 (default):  kiro-cli chat            hooks in .kiro/hooks are active
V2 (legacy):   kiro-cli chat --agent tombstone
```

**CLI version (R10.4).** The tested version (`3.0.0`) is a constant. The installed version is read at `init` and at watcher start, stored in `state.cli_version`, and a mismatch prints one warning line. Hooks never run `kiro-cli`; `cost` and `graveyard` print the stored value.

**Power folder (R9.1).** A top-level `power/` directory with `plugin.json`, `skills/tombstone/SKILL.md`, `mcp.json` (command `tombstone`, args `["mcp"]`), `hooks/v3/*.json` and `hooks/v2/tombstone.json`. The hook files are generated from the same builder functions `init` uses, and a test compares the files with the builder output so they cannot drift.

## Data Models

Summary of persistent files, all UTF-8 JSON with `\n` line ends and ISO 8601 UTC timestamps (`...Z`):

| File | Location | Commit? | Writer | Format |
|---|---|---|---|---|
| `tombstones.json` | repo root | yes | hooks, watcher | see Records |
| `tombstone.toml` | repo root | yes | user, `init` | see caps |
| `state.json` | `.kiro/tombstone/` | no, machine-local | hooks, watcher | see watcher |
| `STOP` | `.kiro/tombstone/` | no | stop sequence | see stop |
| `usage.json` | `.kiro/tombstone/` | no | probe | `{"fetched_at", "balance", "reason"}` |
| `pending.jsonl`, `errors.log` | `.kiro/tombstone/` | no | any | one line per entry |
| `autopsy/*.md` | `.kiro/tombstone/` | user choice | autopsy | Markdown |
| `graveyard.html` | `.kiro/tombstone/` | user choice | `graveyard` | one static HTML file |

Credits are stored as floats rounded to 4 decimals and shown with at least 3. Units are always credits. The repository `.gitignore` does not exclude any of these paths. Keeping the "no" rows out of commits is the user's decision.

## Error Handling

| Situation | Behavior |
|---|---|
| Session file missing, empty, truncated, unknown format | Turn is unpriced with a reason; no number is invented (R1.5). `cost` prints the reason and exits 0 for partial data, 1 when no turn could be read. |
| Last jsonl line half written | Skipped; the next poll sees it complete. |
| Cost not in the session yet at `Stop` | The finishing turn still parses as open (the `usage_summary` lands a line after the `tombstone-stop` event). The record is written now with `actual = null` and a request-based estimate; the Stop hook starts a detached `tombstone hook reconcile` child that polls every 0.5 s for up to 30 s and fills `actual`. The watcher reconciles the same gap on its next poll. |
| Reconcile child times out | `actual` left null, one line in `errors.log` and one in `reconcile.log`; the safety net or the watcher may still reconcile later. |
| Reconcile child dies with the hook runner or `kiro-cli` (seen in the live run of task 3.4: record stuck at the 21-request estimate, empty `errors.log`, the real turn had 12 requests) | Layer 1, the child is fully detached: `start_new_session=True`, `close_fds=True`, stdin from devnull, cwd set to the project root, absolute `sys.executable`, SIGHUP ignored, stdout and stderr appended to `.kiro/tombstone/reconcile.log`. It logs one line at start, one when it fills a record and one on timeout, so a crash before the first line is visible too. The Stop hook still only spawns and returns. Layer 2, a safety net that needs no background process: every `prompt`, `post-tool`, `stop` and `session-start` hook and the `cost` and `graveyard` commands first call `reconcile_pending`. It reads `tombstones.json` without the lock, looks at the 5 newest records with `actual = null` (turns not marked unpriced), and writes through the locked store only when the session now has the cost on disk. With nothing to do it costs one small JSON read, well under 50 ms. `pre-tool` is left out because it is the latency-critical gate. |
| Request count of a record after the cost lands | Once `actual` is known, `requests` comes from the `requestIds` of the `usage_summary`. The count stored at Stop is an open-turn estimate (assistant events) and is replaced. |
| First edit of a run, no `tasks_snapshot` yet | The newly checked task is read from the edit's `oldStr`/`newStr` diff, not from every already checked task. A whole-file `fs_write` with no snapshot records nothing. Prevents the wrong-task, fractional-share attribution seen in `sess_29996a7d`. |
| `tombstones.json` invalid JSON | File untouched; change goes to `pending.jsonl`; warning in state and on stderr. |
| Two hooks at once | `flock` plus atomic replace; both writes are applied (R2.5). |
| `tombstone.toml` missing or bad key | Defaults per key; warning in state. |
| `tasks.md` missing or no tasks | Task id `unknown`; record still written (R2.4). The cap gate allows the call. |
| Hook crashes | Exit 0, line in `errors.log`. Never exit non-zero other than 2. |
| STOP file unreadable | Still exit 2 with a generic reason. A broken STOP is treated as a STOP. |
| `git` missing or slow | Mtime walk fallback; 5 second timeout; the watcher never stalls on it. |
| `/usage` probe fails or times out | Cached as a failure for 5 minutes; `balance = null` with a reason (R8.4). |
| No process bound to the project | Step two skipped, warning in state and autopsy, nothing else is killed (R6.4). |
| Notification tool missing | Logged, ignored; the terminal line and the STOP file still happen. |
| Installed CLI version differs from tested | One warning; behavior unchanged (R10.4). |

## Testing Strategy

`pytest` is a dev dependency only. Tests need no Kiro install, no home directory and no network.

- **Fixtures** in `tests/fixtures/`, with home paths and message text removed by a scrub script: the V3 session of measurement 5 (0.390 credits, 5 requests), the V2 session of measurement 2 (0.357 credits, 9 requests) if still on disk, a 10-request turn for the estimate check, plus small hand-made files for the edge cases: empty file, truncated last line, unknown format, `usage_summary` without a credit entry, an open turn, two turns in one session, a `/usage` output sample, hook STDIN samples for each event.
- **Unit tests per module** with injected `now` and `kiro_home`:
  - `sessions`: both formats, discovery, unpriced reasons, request counting for open turns (this test also settles the two [HYPOTHESIS] items).
  - `tombstones`: task id resolution in all four steps, checkbox diff for `str_replace`-style and whole-file edits, upsert idempotence, shared-turn split, 20 processes writing at once and the file still valid JSON, invalid-JSON path.
  - `caps`: defaults, partial toml, task type, similar-task median, gate decisions for R3.3, R3.4, R3.6 including message text.
  - `watcher`: `evaluate` as a table of (state, observation, time) cases for stall, over-cap, R5.7, quiet clock from turn start, one event per turn; change detection ignores `.kiro/tombstone/`, `.git/` and `tombstones.json`.
  - `stop`: STOP write and block message, resume, grace timing, kill with a fake process list, empty list skips step two.
  - `autopsy`: all fields, last 10 tool calls, home path never appears, actual appended once.
  - `mcp_server`: `budget()` output with and without similar tasks, with the balance missing, and a check that the tool list is exactly `["get_budget"]`.
  - `cli`: `init` into a temp project twice (second run leaves files unchanged), generated hook files contain only `command` actions and no `model` key, the `power/` files equal the builder output.
- **Hook latency.** A test runs `tombstone hook pre-tool` five times in a subprocess against a project with no STOP file and no breach and asserts the median is under 200 ms (R11.5).
- **Zero-credit checks.** A static test greps the package and the generated hook files for prompt-type hooks, a `model` key, and imports of network modules (`socket`, `urllib`, `http`, `requests`). The manual acceptance check from R11 (no extra metered request with Tombstone installed) is recorded in `KIRO_LOG.md`.
- **Estimate accuracy (R4).** A test computes `requests * avg_price` against the actual cost on the 10-request fixture and asserts it is within 30%. If the real data disagrees, the result goes to `LESSONS.md` rather than loosening the threshold silently.
- **Style gates.** A test fails if any tracked text file contains an em dash or en dash character.

## Open points

1. **Subprocess whitelist.** Resolved 05.11 by the project owner: the steering now allows `kiro-cli --version`, `ps`, `lsof`, and `launchctl` / `systemctl --user` from `init` only. `init` loads the unit itself and also prints the command.
2. **STDIN shapes.** The V3 `PreTaskExecution` STDIN and the exact V2 `.jsonl` line shape are [UNKNOWN]. The design is tolerant, and the fixtures decide the final field names.
3. **Request counting for an open turn** (V3 `assistant` events, V2 jsonl lines) is a [HYPOTHESIS] until compared with closed turns. Measured on V3: the count overcounts requests (21 counted, 12 `requestIds`), so it is a unit, priced by calibration (see watcher), not a request.
4. **`init` and MCP registration.** R9.2 does not list `.kiro/settings/mcp.json`, so `init` does not write it. The Power ships `mcp.json`; the steering file written by `init` tells the agent to call `get_budget` when it is available.
5. **`false_positive`** has no setter in the requirements. It stays `null` and is editable by hand.
