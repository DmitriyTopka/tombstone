"""Bug 1 and Bug 2 acceptance: replay the sess_29996a7d event order.

The real V3 run of task 4.1 exposed two bugs:

- Bug 1: the Stop hook fired before the turn cost was on disk (the
  ContextualHookInvoked event for tombstone-stop is line 101 and the
  usage_summary is line 102), so no record was written at all. The fix writes
  the record immediately with ``actual = null`` and reconciles once the
  usage_summary lands.
- Bug 2: with no earlier snapshot, the post-tool hook treated every already
  checked task as checked this turn, attributing the record to task 1.1 with
  share 0.125. The fix reads the newly checked task from the str_replace
  oldStr/newStr diff.

This test replays the event order and asserts the final store holds exactly
one record for task 4.1 with actual 3.2094 and 13 requests.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from tombstone import cli
from tombstone.tombstones import (
    TOMBSTONE_DIR,
    load_tombstones,
    on_turn_end,
    reconcile_turn,
)

# The real turn: 13 requests and 3.2094 credits (rounded to 4 decimals).
TURN_ID = "dec02aaa-3791-48a3-8d7b-a19c21303a9f"
REQUESTS = 13
COST = 3.2094
SESSION_ID = "sess_29996a7d"

# A tasks.md matching the real run: 1.1 through 3.3 already done, 4.1 is the
# task this turn completes, the rest still open.
TASKS_MD = """# Implementation Plan

- [ ] 1. Session parser
  - [x] 1.1 Fixtures and the V3 parser
  - [x] 1.2 The V2 parser
  - [x] 1.3 Session discovery and open-turn progress
- [x] 2. CLI entry point and cost
- [ ] 3. Tombstone writer
  - [x] 3.1 Locked JSON store
  - [x] 3.2 tasks.md parsing and task id resolution
  - [x] 3.3 Record building and the Stop hook
- [ ] 4. Caps and budget math
  - [x] 4.1 Config, task type and similar tasks
  - [ ] 4.2 Gate decision and the pre-tool hook
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".kiro" / "tombstone").mkdir(parents=True)
    specs = tmp_path / ".kiro" / "specs" / "tombstone"
    specs.mkdir(parents=True)
    (specs / "tasks.md").write_text(TASKS_MD, encoding="utf-8")
    return tmp_path


def _session_dir(kiro_home: Path, project: Path) -> Path:
    project_hash = "29abhash"
    sess_dir = kiro_home / "sessions" / project_hash / SESSION_ID
    sess_dir.mkdir(parents=True)
    (sess_dir / "session.json").write_text(json.dumps({
        "id": SESSION_ID,
        "modelId": "auto",
        "workspacePaths": [str(project)],
    }), encoding="utf-8")

    index_dir = kiro_home / "session-index"
    index_dir.mkdir(parents=True)
    (index_dir / f"{project_hash}.jsonl").write_text(json.dumps({
        "op": "create",
        "sessionPath": str(sess_dir),
        "at": "2026-11-05T17:38:11Z",
    }) + "\n", encoding="utf-8")
    return sess_dir


def _event(**payload) -> str:
    return json.dumps({"id": "scrubbed", "timestamp": payload.pop("_ts", None),
                       "payload": payload})


def _open_turn_lines() -> list[str]:
    """The jsonl of the finishing turn BEFORE the usage_summary lands.

    turn_start, 13 assistant events, then the tombstone-stop
    ContextualHookInvoked event. No usage_summary yet, so the parser sees an
    open turn (Bug 1 trigger).
    """
    lines = [
        _event(type="turn_start", turnId=TURN_ID,
               _ts="2026-11-05T17:37:56.000Z"),
    ]
    for i in range(REQUESTS):
        lines.append(_event(type="assistant", turnId=TURN_ID,
                            _ts="2026-11-05T17:37:57.000Z"))
    lines.append(_event(type="ContextualHookInvoked", turnId=TURN_ID,
                        name="probe-Stop", hookActionType="runCommand",
                        status="completed", _ts="2026-11-05T17:38:11.556Z"))
    return lines


def _usage_summary_line() -> str:
    """The usage_summary that lands a moment after Stop (line 102)."""
    return _event(
        type="usage_summary",
        turnId=TURN_ID,
        promptTurnSummaries=[{"unit": "credit", "usage": COST,
                              "unitPlural": "credits"}],
        requestIds=[f"r{i}" for i in range(REQUESTS)],
        _ts="2026-11-05T17:38:11.561Z",
    )


def _run_post_tool(monkeypatch, stdin: dict) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin)))
    return cli.main(["hook", "post-tool"])


def test_replay_sess_29996a7d_ends_with_one_record_for_4_1(tmp_path, monkeypatch):
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    sess_dir = _session_dir(kiro_home, project)
    tasks_path = project / ".kiro" / "specs" / "tombstone" / "tasks.md"

    # 1) Post-tool edits during the turn: the str_replace that checks 4.1.
    #    There is no earlier snapshot, so Bug 2's fix reads the newly checked
    #    task from the oldStr/newStr diff rather than every already-[x] task.
    post_tool_stdin = {
        "cwd": str(project),
        "turn_id": TURN_ID,
        "tool_input": {
            "path": str(tasks_path),
            "oldStr": "  - [ ] 4.1 Config, task type and similar tasks\n",
            "newStr": "  - [x] 4.1 Config, task type and similar tasks\n",
        },
    }
    assert _run_post_tool(monkeypatch, post_tool_stdin) == 0

    state = json.loads((project / TOMBSTONE_DIR / "state.json").read_text())
    assert state["turn"]["checked"] == ["4.1"]

    # 2) Stop fires BEFORE the usage_summary is on disk. The turn parses as
    #    open; Bug 1's fix still writes the record now with actual = null.
    (sess_dir / "messages.jsonl").write_text(
        "\n".join(_open_turn_lines()) + "\n", encoding="utf-8"
    )
    # Do not let the Stop hook spawn a real detached child in the test; it is
    # driven synchronously in step 3. on_turn_end is the body of the Stop hook
    # (cli._hook_stop calls it); we invoke it directly so the test kiro_home is
    # used instead of the real ~/.kiro.
    import tombstone.tombstones as tb
    monkeypatch.setattr(tb, "spawn_reconcile", lambda *a, **k: None)
    on_turn_end(project, {"session_id": SESSION_ID}, kiro_home=kiro_home)

    store = load_tombstones(project / "tombstones.json")
    assert len(store["tombstones"]) == 1
    record = store["tombstones"][0]
    assert record["id"] == "tombstone/4.1"
    assert record["task_id"] == "4.1"
    # Cost is not on disk yet, so actual is null at this point (Bug 1).
    assert record["actual"] is None
    assert record["turns"][0]["share"] == 1.0

    # 3) The usage_summary lands a moment later (line 102).
    with (sess_dir / "messages.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(_usage_summary_line() + "\n")

    # The detached reconcile child, run synchronously here with no real sleep.
    reconcile_turn(project, SESSION_ID, TURN_ID, kiro_home=kiro_home,
                   sleep=lambda _s: None, clock=lambda: 0.0)

    # 4) The final store: exactly one record for 4.1 with actual 3.2094 and 13
    #    requests.
    store = load_tombstones(project / "tombstones.json")
    assert len(store["tombstones"]) == 1
    record = store["tombstones"][0]
    assert record["id"] == "tombstone/4.1"
    assert record["task_id"] == "4.1"
    assert record["requests"] == REQUESTS
    assert record["actual"] == COST
    assert len(record["turns"]) == 1
    assert record["turns"][0]["turn_id"] == TURN_ID
    assert record["turns"][0]["requests"] == REQUESTS
    assert record["turns"][0]["actual"] == COST


def test_reconcile_timeout_leaves_actual_null_and_logs(tmp_path, monkeypatch):
    # Bug 1: when the usage_summary never lands, reconcile times out, leaves
    # actual null and writes one line to errors.log.
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    sess_dir = _session_dir(kiro_home, project)

    # The session never gets a usage_summary, so the turn stays open.
    (sess_dir / "messages.jsonl").write_text(
        "\n".join(_open_turn_lines()) + "\n", encoding="utf-8"
    )

    # Write the open-turn record first (as the Stop hook would).
    import tombstone.tombstones as tb
    monkeypatch.setattr(tb, "spawn_reconcile", lambda *a, **k: None)
    record = on_turn_end(project, {"session_id": SESSION_ID}, kiro_home=kiro_home)
    assert record is not None
    assert record["actual"] is None

    # A monotonic clock that jumps past the deadline on the second read.
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    reconcile_turn(
        project, SESSION_ID, TURN_ID, kiro_home=kiro_home,
        sleep=lambda _s: None, clock=lambda: next(ticks),
    )

    store = load_tombstones(project / "tombstones.json")
    assert store["tombstones"][0]["actual"] is None
    log = (project / TOMBSTONE_DIR / "errors.log").read_text()
    assert "reconcile timed out" in log
