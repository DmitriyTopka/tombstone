"""Reconcile child robustness and the safety net (live run of task 3.4).

The detached reconcile child did not survive in a real run: the record stayed
at actual null with the estimate of 21 requests while the real turn had 12.
Two layers fix it: a sturdier child that logs to ``reconcile.log``, and a
safety net that fills null records at every hook entry, ``cost`` and
``graveyard``, with no background process.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import tombstone.tombstones as tb
from tombstone import cli
from tombstone.tombstones import (
    TOMBSTONE_DIR,
    load_tombstones,
    on_turn_end,
    reconcile_log,
    reconcile_pending,
    reconcile_turn,
)

SESSION_ID = "sess_b2bf8b22"
TURN_ID = "ecb41faf-7b0a-43dd-998c-3745a4b08f2c"
ESTIMATE_REQUESTS = 21
REAL_REQUESTS = 12
COST = 3.8626


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".kiro" / "tombstone").mkdir(parents=True)
    return tmp_path


def _session_dir(kiro_home: Path, project: Path) -> Path:
    sess_dir = kiro_home / "sessions" / "hash1" / SESSION_ID
    sess_dir.mkdir(parents=True)
    (sess_dir / "session.json").write_text(json.dumps({
        "id": SESSION_ID, "modelId": "auto", "workspacePaths": [str(project)],
    }), encoding="utf-8")
    index = kiro_home / "session-index"
    index.mkdir(parents=True)
    (index / "hash1.jsonl").write_text(json.dumps({
        "op": "create", "sessionPath": str(sess_dir),
        "at": "2026-11-05T17:00:00Z",
    }) + "\n", encoding="utf-8")
    return sess_dir


def _event(**payload) -> str:
    return json.dumps({"id": "x", "timestamp": payload.pop("_ts", None),
                       "payload": payload})


def _open_lines() -> list[str]:
    lines = [_event(type="turn_start", turnId=TURN_ID,
                    _ts="2026-11-05T17:00:00.000Z")]
    lines += [_event(type="assistant", turnId=TURN_ID,
                     _ts="2026-11-05T17:00:01.000Z")
              for _ in range(ESTIMATE_REQUESTS)]
    return lines


def _summary_line() -> str:
    return _event(
        type="usage_summary", turnId=TURN_ID,
        promptTurnSummaries=[{"unit": "credit", "usage": COST}],
        requestIds=[f"r{i}" for i in range(REAL_REQUESTS)],
        _ts="2026-11-05T17:05:00.000Z",
    )


def _stop_without_cost(tmp_path, monkeypatch):
    """Write the Stop record while the cost is not on disk. Returns paths."""
    project = _project(tmp_path)
    kiro_home = tmp_path / "kiro_home"
    sess_dir = _session_dir(kiro_home, project)
    (sess_dir / "messages.jsonl").write_text(
        "\n".join(_open_lines()) + "\n", encoding="utf-8")
    monkeypatch.setattr(tb, "spawn_reconcile", lambda *a, **k: None)
    record = on_turn_end(project, {"session_id": SESSION_ID},
                         kiro_home=kiro_home)
    assert record["actual"] is None
    assert record["requests"] == ESTIMATE_REQUESTS
    return project, kiro_home, sess_dir


def _land_cost(sess_dir: Path) -> None:
    with (sess_dir / "messages.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(_summary_line() + "\n")


# --- layer 1: the child ------------------------------------------------------


def test_child_function_fills_record_and_logs_start_and_fill(tmp_path, monkeypatch):
    project, kiro_home, sess_dir = _stop_without_cost(tmp_path, monkeypatch)
    _land_cost(sess_dir)

    reconcile_turn(project, SESSION_ID, TURN_ID, kiro_home=kiro_home,
                   sleep=lambda _s: None, clock=lambda: 0.0)

    record = load_tombstones(project / "tombstones.json")["tombstones"][0]
    assert record["actual"] == COST
    # requests come from requestIds once actual is known, not the estimate.
    assert record["requests"] == REAL_REQUESTS
    assert record["turns"][0]["requests"] == REAL_REQUESTS

    lines = (project / TOMBSTONE_DIR / "reconcile.log").read_text().splitlines()
    assert len(lines) == 2
    assert "start" in lines[0] and TURN_ID in lines[0]
    assert "filled" in lines[1] and "3.8626" in lines[1] and "requests 12" in lines[1]


def test_child_timeout_logs_one_line_to_reconcile_log(tmp_path, monkeypatch):
    project, kiro_home, _sess = _stop_without_cost(tmp_path, monkeypatch)
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    reconcile_turn(project, SESSION_ID, TURN_ID, kiro_home=kiro_home,
                   sleep=lambda _s: None, clock=lambda: next(ticks))

    lines = (project / TOMBSTONE_DIR / "reconcile.log").read_text().splitlines()
    assert len(lines) == 2
    assert "start" in lines[0]
    assert "timed out" in lines[1]
    assert load_tombstones(project / "tombstones.json")["tombstones"][0]["actual"] is None


def test_spawn_is_fully_detached(tmp_path, monkeypatch):
    import subprocess
    import sys

    project = _project(tmp_path)
    seen = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            seen["args"] = args
            seen.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    tb.spawn_reconcile(project, SESSION_ID, TURN_ID, kiro_home=tmp_path / "h")

    assert seen["start_new_session"] is True
    assert seen["close_fds"] is True
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["cwd"] == str(project.resolve())
    assert Path(seen["args"][0]).is_absolute()
    assert seen["args"][0] == str(Path(sys.executable).absolute())
    assert Path(seen["args"][seen["args"].index("--project") + 1]).is_absolute()
    # stdout and stderr go to the project log, not to the parent's pipes.
    assert seen["stdout"] is not subprocess.PIPE
    assert (project / TOMBSTONE_DIR / "reconcile.log").exists()


def test_spawn_returns_fast(tmp_path):
    project = _project(tmp_path)
    started = time.perf_counter()
    tb.spawn_reconcile(project, "sess_none", "t1", kiro_home=tmp_path / "nohome",
                       timeout_s=1)
    assert time.perf_counter() - started < 0.2


def test_reconcile_log_never_raises_without_a_project(tmp_path):
    # A path whose parent is a file cannot be created; the call stays silent.
    blocker = tmp_path / "file"
    blocker.write_text("x")
    reconcile_log(blocker, "ignored")


# --- layer 2: the safety net --------------------------------------------------


def test_safety_net_fills_cost_that_appears_after_stop(tmp_path, monkeypatch):
    project, kiro_home, sess_dir = _stop_without_cost(tmp_path, monkeypatch)
    _land_cost(sess_dir)

    assert reconcile_pending(project, kiro_home=kiro_home) == 1

    record = load_tombstones(project / "tombstones.json")["tombstones"][0]
    assert record["actual"] == COST
    assert record["requests"] == REAL_REQUESTS
    state = json.loads((project / TOMBSTONE_DIR / "state.json").read_text())
    assert state["avg_request_price"] == round(COST / REAL_REQUESTS, 6)

    # Idempotent: a second call finds nothing to do.
    assert reconcile_pending(project, kiro_home=kiro_home) == 0


def test_safety_net_leaves_record_alone_while_cost_is_missing(tmp_path, monkeypatch):
    project, kiro_home, _sess = _stop_without_cost(tmp_path, monkeypatch)
    before = (project / "tombstones.json").read_bytes()
    assert reconcile_pending(project, kiro_home=kiro_home) == 0
    assert (project / "tombstones.json").read_bytes() == before


def test_safety_net_is_fast_with_nothing_to_do(tmp_path):
    project = _project(tmp_path)
    (project / "tombstones.json").write_text(json.dumps({
        "version": 1,
        "tombstones": [{"id": "s/1", "actual": 1.0, "turns": [
            {"turn_id": "t", "session_id": "s", "actual": 1.0}]}],
    }))
    started = time.perf_counter()
    for _ in range(10):
        assert reconcile_pending(project) == 0
    assert (time.perf_counter() - started) / 10 < 0.05
    # No file at all is also a quick no-op.
    assert reconcile_pending(tmp_path / "missing") == 0


def test_safety_net_looks_at_the_five_newest_null_records_only(tmp_path, monkeypatch):
    project = _project(tmp_path)
    records = []
    for i in range(8):
        records.append({
            "id": f"s/{i}", "actual": None,
            "turns": [{"turn_id": f"t{i}", "session_id": f"sess_{i}",
                       "actual": None, "end": f"2026-11-0{i + 1}T10:00:00Z"}],
        })
    (project / "tombstones.json").write_text(
        json.dumps({"version": 1, "tombstones": records}))

    looked_at = []
    from tombstone import sessions
    monkeypatch.setattr(
        sessions, "find_session",
        lambda proj, sid, **kw: looked_at.append(sid) or None)

    assert reconcile_pending(project) == 0
    assert len(looked_at) == 5
    assert set(looked_at) == {f"sess_{i}" for i in range(3, 8)}


def test_safety_net_runs_at_hook_entries(tmp_path, monkeypatch):
    for event in ("prompt", "post-tool", "stop", "session-start"):
        calls = []
        monkeypatch.setattr(tb, "reconcile_pending",
                            lambda project, **kw: calls.append(project) or 0)
        project = tmp_path / event
        (project / ".kiro").mkdir(parents=True)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(project)})))
        monkeypatch.setattr(tb, "on_turn_end", lambda *a, **k: None)
        assert cli.main(["hook", event]) == 0
        assert calls == [project.resolve()], event


def test_pre_tool_hook_does_not_run_the_safety_net(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tb, "reconcile_pending",
                        lambda project, **kw: calls.append(project) or 0)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    cli.main(["hook", "pre-tool"])
    assert calls == []


def test_cost_command_reconciles_first(tmp_path, monkeypatch, capsys):
    project, kiro_home, sess_dir = _stop_without_cost(tmp_path, monkeypatch)
    _land_cost(sess_dir)
    monkeypatch.chdir(project)

    assert cli.main(["cost", str(sess_dir), "--kiro-home", str(kiro_home)]) == 0

    record = load_tombstones(project / "tombstones.json")["tombstones"][0]
    assert record["actual"] == COST
    assert record["requests"] == REAL_REQUESTS


def test_graveyard_reconciles_first(tmp_path, monkeypatch):
    project, kiro_home, sess_dir = _stop_without_cost(tmp_path, monkeypatch)
    _land_cost(sess_dir)
    monkeypatch.chdir(project)
    real = tb.reconcile_pending
    monkeypatch.setattr(
        tb, "reconcile_pending",
        lambda proj, **kw: real(proj, kiro_home=kiro_home))

    cli.main(["graveyard"])

    record = load_tombstones(project / "tombstones.json")["tombstones"][0]
    assert record["actual"] == COST
