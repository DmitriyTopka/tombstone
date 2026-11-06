"""Task 6.2: evaluate, poll_once (calibrated estimate) and the poll loop."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tombstone import caps, cli, sessions, watcher

T0 = datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc)
CFG = caps.Config(task_cap=40.0, quiet_window_minutes=10.0, avg_request_price=0.045)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def state_with_turn(**turn) -> dict:
    state = watcher.default_state()
    base = {
        "watch_turn_id": "t1",
        "started_at": iso(T0),
        "last_growth_at": iso(T0),
        "event_raised": False,
    }
    base.update(turn)
    state["turn"] = base
    return state


def obs(units: int = 5, prior: float = 0.0, turn_id: str | None = "t1",
        engine: str = "v3") -> watcher.Observation:
    return watcher.Observation(
        project="/p", engine=engine, session_id="sess_x", turn_id=turn_id,
        units=units, prior_cost=prior,
    )


# --- unit and calibration --------------------------------------------------


def test_update_unit_window_averages_actual_over_units():
    state = watcher.default_state()
    watcher.update_unit_window(state, 21, 0.63)
    assert state["avg_unit_price"] == pytest.approx(0.03)
    watcher.update_unit_window(state, 9, 0.57)
    assert state["avg_unit_price"] == pytest.approx(1.2 / 30)


def test_update_unit_window_ignores_bad_turns():
    state = watcher.default_state()
    watcher.update_unit_window(state, 0, 1.0)
    watcher.update_unit_window(state, 5, None)  # type: ignore[arg-type]
    assert state["unit_window"] == []
    assert state["avg_unit_price"] is None


def test_update_unit_window_drops_old_entries_keeping_fifty_units():
    state = watcher.default_state()
    for _ in range(5):
        watcher.update_unit_window(state, 30, 3.0)
    assert sum(item[0] for item in state["unit_window"]) == 60


def test_unit_price_bases():
    state = watcher.default_state()
    assert watcher.unit_price(state, "v3", 0.045) == (0.045, "uncalibrated")
    assert watcher.unit_price(state, "v2", 0.045) == (0.045, "per request")
    state["avg_unit_price"] = 0.03
    assert watcher.unit_price(state, "v3", 0.045) == (0.03, "calibrated")
    # V2 counts real requests: the calibrated unit price does not apply.
    assert watcher.unit_price(state, "v2", 0.045) == (0.045, "per request")


def test_open_estimate_is_units_times_unit_price_not_request_price():
    state = watcher.default_state()
    state["avg_unit_price"] = 0.03
    state["avg_request_price"] = 0.05
    assert watcher.open_estimate(state, 21, "v3", 0.045) == pytest.approx(0.63)


# --- evaluate --------------------------------------------------------------


def test_no_open_turn_gives_no_event():
    state = state_with_turn()
    assert watcher.evaluate(state, obs(turn_id=None), CFG, T0) is None


def test_over_cap_uses_unit_estimate_plus_prior_task_cost():
    state = state_with_turn()
    state["avg_unit_price"] = 0.5
    state["current_task"] = {"spec": "tombstone", "task_id": "11"}
    now = T0 + timedelta(seconds=30)
    # 60 units x 0.5 = 30 est., prior 12 -> 42 > 40
    event = watcher.evaluate(state, obs(units=60, prior=12.0), CFG, now)
    assert event is not None and event.kind == "over_cap"
    assert event.task_id == "11" and event.spec == "tombstone"
    assert event.estimate == pytest.approx(42.0)
    assert "task 11 over budget (est. 42.0 of 40.0)" in event.message
    # Below the cap: nothing.
    assert watcher.evaluate(state, obs(units=60, prior=5.0), CFG, now) is None


def test_stall_when_pulse_and_quiet_longer_than_window():
    state = state_with_turn(last_growth_at=iso(T0 + timedelta(minutes=11) - timedelta(seconds=5)))
    now = T0 + timedelta(minutes=11)
    event = watcher.evaluate(state, obs(), CFG, now)
    assert event is not None and event.kind == "stall"
    assert event.quiet_minutes == pytest.approx(11.0)
    assert "est." in event.message


def test_no_stall_inside_quiet_window():
    state = state_with_turn(last_growth_at=iso(T0 + timedelta(minutes=9)))
    now = T0 + timedelta(minutes=9, seconds=10)
    assert watcher.evaluate(state, obs(), CFG, now) is None


def test_no_stall_without_pulse():
    state = state_with_turn(last_growth_at=iso(T0))
    now = T0 + timedelta(minutes=30)  # growth was 30 minutes ago
    assert watcher.evaluate(state, obs(), CFG, now) is None


def test_no_stall_when_project_files_change():
    now = T0 + timedelta(minutes=30)
    state = state_with_turn(last_growth_at=iso(now - timedelta(seconds=3)))
    state["last_project_change"] = iso(now - timedelta(minutes=2))
    assert watcher.evaluate(state, obs(), CFG, now) is None
    # Same state without the recent change would stall.
    state["last_project_change"] = None
    assert watcher.evaluate(state, obs(), CFG, now) is not None


def test_quiet_clock_starts_at_turn_start_after_long_idle():
    # The last project change is hours old but the turn just started.
    now = T0 + timedelta(minutes=1)
    state = state_with_turn(last_growth_at=iso(now - timedelta(seconds=2)))
    state["last_project_change"] = iso(T0 - timedelta(hours=5))
    assert watcher.quiet_since(state, state["turn"]) == T0
    assert watcher.evaluate(state, obs(), CFG, now) is None


def test_one_event_per_turn():
    state = state_with_turn(last_growth_at=iso(T0 + timedelta(minutes=11)),
                            event_raised=True)
    now = T0 + timedelta(minutes=11, seconds=1)
    assert watcher.evaluate(state, obs(), CFG, now) is None


# --- poll_once with real session files ------------------------------------


def line(ts: datetime, **payload) -> str:
    return json.dumps({"id": "x", "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                       "payload": payload})


class Env:
    def __init__(self, tmp_path: Path):
        self.project = tmp_path / "proj"
        (self.project / ".kiro").mkdir(parents=True)
        (self.project / "main.py").write_text("print(1)\n", encoding="utf-8")
        self.home = tmp_path / "kiro"
        self.root = self.home / "sessions" / "hash1" / "sess_a1"
        self.root.mkdir(parents=True)
        (self.root / "session.json").write_text(json.dumps({
            "id": "sess_a1", "workspacePaths": [str(self.project)],
            "rootPaths": [str(self.project)], "modelId": "auto"}), encoding="utf-8")
        index = self.home / "session-index"
        index.mkdir(parents=True)
        (index / "hash1.jsonl").write_text(json.dumps({
            "op": "create", "sessionPath": str(self.root),
            "at": "2026-11-05T09:00:00.000Z"}) + "\n", encoding="utf-8")
        self.lines: list[str] = []

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)
        (self.root / "messages.jsonl").write_text(
            "\n".join(self.lines) + "\n", encoding="utf-8")

    def poll(self, now: datetime, cfg=CFG):
        return watcher.poll_once(self.project, cfg, now, self.home)


def closed_turn(env: Env, turn_id: str, start: datetime, assistants: int,
                requests: int, cost: float) -> None:
    lines = [line(start, type="turn_start", turnId=turn_id, executionId="e")]
    for i in range(assistants):
        lines.append(line(start + timedelta(seconds=i + 1), type="assistant", turnId=turn_id))
    lines.append(line(start + timedelta(seconds=assistants + 2), type="usage_summary",
                      turnId=turn_id, requestIds=[f"r{i}" for i in range(requests)],
                      promptTurnSummaries=[{"unit": "credit", "usage": cost}]))
    env.add(*lines)


def test_assistant_counts_per_turn(tmp_path):
    env = Env(tmp_path)
    closed_turn(env, "t1", T0, assistants=21, requests=12, cost=0.63)
    assert sessions.assistant_counts(env.root) == {"t1": 21}
    assert sessions.assistant_counts(tmp_path / "nothing") == {}


def test_calibrated_estimate_from_closed_turns_not_request_price(tmp_path):
    """Live fact: 21 assistant events counted, 12 requestIds. The open estimate
    must use credits per counted unit, not credits per request."""
    env = Env(tmp_path)
    # Closed turn: 21 units, 12 requests, 0.60 actual -> 0.0285714 per unit,
    # 0.05 per request.
    closed_turn(env, "t1", T0, assistants=21, requests=12, cost=0.60)
    t2 = T0 + timedelta(minutes=5)
    env.add(line(t2, type="turn_start", turnId="t2", executionId="e"),
            *[line(t2 + timedelta(seconds=i + 1), type="assistant", turnId="t2")
              for i in range(21)])
    assert env.poll(t2 + timedelta(seconds=30)) is None

    state = watcher.read_state(env.project)
    assert state["avg_unit_price"] == pytest.approx(0.60 / 21, rel=1e-4)
    assert state["unit_turns"] == ["t1"]
    assert state["turn"]["requests"] == 21
    assert state["turn"]["estimate"] == pytest.approx(0.60, abs=1e-3)
    assert "calibrated" in state["turn"]["estimate_basis"]
    # The per-request way would have said 21 x 0.05 = 1.05.
    assert state["turn"]["estimate"] < 1.0


def test_calibration_folds_each_closed_turn_once(tmp_path):
    env = Env(tmp_path)
    closed_turn(env, "t1", T0, assistants=10, requests=6, cost=0.3)
    env.poll(T0 + timedelta(minutes=1))
    env.poll(T0 + timedelta(minutes=2))
    state = watcher.read_state(env.project)
    assert state["unit_window"] == [[10, 0.3]]
    assert state["session"]["engine"] == "v3"
    assert state["v3_project_hash"] == "hash1"


def test_uncalibrated_first_turn_falls_back_to_request_price(tmp_path):
    env = Env(tmp_path)
    env.add(line(T0, type="turn_start", turnId="t1", executionId="e"),
            *[line(T0 + timedelta(seconds=i + 1), type="assistant", turnId="t1")
              for i in range(10)])
    env.poll(T0 + timedelta(seconds=20))
    turn = watcher.read_state(env.project)["turn"]
    assert turn["estimate"] == pytest.approx(10 * 0.045)
    assert "uncalibrated" in turn["estimate_basis"]


def test_poll_once_raises_over_cap_once(tmp_path):
    env = Env(tmp_path)
    closed_turn(env, "t1", T0, assistants=10, requests=5, cost=10.0)  # 1.0 per unit
    t2 = T0 + timedelta(minutes=1)
    env.add(line(t2, type="turn_start", turnId="t2", executionId="e"),
            *[line(t2 + timedelta(seconds=i + 1), type="assistant", turnId="t2")
              for i in range(45)])
    cfg = caps.Config(task_cap=40.0)
    now = t2 + timedelta(minutes=1)
    event = env.poll(now, cfg)
    assert event is not None and event.kind == "over_cap"
    assert event.estimate == pytest.approx(45.0)
    assert event.session_id == "sess_a1" and event.turn_id == "t2"
    state = watcher.read_state(env.project)
    assert state["last_event"]["kind"] == "over_cap"
    assert env.poll(now + timedelta(seconds=2), cfg) is None


def test_poll_once_stall_then_no_stall_when_files_change(tmp_path):
    env = Env(tmp_path)
    env.add(line(T0, type="turn_start", turnId="t1", executionId="e"))
    env.poll(T0 + timedelta(seconds=1))  # first sight, fingerprint stored

    # Agent keeps calling the model, files never change: stall after 10 min.
    event = None
    for minute in range(1, 13):
        now = T0 + timedelta(minutes=minute)
        env.add(line(now, type="assistant", turnId="t1"))
        event = env.poll(now + timedelta(seconds=1))
        if event is not None:
            break
    assert event is not None and event.kind == "stall"
    assert event.quiet_minutes > 10


def test_poll_once_project_change_keeps_stall_away(tmp_path):
    env = Env(tmp_path)
    env.add(line(T0, type="turn_start", turnId="t1", executionId="e"))
    env.poll(T0 + timedelta(seconds=1))
    for minute in range(1, 25):
        now = T0 + timedelta(minutes=minute)
        (env.project / "main.py").write_text(f"print({minute})\n" * minute,
                                             encoding="utf-8")
        env.add(line(now, type="assistant", turnId="t1"))
        assert env.poll(now + timedelta(seconds=1)) is None


def test_poll_once_without_session_is_quiet(tmp_path):
    project = tmp_path / "p"
    (project / ".kiro").mkdir(parents=True)
    assert watcher.poll_once(project, CFG, T0, tmp_path / "nokiro") is None
    assert watcher.read_state(project)["turn"] == {"watch_turn_id": None}


def test_prior_cost_of_same_task_counts_toward_cap(tmp_path):
    env = Env(tmp_path)
    (env.project / "tombstones.json").write_text(json.dumps({
        "version": 1,
        "tombstones": [{"id": "s/4.2", "task_id": "4.2", "spec": "s",
                        "outcome": "finished", "estimate": 30.0, "actual": 35.0,
                        "turns": [{"turn_id": "t0"}]}]}), encoding="utf-8")
    watcher.update_state(env.project, lambda s: s.update(
        current_task={"spec": "s", "task_id": "4.2"}, avg_unit_price=1.0))
    env.add(line(T0, type="turn_start", turnId="t1", executionId="e"),
            *[line(T0 + timedelta(seconds=i + 1), type="assistant", turnId="t1")
              for i in range(6)])
    event = env.poll(T0 + timedelta(seconds=30), caps.Config(task_cap=40.0))
    assert event is not None and event.kind == "over_cap"
    assert event.estimate == pytest.approx(41.0)  # 35 actual + 6 x 1.0 est.


# --- the loop --------------------------------------------------------------


def test_run_survives_a_failing_poll_and_logs(tmp_path, monkeypatch):
    project = tmp_path / "p"
    (project / ".kiro").mkdir(parents=True)
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("bad poll")
        return None

    monkeypatch.setattr(watcher, "poll_once", boom)
    sleeps: list[float] = []
    polls = watcher.run(project, max_polls=3, sleep=sleeps.append,
                        clock=lambda: T0)
    assert polls == 3 and len(calls) == 3
    assert sleeps == [2.0, 2.0]
    log = (project / ".kiro" / "tombstone" / "errors.log").read_text(encoding="utf-8")
    assert "RuntimeError: bad poll" in log


def test_run_dispatches_events(tmp_path, monkeypatch):
    project = tmp_path / "p"
    (project / ".kiro").mkdir(parents=True)
    event = watcher.Event(kind="stall", at="x", message="m", detail="d")
    monkeypatch.setattr(watcher, "poll_once", lambda *a, **k: event)
    got = []
    watcher.run(project, max_polls=1, sleep=lambda s: None, clock=lambda: T0,
                dispatch=lambda p, e: got.append((p, e)))
    assert got == [(project, event)]


def test_dispatch_event_calls_begin_stop_when_present(tmp_path, monkeypatch):
    from tombstone import stop

    got = []
    monkeypatch.setattr(stop, "begin_stop", got.append, raising=False)
    event = watcher.Event(kind="stall", at="x", message="m", detail="d")
    watcher.dispatch_event(tmp_path, event)
    assert got == [event]


def test_dispatch_event_logs_when_begin_stop_missing(tmp_path, monkeypatch):
    from tombstone import stop

    monkeypatch.delattr(stop, "begin_stop", raising=False)
    (tmp_path / ".kiro").mkdir()
    event = watcher.Event(kind="over_cap", at="x", message="msg", detail="d")
    watcher.dispatch_event(tmp_path, event)
    log = (tmp_path / ".kiro" / "tombstone" / "errors.log").read_text(encoding="utf-8")
    assert "begin_stop not available" in log


def test_watch_command_is_wired():
    args = cli.build_parser().parse_args(["watch", "--project", "/tmp/x"])
    assert args.func is cli.cmd_watch
    assert args.project == "/tmp/x"


def test_cmd_watch_runs_the_loop_for_the_project(tmp_path, monkeypatch):
    seen = {}

    def fake_run(project, kiro_home=None, **kwargs):
        seen["project"] = project
        seen["kiro_home"] = kiro_home
        return 0

    monkeypatch.setattr(watcher, "run", fake_run)
    args = cli.build_parser().parse_args(
        ["watch", "--project", str(tmp_path), "--kiro-home", str(tmp_path / "k")])
    assert cli.cmd_watch(args) == 0
    assert seen == {"project": tmp_path, "kiro_home": tmp_path / "k"}
