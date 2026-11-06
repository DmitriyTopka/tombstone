"""Stop orchestration (task 7.4, R5.5, R6.1, R6.3, R6.7).

Nothing real happens: notification, ``os.kill`` and subprocess are mocked.
Everything is written under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tombstone import stop, tombstones, watcher

T0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
FAKE_SESSION = types.SimpleNamespace(engine="v3", model="auto", session_id="sess_x")


@pytest.fixture(autouse=True)
def _no_real_effects(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a real os.kill or subprocess call was attempted")

    monkeypatch.setattr(os, "kill", boom)
    monkeypatch.setattr(os, "killpg", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def make_event(project: Path, **over) -> watcher.Event:
    base = dict(
        kind="over_cap", at=T0.isoformat(),
        message="TOMBSTONE: task 2.1 over budget (est. 58.0 of 40.0). Stop.",
        detail="est. 58 over cap", project=str(project), task_id="2.1",
        spec="demo", estimate=58.0, requests=40, session_id="sess_x",
        turn_id="turn1",
    )
    base.update(over)
    return watcher.Event(**base)


def make_project(tmp_path: Path) -> Path:
    spec = tmp_path / ".kiro" / "specs" / "demo"
    spec.mkdir(parents=True)
    (spec / "tasks.md").write_text("- [ ] 2.1 Build the parser\n", encoding="utf-8")
    return tmp_path


def run_begin(project: Path, event, **kw):
    sent: list[str] = []
    kw.setdefault("announce_fn", lambda message, proj=None: sent.append(message))
    entry = stop.begin_stop(event, project=project, **kw)
    return entry, sent


def test_begin_stop_writes_stop_announces_and_stores_state(tmp_path):
    project = make_project(tmp_path)
    entry, sent = run_begin(project, make_event(project))
    content = json.loads(stop.stop_path(project).read_text())
    assert content["reason"] == "over_cap"
    assert content["requests_at_stop"] == 40
    assert content["task_id"] == "2.1"
    assert sent == [make_event(project).message]
    state = watcher.read_state(project)
    assert state["stop"]["requests_at_stop"] == 40
    assert state["stop"]["time"] == "2026-11-06T12:00:00Z"
    assert state["stop"]["estimate_label"] == "est."
    assert entry["reason"] == "over_cap"
    assert stop.check_stop(project) == make_event(project).message


def test_begin_stop_writes_killed_record_without_actual(tmp_path):
    project = make_project(tmp_path)
    run_begin(project, make_event(project))
    store = tombstones.load_tombstones(project / "tombstones.json")
    (record,) = store["tombstones"]
    assert record["id"] == "demo/2.1"
    assert record["outcome"] == "killed"
    assert record["actual"] is None
    assert record["estimate"] == 58.0
    assert record["name"] == "Build the parser"
    assert record["turns"][0]["turn_id"] == "turn1"


def test_begin_stop_unknown_task_gets_unknown_record(tmp_path):
    project = make_project(tmp_path)
    run_begin(project, make_event(project, task_id=None, spec=None))
    (record,) = tombstones.load_tombstones(project / "tombstones.json")["tombstones"]
    assert record["id"] == "unknown/turn1"
    assert record["outcome"] == "killed"


def test_begin_stop_second_event_changes_nothing(tmp_path):
    project = make_project(tmp_path)
    run_begin(project, make_event(project))
    before = stop.stop_path(project).read_text()
    entry, sent = run_begin(project, make_event(project, kind="stall", message="other"))
    assert entry is None and sent == []
    assert stop.stop_path(project).read_text() == before


def test_autopsy_missing_is_skipped_with_one_line(tmp_path, monkeypatch):
    from tombstone import autopsy

    monkeypatch.delattr(autopsy, "write_autopsy", raising=False)
    project = make_project(tmp_path)
    run_begin(project, make_event(project))
    log = (project / ".kiro" / "tombstone" / "errors.log").read_text().splitlines()
    assert sum("autopsy skipped" in line for line in log) == 1
    assert (project / "tombstones.json").exists()


def test_autopsy_called_and_path_stored(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    calls = []
    monkeypatch.setattr(stop, "_stop_session", lambda *a, **k: FAKE_SESSION)

    def fake_autopsy(proj, stop_entry, session, state):
        calls.append(stop_entry["reason"])
        out = proj / ".kiro" / "tombstone" / "autopsy" / "x.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok", encoding="utf-8")
        return out

    run_begin(project, make_event(project), autopsy_fn=fake_autopsy)
    assert calls == ["over_cap"]
    state = watcher.read_state(project)
    assert state["stop"]["autopsy"].endswith("autopsy/x.md")
    assert state["autopsy_pending"]["turn_id"] == "turn1"


def test_autopsy_failure_does_not_block_record(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    monkeypatch.setattr(stop, "_stop_session", lambda *a, **k: FAKE_SESSION)

    def bad(*args):
        raise RuntimeError("nope")

    run_begin(project, make_event(project), autopsy_fn=bad)
    assert "autopsy failed" in (project / ".kiro" / "tombstone" / "errors.log").read_text()
    assert (project / "tombstones.json").exists()


def test_announce_failure_is_logged_and_stop_goes_on(tmp_path):
    project = make_project(tmp_path)

    def bad(message, proj=None):
        raise OSError("no notifier")

    run_begin(project, make_event(project), announce_fn=bad)
    assert stop.stop_path(project).exists()
    assert (project / "tombstones.json").exists()


def test_begin_stop_never_touches_project_files(tmp_path):
    project = make_project(tmp_path)
    tasks = project / ".kiro" / "specs" / "demo" / "tasks.md"
    before = tasks.read_text()
    run_begin(project, make_event(project))
    assert tasks.read_text() == before


# --- watcher wiring -----------------------------------------------------------


def test_run_dispatches_event_to_begin_stop_and_calls_kill_step(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    event = make_event(project)
    monkeypatch.setattr(watcher, "poll_once", lambda *a, **k: event)
    monkeypatch.setattr(tombstones, "reconcile_pending", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(stop, "announce", lambda message, proj=None: seen.append(message))
    kills = []
    watcher.run(project, sleep=lambda s: None, clock=lambda: T0, max_polls=1,
                kill_step=lambda proj, cfg, now: kills.append(now))
    assert stop.stop_path(project).exists()
    assert seen == [event.message]
    assert kills == [T0]


def test_kill_step_once_noop_without_stop(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    called = []
    monkeypatch.setattr(stop, "maybe_kill", lambda *a, **k: called.append(1))
    from tombstone import caps

    assert watcher.kill_step_once(project, caps.Config(), T0) is None
    assert called == []


def test_kill_step_once_passes_grace_and_request_count(tmp_path, monkeypatch):
    from tombstone import caps

    project = make_project(tmp_path)
    run_begin(project, make_event(project))
    watcher.update_state(project, lambda s: s["turn"].update(
        watch_turn_id="turn1", requests=55))
    got = {}

    def fake(proj, **kw):
        got.update(kw)
        return {"status": "waiting_grace"}

    monkeypatch.setattr(stop, "maybe_kill", fake)
    cfg = caps.Config(kill_grace_seconds=12.0)
    watcher.kill_step_once(project, cfg, T0 + timedelta(seconds=40))
    assert got["requests_now"] == 55
    assert got["grace_seconds"] == 12.0


def test_full_sequence_with_fake_processes(tmp_path):
    project = make_project(tmp_path)
    run_begin(project, make_event(project))
    sent = []
    result = stop.maybe_kill(
        project, requests_now=60, now=T0 + timedelta(seconds=31),
        grace_seconds=30, find=lambda p: [], kill_fn=lambda pid, sig: sent.append(pid),
    )
    assert result["status"] == "no_process"
    assert sent == []
    assert watcher.read_state(project)["stop"]["warnings"]
